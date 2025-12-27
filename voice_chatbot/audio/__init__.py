"""
音频输入模块 (audio)

提供麦克风采集、VAD检测、流式ASR识别等功能
"""

from .microphone import MicrophoneRecorder
from .webrtc_vad import WebRTCVAD
from .fun_asr_stream import FunASRStream, CircularAudioBuffer, ASRDeduplicator

__all__ = [
    'MicrophoneRecorder',
    'WebRTCVAD',
    'FunASRStream',
    'CircularAudioBuffer',
    'ASRDeduplicator',
]

