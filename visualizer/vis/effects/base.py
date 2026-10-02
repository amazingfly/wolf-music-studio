import abc
import moderngl
import numpy as np

EFFECT_REGISTRY = {}


def register_effect(name: str):
    """Decorator to register an effect class into the global effect registry."""
    def decorator(cls):
        EFFECT_REGISTRY[name] = cls
        return cls
    return decorator


def get_effect_class(name: str):
    """Retrieve an effect class by its registered name."""
    if name not in EFFECT_REGISTRY:
        raise ValueError(
            f"Effect '{name}' is not registered. Available effects: {list(EFFECT_REGISTRY.keys())}"
        )
    return EFFECT_REGISTRY[name]


QUAD_VERTEX_SHADER = """
#version 330
in vec2 in_vert;
out vec2 v_uv;

void main() {
    v_uv = in_vert * 0.5 + 0.5;
    gl_Position = vec4(in_vert, 0.0, 1.0);
}
"""


class BaseEffect(abc.ABC):
    """
    Abstract base class for all visualizer layers and effects.
    Subclasses implement shader setup, uniform binding, and rendering.
    """

    def __init__(
        self,
        ctx: moderngl.Context,
        width: int,
        height: int,
        params: dict = None,
        layer_config: dict = None,
    ):
        self.ctx = ctx
        self.width = width
        self.height = height
        self.params = params or {}
        self.layer_config = layer_config or {}
        self.name = self.layer_config.get("name", self.__class__.__name__)
        self.enabled = self.layer_config.get("enabled", True)
        self.order = self.layer_config.get("order", 0)
        self.blend_mode = self.layer_config.get("blend_mode", "replace")
        self.opacity = float(self.layer_config.get("opacity", 1.0))
        self.vao = None
        self.vbo = None
        self.prog = None

    def setup_fullscreen_quad(self, prog: moderngl.Program):
        """Creates standard fullscreen quad VAO and VBO for a shader program."""
        self.prog = prog
        quad_vertices = np.array([
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
             1.0,  1.0,
        ], dtype='f4')
        self.vbo = self.ctx.buffer(quad_vertices)
        self.vao = self.ctx.simple_vertex_array(self.prog, self.vbo, 'in_vert')

    def set_uniform_safe(self, name: str, value):
        """Safely sets a uniform value if it exists in the active program."""
        if self.prog and name in self.prog:
            self.prog[name].value = value

    @abc.abstractmethod
    def render(self, frame_ctx: dict):
        """
        Render this effect into the currently bound framebuffer.
        frame_ctx contains:
            - time: float
            - frame_index: int
            - perc_exp: float
            - highs: float
            - mids: float
            - morph_param: float
            - width: int
            - height: int
            - extra effect-specific computed data
        """
        pass

    def resize(self, width: int, height: int):
        """Handle viewport resize if needed."""
        self.width = width
        self.height = height
        self.set_uniform_safe("u_resolution", (float(width), float(height)))

    def destroy(self):
        """Free GPU resources allocated by this effect."""
        if self.vao:
            self.vao.release()
            self.vao = None
        if self.vbo:
            self.vbo.release()
            self.vbo = None
        if self.prog:
            self.prog.release()
            self.prog = None
