"""
Edge TTS引擎模块

提供Edge TTS集成、多语言语音选择、静音裁剪、内存处理、语速/音调调整等功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py

性能优化：
- 事件循环复用：避免每次调用asyncio.run()创建新循环
- LRU缓存机制：缓存相同文本的TTS结果，减少重复请求
- 会话复用：复用aiohttp会话，减少连接建立开销
"""

import io
import os
import asyncio
import threading
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from collections import OrderedDict
import xml.etree.ElementTree as ET

import edge_tts
import aiohttp
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


class EdgeTTSEngine:
    """Edge TTS引擎（带性能优化）"""
    
    # 默认语言-语音映射表
    DEFAULT_VOICE_MAP = {
        "ja": "ja-JP-NanamiNeural",
        "fr": "fr-FR-DeniseNeural",
        "es": "es-ES-ElviraNeural",
        "de": "de-DE-KatjaNeural",
        "zh": "zh-CN-XiaoyiNeural",
        "en": "en-US-AnaNeural",
    }
    
    def __init__(self, config, task_lock: Optional[threading.Lock] = None,
                 get_current_task_id: Optional[callable] = None,
                 event_bus: Optional[EventBus] = None):
        """
        初始化Edge TTS引擎
        
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
        
        # 语音映射表（可从配置覆盖，优先使用新的配置路径，保持向后兼容）
        self.voice_map = config.get("tts.engines.edge_tts.voice_map") or config.get("tts.voice_map", self.DEFAULT_VOICE_MAP.copy())
        
        # 性能优化配置（缓存配置从公共配置读取，其他配置优先使用新的配置路径，保持向后兼容）
        self.cache_enabled = config.get("tts.cache_enabled", True)
        self.cache_max_size = config.get("tts.cache_max_size", None)  # None表示不设上限
        self.connection_timeout = config.get("tts.engines.edge_tts.connection_timeout", config.get("tts.connection_timeout", 30.0))
        self.enable_session_reuse = config.get("tts.engines.edge_tts.enable_session_reuse", config.get("tts.enable_session_reuse", True))
        
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
            engine_name="edge_tts"
        ) if self.tts_file_cache_enabled else None
        
        # 事件循环管理
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None
        self._loop_lock = threading.Lock()
        
        # aiohttp会话管理
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = threading.Lock()
        
        self._publish_log("DEBUG", f"Edge TTS引擎初始化完成 [缓存: {'启用' if self.cache_enabled else '禁用'}, 会话复用: {'启用' if self.enable_session_reuse else '禁用'}, 文件缓存: {'启用' if self.tts_file_cache_enabled else '禁用'}, 自动修复: {'启用' if self.tts_file_cache_auto_repair else '禁用'}]")
        
        # 检查缓存文件（根据配置决定是否自动修复）
        if self._file_cache:
            # 在后台线程中检查缓存（避免阻塞初始化）
            def check_cache():
                if self.tts_file_cache_auto_repair:
                    # 启用自动修复，提供生成函数
                    def generate_for_cache(text: str, voice: str) -> Optional[AudioSegment]:
                        """用于缓存修复的生成函数"""
                        try:
                            # 使用同步方法生成音频，使用配置的默认值（trim_silence=None会使用配置值）
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
    
    def _get_event_loop(self) -> asyncio.AbstractEventLoop:
        """
        获取或创建事件循环（线程安全）
        
        Returns:
            事件循环对象
        """
        with self._loop_lock:
            # 检查现有循环是否可用
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            
            # 创建新的事件循环
            self._loop = asyncio.new_event_loop()
            
            # 在后台线程中运行事件循环
            def run_loop():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()
            
            self._loop_thread = threading.Thread(target=run_loop, daemon=True)
            self._loop_thread.start()
            
            self._publish_log("DEBUG", "创建新的事件循环", indent=1)
            return self._loop
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """
        获取或创建aiohttp会话（异步）
        
        Returns:
            aiohttp客户端会话
        """
        if not self.enable_session_reuse:
            # 不启用会话复用，返回None让edge_tts使用默认会话
            return None
        
        with self._session_lock:
            if self._session is None or self._session.closed:
                # 创建连接器，配置连接池
                connector = aiohttp.TCPConnector(
                    limit=10,  # 最大连接数
                    limit_per_host=5,  # 每个主机最大连接数
                    ttl_dns_cache=300,  # DNS缓存时间（秒）
                    enable_cleanup_closed=True,
                )
                
                # 创建超时配置
                timeout = aiohttp.ClientTimeout(
                    total=self.connection_timeout,
                    connect=10.0,
                    sock_connect=10.0,
                    sock_read=self.connection_timeout
                )
                
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout
                )
                self._publish_log("DEBUG", "创建新的aiohttp会话", indent=1)
            
            return self._session
    
    def _generate_cache_key(self, text: str, voice: str, 
                            rate: Optional[str], pitch: Optional[str]) -> str:
        """
        生成缓存键
        
        Args:
            text: 文本
            voice: 语音
            rate: 语速
            pitch: 音调
            
        Returns:
            缓存键（MD5哈希）
        """
        # 使用实际的语速/音调值
        actual_rate = rate if rate is not None else "0%"
        actual_pitch = pitch if pitch is not None else "0%"
        
        key_str = f"{text}|{voice}|{actual_rate}|{actual_pitch}"
        return hashlib.md5(key_str.encode('utf-8')).hexdigest()
    
    def cleanup(self):
        """
        清理资源
        
        清理Edge TTS相关的asyncio资源和缓存
        """
        log_debug("TTS", "清理TTS引擎资源", indent=0)
        
        # 清理缓存
        if self._cache:
            cache_size = len(self._cache)
            self._cache.clear()
            log_debug("TTS", f"已清理缓存 [{cache_size}条目, 命中: {self._cache_hits}, 未命中: {self._cache_misses}]", indent=1)
        
        # 关闭aiohttp会话
        if self._session is not None:
            try:
                with self._session_lock:
                    if self._session and not self._session.closed:
                        # 在事件循环中关闭会话
                        if self._loop and not self._loop.is_closed():
                            future = asyncio.run_coroutine_threadsafe(
                                self._session.close(), self._loop
                            )
                            try:
                                future.result(timeout=2.0)
                            except:
                                pass
                        self._session = None
                        log_debug("TTS", "已关闭aiohttp会话", indent=1)
            except Exception as e:
                log_debug("TTS", f"关闭aiohttp会话时出错（可忽略）: {e}", indent=1)
        
        # 关闭事件循环
        try:
            with self._loop_lock:
                if self._loop and not self._loop.is_closed():
                    # 停止事件循环
                    self._loop.call_soon_threadsafe(self._loop.stop)
                    
                    # 等待循环线程结束
                    if self._loop_thread and self._loop_thread.is_alive():
                        self._loop_thread.join(timeout=2.0)
                    
                    # 取消所有pending任务
                    try:
                        pending = asyncio.all_tasks(self._loop)
                        for task in pending:
                            if not task.done():
                                task.cancel()
                    except:
                        pass
                    
                    # 关闭事件循环
                    self._loop.close()
                    self._loop = None
                    log_debug("TTS", "已关闭事件循环", indent=1)
        except Exception as e:
            log_debug("TTS", f"关闭事件循环时出错（可忽略）: {e}", indent=1)
    
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
    
    def get_voice_for_language(self, language: str) -> str:
        """
        根据语言获取语音
        
        Args:
            language: 语言代码
            
        Returns:
            语音名称
        """
        return self.voice_map.get(language, self.voice_map.get("zh", "zh-CN-XiaoyiNeural"))
    
    def get_voice_for_text(self, text: str) -> str:
        """
        根据文本自动检测语言并返回语音
        
        Args:
            text: 文本
            
        Returns:
            语音名称
        """
        language = self.detect_language(text)
        voice = self.get_voice_for_language(language)
        log_debug("TTS", f"检测语言: {language}, 选择语音: {voice}", indent=1)
        return voice
    
    def _build_ssml(self, text: str, rate: Optional[str] = None, 
                    pitch: Optional[str] = None, emotion: Optional[str] = None) -> str:
        """
        构建SSML文本
        
        Args:
            text: 原始文本
            rate: 语速（如 "+20%", "-10%", "medium"）
            pitch: 音调（如 "+20%", "-10%", "medium"）
            emotion: 情感（预留，Edge TTS可能不支持）
            
        Returns:
            SSML格式的文本
        """
        # 转义XML特殊字符
        text_escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        # 构建prosody标签
        prosody_attrs = []
        if rate is not None:
            prosody_attrs.append(f'rate="{rate}"')
        
        if pitch is not None:
            prosody_attrs.append(f'pitch="{pitch}"')
        
        if prosody_attrs:
            prosody_tag = f"<prosody {' '.join(prosody_attrs)}>"
            return f'<speak>{prosody_tag}{text_escaped}</prosody></speak>'
        else:
            return f'<speak>{text_escaped}</speak>'
    
    def _trim_silence(self, audio_data: bytes) -> Tuple[Optional[AudioSegment], int]:
        """
        裁剪音频开头和结尾的静音部分
        
        Args:
            audio_data: 音频数据（bytes）
            
        Returns:
            (处理后的AudioSegment, 裁剪掉的总时长毫秒)
        """
        try:
            # 从bytes加载音频（内存操作）
            audio = AudioSegment.from_mp3(io.BytesIO(audio_data))
            
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
                return AudioSegment.from_mp3(io.BytesIO(audio_data)), 0
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
    
    async def generate_async(self, text: str, voice: Optional[str] = None, 
                            rate: Optional[str] = None, pitch: Optional[str] = None,
                            emotion: Optional[str] = None, task_id: Optional[int] = None) -> Optional[bytes]:
        """
        异步生成TTS音频
        
        Args:
            text: 要合成的文本
            voice: 语音名称（可选，不提供则自动选择）
            rate: 语速（可选，如 "+20%", "-10%"）
            pitch: 音调（可选，如 "+20%", "-10%"）
            emotion: 情感（预留，暂未实现）
            task_id: 任务ID（用于取消检查）
            
        Returns:
            音频数据（bytes），如果任务被取消则返回None
        """
        # 检查任务是否被取消
        if self._check_task_cancelled(task_id):
            return None
        
        # 自动选择语音（如果未提供）
        if voice is None:
            voice = self.get_voice_for_text(text)
        
        # 检查缓存
        cache_key = None
        if self._cache:
            cache_key = self._generate_cache_key(text, voice, rate, pitch)
            cached_audio = self._cache.get(cache_key)
            if cached_audio is not None:
                self._cache_hits += 1
                log_debug("TTS", f"缓存命中 [key: {cache_key[:8]}..., 命中率: {self._cache_hits}/{self._cache_hits + self._cache_misses}]", indent=1)
                return cached_audio
            self._cache_misses += 1
        
        # 构建SSML（如果需要调整语速/音调）
        use_ssml = (rate is not None or pitch is not None)
        
        if use_ssml:
            ssml_text = self._build_ssml(text, rate=rate, pitch=pitch, emotion=emotion)
            communicate = edge_tts.Communicate(ssml_text, voice)
        else:
            communicate = edge_tts.Communicate(text, voice)
        
        try:
            # 直接获取音频数据到内存，避免文件I/O
            audio_bytes = b""
            async for chunk in communicate.stream():
                # 检查任务是否被取消
                if self._check_task_cancelled(task_id):
                    return None
                
                if chunk["type"] == "audio":
                    audio_bytes += chunk["data"]
            
            # 再次检查任务是否被取消
            if self._check_task_cancelled(task_id):
                return None
            
            # 存入缓存
            if self._cache and cache_key and audio_bytes:
                self._cache.put(cache_key, audio_bytes)
                self._publish_log("DEBUG", f"已缓存 [key: {cache_key[:8]}..., 缓存大小: {len(self._cache)}]", indent=1)
            
            return audio_bytes
        except Exception as e:
            self._publish_log("ERROR", f"TTS生成错误: {e}", indent=1)
            return None
    
    def generate(self, text: str, voice: Optional[str] = None,
                rate: Optional[str] = None, pitch: Optional[str] = None,
                emotion: Optional[str] = None, task_id: Optional[int] = None,
                trim_silence: Optional[bool] = None, return_timing: bool = False) -> Optional[AudioSegment]:
        """
        同步生成TTS音频（返回AudioSegment）
        
        使用事件循环复用优化，避免每次调用创建新的事件循环
        
        Args:
            text: 要合成的文本
            voice: 语音名称（可选，不提供则自动选择）
            rate: 语速（可选，如 "+20%", "-10%"）
            pitch: 音调（可选，如 "+20%", "-10%"）
            emotion: 情感（预留，暂未实现）
            task_id: 任务ID（用于取消检查）
            trim_silence: 是否裁剪静音（可选，默认使用配置）
            return_timing: 是否返回耗时信息
            
        Returns:
            AudioSegment对象，如果生成失败或任务被取消则返回None
            如果return_timing=True，返回(AudioSegment, generation_time, processing_time)
        """
        import time
        tts_start_time = time.time()
        
        # 获取voice（自动选择或使用传入的）
        if voice is None:
            voice = self.get_voice_for_text(text)
        
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
            cached_audio = self._file_cache.load(text, voice)
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
        
        try:
            # 获取事件循环并异步执行
            loop = self._get_event_loop()
            
            # 使用事件循环复用
            future = asyncio.run_coroutine_threadsafe(
                self.generate_async(
                    text, voice=voice, rate=rate, pitch=pitch, 
                    emotion=emotion, task_id=task_id
                ),
                loop
            )
            
            # 等待结果（带超时）
            audio_bytes = future.result(timeout=self.connection_timeout)
            
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
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, voice)
                
                if return_timing:
                    return audio_segment, tts_generation_time, processing_time
                return audio_segment
            else:
                # 不裁剪静音，直接返回AudioSegment
                audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_bytes))
                
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
                
                # 保存到文件缓存（所有句子都保存）
                if self._file_cache and audio_segment:
                    self._file_cache.save(text, audio_segment, voice)
                
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
