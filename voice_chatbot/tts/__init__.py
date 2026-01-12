"""
TTS模块 (tts)

提供文本转语音功能
"""

from .edge_tts_engine import EdgeTTSEngine

# CosyVoice3引擎（可选，需要CosyVoice模型）
try:
    from .cosyvoice3_engine import CosyVoice3Engine
    HAS_COSYVOICE3 = True
except ImportError:
    CosyVoice3Engine = None
    HAS_COSYVOICE3 = False

# MeloTTS引擎（可选，需要MeloTTS API服务）
try:
    from .melo_tts_engine import MeloTTSEngine
    HAS_MELOTTS = True
except ImportError:
    MeloTTSEngine = None
    HAS_MELOTTS = False

# CosyVoice TTS引擎（可选，需要CosyVoice API服务）
try:
    from .cosyvoice_tts_engine import CosyVoiceTTSEngine
    HAS_COSYVOICE_TTS = True
except ImportError:
    CosyVoiceTTSEngine = None
    HAS_COSYVOICE_TTS = False

__all__ = [
    'EdgeTTSEngine',
    'CosyVoice3Engine',
    'HAS_COSYVOICE3',
    'MeloTTSEngine',
    'HAS_MELOTTS',
    'CosyVoiceTTSEngine',
    'HAS_COSYVOICE_TTS',
]

