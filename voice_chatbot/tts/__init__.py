"""
TTS模块 (tts)

提供文本转语音功能
"""

from .edge_tts_engine import EdgeTTSEngine
from .audio_player import AudioPlayer, PlaybackStatus, InterruptReason

# CosyVoice3引擎（可选，需要CosyVoice模型）
try:
    from .cosyvoice3_engine import CosyVoice3Engine
    HAS_COSYVOICE3 = True
except ImportError:
    CosyVoice3Engine = None
    HAS_COSYVOICE3 = False

__all__ = [
    'EdgeTTSEngine',
    'CosyVoice3Engine',
    'HAS_COSYVOICE3',
    'AudioPlayer',
    'PlaybackStatus',
    'InterruptReason',
]

