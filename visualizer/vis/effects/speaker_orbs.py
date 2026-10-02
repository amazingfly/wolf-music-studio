import numpy as np
import moderngl
from .base import BaseEffect, register_effect, QUAD_VERTEX_SHADER

SPEAKER_ORBS_FRAGMENT_SHADER = """
#version 330

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_perc_exp;
uniform float u_highs;
uniform float u_mids;
uniform float u_opacity;

uniform int   u_num_orbs;
uniform vec2  u_orb_pos[8];

// Configurable uniforms
uniform float u_flare_perc_scale;
uniform float u_reach_scale_base;
uniform float u_reach_scale_perc;
uniform float u_wobble_freq_base;
uniform float u_wobble_amp_base;
uniform float u_wobble_amp_perc;
uniform int   u_ring_count;
uniform float u_ring_speed;
uniform float u_inter_arcs_dist_threshold;
uniform float u_inter_arcs_threshold_scale;

in vec2 v_uv;
out vec4 fragColor;

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

float distToOrbSegment(vec2 p, vec2 a, vec2 b, float highs, float t) {
    vec2 pa = p - a, ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    vec2 seg_pt = a + ba * h;
    
    vec2 perp = vec2(-ba.y, ba.x);
    float len = length(perp);
    if (len > 1e-4) perp /= len;
    
    float wave = sin(h * 35.0 + t * 45.0) * cos(h * 15.0 - t * 20.0) * 8.0 * (0.25 + 0.20 * highs);
    vec2 w_pt = seg_pt + perp * wave;
    return length(p - w_pt);
}

void main() {
    vec2 fragCoord = gl_FragCoord.xy;
    vec3 frame_orbs = vec3(0.0);
    float perc_flare = 1.0 + u_flare_perc_scale * u_perc_exp;
    float aa = 1.5;

    // --- 3. ORBS, CONES & EXPONENTIAL RINGS ---
    for (int k = 0; k < 8; k++) {
        if (k >= u_num_orbs) break;
        vec2 px = u_orb_pos[k];
        float d = length(fragCoord - px);

        float base_r = float(24 + (k % 3) * 8);
        float r_orb = base_r * (1.0 + 7.5 * u_perc_exp);
        float d_orb = r_orb * 2.0;

        float hue_orb = mod(u_time * 30.0 + float(k) * 25.0, 180.0) / 180.0;
        vec3 base_bgr = hsv2rgb(vec3(hue_orb, 1.0, 1.0));
        vec3 flared_bgr = clamp(base_bgr * perc_flare, 0.0, 1.0);

        // Distorted Concentric Soundwave Rings
        float max_reach = d_orb * (u_reach_scale_base + u_reach_scale_perc * u_perc_exp);
        vec2 diff = fragCoord - px;
        float ring_angle = atan(diff.y, diff.x);

        for (int r_idx = 0; r_idx < 8; r_idx++) {
            if (r_idx >= u_ring_count) break;
            float ring_phase = mod(u_time * u_ring_speed + float(r_idx) * 0.25 + float(k) * 0.25, 1.0);
            float curr_r = ring_phase * max_reach;
            float ring_alpha = pow(1.0 - ring_phase, 1.2) * (0.05 + 0.95 * u_perc_exp);

            if (curr_r > r_orb * 0.4 && ring_alpha > 0.04) {
                float wobble_freq = u_wobble_freq_base + mod(float(k), 3.0) * 2.0;
                float wobble_amp = curr_r * (u_wobble_amp_base + u_wobble_amp_perc * u_perc_exp);
                float distorted_r = curr_r + wobble_amp * sin(wobble_freq * ring_angle + u_time * 9.0);
                
                float thickness = max(4.0, (10.0 + 45.0 * u_perc_exp) * (1.0 - ring_phase * 0.4));
                float ring_dist = abs(d - distorted_r);
                
                float ring_mask = 1.0 - smoothstep(thickness * 0.5 - aa, thickness * 0.5 + aa, ring_dist);
                frame_orbs += flared_bgr * ring_alpha * ring_mask;

                if (u_perc_exp > 0.12) {
                    float white_thick = max(2.0, thickness * 0.35);
                    float white_mask = 1.0 - smoothstep(white_thick * 0.5 - aa, white_thick * 0.5 + aa, ring_dist);
                    frame_orbs += vec3(1.0) * ring_alpha * 0.95 * white_mask;
                }
            }
        }

        // Cartoon Speaker Cone Body
        float surround_r = r_orb * 1.25;
        float surround_mask = 1.0 - smoothstep(surround_r - aa, surround_r + aa, d);
        float surround_stroke = abs(d - surround_r);
        float surround_line = 1.0 - smoothstep(max(2.0, 3.0 + 6.0 * u_perc_exp) - aa, max(2.0, 3.0 + 6.0 * u_perc_exp) + aa, surround_stroke);
        
        if (d <= surround_r + aa) {
            frame_orbs = mix(frame_orbs, flared_bgr * 0.4, surround_mask);
            frame_orbs = mix(frame_orbs, vec3(1.0), surround_line);
        }

        // Paper Cone Body
        float cone_mask = 1.0 - smoothstep(r_orb - aa, r_orb + aa, d);
        float cone_line = 1.0 - smoothstep(max(2.0, 3.0 + 7.0 * u_perc_exp) - aa, max(2.0, 3.0 + 7.0 * u_perc_exp) + aa, abs(d - r_orb));

        if (d <= r_orb + aa) {
            frame_orbs = mix(frame_orbs, flared_bgr, cone_mask);
            frame_orbs = mix(frame_orbs, vec3(0.0), cone_line);

            // Concentric Ridges
            for (int r_scale = 0; r_scale < 2; r_scale++) {
                float scale_val = (r_scale == 0) ? 0.75 : 0.52;
                float ridge_r = max(2.0, r_orb * scale_val);
                float ridge_line = 1.0 - smoothstep(max(2.0, 2.0 + 5.0 * u_perc_exp) - aa, max(2.0, 2.0 + 5.0 * u_perc_exp) + aa, abs(d - ridge_r));
                frame_orbs = mix(frame_orbs, flared_bgr * 0.6, ridge_line);
            }

            // Center Dust Cap
            float cap_r = max(3.0, r_orb * 0.35 * (1.0 + 2.5 * u_perc_exp));
            float cap_mask = 1.0 - smoothstep(cap_r - aa, cap_r + aa, d);
            float cap_line = 1.0 - smoothstep(max(2.0, 2.0 + 4.0 * u_perc_exp) - aa, max(2.0, 2.0 + 4.0 * u_perc_exp) + aa, abs(d - cap_r));
            frame_orbs = mix(frame_orbs, vec3(1.0), cap_mask);
            frame_orbs = mix(frame_orbs, vec3(0.0), cap_line);
        }
    }

    // --- 4. ORB INTER-LIGHTNING ARCS ---
    float arc_thresh = min(u_resolution.x, u_resolution.y) * u_inter_arcs_dist_threshold + u_perc_exp * u_inter_arcs_threshold_scale;
    for (int a = 0; a < 8; a++) {
        for (int b = a + 1; b < 8; b++) {
            if (a >= u_num_orbs || b >= u_num_orbs) break;
            vec2 p1 = u_orb_pos[a];
            vec2 p2 = u_orb_pos[b];
            float dist_orbs = length(p1 - p2);

            if (dist_orbs < arc_thresh) {
                float arc_d = distToOrbSegment(fragCoord, p1, p2, u_highs, u_time);
                float arc_hue = mod(u_time * 50.0 + dist_orbs, 180.0) / 180.0;
                vec3 arc_col = hsv2rgb(vec3(arc_hue, 1.0, 1.0));

                float arc_mask = 1.0 - smoothstep(1.5 - aa, 1.5 + aa, arc_d);
                float core_mask = 1.0 - smoothstep(0.5 - aa, 0.5 + aa, arc_d);

                frame_orbs = mix(frame_orbs, arc_col, arc_mask);
                frame_orbs = mix(frame_orbs, vec3(1.0), core_mask);
            }
        }
    }

    fragColor = vec4(frame_orbs * u_opacity, 1.0);
}
"""


@register_effect("speaker_orbs")
class SpeakerOrbsEffect(BaseEffect):
    """Soundwave cartoon speaker cones with expanding rings and lightning arcs."""

    def __init__(self, ctx: moderngl.Context, width: int, height: int, params: dict = None, layer_config: dict = None):
        super().__init__(ctx, width, height, params, layer_config)
        # Default blend mode for orbs is additive
        if "blend_mode" not in self.layer_config:
            self.blend_mode = "additive"

        prog = self.ctx.program(
            vertex_shader=QUAD_VERTEX_SHADER,
            fragment_shader=SPEAKER_ORBS_FRAGMENT_SHADER,
        )
        self.setup_fullscreen_quad(prog)

        self.num_orbs = min(8, int(self.params.get("num_orbs", 8)))
        seed = int(self.params.get("random_seed", 42))
        rng = np.random.RandomState(seed)
        self.orbs_phase = rng.rand(self.num_orbs) * 2 * np.pi
        speed_min = float(self.params.get("orb_speed_min", 0.8))
        speed_max = float(self.params.get("orb_speed_max", 2.0))
        self.orbs_speed = rng.uniform(speed_min, speed_max, size=self.num_orbs)
        self.orb_motion_radius = float(self.params.get("orb_motion_radius", 0.35))

        self.morph_base = float(self.params.get("morph_base", 0.3))
        self.morph_scale = float(self.params.get("morph_scale", 0.7))

        self.set_uniform_safe("u_resolution", (float(width), float(height)))
        self.set_uniform_safe("u_num_orbs", int(self.num_orbs))
        self.set_uniform_safe("u_flare_perc_scale", float(self.params.get("flare_percussion_scale", 3.0)))
        self.set_uniform_safe("u_reach_scale_base", float(self.params.get("reach_scale_base", 10.0)))
        self.set_uniform_safe("u_reach_scale_perc", float(self.params.get("reach_scale_percussion", 35.0)))
        self.set_uniform_safe("u_wobble_freq_base", float(self.params.get("wobble_freq_base", 5.0)))
        self.set_uniform_safe("u_wobble_amp_base", float(self.params.get("wobble_amp_base", 0.08)))
        self.set_uniform_safe("u_wobble_amp_perc", float(self.params.get("wobble_amp_percussion", 0.30)))
        self.set_uniform_safe("u_ring_count", int(self.params.get("ring_count", 4)))
        self.set_uniform_safe("u_ring_speed", float(self.params.get("ring_speed", 4.0)))
        self.set_uniform_safe("u_inter_arcs_dist_threshold", float(self.params.get("inter_arcs_dist_threshold", 0.35)))
        self.set_uniform_safe("u_inter_arcs_threshold_scale", float(self.params.get("inter_arcs_threshold_scale", 150.0)))

    def compute_positions(self, t: float):
        positions = []
        cx = self.width / 2.0
        cy = self.height / 2.0
        rx = self.width * self.orb_motion_radius
        ry = self.height * self.orb_motion_radius

        for k in range(self.num_orbs):
            px = float(cx + rx * np.sin(t * self.orbs_speed[k] + self.orbs_phase[k]))
            py = float(cy + ry * np.cos(t * self.orbs_speed[k] * 0.8 + self.orbs_phase[k]))
            positions.append((px, py))
        return positions

    def render(self, frame_ctx: dict):
        t = float(frame_ctx["time"])
        positions = self.compute_positions(t)
        morph = frame_ctx.get("morph_param", 0.5)

        # Match original: frame_orbs * (0.3 + u_morph_param * 0.7)
        effective_opacity = self.opacity * (self.morph_base + morph * self.morph_scale)

        self.set_uniform_safe("u_time", t)
        self.set_uniform_safe("u_perc_exp", float(frame_ctx["perc_exp"]))
        self.set_uniform_safe("u_highs", float(frame_ctx["highs"]))
        self.set_uniform_safe("u_mids", float(frame_ctx["mids"]))
        self.set_uniform_safe("u_opacity", float(effective_opacity))
        self.set_uniform_safe("u_orb_pos", positions)

        self.vao.render(moderngl.TRIANGLE_STRIP)
