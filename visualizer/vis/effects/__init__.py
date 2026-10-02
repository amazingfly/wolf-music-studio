from .base import BaseEffect, register_effect, get_effect_class, EFFECT_REGISTRY
from .tunnel import TunnelEffect
from .speaker_orbs import SpeakerOrbsEffect
from .feedback import FeedbackPass
from .plasma_waves import PlasmaWavesEffect

__all__ = [
    "BaseEffect",
    "register_effect",
    "get_effect_class",
    "EFFECT_REGISTRY",
    "TunnelEffect",
    "SpeakerOrbsEffect",
    "FeedbackPass",
    "PlasmaWavesEffect",
]
