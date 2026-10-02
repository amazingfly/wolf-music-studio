from .config import load_config, save_config, DEFAULT_CONFIG
from .audio import extract_audio_features, compute_frame_signals
from .encoder import FFmpegEncoder
from .renderer import ModernGLRenderer, render_visualizer_pipeline
from .effects import (
    BaseEffect,
    register_effect,
    get_effect_class,
    EFFECT_REGISTRY,
    TunnelEffect,
    SpeakerOrbsEffect,
    FeedbackPass,
    PlasmaWavesEffect,
)

__all__ = [
    "load_config",
    "save_config",
    "DEFAULT_CONFIG",
    "extract_audio_features",
    "compute_frame_signals",
    "FFmpegEncoder",
    "ModernGLRenderer",
    "render_visualizer_pipeline",
    "BaseEffect",
    "register_effect",
    "get_effect_class",
    "EFFECT_REGISTRY",
    "TunnelEffect",
    "SpeakerOrbsEffect",
    "FeedbackPass",
    "PlasmaWavesEffect",
]
