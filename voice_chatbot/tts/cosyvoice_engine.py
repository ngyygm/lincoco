"""
CosyVoice引擎模块

提供CosyVoice集成、多模式语音合成（SFT、ZeroShot、CrossLingual、Instruct）、
静音裁剪、内存处理、语速调整等功能
通过HTTP API调用CosyVoice服务

性能优化：
- LRU缓存机制：缓存相同文本的TTS结果，减少重复请求
- 文件缓存：持久化缓存TTS音频文件
"""

import io
import os
import threading
import hashlib
import base64
import random
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from collections import OrderedDict

import requests
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


class CosyVoiceEngine:
    """CosyVoice引擎（通过HTTP API调用）"""
    
    # 支持的模式
    SUPPORTED_MODES = ["sft", "zero_shot", "cross_lingual", "instruct"]
    
    def __init__(self, config, task_lock: Optional[threading.Lock] = None,
                 get_current_task_id: Optional[callable] = None,
                 event_bus: Optional[EventBus] = None):
        """
        初始化CosyVoice引擎
        
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
        cosyvoice_config = config.get("tts.engines.cosyvoice", {})
        self.api_url = cosyvoice_config.get("api_url", "http://127.0.0.1:8000")
        self.api_timeout = cosyvoice_config.get("timeout", 30.0)
        
        # 模式配置
        self.default_mode = cosyvoice_config.get("default_mode", "sft")
        if self.default_mode not in self.SUPPORTED_MODES:
            self._publish_log("WARN", f"默认模式 {self.default_mode} 不支持，将使用 'sft'", indent=1)
            self.default_mode = "sft"
        
        # SFT模式配置
        self.default_spk = cosyvoice_config.get("default_spk", "中文女")
        self.voice_map = cosyvoice_config.get("voice_map", {
            "zh": "中文女",
            "en": "英文女",
            "ja": "日语男",
            "ko": "韩语女",
            "yue": "粤语女",
        })
        
        # ZeroShot模式配置
        self.zero_shot_prompt_text = cosyvoice_config.get("zero_shot_prompt_text", "")
        self.zero_shot_prompt_audio_path = cosyvoice_config.get("zero_shot_prompt_audio_path", "")
        self._zero_shot_prompt_audio_base64 = None
        
        # CrossLingual模式配置
        self.cross_lingual_prompt_audio_path = cosyvoice_config.get("cross_lingual_prompt_audio_path", "")
        self._cross_lingual_prompt_audio_base64 = None
        
        # Instruct模式配置
        self.instruct_text = cosyvoice_config.get("instruct_text", "")
        
        # 通用参数
        self.default_speed = cosyvoice_config.get("default_speed", 1.0)
        self.use_random_seed = cosyvoice_config.get("use_random_seed", True)
        self.default_seed = cosyvoice_config.get("default_seed", 0)
        
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
        
        # 初始化TTS文件缓存（扩展到所有句子）
        # 如果路径是绝对路径，直接使用；如果是相对路径，则相对于项目根目录（voice_chatbot的父目录）
        if not os.path.isabs(tts_file_cache_dir):
            # 获取voice_chatbot目录的父目录作为项目根目录
            import voice_chatbot
            project_root = Path(voice_chatbot.__file__).parent.parent
            tts_file_cache_dir = project_root / tts_file_cache_dir
        
        self._file_cache = TTSFileCache(
            cache_dir=str(tts_file_cache_dir),
            enabled=self.tts_file_cache_enabled,
            max_length=tts_file_cache_max_length,
            engine_name="cosyvoice"
        ) if self.tts_file_cache_enabled else None
        
        # HTTP会话（复用连接）
        self._session = requests.Session()
        
        # 加载ZeroShot和CrossLingual的提示音频
        self._load_prompt_audios()
        
        self._publish_log("DEBUG", f"CosyVoice引擎初始化完成 [API: {self.api_url}, 模式: {self.default_mode}, 缓存: {'启用' if self.cache_enabled else '禁用'}, 文件缓存: {'启用' if self.tts_file_cache_enabled else '禁用'}, 自动修复: {'启用' if self.tts_file_cache_auto_repair else '禁用'}]")
        
        # 检查缓存文件（根据配置决定是否自动修复）
        if self._file_cache:
            # 在后台线程中检查缓存（避免阻塞初始化）
            def check_cache():
                if self.tts_file_cache_auto_repair:
                    # 启用自动修复，提供生成函数
                    def generate_for_cache(text: str, voice: str) -> Optional[AudioSegment]:
                        """用于缓存修复的生成函数"""
                        try:
                            # 使用配置的默认值（trim_silence=None会使用配置值）
                            # 这样缓存的文件会和应用相同的处理（裁剪静音+归一化），保持一致性
                            return self.generate(text, voice=voice, trim_silence=None, task_id=None)
                        except Exception as e:
                            self._publish_log("ERROR", f"修复缓存时生成音频失败 {voice}/{text[:30]}...: {e}", indent=2)
                            return None
                    
                    self._file_cache.check_and_repair_missing_files(generate_for_cache)
                else:
                    # 不启用自动修复，仅检查并报告缺失文件数量
                    self._file_cache.check_and_repair_missing_files(None)
            
            check_thread = threading.Thread(target=check_cache, daemon=True, name="TTS-Cache-Init-Check")
            check_thread.start()
    
    def _load_prompt_audios(self):
        """加载ZeroShot和CrossLingual模式的提示音频"""
        # 加载ZeroShot提示音频
        if self.zero_shot_prompt_audio_path and os.path.exists(self.zero_shot_prompt_audio_path):
            try:
                with open(self.zero_shot_prompt_audio_path, 'rb') as f:
                    audio_data = f.read()
                self._zero_shot_prompt_audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                self._publish_log("DEBUG", "ZeroShot提示音频已加载", indent=1)
            except Exception as e:
                self._publish_log("WARN", f"加载ZeroShot提示音频失败: {e}", indent=1)
        
        # 加载CrossLingual提示音频
        if self.cross_lingual_prompt_audio_path and os.path.exists(self.cross_lingual_prompt_audio_path):
            try:
                with open(self.cross_lingual_prompt_audio_path, 'rb') as f:
                    audio_data = f.read()
                self._cross_lingual_prompt_audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                self._publish_log("DEBUG", "CrossLingual提示音频已加载", indent=1)
            except Exception as e:
                self._publish_log("WARN", f"加载CrossLingual提示音频失败: {e}", indent=1)
    
    def _generate_cache_key(self, text: str, mode: str, voice: str, speed: float) -> str:
        """
        生成缓存键
        
        Args:
            text: 文本
            mode: 模式
            voice: 音色标识
            speed: 语速
            
        Returns:
            缓存键（MD5哈希）
        """
        key_str = f"{text}|{mode}|{voice}|{speed}"
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
    
    def detect_language(self, text: str) -> str:
        """
        检测文本语言
        
        Args:
            text: 文本
            
        Returns:
            语言代码（如 'zh', 'en', 'ja' 等）
        """
        if not HAS_LANGID:
            # 如果没有langid，使用简单启发式
            if any('\u4e00' <= char <= '\u9fff' for char in text):
                return "zh"
            elif any('\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff' for char in text):
                return "ja"
            else:
                return "en"
        
        try:
            language, _ = langid.classify(text)
            return language
        except:
            return "zh"  # 默认中文
    
    def get_voice_for_text(self, text: str) -> str:
        """
        根据文本自动检测语言并返回音色
        
        Args:
            text: 文本
            
        Returns:
            音色名称
        """
        language = self.detect_language(text)
        voice = self.voice_map.get(language, self.default_spk)
        log_debug("TTS", f"检测语言: {language}, 选择音色: {voice}", indent=1)
        return voice
    
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
    
    def _call_api(self, text: str, mode: str = None,
                  spk_id: str = None, prompt_text: str = None,
                  prompt_audio_base64: str = None, instruct_text: str = None,
                  speed: float = None, task_id: Optional[int] = None) -> Optional[bytes]:
        """
        统一的API调用方法，根据参数自动选择模式
        
        Args:
            text: 要合成的文本
            mode: 指定模式（可选，如果不指定则根据其他参数自动判断）
            spk_id: 说话人ID（SFT和Instruct模式使用）
            prompt_text: 提示文本（ZeroShot模式使用）
            prompt_audio_base64: 提示音频的Base64编码（ZeroShot和CrossLingual模式使用）
            instruct_text: 指令文本（Instruct模式使用）
            speed: 语速
            task_id: 任务ID（用于取消检查）
            
        Returns:
            音频数据（bytes），如果生成失败或任务被取消则返回None
        """
        # 检查任务是否被取消
        if self._check_task_cancelled(task_id):
            return None
        
        # 使用默认值（如果未提供）
        speed = speed if speed is not None else self.default_speed
        
        # 生成随机种子（如果配置要求）
        seed = random.randint(1, 100000000) if self.use_random_seed else self.default_seed
        
        # 自动判断模式（如果未明确指定）
        if mode is None:
            # 优先级：Instruct > ZeroShot > CrossLingual > SFT
            if instruct_text is not None:
                mode = "instruct"
            elif prompt_audio_base64 is not None:
                if prompt_text is not None:
                    mode = "zero_shot"
                else:
                    mode = "cross_lingual"
            else:
                mode = "sft"
        
        # 验证模式
        if mode not in self.SUPPORTED_MODES:
            self._publish_log("ERROR", f"不支持的模式: {mode}", indent=1)
            return None
        
        # 根据模式构建请求
        request_data = {
            "tts_text": text,
            "stream": False,  # 非流式
            "speed": speed,
            "seed": seed,
        }
        api_endpoint = ""
        
        if mode == "sft":
            # SFT模式：需要spk_id
            if spk_id is None:
                spk_id = self.default_spk
            request_data["spk_id"] = spk_id
            api_endpoint = f"{self.api_url}/tts/sft"
            
        elif mode == "zero_shot":
            # ZeroShot模式：需要prompt_text和prompt_audio_base64
            if prompt_text is None:
                prompt_text = self.zero_shot_prompt_text
            if prompt_audio_base64 is None:
                prompt_audio_base64 = self._zero_shot_prompt_audio_base64
            if not prompt_audio_base64:
                self._publish_log("ERROR", "ZeroShot模式需要提示音频", indent=1)
                return None
            request_data["prompt_text"] = prompt_text
            request_data["prompt_audio_base64"] = prompt_audio_base64
            api_endpoint = f"{self.api_url}/tts/zero_shot"
            
        elif mode == "cross_lingual":
            # CrossLingual模式：只需要prompt_audio_base64
            if prompt_audio_base64 is None:
                prompt_audio_base64 = self._cross_lingual_prompt_audio_base64
            if not prompt_audio_base64:
                self._publish_log("ERROR", "CrossLingual模式需要提示音频", indent=1)
                return None
            request_data["prompt_audio_base64"] = prompt_audio_base64
            api_endpoint = f"{self.api_url}/tts/cross_lingual"
            
        elif mode == "instruct":
            # Instruct模式：需要spk_id和instruct_text
            if spk_id is None:
                spk_id = self.default_spk
            if instruct_text is None:
                instruct_text = self.instruct_text
            if not instruct_text:
                self._publish_log("ERROR", "Instruct模式需要指令文本", indent=1)
                return None
            request_data["spk_id"] = spk_id
            request_data["instruct_text"] = instruct_text
            api_endpoint = f"{self.api_url}/tts/instruct"
        
        try:
            # 发送HTTP POST请求
            response = self._session.post(
                api_endpoint,
                json=request_data,
                timeout=self.api_timeout
            )
            
            # 检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return None
            
            # 检查HTTP状态码
            response.raise_for_status()
            
            # 读取音频数据
            audio_bytes = response.content
            
            # 再次检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return None
            
            return audio_bytes
        except requests.exceptions.RequestException as e:
            self._publish_log("ERROR", f"CosyVoice {mode.upper()} API调用错误: {e}", indent=1)
            return None
        except Exception as e:
            self._publish_log("ERROR", f"CosyVoice {mode.upper()}生成错误: {e}", indent=1)
            return None
    
    def generate(self, text: str, voice: Optional[str] = None,
                mode: Optional[str] = None, speed: Optional[float] = None,
                task_id: Optional[int] = None,
                trim_silence: Optional[bool] = None, return_timing: bool = False,
                instruct_text: Optional[str] = None,
                prompt_audio_base64: Optional[str] = None,
                prompt_text: Optional[str] = None) -> Optional[AudioSegment]:
        """
        同步生成TTS音频（返回AudioSegment）
        
        Args:
            text: 要合成的文本
            voice: 音色名称（可选，不提供则自动选择）
            mode: 模式（可选，不提供则使用默认模式）
            speed: 语速（可选，默认使用配置值）
            task_id: 任务ID（用于取消检查）
            trim_silence: 是否裁剪静音（可选，默认使用配置）
            return_timing: 是否返回耗时信息
            instruct_text: 指令文本（Instruct模式使用）
            prompt_audio_base64: 提示音频Base64编码（ZeroShot和CrossLingual模式使用）
            prompt_text: 提示文本（ZeroShot模式使用）
            
        Returns:
            AudioSegment对象，如果生成失败或任务被取消则返回None
            如果return_timing=True，返回(AudioSegment, generation_time, processing_time)
        """
        import time
        tts_start_time = time.time()
        
        # 根据参数自动判断模式（优先级：参数 > 默认模式）
        if instruct_text is not None:
            # 优先使用 Instruct 模式
            mode = "instruct"
        elif prompt_audio_base64 is not None:
            # 其次根据 prompt_audio_base64 判断
            if prompt_text is not None:
                mode = "zero_shot"
            else:
                mode = "cross_lingual"
        # 如果未通过参数指定模式，则使用配置的默认模式
        elif mode is None:
            mode = self.default_mode
        
        # 验证模式
        if mode not in self.SUPPORTED_MODES:
            self._publish_log("WARN", f"模式 {mode} 不支持，将使用默认模式 {self.default_mode}", indent=1)
            mode = self.default_mode
        
        # 根据模式确定voice标识（用于缓存）
        cache_voice = voice
        if mode in ("sft", "instruct") and voice is None:
            voice = self.get_voice_for_text(text)
            cache_voice = voice
        elif mode in ("zero_shot", "cross_lingual"):
            # 对于零样本模式，使用模式名称作为voice标识
            cache_voice = mode
        
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
                self._publish_log("ERROR", f"发送TTS_GENERATION_START事件错误: {e}", indent=1)
        
        # 检查文件缓存（所有句子都检查）
        if self._file_cache:
            cached_audio = self._file_cache.load(text, cache_voice)
            if cached_audio is not None:
                # 缓存命中，发送缓存命中事件
                self._publish_log("DEBUG", f"缓存命中，跳过TTS生成 [文本: {text[:50]}...]", indent=1)
                if self.event_bus:
                    try:
                        cache_event = create_event(
                            EventType.TTS_CACHE_HIT,
                            data={"task_id": task_id, "text": text[:50]},
                            source="TTS"
                        )
                        self.event_bus.publish(cache_event)
                    except Exception as e:
                        self._publish_log("ERROR", f"发送TTS_CACHE_HIT事件错误: {e}", indent=1)
                
                if return_timing:
                    # 文件加载时间很短，返回0作为生成时间
                    return cached_audio, 0.0, 0.0
                return cached_audio
        
        # 检查内存缓存
        cache_key = None
        if self._cache:
            actual_speed = speed if speed is not None else self.default_speed
            cache_key = self._generate_cache_key(text, mode, cache_voice, actual_speed)
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
                    self._publish_log("ERROR", f"从缓存加载音频失败: {e}", indent=1)
            self._cache_misses += 1
        
        try:
            # 使用统一的API调用方法，根据模式传入对应参数
            audio_bytes = self._call_api(
                text=text,
                mode=mode,
                spk_id=voice if mode in ("sft", "instruct") else None,
                prompt_text=prompt_text if mode == "zero_shot" else None,
                prompt_audio_base64=prompt_audio_base64 if mode in ("zero_shot", "cross_lingual") else None,
                instruct_text=instruct_text if mode == "instruct" else None,
                speed=speed,
                task_id=task_id
            )
            
            if audio_bytes is None:
                return None
            
            tts_generation_time = time.time() - tts_start_time
            
            # 裁剪静音（如果启用）
            if trim_silence is None:
                trim_silence = self.enable_trim_silence
            
            if trim_silence:
                processing_start_time = time.time()
                audio_segment, trimmed_ms = self._trim_silence(audio_bytes)
                processing_time = time.time() - processing_start_time
                tts_total_elapsed = time.time() - tts_start_time
                
                # 响度归一化（在裁剪静音之后应用）
                if self.enable_normalize_loudness and audio_segment:
                    audio_segment = self._normalize_loudness(audio_segment)
                
                if trimmed_ms > 0:
                    self._publish_log("DEBUG", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms, 处理: {processing_time*1000:.1f}ms，裁剪静音{trimmed_ms}ms]", indent=1)
                else:
                    self._publish_log("DEBUG", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms, 处理: {processing_time*1000:.1f}ms]", indent=1)
                
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
                        self._publish_log("ERROR", f"发送TTS_FIRST_AUDIO_READY事件错误: {e}", indent=1)
                
                # 保存到内存缓存
                if self._cache and cache_key and audio_bytes:
                    self._cache.put(cache_key, audio_bytes)
                    self._publish_log("DEBUG", f"已缓存 [key: {cache_key[:8]}..., 缓存大小: {len(self._cache)}]", indent=1)
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, cache_voice)
                
                if return_timing:
                    return audio_segment, tts_generation_time, processing_time
                return audio_segment
            else:
                # 不裁剪静音，直接返回AudioSegment
                audio_segment = AudioSegment.from_wav(io.BytesIO(audio_bytes))
                
                # 响度归一化（即使不裁剪静音也应用归一化）
                if self.enable_normalize_loudness and audio_segment:
                    audio_segment = self._normalize_loudness(audio_segment)
                
                tts_total_elapsed = time.time() - tts_start_time
                self._publish_log("DEBUG", f"生成完成 [总耗时: {tts_total_elapsed*1000:.1f}ms, 生成: {tts_generation_time*1000:.1f}ms]", indent=1)
                
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
                        self._publish_log("ERROR", f"发送TTS_FIRST_AUDIO_READY事件错误: {e}", indent=1)
                
                # 保存到内存缓存
                if self._cache and cache_key and audio_bytes:
                    self._cache.put(cache_key, audio_bytes)
                    self._publish_log("DEBUG", f"已缓存 [key: {cache_key[:8]}..., 缓存大小: {len(self._cache)}]", indent=1)
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, cache_voice)
                
                if return_timing:
                    return audio_segment, tts_generation_time, 0.0
                return audio_segment
                
        except Exception as e:
            tts_elapsed = time.time() - tts_start_time
            self._publish_log("ERROR", f"同步生成错误: {e} [耗时: {tts_elapsed*1000:.1f}ms]", indent=1)
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
            self._publish_log("DEBUG", "缓存已清空")
    
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
