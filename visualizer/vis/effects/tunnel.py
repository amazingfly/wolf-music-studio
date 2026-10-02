import moderngl
from .base import BaseEffect, register_effect, QUAD_VERTEX_SHADER

TUNNEL_FRAGMENT_SHADER = """
#version 330

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_perc_exp;
uniform float u_highs;
uniform float u_mids;
uniform float u_opacity;

// Configurable effect uniforms
uniform float u_speed_base;
uniform float u_speed_perc_scale;
uniform float u_curve_amp_base;
uniform float u_curve_amp_scale;
uniform float u_rebound_pulse_freq;
uniform float u_distortion_scale;
uniform float u_pinpoint_base;
uniform float u_pinpoint_scale;
uniform float u_radial_boost_base;
uniform float u_radial_boost_scale;
uniform int   u_arcs_count;
uniform float u_arc_slot_rate;
uniform float u_arc_strike_prob;
uniform float u_arc_core_thick_base;
uniform float u_arc_core_thick_scale;
uniform float u_arc_aura_thick_base;
uniform float u_arc_aura_thick_scale;
uniform float u_arc_glow_intensity;
uniform float u_arc_core_intensity;
uniform float u_hue_speed;
uniform float u_highs_hue_scale;
uniform float u_saturation;

in vec2 v_uv;
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float tinfoilDisplacement(float h, float arc_id, float time, float highs) {
    float ftime = floor(time * 25.0) + arc_id * 17.13;
    float j1 = sin(h * 38.0 + ftime * 7.0) * 0.035;
    float j2 = cos(h * 95.0 - ftime * 13.0) * 0.020;
    float step_noise = (hash11(h * 140.0 + ftime) - 0.5) * 0.045 * (1.0 + highs * 2.5);
    float envelope = sin(h * 3.14159265);
    return (j1 + j2 + step_noise) * envelope;
}

float distToJaggedSegment(vec2 p, vec2 a, vec2 b, float arc_id, float time, float highs) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / (dot(ba, ba) + 1e-5), 0.0, 1.0);
    vec2 seg_pt = a + ba * h;
    
    vec2 perp = vec2(-ba.y, ba.x);
    float len = length(perp);
    if (len > 1e-4) perp /= len;
    
    float disp = tinfoilDisplacement(h, arc_id, time, highs);
    vec2 jagged_pt = seg_pt + perp * disp;
    return length(p - jagged_pt);
}

void main() {
    vec2 fragCoord = gl_FragCoord.xy;
    vec2 st = (fragCoord - 0.5 * u_resolution) / u_resolution.y;

    // --- 3D CURVING TUNNEL TRAJECTORY (RAPID REBOUND HARMONICS) ---
    float r_init = length(st) + 1e-5;
    float z_est = 0.6 / r_init;

    // Audio-reactive curve intensity
    float curve_amplitude = u_curve_amp_base + u_curve_amp_scale * u_perc_exp;
    vec2 center_offset = vec2(0.0);

    // Higher frequency time modulation + sine envelope forces frequent center rebounds
    float rebound_pulse = sin(u_time * u_rebound_pulse_freq);

    for (int iter = 0; iter < 2; iter++) {
        center_offset.x = (sin(z_est * 0.45 + u_time * 1.85) * 0.50 + 
                           cos(z_est * 0.22 - u_time * 1.15) * 0.30) * curve_amplitude * rebound_pulse;
        center_offset.y = (cos(z_est * 0.38 + u_time * 1.65) * 0.50 + 
                           sin(z_est * 0.18 + u_time * 1.35) * 0.30) * curve_amplitude * rebound_pulse;
        
        vec2 st_iter = st - center_offset;
        z_est = 0.6 / (length(st_iter) + 1e-5);
    }

    vec2 st_warped = st - center_offset;
    float r = length(st_warped) + 1e-5;
    float theta = atan(st_warped.y, st_warped.x);

    // --- 1. WORMHOLE TUNNEL BASE ---
    float distortion = u_distortion_scale * u_perc_exp * sin(10.0 * theta + 12.0 * r - u_time * 6.0);
    float r_distorted = r + distortion;
    float z = 0.6 / r_distorted + (u_time * (u_speed_base + u_perc_exp * u_speed_perc_scale));
    float angle = theta + sin(z * 0.5 + u_time) * 0.5;

    float pattern = (sin(8.0 * angle) * cos(10.0 * z) + 1.0) / 2.0;
    float hue = mod(z * 15.0 + u_time * u_hue_speed + u_highs * u_highs_hue_scale, 180.0) / 180.0;
    float sat = u_saturation;
    float val = clamp(pattern * (0.6 + u_perc_exp * 0.6), 0.0, 1.0);

    vec3 frame_tunnel = hsv2rgb(vec3(hue, sat, val));

    // --- 1b. INTENSIFIED TUNNEL CENTER & BRIGHT PINPOINT ---
    float radial_intensity_boost = (0.045 / (r + 0.015)) * (u_radial_boost_base + u_radial_boost_scale * u_perc_exp);
    frame_tunnel *= (1.0 + radial_intensity_boost);

    float pin_radius = u_pinpoint_base + u_pinpoint_scale * u_perc_exp;
    float pinpoint = smoothstep(pin_radius, 0.0, r);
    frame_tunnel += vec3(pinpoint * (2.8 + 3.5 * u_perc_exp));

    // --- 2. TUNNEL WALL ELECTRICAL ARCS ---
    vec3 tunnel_arcs = vec3(0.0);
    float aa = 1.5;

    for (int i = 0; i < 8; i++) {
        if (i >= u_arcs_count) break;
        float arc_id = float(i);
        float slot_rate = u_arc_slot_rate; 
        float ftime_slot = floor(u_time * slot_rate + arc_id * 3.7);
        float arc_phase = fract(u_time * slot_rate + arc_id * 3.7);
        
        if (hash11(ftime_slot * 8.3 + arc_id) < u_arc_strike_prob) continue;
        
        float a1 = hash11(ftime_slot * 1.1 + arc_id) * 6.28318;
        float a2 = a1 + 1.57 + hash11(ftime_slot * 2.3 + arc_id) * 3.14159; 
        
        float wall_r1 = 0.15 + 0.35 * hash11(ftime_slot * 3.1 + arc_id);
        float wall_r2 = wall_r1 + (hash11(ftime_slot * 4.7) - 0.5) * 0.15;

        vec2 pA = vec2(cos(a1), sin(a1)) * wall_r1;
        vec2 pB = vec2(cos(a2), sin(a2)) * wall_r2;

        float d_arc = distToJaggedSegment(st_warped, pA, pB, arc_id, u_time, u_highs);

        float core_thick = u_arc_core_thick_base + u_arc_core_thick_scale * u_perc_exp;
        float aura_thick = u_arc_aura_thick_base + u_arc_aura_thick_scale * u_perc_exp;

        float arc_hue = mod(0.55 + 0.3 * hash11(arc_id * 12.3) + u_time * 0.1, 1.0);
        vec3 aura_col = hsv2rgb(vec3(arc_hue, 0.9, 1.0));
        vec3 core_col = vec3(1.0);

        float core_mask = 1.0 - smoothstep(core_thick * 0.5 - 0.001, core_thick * 0.5 + 0.001, d_arc);
        float aura_mask = 1.0 - smoothstep(0.0, aura_thick, d_arc);

        float strike_flash = exp(-arc_phase * 7.0) * 1.6;
        float lingering_glow = pow(1.0 - arc_phase, 0.8) * 0.85;
        float persistence_factor = strike_flash + lingering_glow;

        float arc_intensity = (0.4 + 0.6 * hash11(ftime_slot * 5.9)) * (1.0 + 2.0 * u_perc_exp) * persistence_factor;

        tunnel_arcs += aura_col * pow(aura_mask, 2.0) * u_arc_glow_intensity * arc_intensity;
        tunnel_arcs += core_col * core_mask * u_arc_core_intensity * arc_intensity;
    }

    frame_tunnel += tunnel_arcs;

    fragColor = vec4(frame_tunnel * u_opacity, 1.0);
}
"""


@register_effect("tunnel")
class TunnelEffect(BaseEffect):
    """3D Curving Wormhole Tunnel with electric wall arcs and audio reactivity."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int, params: dict = None, layer_config: dict = None):
        super().__init__(ctx, width, height, params, layer_config)
        prog = self.ctx.program(
            vertex_shader=QUAD_VERTEX_SHADER,
            fragment_shader=TUNNEL_FRAGMENT_SHADER,
        )
        self.setup_fullscreen_quad(prog)

        # Apply static config params to uniforms
        self.set_uniform_safe("u_resolution", (float(width), float(height)))
        self.set_uniform_safe("u_speed_base", float(self.params.get("speed_base", 1.5)))
        self.set_uniform_safe("u_speed_perc_scale", float(self.params.get("speed_percussion_scale", 2.5)))
        self.set_uniform_safe("u_curve_amp_base", float(self.params.get("curve_amplitude_base", 0.55)))
        self.set_uniform_safe("u_curve_amp_scale", float(self.params.get("curve_amplitude_scale", 0.45)))
        self.set_uniform_safe("u_rebound_pulse_freq", float(self.params.get("rebound_pulse_freq", 2.2)))
        self.set_uniform_safe("u_distortion_scale", float(self.params.get("distortion_scale", 0.25)))
        self.set_uniform_safe("u_pinpoint_base", float(self.params.get("pinpoint_radius_base", 0.009)))
        self.set_uniform_safe("u_pinpoint_scale", float(self.params.get("pinpoint_radius_scale", 0.006)))
        self.set_uniform_safe("u_radial_boost_base", float(self.params.get("radial_boost_base", 1.3)))
        self.set_uniform_safe("u_radial_boost_scale", float(self.params.get("radial_boost_scale", 2.2)))
        self.set_uniform_safe("u_arcs_count", int(self.params.get("arcs_count", 3)))
        self.set_uniform_safe("u_arc_slot_rate", float(self.params.get("arc_slot_rate", 2.4)))
        self.set_uniform_safe("u_arc_strike_prob", float(self.params.get("arc_strike_prob", 0.45)))
        self.set_uniform_safe("u_arc_core_thick_base", float(self.params.get("arc_core_thick_base", 0.0035)))
        self.set_uniform_safe("u_arc_core_thick_scale", float(self.params.get("arc_core_thick_scale", 0.004)))
        self.set_uniform_safe("u_arc_aura_thick_base", float(self.params.get("arc_aura_thick_base", 0.020)))
        self.set_uniform_safe("u_arc_aura_thick_scale", float(self.params.get("arc_aura_thick_scale", 0.025)))
        self.set_uniform_safe("u_arc_glow_intensity", float(self.params.get("arc_glow_intensity", 1.8)))
        self.set_uniform_safe("u_arc_core_intensity", float(self.params.get("arc_core_intensity", 2.5)))
        self.set_uniform_safe("u_hue_speed", float(self.params.get("hue_speed", 40.0)))
        self.set_uniform_safe("u_highs_hue_scale", float(self.params.get("highs_hue_scale", 60.0)))
        self.set_uniform_safe("u_saturation", float(self.params.get("saturation", 230.0 / 255.0)))

        self.morph_scale = float(self.params.get("morph_scale", 0.7))

    def render(self, frame_ctx: dict):
        morph = frame_ctx.get("morph_param", 0.5)
        # Match original: frame_tunnel * (1.0 - u_morph_param * 0.7)
        effective_opacity = self.opacity * (1.0 - morph * self.morph_scale)

        self.set_uniform_safe("u_time", float(frame_ctx["time"]))
        self.set_uniform_safe("u_perc_exp", float(frame_ctx["perc_exp"]))
        self.set_uniform_safe("u_highs", float(frame_ctx["highs"]))
        self.set_uniform_safe("u_mids", float(frame_ctx["mids"]))
        self.set_uniform_safe("u_opacity", float(effective_opacity))

        self.vao.render(moderngl.TRIANGLE_STRIP)
