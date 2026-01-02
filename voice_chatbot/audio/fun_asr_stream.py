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
        self.pre_buffer_seconds = config.get("asr.pre_buffer_seconds", 0.5)
        self.language = config.get("asr.language", "zh")  # 默认中文
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
        
        # ASR去重器
        self.deduplicator = ASRDeduplicator()
        
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
        
        # 预加载模型（如果配置启用）
        self.preload_model = config.get("asr.preload_model", True)
        if self.preload_model:
            self._publish_log("INFO", "开始预加载ASR模型...")
            try:
                if self._load_model():
                    self._publish_log("INFO", "ASR模型预加载完成")
                else:
                    self._publish_log("WARN", "ASR模型预加载失败，将在首次使用时重试")
            except Exception as e:
                self._publish_log("WARN", f"ASR模型预加载出错: {e}，将在首次使用时重试")
        
        self._publish_log("INFO", "FunASR流式识别器初始化完成")
    
    def _load_model(self) -> bool:
        """
        加载FunASR模型（带错误处理和重试）
        
        Returns:
            True表示加载成功，False表示失败
        """
        if self.model_loaded and self.asr_model is not None:
            return True
        
        if not HAS_FUNASR:
            self._publish_log("ERROR", "FunASR库未安装，无法加载模型")
            return False
        
        max_retries = 2  # 最多重试2次
        last_error = None
        
        for retry_count in range(max_retries):
            try:
                if self.model_path:
                    self._publish_log("INFO", f"正在加载流式ASR模型: {self.model_path} [尝试 {retry_count + 1}/{max_retries}]")
                    try:
                        self.asr_model = AutoModel(model=self.model_path, device="cuda")
                    except Exception as e:
                        # 如果CUDA失败，尝试使用CPU
                        self._publish_log("WARN", f"CUDA加载失败，尝试使用CPU: {e}", indent=1)
                        self.asr_model = AutoModel(model=self.model_path, device="cpu")
                else:
                    self._publish_log("WARN", "模型路径未配置，尝试使用默认模型")
                    try:
                        # 尝试使用默认模型（可能需要网络下载）
                        self.asr_model = AutoModel(model="paraformer-zh-streaming", device="cuda")
                    except Exception as e:
                        # 如果CUDA失败，尝试使用CPU
                        self._publish_log("WARN", f"CUDA加载失败，尝试使用CPU: {e}", indent=1)
                        self.asr_model = AutoModel(model="paraformer-zh-streaming", device="cpu")
                
                self.model_loaded = True
                self._publish_log("INFO", "流式ASR模型加载完成")
                return True
            except Exception as e:
                last_error = e
                self._publish_log("ERROR", f"模型加载失败（尝试 {retry_count + 1}/{max_retries}）: {e}")
                if retry_count < max_retries - 1:
                    import time
                    time.sleep(1.0)  # 等待1秒后重试
                    continue
                else:
                    self._publish_log("ERROR", "模型加载失败，已达到最大重试次数")
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
                self._publish_log("WARN", "已经在识别中，不重复启动")
                return False

            # 记录ASR开始时间
            import time
            start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._publish_log("INFO", f"ASR识别开始 - 时间: {start_time}")
            self._publish_log("DEBUG", f"任务ID: {self.current_task_id}", indent=1)
            self._publish_log("DEBUG", f"初始speaking状态: True（等待VAD）", indent=1)

            # 检查模型是否已加载（如果已加载，直接使用，不再调用_load_model）
            if not self.model_loaded or self.asr_model is None:
                # 加载模型（如果未加载）
                if not self._load_model():
                    self._publish_log("ERROR", "模型加载失败，无法启动识别")
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
            self.deduplicator.reset()
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
                    self._publish_log("ERROR", f"发送ASR_RECOGNITION_START事件错误: {e}", indent=1)

            self._publish_log("INFO", "启动流式识别")
            return True
    
    def stop_recognition(self):
        """停止流式识别"""
        with self.state_lock:
            if not self.recognizing:
                return

            # 记录停止识别的时间和原因
            import time
            stop_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._publish_log("INFO", f"ASR停止识别调用 - 时间: {stop_time}")
            self._publish_log("INFO", f"停止原因: 外部调用stop_recognition()", indent=1)
            self._publish_log("DEBUG", f"调用前speaking状态: {self.speaking}", indent=1)
            self._publish_log("DEBUG", f"调用前recognizing状态: {self.recognizing}", indent=1)
            self._publish_log("DEBUG", f"当前部分结果: {self.partial_result}", indent=1)

            self.speaking = False
            self._publish_log("INFO", "停止识别", indent=1)
            self._publish_log("DEBUG", f"设置speaking=False，识别线程将退出主循环", indent=1)
    
    def feed_audio(self, audio_data: np.ndarray):
        """
        输入音频数据到识别队列
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        if not self.recognizing:
            return
        
        try:
            self.audio_queue.put(audio_data, timeout=0.1)
        except queue.Full:
            self._publish_log("WARN", "音频队列已满，丢弃音频数据", indent=1)
    
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
        
        # 清空去重器，准备下一次识别
        self.deduplicator.reset()
        
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
    
    def _recognition_thread_func(self):
        """流式ASR识别线程函数"""
        import time
        thread_start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._publish_log("INFO", f"识别线程启动 - 时间: {thread_start_time}")
        self._publish_log("DEBUG", f"线程ID: {threading.get_ident()}", indent=1)
        self._publish_log("DEBUG", f"初始状态: recognizing={self.recognizing}, speaking={self.speaking}", indent=1)

        try:
            buffer = np.array([], dtype=np.float32)

            # 首先处理预缓存的音频（VAD检测前的音频）
            pre_audio = self.pre_buffer.get_all()
            if len(pre_audio) > 0:
                self._publish_log("DEBUG", f"加载预缓存音频: {len(pre_audio)/self.sample_rate:.2f}秒 ({len(pre_audio)}采样点)", indent=1)
                buffer = np.concatenate([buffer, pre_audio])

            chunk_count = 0
            last_text_length = 0  # 记录上一次文本长度，用于检测是否有新文本
        
            while True:
                try:
                    # 尝试从队列获取音频
                    try:
                        audio_chunk = self.audio_queue.get(timeout=0.05)
                        buffer = np.concatenate([buffer, audio_chunk])
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
                                self._publish_log("INFO", f"ASR结束判定 - 时间: {end_time}", indent=1)
                                self._publish_log("INFO", f"结束原因: VAD判定speaking=False（用户停止说话）", indent=1)
                                self._publish_log("INFO", f"已处理音频chunks: {chunk_count}个", indent=1)
                                self._publish_log("INFO", f"当前识别文本: {current_text}", indent=1)
                                self._publish_log("INFO", f"当前文本长度: {len(current_text)}字符", indent=1)
                                self._publish_log("DEBUG", f"缓冲区剩余音频: {len(buffer)}采样点", indent=1)
                                # 语音已结束，处理剩余音频
                                break
                        continue
                    
                    # 当累积足够的音频时进行识别
                    while len(buffer) >= self.asr_chunk_stride:
                        speech_chunk = buffer[:self.asr_chunk_stride]
                        buffer = buffer[self.asr_chunk_stride:]
                        chunk_count += 1
                        
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
                                    # 使用去重器处理
                                    full_text = self.deduplicator.process(new_text)
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
                                            self._publish_log("ERROR", f"部分结果回调函数错误: {e}", indent=2)
                                    
                                    self._publish_log("DEBUG", f"部分结果: {full_text}", indent=2)
                                    
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
                                            self._publish_log("ERROR", f"发送ASR_PARTIAL_RESULT事件错误: {e}", indent=2)
                                    
                        except Exception as e:
                            self._publish_log("ERROR", f"识别错误: {e}", indent=1)
                            
                except Exception as e:
                    self._publish_log("ERROR", f"线程错误: {e}")
                    break
            
            # 处理最后剩余的音频
            if len(buffer) > 0:
                self._publish_log("DEBUG", f"处理剩余音频: {len(buffer)} 采样点", indent=1)
                
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
                            self.final_result = self.deduplicator.process(final_chunk_text)
                        else:
                            self.final_result = self.deduplicator.get_text()
                        
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
                                self._publish_log("ERROR", f"最终结果回调函数错误: {e}", indent=1)
                    else:
                        self.final_result = self.deduplicator.get_text()
                        
                        # 发送ASR最终结果事件
                        self._send_final_result_events(self.final_result, None)
                        
                        # 触发最终结果回调（即使没有新文本）
                        if self.on_final_result:
                            try:
                                self.on_final_result(self.final_result, None)
                            except Exception as e:
                                self._publish_log("ERROR", f"最终结果回调函数错误: {e}", indent=1)
                        
                except Exception as e:
                    self._publish_log("ERROR", f"最终识别错误: {e}", indent=1)
                    self.final_result = self.deduplicator.get_text()
                    
                    # 发送ASR最终结果事件
                    self._send_final_result_events(self.final_result, None)
                    
                    # 触发最终结果回调（即使出错）
                    if self.on_final_result:
                        try:
                            self.on_final_result(self.final_result, None)
                        except Exception as e:
                            self._publish_log("ERROR", f"最终结果回调函数错误: {e}", indent=1)
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
                                    self.final_result = self.deduplicator.process(final_chunk_text)
                                else:
                                    self.final_result = self.deduplicator.get_text()
                            else:
                                self.final_result = self.deduplicator.get_text()
                            
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
                                    self._publish_log("ERROR", f"最终结果回调函数错误: {e}", indent=1)
                            
                        except Exception as e:
                            self.final_result = self.deduplicator.get_text()
                            
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
                                    self._publish_log("ERROR", f"最终结果回调函数错误: {e}", indent=1)
            
            # 输出最终结果
            import time
            final_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            self._publish_log("INFO", f"ASR识别完成 - 时间: {final_time}")
            self._publish_log("INFO", f"最终识别文本: {self.final_result}")
            self._publish_log("INFO", f"最终文本长度: {len(self.final_result)}字符")
            self._publish_log("DEBUG", f"共处理 {chunk_count} 个chunk", indent=1)
            self._publish_log("DEBUG", f"音频chunk_size配置: {self.chunk_size}", indent=1)
            self._publish_log("DEBUG", f"encoder_chunk_look_back: {self.encoder_chunk_look_back}", indent=1)
            self._publish_log("DEBUG", f"decoder_chunk_look_back: {self.decoder_chunk_look_back}", indent=1)
        except Exception as e:
            self._publish_log("ERROR", f"识别线程发生未捕获的异常: {e}")
            import traceback
            self._publish_log("ERROR", f"异常堆栈: {traceback.format_exc()}", indent=1)
        finally:
            # 确保重置ASR状态，即使发生异常也要重置
            with self.state_lock:
                self.recognizing = False
                self.speaking = False
                self.asr_cache = {}
            
            self._publish_log("INFO", "识别线程结束")
    
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
            self._publish_log("ERROR", f"发送ASR事件错误: {e}", indent=1)
    
    def _publish_log(self, level: str, message: str, indent: int = 0):
        """
        通过事件系统发布日志

        Args:
            level: 日志级别（INFO, WARN, ERROR, DEBUG）
            message: 日志消息
            indent: 缩进级别
        """
        # 直接输出日志（确保一定显示）
        level_upper = level.upper()
        if level_upper == "DEBUG":
            log_debug("ASR", message, indent=indent)
        elif level_upper == "WARN":
            log_warn("ASR", message, indent=indent)
        elif level_upper == "ERROR":
            log_error("ASR", message, indent=indent)
        else:
            log_info("ASR", message, indent=indent)

        # 同时也通过事件系统发布（如果event_bus可用）
        if self.event_bus:
            try:
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
                        "category": "ASR",
                        "indent": indent,
                    },
                    source="ASR"
                )
                self.event_bus.publish(event)
            except Exception as e:
                # 事件发布失败，已经通过直接日志输出了，忽略异常
                pass

