import os
import json
from copy import deepcopy

DEFAULT_CONFIG = {
    "video": {
        "fps": 30,
        "width": 1280,
        "height": 720,
        "use_vaapi": True,
        "vaapi_device": "/dev/dri/renderD128",
        "output_dir": "./output",
        "audio_bitrate": "192k",
    },
    "audio": {
        "n_fft": 2048,
        "mid_freq_range": [180, 2000],
        "high_freq_range": [2000, 8000],
        "percussion_decay": 0.30,
        "energy_decay": 0.65,
        "exp_factor": 3.8,
        "morph_speed": 0.3,
    },
    "feedback": {
        "enabled": True,
        "base_decay": 0.65,
        "percussion_decay_boost": 0.20,
    },
    "layers": [
        {
            "name": "tunnel",
            "type": "tunnel",
            "enabled": True,
            "order": 10,
            "blend_mode": "replace",
            "opacity": 1.0,
            "params": {
                "speed_base": 1.5,
                "speed_percussion_scale": 2.5,
                "curve_amplitude_base": 0.55,
                "curve_amplitude_scale": 0.45,
                "rebound_pulse_freq": 2.2,
                "distortion_scale": 0.25,
                "pinpoint_radius_base": 0.009,
                "pinpoint_radius_scale": 0.006,
                "radial_boost_base": 1.3,
                "radial_boost_scale": 2.2,
                "arcs_count": 3,
                "arc_slot_rate": 2.4,
                "arc_strike_prob": 0.45,
                "arc_core_thick_base": 0.0035,
                "arc_core_thick_scale": 0.004,
                "arc_aura_thick_base": 0.020,
                "arc_aura_thick_scale": 0.025,
                "arc_glow_intensity": 1.8,
                "arc_core_intensity": 2.5,
                "hue_speed": 40.0,
                "highs_hue_scale": 60.0,
                "morph_scale": 0.7,
                "saturation": 0.902,
            },
        },
        {
            "name": "speaker_orbs",
            "type": "speaker_orbs",
            "enabled": True,
            "order": 20,
            "blend_mode": "additive",
            "opacity": 1.0,
            "params": {
                "num_orbs": 8,
                "random_seed": 42,
                "orb_speed_min": 0.8,
                "orb_speed_max": 2.0,
                "orb_motion_radius": 0.35,
                "flare_percussion_scale": 3.0,
                "reach_scale_base": 10.0,
                "reach_scale_percussion": 35.0,
                "wobble_freq_base": 5.0,
                "wobble_amp_base": 0.08,
                "wobble_amp_percussion": 0.30,
                "ring_count": 4,
                "ring_speed": 4.0,
                "inter_arcs_dist_threshold": 0.35,
                "inter_arcs_threshold_scale": 150.0,
                "morph_base": 0.3,
                "morph_scale": 0.7,
            },
        },
        {
            "name": "plasma_waves",
            "type": "plasma_waves",
            "enabled": False,
            "order": 30,
            "blend_mode": "additive",
            "opacity": 0.75,
            "params": {
                "frequency": 6.0,
                "speed": 1.8,
                "color_shift": 0.0,
                "rings_intensity": 0.4,
                "percussion_scale": 1.5,
            },
        },
        {
            "name": "caption_layer",
            "type": "overlay",
            "enabled": False,
            "order": 100,
            "blend_mode": "alpha",
            "opacity": 1.0,
            "params": {
                "placeholder": "Reserved for future karaoke / captioning stage",
            },
        },
    ],
}


def load_config(config_path: str = None) -> dict:
    """
    Loads JSON configuration from disk, merging with DEFAULT_CONFIG for safety.
    """
    config = deepcopy(DEFAULT_CONFIG)

    if not config_path:
        config_path = "config.json"

    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)

            # Deep merge sections
            for section in ["video", "audio", "feedback"]:
                if section in user_cfg and isinstance(user_cfg[section], dict):
                    config[section].update(user_cfg[section])

            if "layers" in user_cfg and isinstance(user_cfg["layers"], list):
                config["layers"] = user_cfg["layers"]

        except Exception as e:
            print(f"[!] Warning: Failed to parse '{config_path}': {e}. Using defaults.")
    else:
        print(f"[*] Config file '{config_path}' not found. Using defaults.")

    return config


def save_config(config: dict, config_path: str = "config.json"):
    """Saves configuration dict to a formatted JSON file."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
