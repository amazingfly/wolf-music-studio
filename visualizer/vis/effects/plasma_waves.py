import moderngl
from .base import BaseEffect, register_effect, QUAD_VERTEX_SHADER

PLASMA_FRAGMENT_SHADER = """
#version 330

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_perc_exp;
uniform float u_highs;
uniform float u_mids;
uniform float u_opacity;

// Configurable uniforms
uniform float u_frequency;
uniform float u_speed;
uniform float u_color_shift;
uniform float u_rings_intensity;
uniform float u_percussion_scale;

in vec2 v_uv;
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

void main() {
    vec2 st = (gl_FragCoord.xy - 0.5 * u_resolution) / u_resolution.y;
    float r = length(st) + 1e-4;
    float a = atan(st.y, st.x);

    float t = u_time * u_speed;
    float perc = u_perc_exp * u_percussion_scale;

    // Multi-harmonic plasma waves
    float v1 = sin(st.x * u_frequency + t);
    float v2 = sin(st.y * u_frequency + t * 0.7);
    float v3 = sin((st.x + st.y) * u_frequency * 0.7 + t * 1.2);
    float v4 = sin(r * (u_frequency * 1.5) - t * 2.0 + perc * 4.0);

    float plasma = (v1 + v2 + v3 + v4) / 4.0;

    // Audio-reactive vortex ring ripples
    float ripple = sin(r * 25.0 - t * 5.0 + a * 3.0) * (0.2 + 0.8 * perc);
    plasma += ripple * u_rings_intensity;

    float hue = fract(plasma * 0.5 + t * 0.1 + u_color_shift + u_highs * 0.3);
    float sat = 0.85;
    float val = clamp((plasma * 0.5 + 0.5) * (0.7 + 0.5 * perc), 0.0, 1.0);

    vec3 col = hsv2rgb(vec3(hue, sat, val));

    // Alpha falloff toward edges so it layers smoothly
    float edge_fade = smoothstep(1.2, 0.2, r);

    fragColor = vec4(col * u_opacity, edge_fade * u_opacity);
}
"""


@register_effect("plasma_waves")
class PlasmaWavesEffect(BaseEffect):
    """Psychedelic audio-reactive multi-harmonic plasma wave effect."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int, params: dict = None, layer_config: dict = None):
        super().__init__(ctx, width, height, params, layer_config)
        prog = self.ctx.program(
            vertex_shader=QUAD_VERTEX_SHADER,
            fragment_shader=PLASMA_FRAGMENT_SHADER,
        )
        self.setup_fullscreen_quad(prog)

        self.set_uniform_safe("u_resolution", (float(width), float(height)))
        self.set_uniform_safe("u_frequency", float(self.params.get("frequency", 6.0)))
        self.set_uniform_safe("u_speed", float(self.params.get("speed", 1.8)))
        self.set_uniform_safe("u_color_shift", float(self.params.get("color_shift", 0.0)))
        self.set_uniform_safe("u_rings_intensity", float(self.params.get("rings_intensity", 0.4)))
        self.set_uniform_safe("u_percussion_scale", float(self.params.get("percussion_scale", 1.5)))

    def render(self, frame_ctx: dict):
        self.set_uniform_safe("u_time", float(frame_ctx["time"]))
        self.set_uniform_safe("u_perc_exp", float(frame_ctx["perc_exp"]))
        self.set_uniform_safe("u_highs", float(frame_ctx["highs"]))
        self.set_uniform_safe("u_mids", float(frame_ctx["mids"]))
        self.set_uniform_safe("u_opacity", float(self.opacity))

        self.vao.render(moderngl.TRIANGLE_STRIP)
