import moderngl
from .base import BaseEffect, register_effect, QUAD_VERTEX_SHADER

FEEDBACK_FRAGMENT_SHADER = """
#version 330

uniform sampler2D u_scene_tex;
uniform sampler2D u_prev_feedback;
uniform float u_base_decay;
uniform float u_decay_boost;
uniform float u_perc_exp;
uniform float u_time;

in vec2 v_uv;
out vec4 fragColor;

void main() {
    vec3 scene_col = texture(u_scene_tex, v_uv).rgb;
    vec3 prev_col  = texture(u_prev_feedback, v_uv).rgb;

    float decay_factor = clamp(u_base_decay + u_perc_exp * u_decay_boost, 0.0, 0.98);
    vec3 final_color = mix(scene_col, prev_col, decay_factor);

    fragColor = vec4(final_color, 1.0);
}
"""


@register_effect("feedback")
class FeedbackPass(BaseEffect):
    """Composites current scene with previous frame feedback for trails and decay."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int, params: dict = None, layer_config: dict = None):
        super().__init__(ctx, width, height, params, layer_config)
        prog = self.ctx.program(
            vertex_shader=QUAD_VERTEX_SHADER,
            fragment_shader=FEEDBACK_FRAGMENT_SHADER,
        )
        self.setup_fullscreen_quad(prog)

        self.base_decay = float(self.params.get("base_decay", 0.65))
        self.decay_boost = float(self.params.get("percussion_decay_boost", 0.20))

        self.set_uniform_safe("u_scene_tex", 0)
        self.set_uniform_safe("u_prev_feedback", 1)
        self.set_uniform_safe("u_base_decay", self.base_decay)
        self.set_uniform_safe("u_decay_boost", self.decay_boost)

    def render_feedback(self, scene_tex: moderngl.Texture, prev_fb_tex: moderngl.Texture, frame_ctx: dict):
        self.set_uniform_safe("u_perc_exp", float(frame_ctx["perc_exp"]))
        self.set_uniform_safe("u_time", float(frame_ctx["time"]))

        scene_tex.use(location=0)
        prev_fb_tex.use(location=1)

        self.vao.render(moderngl.TRIANGLE_STRIP)

    def render(self, frame_ctx: dict):
        # When called as standard layer, fallback if textures provided in frame_ctx
        scene_tex = frame_ctx.get("scene_tex")
        prev_fb_tex = frame_ctx.get("prev_feedback_tex")
        if scene_tex and prev_fb_tex:
            self.render_feedback(scene_tex, prev_fb_tex, frame_ctx)
