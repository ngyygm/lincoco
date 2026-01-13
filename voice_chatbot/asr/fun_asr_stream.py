"""
FunASR流式语音识别模块

提供FunASR流式识别、预缓存机制、ASR去重器、部分/最终结果回调、多语言支持和识别置信度输出
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import threading
import queue
import time
import inspect
from typing import Optional, Callable, Dict, Any
from collections import deque

import numpy as np

try:
    from funasr import AutoModel
    HAS_FUNASR = True
except ImportError:
    HAS_FUNASR = False
    AutoModel = None

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.dialogue.events import EventType, EventBus, create_event

# 尝试导入声纹验证模块
try:
    from voice_chatbot.asr.speaker_verification import SpeakerVerification
    HAS_SPEAKER_VERIFICATION = True
except ImportError:
    HAS_SPEAKER_VERIFICATION = False
    SpeakerVerification = None


class CircularAudioBuffer:
    """环形音频缓冲区 - 用于预缓存VAD检测前的音频"""
    
    def __init__(self, max_seconds: float, sample_rate: int):
        """
        初始化环形音频缓冲区
        
        Args:
            max_seconds: 最大缓存时长（秒）
            sample_rate: 采样率
        """
        self.max_samples = int(max_seconds * sample_rate)
        self.buffer = deque(maxlen=self.max_samples)
        self.lock = threading.Lock()
    
    def append(self, audio_data: np.ndarray):
        """
        添加音频数据到缓冲区
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        with self.lock:
            for sample in audio_data:
                self.buffer.append(sample)
    
    def get_all(self) -> np.ndarray:
        """
        获取缓冲区中所有音频数据
        
        Returns:
            所有音频数据（numpy数组，float32格式）
        """
        with self.lock:
            return np.array(list(self.buffer), dtype=np.float32)
    
    def clear(self):
        """清空缓冲区"""
        with self.lock:
            self.buffer.clear()


class ASRDeduplicator:
    """
    ASR去重器 - 处理流式识别中的重复文本
    策略：
    1. 基于最长公共子串进行去重
    2. 保留新增的内容
    3. 处理纠错情况（识别错误后修正）
    """
    
    def __init__(self):
        self.accumulated_text = ""  # 累积的已确认文本
        self.last_chunk_text = ""   # 上一个chunk的原始输出
        self.confirmed_length = 0   # 已确认的文本长度（不会被修改）
    
    def process(self, new_text: str) -> str:
        """
        处理新的识别结果，返回去重后的完整文本
        
        Args:
            new_text: ASR模型返回的当前chunk识别结果
            
        Returns:
            去重后的完整文本
        """
        if not new_text:
            return self.accumulated_text
        
        if not self.accumulated_text:
            self.accumulated_text = new_text
            self.last_chunk_text = new_text
            return self.accumulated_text
        
        # 查找重叠部分
        overlap_len = self._find_overlap(self.accumulated_text, new_text)
        
        if overlap_len > 0:
            # 有重叠，取新文本中超出重叠部分的内容
            new_content = new_text[overlap_len:]
            if new_content:
                self.accumulated_text += new_content
        else:
            # 无重叠，可能是模型纠错或新句子
            # 检查是否是对上一段的纠正
            if self._is_correction(self.last_chunk_text, new_text):
                # 替换最后一个chunk的内容
                correction_start = len(self.accumulated_text) - len(self.last_chunk_text)
                if correction_start >= 0:
                    self.accumulated_text = self.accumulated_text[:correction_start] + new_text
                else:
                    self.accumulated_text = new_text
            else:
                # 直接追加
                self.accumulated_text += new_text
        
        self.last_chunk_text = new_text
        return self.accumulated_text
    
    def _find_overlap(self, text1: str, text2: str) -> int:
        """
        查找text1的后缀与text2的前缀的最长重叠
        返回重叠长度
        """
        max_overlap = min(len(text1), len(text2))
        for i in range(max_overlap, 0, -1):
            if text1[-i:] == text2[:i]:
                return i
        return 0
    
    def _is_correction(self, old_text: str, new_text: str) -> bool:
        """
        判断new_text是否是对old_text的纠正
        简单策略：如果两者相似度高但不完全相同，认为是纠正
        """
        if not old_text or not new_text:
            return False
        
        # 计算相似字符数
        common_chars = sum(1 for a, b in zip(old_text, new_text) if a == b)
        max_len = max(len(old_text), len(new_text))
        
        if max_len == 0:
            return False
        
        similarity = common_chars / max_len
        
        # 相似度高于0.5但不完全相同，认为是纠正
        return 0.5 < similarity < 1.0
    
    def reset(self):
        """重置去重器状态"""
        self.accumulated_text = ""
        self.last_chunk_text = ""
        self.confirmed_length = 0
    
    def get_text(self) -> str:
        """获取当前累积的文本"""
        return self.accumulated_text


class FunASRStream:
    """FunASR流式语音识别器"""
    
    def __init__(self, config,
                 pre_buffer: Optional[CircularAudioBuffer] = None,
                 on_partial_result: Optional[Callable[[str, Optional[float]], None]] = None,
                 on_final_result: Optional[Callable[[str, Optional[float]], None]] = None,
                 event_bus: Optional[EventBus] = None):
        """
        初始化FunASR流式识别器
        
        Args:
            config: 配置对象（Config实例）
            pre_buffer: 预缓存缓冲区（可选，如果不提供则创建新的）
            on_partial_result: 部分结果回调函数 (text, confidence)
            on_final_result: 最终结果回调函数 (text, confidence)
            event_bus: 事件总线（用于发送事件和统一日志输出）
        """
        # 检查回调函数接受的参数数量
        self._on_partial_result_needs_confidence = False
        if on_partial_result:
            import inspect
            try:
                sig = inspect.signature(on_partial_result)
                # 排除self参数后的参数数量
                param_count = len([p for p in sig.parameters.values() if p.name != 'self'])
                self._on_partial_result_needs_confidence = param_count >= 2
            except:
                pass
        
        self._on_final_result_needs_confidence = False
        if on_final_result:
            import inspect
            try:
                sig = inspect.signature(on_final_result)
                param_count = len([p for p in sig.parameters.values() if p.name != 'self'])
                self._on_final_result_needs_confidence = param_count >= 2
            except:
                pass
        if not HAS_FUNASR:
            raise ImportError("funasr库未安装，请安装：pip install funasr")
        
        self.config = config
        self.on_partial_result = on_partial_result
        self.on_final_result = on_final_result
        self.event_bus = event_bus
        self.current_task_id: Optional[int] = None  # 当前任务ID
        
        # 从配置获取参数
        self.sample_rate = config.get("audio.sample_rate", 16000)
        self.model_path = config.get("asr.model_path", "")
        self.chunk_size = config.get("asr.chunk_size", [0, 10, 5])
        self.encoder_chunk_look_back = config.get("asr.encoder_chunk_look_back", 4)
        self.decoder_chunk_look_back = config.get("asr.decoder_chunk_look_back", 1)
        self.pre_buffer_seconds = config.get("asr.pre_buffer_seconds", 1)
        self.language = config.get("asr.language", "zh")  # 默认中文
        self.enable_deduplication = config.get("asr.enable_deduplication", True)  # 是否启用去重
        # 注意：ASR不再使用no_speech_threshold，完全依赖VAD的speaking标志来决定何时结束识别
        
        # 验证模型路径
        if not self.model_path:
            # 此时event_bus可能还未完全初始化，使用直接日志
            log_warn("ASR", "模型路径未配置，将尝试使用默认模型", indent=0)
        
        # 计算chunk stride（采样点数）
        # chunk_size[1] * 960 = 600ms的采样点数（假设采样率16000）
        self.asr_chunk_stride = self.chunk_size[1] * 960 if len(self.chunk_size) > 1 else 9600
        
        # 预缓存缓冲区
        if pre_buffer is None:
            self.pre_buffer = CircularAudioBuffer(self.pre_buffer_seconds, self.sample_rate)
        else:
            self.pre_buffer = pre_buffer
        
        # ASR去重器（总是创建，但根据配置决定是否使用）
        self.deduplicator = ASRDeduplicator()
        # 用于存储累积文本（当去重禁用时使用）
        self.accumulated_text = ""
        
        # 状态控制
        self.recognizing = False
        self.speaking = False  # 是否正在说话
        self.state_lock = threading.Lock()
        
        # ASR模型和缓存
        self.asr_model: Optional[AutoModel] = None
        self.asr_cache: Dict[str, Any] = {}
        
        # 音频队列（限制大小防止内存泄漏）
        audio_queue_maxsize = config.get("asr.audio_queue_maxsize", 100)
        self.audio_queue = queue.Queue(maxsize=audio_queue_maxsize)
        
        # 识别线程
        self.recognition_thread: Optional[threading.Thread] = None
        
        # 识别结果
        self.final_result = ""
        self.partial_result = ""
        
        # 模型加载标志
        self.model_loaded = False
        
        # 初始化声纹验证器（如果启用）
        self.speaker_verification = None
        if HAS_SPEAKER_VERIFICATION:
            try:
                # 先创建SpeakerVerification（传入asr_model=None，会在模型加载后设置）
                self.speaker_verification = SpeakerVerification(config, asr_model=None)
                log_info("ASR", f"声纹验证器初始化成功")
            except Exception as e:
                log_warn("ASR", f"声纹验证器初始化失败: {e}，将禁用声纹锁功能")
                self.speaker_verification = None
        
        # 声纹验证相关状态
        self.speaker_verified = False  # 当前识别任务是否已通过声纹验证
        self.min_verification_length = config.get("asr.speaker_verification.min_audio_length", 1.0) * self.sample_rate  # 最小验证音频长度（采样点数）
        
        # 滑动窗口声纹验证参数
        self.sv_sliding_window_enabled = config.get("asr.speaker_verification.sliding_window.enabled", True)
        self.sv_window_length = config.get("asr.speaker_verification.sliding_window.window_length", 1.0)
        self.sv_window_stride = config.get("asr.speaker_verification.sliding_window.window_stride", 0.5)
        self.sv_consecutive_hits = config.get("asr.speaker_verification.sliding_window.consecutive_hits", 3)  # 连续命中次数
        
        # 预加载模型（如果配置启用）
        self.preload_model = config.get("asr.preload_model", True)
        if self.preload_model:
            log_debug("ASR", "开始预加载ASR模型...")
            try:
                if self._load_model():
                    log_debug("ASR", "ASR模型预加载完成")
                else:
                    log_warn("ASR", "ASR模型预加载失败，将在首次使用时重试")
            except Exception as e:
                log_warn("ASR", f"ASR模型预加载出错: {e}，将在首次使用时重试")
        
        log_debug("ASR", "FunASR流式识别器初始化完成")
    
    def _load_model(self) -> bool:
        """
        加载FunASR模型（带错误处理和重试）
        
        Returns:
            True表示加载成功，False表示失败
        """
        if self.model_loaded and self.asr_model is not None:
            return True
        
        if not HAS_FUNASR:
            log_error("ASR", "FunASR库未安装，无法加载模型")
            return False
        
        max_retries = 2  # 最多重试2次
        last_error = None
        
        for retry_count in range(max_retries):
            try:
                if self.model_path:
                    log_debug("ASR", f"正在加载流式ASR模型: {self.model_path} [尝试 {retry_count + 1}/{max_retries}]")
                    try:
                        self.asr_model = AutoModel(model=self.model_path, device="cuda", disable_pbar=True)
                    except Exception as e:
                        # 如果CUDA失败，尝试使用CPU
                        log_warn("ASR", f"CUDA加载失败，尝试使用CPU: {e}", indent=1)
                        self.asr_model = AutoModel(model=self.model_path, device="cpu", disable_pbar=True)
                else:
                    log_warn("ASR", "模型路径未配置，尝试使用默认模型")
                    try:
                        # 尝试使用默认模型（可能需要网络下载）
                        self.asr_model = AutoModel(model="paraformer-zh-streaming", device="cuda", disable_pbar=True)
                    except Exception as e:
                        # 如果CUDA失败，尝试使用CPU
                        log_warn("ASR", f"CUDA加载失败，尝试使用CPU: {e}", indent=1)
                        self.asr_model = AutoModel(model="paraformer-zh-streaming", device="cpu", disable_pbar=True)
                
                self.model_loaded = True
                log_debug("ASR", "流式ASR模型加载完成")
                
                # 将ASR模型传递给声纹验证器（如果存在）
                if self.speaker_verification is not None:
                    self.speaker_verification.asr_model = self.asr_model
                    log_debug("ASR", "已将ASR模型传递给声纹验证器", indent=1)
                
                return True
            except Exception as e:
                last_error = e
                log_error("ASR", f"模型加载失败（尝试 {retry_count + 1}/{max_retries}）: {e}")
                if retry_count < max_retries - 1:
                    import time
                    time.sleep(1.0)  # 等待1秒后重试
                    continue
                else:
                    log_error("ASR", "模型加载失败，已达到最大重试次数")
                    break
        
        return False
    
    def start_recognition(self) -> bool:
        """
        启动流式识别

        Returns:
            True表示启动成功，False表示失败
        """
        with self.state_lock:
            if self.recognizing:
                log_warn("ASR", "已经在识别中，不重复启动")
                return False

            # 记录ASR开始时间
            import time
            start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            log_debug("ASR", f"ASR识别开始 - 时间: {start_time}")
            log_debug("ASR", f"任务ID: {self.current_task_id}, 初始speaking状态: True（等待VAD）", indent=1)

            # 检查模型是否已加载（如果已加载，直接使用，不再调用_load_model）
            if not self.model_loaded or self.asr_model is None:
                # 加载模型（如果未加载）
                if not self._load_model():
                    log_error("ASR", "模型加载失败，无法启动识别")
                    return False

            # 清空队列和状态
            while not self.audio_queue.empty():
                try:
                    self.audio_queue.get_nowait()
                except:
                    pass

            self.asr_cache = {}
            self.final_result = ""
            self.partial_result = ""
            self._reset_text_accumulator()
            
            # 重置声纹验证状态
            self.speaker_verified = False
            
            self.recognizing = True
            self.speaking = True

            # 启动识别线程
            self.recognition_thread = threading.Thread(
                target=self._recognition_thread_func,
                daemon=True,
                name="ASR-Recog"
            )
            self.recognition_thread.start()

            # 发送ASR识别开始事件
            if self.event_bus:
                try:
                    event = create_event(
                        EventType.ASR_RECOGNITION_START,
                        data={"task_id": self.current_task_id} if self.current_task_id else {},
                        source="ASR"
                    )
                    self.event_bus.publish(event)
                except Exception as e:
                    log_error("ASR", f"发送ASR_RECOGNITION_START事件错误: {e}", indent=1)

            log_debug("ASR", "启动流式识别")
            return True
    
    def stop_recognition(self):
        """停止流式识别"""
        with self.state_lock:
            if not self.recognizing:
                return

            # 记录停止识别的时间和原因
            import time
            stop_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            log_debug("ASR", f"ASR停止识别调用 - 时间: {stop_time}, 原因: 外部调用stop_recognition()", indent=1)
            log_debug("ASR", f"调用前speaking={self.speaking}, recognizing={self.recognizing}, 部分结果: {self.partial_result}", indent=1)

            self.speaking = False
            log_debug("ASR", f"设置speaking=False，识别线程将退出主循环", indent=1)
    
    def feed_audio(self, audio_data: np.ndarray):
        """
        输入音频数据到识别队列
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        if not self.recognizing:
            return
        
        # 如果启用了声纹锁，需要先通过验证才能进行识别
        # 验证在识别线程中进行，这里只需要将音频送入队列
        # 识别线程会在开始时进行验证
        try:
            self.audio_queue.put(audio_data, timeout=0.1)
        except queue.Full:
            log_warn("ASR", "音频队列已满，丢弃音频数据", indent=1)
    
    def wait_for_result(self, timeout: Optional[float] = None) -> str:
        """
        等待识别完成并返回结果
        
        Args:
            timeout: 超时时间（秒），如果为None则无限等待
            
        Returns:
            最终识别结果文本
        """
        # 先停止识别
        self.stop_recognition()
        
        # 等待识别线程完成
        if self.recognition_thread and self.recognition_thread.is_alive():
            self.recognition_thread.join(timeout=timeout)
        
        # 获取结果
        result = self.final_result
        
        # 清空文本累积器，准备下一次识别
        self._reset_text_accumulator()
        
        # 清空结果
        self.final_result = ""
        self.partial_result = ""
        
        return result
    
    def is_recognizing(self) -> bool:
        """
        检查是否正在识别
        
        Returns:
            True表示正在识别，False表示未识别
        """
        with self.state_lock:
            return self.recognizing
    
    def _extract_confidence(self, result: Dict[str, Any]) -> Optional[float]:
        """
        从FunASR结果中提取置信度
        
        Args:
            result: FunASR模型返回的结果字典
            
        Returns:
            置信度值（0.0-1.0），如果不可用则返回None
        """
        # FunASR的结果格式可能包含置信度信息
        # 根据实际模型输出格式调整
        if isinstance(result, dict):
            # 尝试从不同可能的字段中提取置信度
            confidence = result.get('confidence') or result.get('score') or result.get('prob')
            if confidence is not None:
                return float(confidence)
        
        # 如果结果是一个列表，检查第一个元素
        if isinstance(result, list) and len(result) > 0:
            first_item = result[0]
            if isinstance(first_item, dict):
                confidence = first_item.get('confidence') or first_item.get('score') or first_item.get('prob')
                if confidence is not None:
                    return float(confidence)
        
        return None
    
    def _process_text(self, new_text: str) -> str:
        """
        处理新的识别文本，根据配置决定是否使用去重
        
        Args:
            new_text: ASR模型返回的当前chunk识别结果
            
        Returns:
            处理后的完整文本
        """
        if self.enable_deduplication:
            return self.deduplicator.process(new_text)
        else:
            # 如果禁用去重，累加文本
            if new_text:
                self.accumulated_text += new_text
            return self.accumulated_text
    
    def _get_accumulated_text(self) -> str:
        """
        获取累积的文本，根据配置决定是否使用去重器
        
        Returns:
            累积的文本
        """
        if self.enable_deduplication:
            return self.deduplicator.get_text()
        else:
            return self.accumulated_text
    
    def _reset_text_accumulator(self):
        """重置文本累积器（根据配置重置对应的存储）"""
        if self.enable_deduplication:
            self.deduplicator.reset()
        else:
            self.accumulated_text = ""
    
    def _recognition_thread_func(self):
        """流式ASR识别线程函数"""
        import time
        thread_start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        log_debug("ASR", f"识别线程启动 - 时间: {thread_start_time}, 线程ID: {threading.get_ident()}, 初始状态: recognizing={self.recognizing}, speaking={self.speaking}")

        try:
            buffer = np.array([], dtype=np.float32)

            # 首先处理预缓存的音频（VAD检测前的音频）
            pre_audio = self.pre_buffer.get_all()
            if len(pre_audio) > 0:
                duration = len(pre_audio) / self.sample_rate
                log_debug("ASR", f"加载预缓存音频: {duration:.2f}秒 ({len(pre_audio)}采样点)", indent=1)
                buffer = np.concatenate([buffer, pre_audio])
                log_debug("ASR", f"加载预缓存后，buffer长度: {len(buffer)}采样点({len(buffer)/self.sample_rate:.2f}秒)", indent=1)
            
            # 检查声纹验证状态
            speaker_verification_enabled = self.speaker_verification and self.speaker_verification.is_enabled() and not self.speaker_verified
            log_debug("ASR", f"声纹验证状态: enabled={speaker_verification_enabled}, speaker_verification={self.speaker_verification is not None}, is_enabled={self.speaker_verification.is_enabled() if self.speaker_verification else False}, speaker_verified={self.speaker_verified if hasattr(self, 'speaker_verified') else 'N/A'}", indent=1)
            
            # 如果启用了声纹锁且尚未验证，使用滑动窗口进行声纹验证
            if speaker_verification_enabled:
                # 从配置读取滑动窗口参数
                window_length = self.sv_window_length if self.sv_sliding_window_enabled else self.min_verification_length / self.sample_rate
                window_stride = self.sv_window_stride if self.sv_sliding_window_enabled else 0
                window_samples = int(window_length * self.sample_rate)
                stride_samples = int(window_stride * self.sample_rate)
                consecutive_hits_required = self.sv_consecutive_hits if self.sv_sliding_window_enabled else 1
                
                # 计算需要保留的最小长度（优先遵循pre_buffer_seconds）
                pre_buffer_samples = int(self.pre_buffer_seconds * self.sample_rate)
                log_debug("ASR", f"预缓存保留长度: {pre_buffer_samples}采样点({self.pre_buffer_seconds:.2f}秒)", indent=1)
                
                log_debug("ASR", f"启用声纹 {'滑动窗口' if self.sv_sliding_window_enabled else '固定窗口'}验证 - 窗口长度: {window_length}秒" + (f", 滑动步长: {window_stride}秒, 连续命中次数: {consecutive_hits_required}" if self.sv_sliding_window_enabled else ""))
                
                verification_complete = False
                consecutive_hits = 0  # 连续命中计数
                first_hit_position = None  # 第一次通过的声纹位置（在buffer中的起始位置）
                
                # 声纹验证循环
                while not verification_complete:
                    # 检查VAD状态，如果用户停止说话则退出验证循环
                    with self.state_lock:
                        if not self.speaking:
                            log_debug("ASR", "用户停止说话，结束声纹验证")
                            break
                    
                    # 尝试获取更多音频
                    try:
                        audio_chunk = self.audio_queue.get(timeout=0.1)
                        buffer = np.concatenate([buffer, audio_chunk])
                    except queue.Empty:
                        continue
                    
                    # 当缓冲区有足够的音频时进行验证
                    while len(buffer) >= window_samples:
                        # 从buffer末尾往前取window_samples长度的音频进行验证
                        window_start = len(buffer) - window_samples
                        window_audio = buffer[window_start:]
                        
                        # 进行声纹验证
                        is_match, similarity = self.speaker_verification.verify(window_audio, self.sample_rate)
                        
                        if is_match:
                            # 验证通过，增加连续命中计数
                            consecutive_hits += 1
                            
                            # 记录第一次通过的声纹位置
                            if first_hit_position is None:
                                first_hit_position = window_start
                                log_debug("ASR", f"第一次声纹验证通过，相似度: {similarity:.4f}, 位置: {window_start}采样点 ({window_start/self.sample_rate:.2f}秒)")
                            
                            log_debug("ASR", f"声纹验证通过 ({consecutive_hits}/{consecutive_hits_required})，相似度: {similarity:.4f}")
                            
                            # 检查是否达到连续命中次数要求
                            if consecutive_hits >= consecutive_hits_required:
                                # 达到连续命中次数要求，验证完成
                                self.speaker_verified = True
                                verification_complete = True
                                log_debug("ASR", f"连续{consecutive_hits}次声纹验证通过，开始正式识别")
                                
                                # 从第一次通过的声纹位置开始记录音频数据流
                                # 但优先遵循pre_buffer_seconds设定的长度，确保预缓存不被截断
                                if first_hit_position is not None and first_hit_position > 0:
                                    # 计算需要保留的起始位置
                                    # 优先保留pre_buffer_seconds长度的音频（从buffer开头）
                                    # 如果first_hit_position在pre_buffer_samples之前，保留整个pre_buffer_samples（从0开始）
                                    # 如果first_hit_position在pre_buffer_samples之后，从first_hit_position开始，但也要确保至少保留pre_buffer_samples长度
                                    if first_hit_position < pre_buffer_samples:
                                        # first_hit_position在预缓存范围内，保留整个pre_buffer_samples（从0开始）
                                        keep_start = 0
                                        log_debug("ASR", 
                                                f"first_hit_position({first_hit_position})在预缓存范围内，保留整个预缓存（从0开始）", 
                                                indent=1)
                                    else:
                                        # first_hit_position在预缓存范围外
                                        # 从first_hit_position开始，但确保至少保留pre_buffer_samples长度
                                        # 如果从first_hit_position开始会丢失太多预缓存，则从len(buffer) - pre_buffer_samples开始
                                        min_keep_start = max(0, len(buffer) - pre_buffer_samples)
                                        keep_start = min(first_hit_position, min_keep_start)
                                        log_debug("ASR", 
                                                f"first_hit_position({first_hit_position})在预缓存范围外，"
                                                f"min_keep_start={min_keep_start}，最终keep_start={keep_start}", 
                                                indent=1)
                                    
                                    buffer = buffer[keep_start:]
                                    log_debug("ASR", 
                                            f"声纹验证通过后保留音频: 从位置{keep_start}开始, "
                                            f"保留 {len(buffer)} 采样点 ({len(buffer)/self.sample_rate:.2f}秒), "
                                            f"first_hit_position={first_hit_position}, pre_buffer_samples={pre_buffer_samples}", 
                                            indent=1)
                                else:
                                    # 如果没有first_hit_position，确保至少保留pre_buffer_seconds长度
                                    if len(buffer) > pre_buffer_samples:
                                        keep_start = max(0, len(buffer) - pre_buffer_samples)
                                        buffer = buffer[keep_start:]
                                        log_debug("ASR", 
                                                f"声纹验证通过后保留音频（无first_hit_position）: "
                                                f"从位置{keep_start}开始，保留 {len(buffer)} 采样点 ({len(buffer)/self.sample_rate:.2f}秒)", 
                                                indent=1)
                                    else:
                                        log_debug("ASR", 
                                                f"声纹验证通过后保留音频（无first_hit_position）: "
                                                f"buffer长度({len(buffer)})小于pre_buffer_samples({pre_buffer_samples})，保留全部", 
                                                indent=1)
                                
                                break
                            else:
                                # 未达到连续命中次数，继续滑动验证（保持连续命中计数）
                                if self.sv_sliding_window_enabled and stride_samples > 0:
                                    # 滑动窗口模式：向后滑动一个步长
                                    # 保留从第一次命中位置（如果有）或当前窗口开始位置往后的所有音频
                                    if first_hit_position is not None:
                                        # 保留从第一次命中位置往后的所有音频
                                        buffer = buffer[first_hit_position:]
                                    else:
                                        # 还没有第一次命中，滑动stride_samples
                                        if len(buffer) > stride_samples:
                                            buffer = buffer[-stride_samples:]
                                    
                                    # 检查剩余音频是否足够继续验证
                                    if len(buffer) < window_samples:
                                        break
                                    # 继续循环，从新位置验证
                                else:
                                    # 固定窗口模式或步长为0：只验证一次，通过则完成
                                    self.speaker_verified = True
                                    verification_complete = True
                                    break
                        else:
                            # 验证失败，重置连续命中计数
                            consecutive_hits = 0
                            first_hit_position = None
                            log_debug("ASR", f"声纹验证未通过，相似度: {similarity:.4f}，重置连续命中计数")
                            
                            if self.sv_sliding_window_enabled and stride_samples > 0:
                                # 滑动窗口模式：向后滑动一个步长
                                if len(buffer) > stride_samples:
                                    buffer = buffer[-stride_samples:]
                                else:
                                    buffer = buffer
                                
                                # 检查剩余音频是否足够继续验证
                                if len(buffer) < window_samples:
                                    break
                                # 继续循环，从新位置验证
                            else:
                                # 固定窗口模式或步长为0：只验证一次，失败则退出
                                break
                    
                    # 如果验证完成，退出循环
                    if verification_complete:
                        break
                
                # 声纹验证循环结束
                if self.speaker_verified:
                    log_debug("ASR", "声纹验证通过，继续ASR识别")
                else:
                    # 声纹验证未通过，停止本次识别
                    log_debug("ASR", "声纹验证未通过，停止本次ASR识别")
                    
                    # 重置识别状态
                    with self.state_lock:
                        self.recognizing = False
                        self.speaking = False
                        self.partial_result = ""
                        self._reset_text_accumulator()
                    
                    return  # 直接退出识别线程，不进行ASR识别
            else:
                log_debug("ASR", "声纹验证未启用，跳过声纹验证部分", indent=1)

            chunk_count = 0
            last_text_length = 0  # 记录上一次文本长度，用于检测是否有新文本
            
            # 记录进入主循环前的buffer状态
            log_debug("ASR", f"进入主识别循环前，buffer长度: {len(buffer)}采样点({len(buffer)/self.sample_rate:.2f}秒)", indent=1)
            if len(buffer) == 0:
                log_warn("ASR", "警告：进入主识别循环时buffer为空！预缓存可能丢失", indent=1)
        
            while True:
                try:
                    # 尝试从队列获取音频
                    try:
                        audio_chunk = self.audio_queue.get(timeout=0.05)
                        buffer_before = len(buffer)
                        buffer = np.concatenate([buffer, audio_chunk])
                        buffer_after = len(buffer)
                    except queue.Empty:
                        # 队列为空，检查VAD状态
                        with self.state_lock:
                            # 如果VAD已经标记为结束，直接退出
                            # 注意：ASR不应该自己决定何时结束，应该完全依赖VAD的speaking标志
                            if not self.speaking:
                                # ASR结束：记录结束时间和原因
                                import time
                                end_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                                current_text = self.partial_result
                                log_debug("ASR", f"ASR结束判定 - 时间: {end_time}, 原因: VAD判定speaking=False（用户停止说话）, 已处理{chunk_count}个chunk, 当前文本: {current_text}, 缓冲区剩余: {len(buffer)}采样点", indent=1)
                                # 语音已结束，处理剩余音频
                                break
                        continue
                    
                    
                    while len(buffer) >= self.asr_chunk_stride:
                        # 提取chunk
                        speech_chunk = buffer[:self.asr_chunk_stride]
                        buffer = buffer[self.asr_chunk_stride:]
                        chunk_count += 1
                        
                        # 第一次进入识别循环时，记录日志
                        if chunk_count == 1:
                            log_debug("ASR", f"第一次进入识别循环，当前buffer长度: {len(buffer)}采样点({len(buffer)/self.sample_rate:.2f}秒), asr_chunk_stride: {self.asr_chunk_stride}采样点", indent=1)
                        
                        # 进行流式识别
                        try:
                            res = self.asr_model.generate(
                                input=speech_chunk,
                                cache=self.asr_cache,
                                is_final=False,
                                disable_pbar=True,
                                chunk_size=self.chunk_size,
                                encoder_chunk_look_back=self.encoder_chunk_look_back,
                                decoder_chunk_look_back=self.decoder_chunk_look_back
                            )
                            
                            if res and len(res) > 0:
                                new_text = res[0].get('text', '').strip()
                                if new_text:
                                    # 处理文本（根据配置决定是否使用去重）
                                    full_text = self._process_text(new_text)
                                    self.partial_result = full_text
                                    
                                    # 更新文本长度（有新文本）
                                    if len(full_text) != last_text_length:
                                        last_text_length = len(full_text)
                                    
                                    # 提取置信度
                                    confidence = self._extract_confidence(res[0])
                                    
                                    # 触发部分结果回调
                                    if self.on_partial_result:
                                        try:
                                            # 根据回调函数接受的参数数量决定是否传递confidence
                                            if self._on_partial_result_needs_confidence:
                                                self.on_partial_result(full_text, confidence)
                                            else:
                                                self.on_partial_result(full_text)
                                        except Exception as e:
                                            log_error("ASR", f"部分结果回调函数错误: {e}", indent=2)
                                    
                                    log_debug("ASR", f"部分结果: {full_text}", indent=2)
                                    
                                    # 发送ASR部分结果事件（用于实时日志输出）
                                    if self.event_bus:
                                        try:
                                            partial_event = create_event(
                                                EventType.ASR_PARTIAL_RESULT,
                                                data={
                                                    "task_id": self.current_task_id,
                                                    "partial_text": full_text,
                                                    "confidence": confidence
                                                },
                                                source="ASR"
                                            )
                                            self.event_bus.publish(partial_event)
                                        except Exception as e:
                                            log_error("ASR", f"发送ASR_PARTIAL_RESULT事件错误: {e}", indent=2)
                                    
                        except Exception as e:
                            log_error("ASR", f"识别错误: {e}", indent=1)
                            
                except Exception as e:
                    log_error("ASR", f"线程错误: {e}")
                    break
            
            # 处理最后剩余的音频
            if len(buffer) > 0:
                log_debug("ASR", f"处理剩余音频: {len(buffer)} 采样点", indent=1)
                
                # 用静音补齐到完整的chunk大小
                padding_size = self.asr_chunk_stride - len(buffer)
                if padding_size > 0:
                    # 使用静音（零值）补齐
                    padded_audio = np.concatenate([
                        buffer,
                        np.zeros(padding_size, dtype=np.float32)
                    ])
                else:
                    padded_audio = buffer
                
                # 进行最终识别
                try:
                    res = self.asr_model.generate(
                        input=padded_audio,
                        cache=self.asr_cache,
                        is_final=True,  # 标记为最终识别
                        disable_pbar=True,
                        chunk_size=self.chunk_size,
                        encoder_chunk_look_back=self.encoder_chunk_look_back,
                        decoder_chunk_look_back=self.decoder_chunk_look_back
                    )
                    
                    if res and len(res) > 0:
                        final_chunk_text = res[0].get('text', '').strip()
                        if final_chunk_text:
                            self.final_result = self._process_text(final_chunk_text)
                        else:
                            self.final_result = self._get_accumulated_text()
                        
                        # 提取置信度
                        confidence = self._extract_confidence(res[0])
                        
                        # 发送ASR最终结果事件
                        self._send_final_result_events(self.final_result, confidence)
                        
                        # 触发最终结果回调
                        if self.on_final_result:
                            try:
                                if self._on_final_result_needs_confidence:
                                    self.on_final_result(self.final_result, confidence)
                                else:
                                    self.on_final_result(self.final_result)
                            except Exception as e:
                                log_error("ASR", f"最终结果回调函数错误: {e}", indent=1)
                    else:
                        self.final_result = self._get_accumulated_text()
                        
                        # 发送ASR最终结果事件
                        self._send_final_result_events(self.final_result, None)
                        
                        # 触发最终结果回调（即使没有新文本）
                        if self.on_final_result:
                            try:
                                self.on_final_result(self.final_result, None)
                            except Exception as e:
                                log_error("ASR", f"最终结果回调函数错误: {e}", indent=1)
                        
                except Exception as e:
                    log_error("ASR", f"最终识别错误: {e}", indent=1)
                    self.final_result = self._get_accumulated_text()
                    
                    # 发送ASR最终结果事件
                    self._send_final_result_events(self.final_result, None)
                    
                    # 触发最终结果回调（即使出错）
                    if self.on_final_result:
                        try:
                            self.on_final_result(self.final_result, None)
                        except Exception as e:
                            log_error("ASR", f"最终结果回调函数错误: {e}", indent=1)
                    else:
                        # 没有剩余音频，使用空数组进行最终识别以获取缓存中的内容
                        try:
                            res = self.asr_model.generate(
                                input=np.array([], dtype=np.float32),
                                cache=self.asr_cache,
                                is_final=True,
                                disable_pbar=True,
                                chunk_size=self.chunk_size,
                                encoder_chunk_look_back=self.encoder_chunk_look_back,
                                decoder_chunk_look_back=self.decoder_chunk_look_back
                            )
                            
                            if res and len(res) > 0:
                                final_chunk_text = res[0].get('text', '').strip()
                                if final_chunk_text:
                                    self.final_result = self._process_text(final_chunk_text)
                                else:
                                    self.final_result = self._get_accumulated_text()
                            else:
                                self.final_result = self._get_accumulated_text()
                            
                            # 提取置信度
                            confidence = None
                            if res and len(res) > 0:
                                confidence = self._extract_confidence(res[0])
                            
                            # 发送ASR最终结果事件
                            self._send_final_result_events(self.final_result, confidence)
                            
                            # 触发最终结果回调
                            if self.on_final_result:
                                try:
                                    if self._on_final_result_needs_confidence:
                                        self.on_final_result(self.final_result, confidence)
                                    else:
                                        self.on_final_result(self.final_result)
                                except Exception as e:
                                    log_error("ASR", f"最终结果回调函数错误: {e}", indent=1)
                            
                        except Exception as e:
                            self.final_result = self._get_accumulated_text()
                            
                            # 发送ASR最终结果事件
                            self._send_final_result_events(self.final_result, None)
                            
                            # 触发最终结果回调（即使出错）
                            if self.on_final_result:
                                try:
                                    if self._on_final_result_needs_confidence:
                                        self.on_final_result(self.final_result, None)
                                    else:
                                        self.on_final_result(self.final_result)
                                except Exception as e:
                                    log_error("ASR", f"最终结果回调函数错误: {e}", indent=1)
            
            # 输出最终结果
            import time
            final_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            log_debug("ASR", f"ASR识别完成 - 时间: {final_time}, 最终文本 {len(self.final_result)}字符: {self.final_result}")
            log_debug("ASR", f"共处理 {chunk_count} 个chunk, chunk_size={self.chunk_size}, encoder_chunk_look_back={self.encoder_chunk_look_back}, decoder_chunk_look_back={self.decoder_chunk_look_back}", indent=1)
        except Exception as e:
            log_error("ASR", f"识别线程发生未捕获的异常: {e}")
            import traceback
            log_error("ASR", f"异常堆栈: {traceback.format_exc()}", indent=1)
        finally:
            # 确保重置ASR状态，即使发生异常也要重置
            with self.state_lock:
                self.recognizing = False
                self.speaking = False
                self.asr_cache = {}
            
            log_debug("ASR", "识别线程结束")
    
    def _send_final_result_events(self, text: str, confidence: Optional[float]):
        """
        发送ASR最终结果相关事件
        
        Args:
            text: 识别结果文本
            confidence: 置信度
        """
        if not self.event_bus:
            return
        
        try:
            # 发送ASR_FINAL_RESULT事件
            event_data = {
                "task_id": self.current_task_id,
                "text": text,
                "confidence": confidence
            }
            final_event = create_event(
                EventType.ASR_FINAL_RESULT,
                data=event_data,
                source="ASR"
            )
            self.event_bus.publish(final_event)
            
            # 发送ASR_RECOGNITION_END事件
            end_event = create_event(
                EventType.ASR_RECOGNITION_END,
                data=event_data,
                source="ASR"
            )
            self.event_bus.publish(end_event)
        except Exception as e:
            log_error("ASR", f"发送ASR事件错误: {e}", indent=1)
    
    