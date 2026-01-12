"""
CosyVoice TTS引擎模块

提供CosyVoice集成、参考音频设置、静音裁剪、内存处理、语速调整等功能
通过HTTP API调用CosyVoice服务

性能优化：
- LRU缓存机制：缓存相同文本的TTS结果，减少重复请求
- 文件缓存：持久化缓存TTS音频文件
- 参考音频复用：启动时设置参考音频，后续生成无需重复设置
"""

import io
import os
import threading
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from collections import OrderedDict

import requests
import torchaudio
import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

try:
    import langid
    HAS_LANGID = True
except ImportError:
    HAS_LANGID = False

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.tts.tts_file_cache import TTSFileCache
from voice_chatbot.dialogue.events import EventType, EventBus, create_event


class LRUCache:
    """线程安全的LRU缓存"""
    
    def __init__(self, max_size: Optional[int] = None):
        """
        初始化LRU缓存
        
        Args:
            max_size: 最大缓存条目数，None表示不设上限（不淘汰旧缓存）
        """
        self.max_size = max_size
        self.cache: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Optional[bytes]:
        """
        获取缓存值
        
        Args:
            key: 缓存键
            
        Returns:
            缓存值，如果不存在则返回None
        """
        with self._lock:
            if key in self.cache:
                # 移动到末尾（最近使用）
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def put(self, key: str, value: bytes):
        """
        放入缓存
        
        Args:
            key: 缓存键
            value: 缓存值
        """
        with self._lock:
            if key in self.cache:
                # 已存在，移动到末尾
                self.cache.move_to_end(key)
                self.cache[key] = value
            else:
                # 新增
                self.cache[key] = value
                # 如果设置了最大容量限制，超过时删除最旧的
                if self.max_size is not None and len(self.cache) > self.max_size:
                    self.cache.popitem(last=False)
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self.cache.clear()
    
    def __len__(self):
        """获取缓存大小"""
        with self._lock:
            return len(self.cache)


class CosyVoiceTTSEngine:
    """CosyVoice TTS引擎（通过HTTP API调用）"""
    
    def __init__(self, config, task_lock: Optional[threading.Lock] = None,
                 get_current_task_id: Optional[callable] = None,
                 event_bus: Optional[EventBus] = None):
        """
        初始化CosyVoice TTS引擎
        
        Args:
            config: 配置对象（Config实例）
            task_lock: 任务锁（可选，用于任务取消检查）
            get_current_task_id: 获取当前任务ID的函数（可选）
            event_bus: 事件总线（用于发送事件和统一日志输出）
        """
        self.config = config
        self.task_lock = task_lock
        self.get_current_task_id = get_current_task_id
        self.event_bus = event_bus
        
        # 从配置获取TTS参数
        self.silence_threshold = config.get("tts.silence_threshold", -40)
        self.min_silence_len = config.get("tts.min_silence_len", 30)
        self.enable_trim_silence = config.get("tts.enable_trim_silence", True)
        self.trim_keep_start = config.get("tts.trim_keep_start", 20)  # 裁剪时开头固定保留长度（毫秒）
        self.trim_keep_end = config.get("tts.trim_keep_end", 300)  # 裁剪时结尾固定保留长度（毫秒）
        self.enable_normalize_loudness = config.get("tts.enable_normalize_loudness", True)  # 是否启用响度归一化
        
        # CosyVoice API配置
        cosyvoice_config = config.get("tts.engines.cosyvoice_tts", {})
        self.api_url = cosyvoice_config.get("api_url", "http://127.0.0.1:9880")
        self.set_model_endpoint = cosyvoice_config.get("set_model_endpoint", "/set_model")
        self.generate_endpoint = cosyvoice_config.get("generate_endpoint", "/generate")
        self.get_model_endpoint = cosyvoice_config.get("get_model_endpoint", "/get_model")
        self.api_timeout = cosyvoice_config.get("timeout", 30.0)
        
        # 采样率配置（CosyVoice2 使用 24000 Hz）
        self.sample_rate = cosyvoice_config.get("sample_rate", 24000)
        
        # 参考音频配置（用于3s极速复刻）
        self.reference_wav_path = cosyvoice_config.get("reference_wav_path", None)
        self.reference_text = cosyvoice_config.get("reference_text", None)
        self.use_filename_as_text = cosyvoice_config.get("use_filename_as_text", True)  # 是否使用文件名作为参考文本
        
        # 默认语速
        self.default_speed = cosyvoice_config.get("speed", 1.0)
        
        # 性能优化配置（缓存配置从公共配置读取）
        self.cache_enabled = config.get("tts.cache_enabled", True)
        self.cache_max_size = config.get("tts.cache_max_size", None)  # None表示不设上限
        
        # 初始化LRU缓存
        self._cache = LRUCache(max_size=self.cache_max_size) if self.cache_enabled else None
        self._cache_hits = 0
        self._cache_misses = 0
        
        # TTS文件缓存配置（所有句子都缓存）
        self.tts_file_cache_enabled = config.get("tts.tts_file_cache_enabled", True)
        tts_file_cache_dir = config.get("tts.tts_file_cache_dir", "tts_cache")
        tts_file_cache_max_length = config.get("tts.tts_file_cache_max_length", 30)
        self.tts_file_cache_auto_repair = config.get("tts.tts_file_cache_auto_repair", False)
        
        # 自然音频配置
        # 从总配置读取natural_audio_count，从引擎配置读取support_natural_audio
        # 如果引擎不支持自然音频，即使总配置>0，也使用0
        natural_audio_count = config.get("tts.natural_audio_count", 0)
        support_natural_audio = config.get("tts.engines.cosyvoice_tts.support_natural_audio", False)
        if not support_natural_audio:
            natural_audio_count = 0
        
        # 初始化TTS文件缓存（扩展到所有句子）
        # 如果路径是绝对路径，直接使用；如果是相对路径，则相对于项目根目录（voice_chatbot的父目录）
        if not os.path.isabs(tts_file_cache_dir):
            # 获取voice_chatbot目录的父目录作为项目根目录
            import voice_chatbot
            project_root = Path(voice_chatbot.__file__).parent.parent
            tts_file_cache_dir = project_root / tts_file_cache_dir
        
        # 使用参考音频路径作为voice标识（用于文件缓存）
        # 如果未配置参考音频，使用默认标识
        voice_identifier = "default"
        if self.reference_wav_path:
            # 使用参考音频文件名（不含扩展名）作为标识
            voice_identifier = Path(self.reference_wav_path).stem
        
        self._file_cache = TTSFileCache(
            cache_dir=str(tts_file_cache_dir),
            enabled=self.tts_file_cache_enabled,
            max_length=tts_file_cache_max_length,
            engine_name="cosyvoice",
            natural_audio_count=natural_audio_count
        ) if self.tts_file_cache_enabled else None
        
        # HTTP会话（复用连接）
        self._session = requests.Session()
        
        # 初始化参考音频（如果配置了）
        self._init_reference_audio()
        
        log_debug("TTS", f"CosyVoice TTS引擎初始化完成 [API: {self.api_url}{self.generate_endpoint}, 参考音频: {self.reference_wav_path or '未设置'}, 缓存: {'启用' if self.cache_enabled else '禁用'}, 文件缓存: {'启用' if self.tts_file_cache_enabled else '禁用'}, 自动修复: {'启用' if self.tts_file_cache_auto_repair else '禁用'}]")
        
        # 检查缓存文件（根据配置决定是否自动修复）
        if self._file_cache:
            # 在后台线程中检查缓存（避免阻塞初始化）
            def check_cache():
                if self.tts_file_cache_auto_repair:
                    # 启用自动修复，提供生成函数
                    def generate_for_cache(text: str, voice: str) -> Optional[AudioSegment]:
                        """用于缓存修复的生成函数"""
                        try:
                            # 缓存修复时，应该使用与实际使用时相同的逻辑
                            # 使用配置的默认值（trim_silence=None会使用配置值，speed=None会使用默认语速）
                            # 这样缓存的文件会和应用相同的处理（裁剪静音+归一化），保持一致性
                            return self.generate(text, speed=None, trim_silence=None, task_id=None)
                        except Exception as e:
                            log_error("TTS", f"修复缓存时生成音频失败 {voice}/{text[:30]}...: {e}", indent=2)
                            return None
                    
                    self._file_cache.check_and_repair_missing_files(generate_for_cache)
                else:
                    # 不启用自动修复，仅检查并报告缺失文件数量
                    self._file_cache.check_and_repair_missing_files(None)
            
            check_thread = threading.Thread(target=check_cache, daemon=True, name="TTS-Cache-Init-Check")
            check_thread.start()
    
    def _init_reference_audio(self):
        """
        初始化CosyVoice参考音频（调用 /set_model 接口）
        """
        if not self.reference_wav_path:
            log_warn("TTS", "未配置参考音频路径，请确保API服务已设置参考音频", indent=1)
            return
        
        if not os.path.exists(self.reference_wav_path):
            log_warn("TTS", f"参考音频文件不存在: {self.reference_wav_path}", indent=1)
            return
        
        try:
            set_model_url = f"{self.api_url}{self.set_model_endpoint}"
            request_data = {
                "wav_path": self.reference_wav_path,
                "use_filename_as_text": self.use_filename_as_text,
            }
            
            # 如果提供了参考文本，则使用提供的文本
            if self.reference_text:
                request_data["reference_text"] = self.reference_text
            
            response = self._session.post(
                set_model_url,
                json=request_data,
                timeout=self.api_timeout
            )
            
            response.raise_for_status()
            result = response.json()
            
            if result.get("success", False):
                actual_text = result.get("reference_text", self.reference_text or "从文件名提取")
                log_debug("TTS", f"成功设置参考音频: {self.reference_wav_path}, 参考文本: {actual_text}", indent=1)
            else:
                log_warn("TTS", f"设置参考音频失败: {result.get('error', '未知错误')}", indent=1)
        except requests.exceptions.RequestException as e:
            log_error("TTS", f"初始化参考音频失败（API调用错误）: {e}", indent=1)
        except Exception as e:
            log_error("TTS", f"初始化参考音频失败: {e}", indent=1)
    
    def _get_voice_identifier(self) -> str:
        """
        生成voice标识符（用于缓存目录组织）
        
        Returns:
            voice标识符，基于参考音频文件名
        """
        if self.reference_wav_path:
            return Path(self.reference_wav_path).stem
        return "default"
    
    def _generate_cache_key(self, text: str, speed: float) -> str:
        """
        生成缓存键
        
        Args:
            text: 文本
            speed: 语速
            
        Returns:
            缓存键（MD5哈希）
        """
        key_str = f"{text}|{speed}|{self._get_voice_identifier()}"
        return hashlib.md5(key_str.encode('utf-8')).hexdigest()
    
    def cleanup(self):
        """
        清理资源
        
        清理CosyVoice相关的HTTP会话和缓存
        """
        log_debug("TTS", "清理TTS引擎资源", indent=0)
        
        # 清理缓存
        if self._cache:
            cache_size = len(self._cache)
            self._cache.clear()
            log_debug("TTS", f"已清理缓存 [{cache_size}条目, 命中: {self._cache_hits}, 未命中: {self._cache_misses}]", indent=1)
        
        # 关闭HTTP会话
        if self._session:
            try:
                self._session.close()
                log_debug("TTS", "已关闭HTTP会话", indent=1)
            except Exception as e:
                log_debug("TTS", f"关闭HTTP会话时出错（可忽略）: {e}", indent=1)
    
    def _trim_silence(self, audio_data: bytes) -> Tuple[Optional[AudioSegment], int]:
        """
        裁剪音频开头和结尾的静音部分
        
        Args:
            audio_data: 音频数据（bytes）
            
        Returns:
            (处理后的AudioSegment, 裁剪掉的总时长毫秒)
        """
        try:
            # 从bytes加载音频（内存操作，WAV格式）
            audio = AudioSegment.from_wav(io.BytesIO(audio_data))
            
            # 检测开头静音的时长（毫秒）
            leading_silence = detect_leading_silence(
                audio, 
                silence_threshold=self.silence_threshold, 
                chunk_size=10
            )
            
            # 检测结尾静音：反转音频后检测开头（即原音频的结尾）
            reversed_audio = audio.reverse()
            trailing_silence = detect_leading_silence(
                reversed_audio, 
                silence_threshold=self.silence_threshold, 
                chunk_size=10
            )
            
            # 保留一小段避免裁剪过度（使用配置的固定保留长度）
            trim_start = max(0, leading_silence - self.trim_keep_start)
            trim_end = max(0, trailing_silence - self.trim_keep_end)
            
            total_trimmed = 0
            trimmed = audio
            
            # 裁剪开头静音
            if trim_start > self.min_silence_len:
                trimmed = trimmed[trim_start:]
                total_trimmed += trim_start
            
            # 裁剪结尾静音
            if trim_end > self.min_silence_len:
                trimmed = trimmed[:-trim_end] if trim_end > 0 else trimmed
                total_trimmed += trim_end
            
            if total_trimmed > 0:
                log_debug("TTS", f"裁剪静音{total_trimmed}ms (开头{trim_start if trim_start > self.min_silence_len else 0}ms, 结尾{trim_end if trim_end > self.min_silence_len else 0}ms)", indent=2)
            
            return trimmed, total_trimmed
        except Exception as e:
            log_warn("TTS", f"裁剪静音失败: {e}", indent=1)
            # 失败时尝试返回原始音频
            try:
                return AudioSegment.from_wav(io.BytesIO(audio_data)), 0
            except:
                return None, 0
    
    def _normalize_loudness(self, audio: AudioSegment) -> AudioSegment:
        """
        归一化音频响度（峰值归一化）
        
        Args:
            audio: 输入音频（AudioSegment）
            
        Returns:
            归一化后的音频（AudioSegment）
        """
        try:
            # 使用pydub的normalize方法进行峰值归一化
            # normalize()会将音频的最大峰值调整到0dBFS（最大音量），避免削波
            normalized = audio.normalize()
            log_debug("TTS", "已应用响度归一化", indent=2)
            return normalized
        except Exception as e:
            log_warn("TTS", f"响度归一化失败: {e}，返回原始音频", indent=1)
            return audio
    
    def _check_task_cancelled(self, task_id: Optional[int]) -> bool:
        """
        检查任务是否被取消
        
        Args:
            task_id: 任务ID
            
        Returns:
            True表示任务已取消，False表示任务有效
        """
        if task_id is None or self.task_lock is None or self.get_current_task_id is None:
            return False
        
        with self.task_lock:
            current_task_id = self.get_current_task_id()
            return task_id != current_task_id
    
    def _call_api(self, text: str, speed: float = None, task_id: Optional[int] = None) -> Optional[bytes]:
        """
        调用CosyVoice API生成音频
        
        Args:
            text: 要合成的文本
            speed: 语速（可选，默认使用配置值）
            task_id: 任务ID（用于取消检查）
            
        Returns:
            音频数据（bytes），如果生成失败或任务被取消则返回None
        """
        # 检查任务是否被取消
        if self._check_task_cancelled(task_id):
            return None
        
        # 使用默认值（如果未提供）
        speed = speed if speed is not None else self.default_speed
        
        # 构建API请求URL
        api_full_url = f"{self.api_url}{self.generate_endpoint}"
        
        # 构建请求数据
        request_data = {
            "text": text,
            "speed": speed,
        }
        
        try:
            # 发送HTTP POST请求
            response = self._session.post(
                api_full_url,
                json=request_data,
                timeout=self.api_timeout,
                stream=False  # CosyVoice /generate 接口返回完整音频，不需要流式
            )
            
            # 检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return None
            
            # 检查HTTP状态码
            response.raise_for_status()
            
            # 检查响应内容类型
            content_type = response.headers.get('Content-Type', '')
            if 'application/json' in content_type:
                # 如果返回JSON，可能是错误信息
                error_data = response.json()
                error_msg = error_data.get('error', '未知错误')
                log_error("TTS", f"CosyVoice API返回错误: {error_msg}", indent=1)
                return None
            
            # 读取音频数据
            audio_bytes = response.content
            
            # 再次检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return None
            
            return audio_bytes
        except requests.exceptions.RequestException as e:
            log_error("TTS", f"CosyVoice API调用错误: {e}", indent=1)
            return None
        except Exception as e:
            log_error("TTS", f"CosyVoice生成错误: {e}", indent=1)
            return None
    
    def generate(self, text: str,
                speed: Optional[float] = None, task_id: Optional[int] = None,
                trim_silence: Optional[bool] = None, return_timing: bool = False) -> Optional[AudioSegment]:
        """
        同步生成TTS音频（返回AudioSegment）
        
        Args:
            text: 要合成的文本
            speed: 语速（可选，默认使用配置值）
            task_id: 任务ID（用于取消检查）
            trim_silence: 是否裁剪静音（可选，默认使用配置）
            return_timing: 是否返回耗时信息
            
        Returns:
            AudioSegment对象，如果生成失败或任务被取消则返回None
            如果return_timing=True，返回(AudioSegment, generation_time, processing_time)
        """
        import time
        tts_start_time = time.time()
        
        # CosyVoice使用参考音频，不需要speaker_id
        # 使用参考音频标识作为voice标识（用于文件缓存）
        voice = self._get_voice_identifier()
        
        # 发送TTS生成开始事件
        if self.event_bus:
            try:
                start_event = create_event(
                    EventType.TTS_GENERATION_START,
                    data={"task_id": task_id, "text": text[:50] + "..." if len(text) > 50 else text},
                    source="TTS"
                )
                self.event_bus.publish(start_event)
            except Exception as e:
               log_error("TTS", f"发送TTS_GENERATION_START事件错误: {e}", indent=1)
        
        # 检查文件缓存（所有句子都检查）
        if self._file_cache:
            cached_audio = self._file_cache.load(text, voice)
            if cached_audio is not None:
                # 缓存命中，发送缓存命中事件
                log_debug("TTS", f"缓存命中，跳过TTS生成 [文本: {text[:50]}...]", indent=1)
                if self.event_bus:
                    try:
                        cache_event = create_event(
                            EventType.TTS_CACHE_HIT,
                            data={"task_id": task_id, "text": text[:50]},
                            source="TTS"
                        )
                        self.event_bus.publish(cache_event)
                    except Exception as e:
                        log_error("TTS", f"发送TTS_CACHE_HIT事件错误: {e}", indent=1)
                
                if return_timing:
                    # 文件加载时间很短，返回0作为生成时间
                    return cached_audio, 0.0, 0.0
                return cached_audio
        
        # 检查内存缓存
        cache_key = None
        if self._cache:
            actual_speed = speed if speed is not None else self.default_speed
            cache_key = self._generate_cache_key(text, actual_speed)
            cached_audio_bytes = self._cache.get(cache_key)
            if cached_audio_bytes is not None:
                self._cache_hits += 1
                log_debug("TTS", f"缓存命中 [key: {cache_key[:8]}..., 命中率: {self._cache_hits}/{self._cache_hits + self._cache_misses}]", indent=1)
                # 从bytes加载AudioSegment
                try:
                    audio_segment = AudioSegment.from_wav(io.BytesIO(cached_audio_bytes))
                    if return_timing:
                        return audio_segment, 0.0, 0.0
                    return audio_segment
                except Exception as e:
                   log_error("TTS", f"从缓存加载音频失败: {e}", indent=1)
            self._cache_misses += 1
        
        try:
            # 调用API生成音频
            audio_bytes = self._call_api(
                text, speed=speed, task_id=task_id
            )
            
            if audio_bytes is None:
                return None
            
            tts_generation_time = time.time() - tts_start_time
            
            # 裁剪静音（如果启用）
            if trim_silence is None:
                trim_silence = self.enable_trim_silence
            
            # 使用 torchaudio 加载音频（参考 webui_streaming.py 的实现）
            audio_buffer = io.BytesIO(audio_bytes)
            try:
                waveform, sr = torchaudio.load(audio_buffer)
                
                # 转换为目标采样率
                if sr != self.sample_rate:
                    resampler = torchaudio.transforms.Resample(sr, self.sample_rate)
                    waveform = resampler(waveform)
                
                # 转换为 numpy 数组
                audio_data = waveform.numpy().flatten()
                
                # 转换为 AudioSegment（用于后续处理）
                # torchaudio 返回的波形数据通常是归一化的浮点数（-1.0 到 1.0）
                # AudioSegment 需要的是 int16 格式的数组
                # 确保数据在有效范围内
                audio_data = np.clip(audio_data, -1.0, 1.0)
                audio_data_int16 = (audio_data * 32767).astype(np.int16)
                audio_segment = AudioSegment(
                    audio_data_int16.tobytes(),
                    frame_rate=self.sample_rate,
                    channels=1,
                    sample_width=2  # 16-bit = 2 bytes
                )
            except Exception as e:
                # 如果 torchaudio 加载失败，回退到原来的方法
                log_warn("TTS", f"使用 torchaudio 加载音频失败，回退到 AudioSegment.from_wav: {e}", indent=1)
                try:
                    audio_segment = AudioSegment.from_wav(io.BytesIO(audio_bytes))
                except Exception as e2:
                    log_error("TTS", f"使用 AudioSegment.from_wav 加载音频也失败: {e2}", indent=1)
                    return None
            
            if trim_silence:
                processing_start_time = time.time()
                # _trim_silence 需要 bytes，所以我们需要将 audio_segment 转换回 bytes
                wav_buffer = io.BytesIO()
                audio_segment.export(wav_buffer, format="wav")
                wav_buffer.seek(0)
                audio_segment, trimmed_ms = self._trim_silence(wav_buffer.read())
                processing_time = time.time() - processing_start_time
                tts_total_elapsed = time.time() - tts_start_time
                
                # 响度归一化（在裁剪静音之后应用）
                if self.enable_normalize_loudness and audio_segment:
                    audio_segment = self._normalize_loudness(audio_segment)
                
                if trimmed_ms > 0:
                    log_debug("TTS", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms, 处理: {processing_time*1000:.1f}ms，裁剪静音{trimmed_ms}ms]", indent=1)
                else:
                    log_debug("TTS", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms, 处理: {processing_time*1000:.1f}ms]", indent=1)
                
                # 发送TTS第一个音频就绪事件
                if self.event_bus:
                    try:
                        ready_event = create_event(
                            EventType.TTS_FIRST_AUDIO_READY,
                            data={
                                "task_id": task_id,
                                "text": text[:50],
                                "generation_time": tts_generation_time,
                                "processing_time": processing_time
                            },
                            source="TTS"
                        )
                        self.event_bus.publish(ready_event)
                    except Exception as e:
                        log_error("TTS", f"发送TTS_FIRST_AUDIO_READY事件错误: {e}", indent=1)
                
                # 保存到内存缓存
                if self._cache and cache_key and audio_bytes:
                    self._cache.put(cache_key, audio_bytes)
                    log_debug("TTS", f"已缓存 [key: {cache_key[:8]}..., 缓存大小: {len(self._cache)}]", indent=1)
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, voice)
                
                if return_timing:
                    return audio_segment, tts_generation_time, processing_time
                return audio_segment
            else:
                # 不裁剪静音，响度归一化（如果启用）
                if self.enable_normalize_loudness and audio_segment:
                    audio_segment = self._normalize_loudness(audio_segment)
                
                tts_total_elapsed = time.time() - tts_start_time
                processing_time = 0.0
                log_debug("TTS", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms]", indent=1)
                
                # 发送TTS第一个音频就绪事件
                if self.event_bus:
                    try:
                        ready_event = create_event(
                            EventType.TTS_FIRST_AUDIO_READY,
                            data={
                                "task_id": task_id,
                                "text": text[:50],
                                "generation_time": tts_generation_time,
                                "processing_time": 0.0
                            },
                            source="TTS"
                        )
                        self.event_bus.publish(ready_event)
                    except Exception as e:
                       log_error("TTS", f"发送TTS_FIRST_AUDIO_READY事件错误: {e}", indent=1)
                
                # 保存到内存缓存
                if self._cache and cache_key and audio_bytes:
                    self._cache.put(cache_key, audio_bytes)
                    log_debug("TTS", f"已缓存 [key: {cache_key[:8]}..., 缓存大小: {len(self._cache)}]", indent=1)
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, voice)
                
                if return_timing:
                    return audio_segment, tts_generation_time, 0.0
                return audio_segment
                
        except Exception as e:
            tts_elapsed = time.time() - tts_start_time
            log_error("TTS", f"同步生成错误: {e} [耗时: {tts_elapsed*1000:.1f}ms]", indent=1)
            return None
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            包含缓存统计的字典
        """
        total_requests = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total_requests if total_requests > 0 else 0.0
        
        return {
            "enabled": self.cache_enabled,
            "size": len(self._cache) if self._cache else 0,
            "max_size": self.cache_max_size,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": hit_rate,
        }
    
    def clear_cache(self):
        """清空TTS缓存"""
        if self._cache:
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            log_debug("TTS", "缓存已清空")
    
    def _publish_log(self, level: str, message: str, indent: int = 0):
        """
        通过事件系统发布日志
        
        Args:
            level: 日志级别（INFO, WARN, ERROR, DEBUG）
            message: 日志消息
            indent: 缩进级别
        """
        if self.event_bus:
            level_upper = level.upper()
            if level_upper == "DEBUG":
                event_type = EventType.LOG_DEBUG
            elif level_upper == "WARN":
                event_type = EventType.LOG_WARN
            elif level_upper == "ERROR":
                event_type = EventType.LOG_ERROR
            else:
                event_type = EventType.LOG_INFO
            
            event = create_event(
                event_type=event_type,
                data={
                    "message": message,
                    "category": "TTS",
                    "indent": indent,
                },
                source="TTS"
            )
            self.event_bus.publish(event)
        else:
            # 回退到直接日志输出
            level_upper = level.upper()
            if level_upper == "DEBUG":
                log_debug("TTS", message, indent=indent)
            elif level_upper == "WARN":
                log_warn("TTS", message, indent=indent)
            elif level_upper == "ERROR":
                log_error("TTS", message, indent=indent)
            else:
                log_info("TTS", message, indent=indent)
