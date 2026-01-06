"""
VAD（语音活动检测）模块

基于 WebRTC VAD 库实现语音活动检测，提供语音开始/结束回调
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import threading
import time
import queue
from typing import Optional, Callable

import numpy as np

try:
    import webrtcvad
except ImportError:
    webrtcvad = None
    print("警告: webrtcvad 未安装，VAD 功能将不可用")

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug


class WebRTCVAD:
    """WebRTC VAD 语音活动检测器"""
    
    def __init__(
        self,
        config,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_speech_end: Optional[Callable[[], None]] = None,
        on_speech_active: Optional[Callable[[], None]] = None,
        event_bus = None
    ):
        """
        初始化 VAD 检测器

        Args:
            config: 配置对象
            on_speech_start: 语音开始回调函数
            on_speech_end: 语音结束回调函数
            on_speech_active: 持续语音活动回调函数（在检测到语音活动时持续调用）
            event_bus: 事件总线（用于发布 VAD 事件）
        """
        self.config = config
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.on_speech_active = on_speech_active
        self.event_bus = event_bus
        
        # 从配置获取参数
        self.sample_rate = config.get("audio.sample_rate", 16000)
        self.vad_mode = config.get("vad.mode", 3)  # VAD 模式 (0-3, 数字越大越敏感)
        self.check_interval = config.get("vad.check_interval", 0.5)  # VAD 检测间隔（秒）
        self.speech_threshold = config.get("vad.speech_threshold", 0.4)  # 语音活动阈值（比例）
        self.frame_duration_ms = config.get("vad.frame_duration_ms", 30)  # 每帧时长（毫秒）
        
        # 计算每帧的样本数（WebRTC VAD 仅支持 10, 20, 30 毫秒的帧）
        self.frame_size = int(self.sample_rate * self.frame_duration_ms / 1000)
        
        # 初始化 WebRTC VAD
        self.vad = None
        if webrtcvad is not None:
            try:
                self.vad = webrtcvad.Vad()
                self.vad.set_mode(self.vad_mode)
                log_info("VAD", f"WebRTC VAD 初始化完成 (模式: {self.vad_mode})", indent=0)
            except Exception as e:
                log_error("VAD", f"初始化 WebRTC VAD 失败: {e}", indent=0)
                self.vad = None
        else:
            log_warn("VAD", "webrtcvad 库不可用，VAD 检测将被禁用", indent=0)
        
        # 状态控制
        self.is_speaking = False  # 当前是否检测到语音活动
        self._previous_is_speaking = False  # 上一次检测的状态
        self.running = False  # 后台线程是否运行
        self.background_thread: Optional[threading.Thread] = None
        
        # 音频缓冲队列
        self.audio_queue: queue.Queue = queue.Queue(maxsize=1000)
        self.audio_buffer: list = []  # 累积的音频数据
        
        # 线程同步
        self._state_lock = threading.Lock()
    
    def feed_audio(self, audio_data: np.ndarray):
        """
        接收音频数据（由线程1调用）
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        try:
            # 非阻塞添加到队列
            self.audio_queue.put_nowait(audio_data)
        except queue.Full:
            # 队列满了，丢弃旧数据
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put_nowait(audio_data)
            except:
                pass
    
    def _detect_speech(self, audio_data: bytes) -> bool:
        """
        检测音频数据中是否有语音活动
        
        Args:
            audio_data: int16 格式的音频数据（bytes）
            
        Returns:
            True 表示检测到语音，False 表示静音
        """
        if self.vad is None:
            return False
        
        # 将音频数据分块检测
        num_speech_frames = 0
        total_frames = 0
        step = self.frame_size * 2  # int16 每样本 2 字节
        
        for i in range(0, len(audio_data) - step + 1, step):
            chunk = audio_data[i:i + step]
            if len(chunk) == step:
                total_frames += 1
                try:
                    if self.vad.is_speech(chunk, sample_rate=self.sample_rate):
                        num_speech_frames += 1
                except Exception as e:
                    log_debug("VAD", f"VAD 检测错误: {e}", indent=2)
                    continue
        
        # 根据阈值判断是否为语音
        if total_frames > 0:
            speech_ratio = num_speech_frames / total_frames
            return speech_ratio > self.speech_threshold
        
        return False
    
    def _convert_float32_to_int16(self, audio_float32: np.ndarray) -> bytes:
        """
        将 float32 格式的音频数据转换为 int16 格式的 bytes
        
        Args:
            audio_float32: float32 格式的音频数据
            
        Returns:
            int16 格式的音频数据（bytes）
        """
        # 确保数据在 [-1, 1] 范围内
        audio_clipped = np.clip(audio_float32, -1.0, 1.0)
        # 转换为 int16
        audio_int16 = (audio_clipped * 32767).astype(np.int16)
        return audio_int16.tobytes()
    
    def _background_thread_func(self):
        """后台检测线程函数（线程2）"""
        log_info("VAD", "VAD 后台线程启动", indent=0)
        
        last_check_time = time.time()
        
        while self.running:
            try:
                # 从队列获取音频数据（阻塞等待，最多等待 100ms）
                try:
                    audio_data = self.audio_queue.get(timeout=0.1)
                    self.audio_buffer.append(audio_data)
                except queue.Empty:
                    continue
                
                # 检查是否达到检测间隔
                current_time = time.time()
                if current_time - last_check_time >= self.check_interval:
                    last_check_time = current_time
                    
                    # 合并累积的音频数据
                    if len(self.audio_buffer) > 0:
                        combined_audio = np.concatenate(self.audio_buffer)
                        self.audio_buffer = []  # 清空缓冲区
                        
                        # 转换为 int16 格式
                        audio_bytes = self._convert_float32_to_int16(combined_audio)
                        
                        # 检测语音活动
                        current_speaking = self._detect_speech(audio_bytes)

                        # 更新状态并触发回调
                        with self._state_lock:
                            self._previous_is_speaking = self.is_speaking
                            self.is_speaking = current_speaking

                        # 状态变化时触发回调
                        if current_speaking and not self._previous_is_speaking:
                            # 从静音变为有语音
                            log_info("VAD", "检测到语音活动开始", indent=0)
                            self._publish_vad_event("speech_start")
                            if self.on_speech_start:
                                try:
                                    self.on_speech_start()
                                except Exception as e:
                                    log_error("VAD", f"语音开始回调错误: {e}", indent=1)

                        elif not current_speaking and self._previous_is_speaking:
                            # 从有语音变为静音
                            log_info("VAD", "检测到语音活动结束", indent=0)
                            self._publish_vad_event("speech_end")
                            if self.on_speech_end:
                                try:
                                    self.on_speech_end()
                                except Exception as e:
                                    log_error("VAD", f"语音结束回调错误: {e}", indent=1)

                        # 持续语音活动时触发回调（用于更新last_active_time）
                        elif current_speaking:
                            # 持续有语音
                            if self.on_speech_active:
                                try:
                                    self.on_speech_active()
                                except Exception as e:
                                    log_error("VAD", f"持续语音活动回调错误: {e}", indent=1)
            
            except Exception as e:
                log_error("VAD", f"后台线程错误: {e}", indent=0)
                time.sleep(0.1)  # 避免错误时紧密循环
        
        log_info("VAD", "VAD 后台线程结束", indent=0)
    
    def _publish_vad_event(self, event_type: str):
        """
        发布 VAD 事件到事件总线
        
        Args:
            event_type: 事件类型 ("speech_start" 或 "speech_end")
        """
        if self.event_bus is None:
            return
        
        try:
            from voice_chatbot.dialogue.events import EventType, create_event
            
            if event_type == "speech_start":
                event = create_event(
                    EventType.VAD_SPEECH_START,
                    data={},
                    source="VAD"
                )
            elif event_type == "speech_end":
                event = create_event(
                    EventType.VAD_SPEECH_END,
                    data={},
                    source="VAD"
                )
            else:
                return
            
            self.event_bus.publish(event)
        except Exception as e:
            log_debug("VAD", f"发布 VAD 事件失败: {e}", indent=1)
    
    def start_background_thread(self):
        """启动后台检测线程（线程2）"""
        if self.running:
            log_warn("VAD", "后台线程已在运行", indent=0)
            return
        
        self.running = True
        self.background_thread = threading.Thread(
            target=self._background_thread_func,
            daemon=True,
            name="VAD-Background"
        )
        self.background_thread.start()
        log_info("VAD", "后台线程已启动", indent=0)
    
    def stop_background_thread(self):
        """停止后台检测线程"""
        if not self.running:
            return
        
        self.running = False
        
        if self.background_thread and self.background_thread.is_alive():
            self.background_thread.join(timeout=2.0)
        
        # 清空队列
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        
        self.audio_buffer = []
        log_info("VAD", "后台线程已停止", indent=0)
    
    def reset_state(self):
        """重置 VAD 状态"""
        with self._state_lock:
            self.is_speaking = False
            self._previous_is_speaking = False
        
        # 清空缓冲区
        self.audio_buffer = []
        
        # 清空队列
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        
        log_debug("VAD", "VAD 状态已重置", indent=0)
    
    def get_is_speaking(self) -> bool:
        """
        获取当前是否检测到语音活动
        
        Returns:
            True 表示有语音活动，False 表示静音
        """
        with self._state_lock:
            return self.is_speaking



