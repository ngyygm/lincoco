"""
性能监控模块

通过事件系统统一管理所有操作的开始/结束信号，实现统一的时间记录和log输出
使用事件时间轴精确计算各阶段耗时，输出更详细的响应耗时分析
"""

import sys
import time
import threading
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from voice_chatbot.dialogue.events import Event, EventType, EventFilter
from voice_chatbot.utils.logger import log_error, console_exclusive


# 启用Windows虚拟终端支持（用于ANSI转义码显示颜色）
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except:
        pass


@dataclass
class PerformanceData:
    """性能数据存储"""
    task_id: Optional[int] = None
    
    # VAD时间点
    vad_speech_start_time: float = 0
    vad_speech_end_time: float = 0
    
    # ASR时间点
    asr_recognition_start_time: float = 0
    asr_model_inference_time: float = 0
    asr_final_result_time: float = 0
    asr_recognition_end_time: float = 0
    
    # LLM时间点
    llm_generation_start_time: float = 0
    llm_request_sent_time: float = 0
    llm_stream_start_time: float = 0
    llm_first_sentence_time: float = 0
    llm_generation_complete_time: float = 0
    
    # TTS时间点
    tts_generation_start_time: float = 0
    tts_first_audio_ready_time: float = 0
    tts_generation_time: float = 0  # TTS纯生成时间（秒）
    tts_processing_time: float = 0  # TTS处理时间（秒）
    tts_cache_hit: bool = False
    
    # 播放时间点
    audio_play_start_time: float = 0
    audio_play_end_time: float = 0
    
    def reset(self):
        """重置所有时间点"""
        self.task_id = None
        self.vad_speech_start_time = 0
        self.vad_speech_end_time = 0
        self.asr_recognition_start_time = 0
        self.asr_model_inference_time = 0
        self.asr_final_result_time = 0
        self.asr_recognition_end_time = 0
        self.llm_generation_start_time = 0
        self.llm_request_sent_time = 0
        self.llm_stream_start_time = 0
        self.llm_first_sentence_time = 0
        self.llm_generation_complete_time = 0
        self.tts_generation_start_time = 0
        self.tts_first_audio_ready_time = 0
        self.tts_generation_time = 0
        self.tts_processing_time = 0
        self.tts_cache_hit = False
        self.audio_play_start_time = 0
        self.audio_play_end_time = 0


class PerformanceMonitor:
    """
    性能监控器 - 通过事件系统统一管理时间记录和log输出
    
    使用事件时间轴精确计算各阶段耗时，输出更详细的响应耗时分析
    """
    
    def __init__(self, enable_output: bool = True):
        """
        初始化性能监控器
        
        Args:
            enable_output: 是否启用耗时分析输出
        """
        self.enable_output = enable_output
        
        # 性能数据
        self.perf_data = PerformanceData()
        
        # 事件时间轴（用于调试和详细分析）
        self.event_timeline: List[Event] = []
        
        # 线程锁
        self._lock = threading.Lock()
        
        # 是否已输出本轮分析
        self._analysis_output = False
    
    def handle_event(self, event: Event):
        """
        处理事件（事件监听器回调函数）
        
        使用事件自带的时间戳进行精确计时
        
        Args:
            event: 事件对象
        """
        with self._lock:
            try:
                # 记录到事件时间轴
                self.event_timeline.append(event)
                
                # 获取任务ID
                task_id = event.data.get("task_id")
                if task_id is not None:
                    self.perf_data.task_id = task_id
                
                # 使用事件时间戳记录各阶段时间
                timestamp = event.timestamp
                
                # 通过 event_type.value 比较字符串值，兼容新旧架构的事件类型
                event_type_value = event.event_type.value if hasattr(event.event_type, 'value') else str(event.event_type)
                
                if event_type_value == "vad_speech_start":
                    self._handle_vad_speech_start(timestamp)
                elif event_type_value == "vad_speech_end":
                    self._handle_vad_speech_end(timestamp)
                # 支持新旧架构的事件类型
                elif event_type_value in ("asr_start", "asr_recognition_start"):
                    self._handle_asr_recognition_start(timestamp)
                elif event_type_value == "asr_model_inference":
                    self._handle_asr_model_inference(timestamp)
                elif event_type_value == "asr_final_result":
                    self._handle_asr_final_result(timestamp)
                elif event_type_value in ("asr_end", "asr_recognition_end"):
                    self._handle_asr_recognition_end(timestamp)
                elif event_type_value in ("llm_start", "llm_generation_start"):
                    self._handle_llm_generation_start(timestamp)
                elif event_type_value == "llm_request_sent":
                    self._handle_llm_request_sent(timestamp)
                elif event_type_value == "llm_stream_start":
                    self._handle_llm_stream_start(timestamp)
                elif event_type_value == "llm_first_sentence":
                    self._handle_llm_first_sentence(timestamp)
                elif event_type_value in ("llm_complete", "llm_generation_complete"):
                    self._handle_llm_generation_complete(timestamp)
                elif event_type_value in ("tts_start", "tts_generation_start"):
                    self._handle_tts_generation_start(timestamp)
                elif event_type_value == "tts_cache_hit":
                    self._handle_tts_cache_hit(timestamp)
                elif event_type_value in ("tts_first_audio", "tts_first_audio_ready"):
                    self._handle_tts_first_audio_ready(timestamp, event.data)
                elif event_type_value in ("tts_complete", "tts_generation_complete"):
                    self._handle_tts_generation_complete(timestamp)
                elif event_type_value == "audio_play_start":
                    self._handle_audio_play_start(timestamp)
                elif event_type_value == "audio_play_end":
                    self._handle_audio_play_end(timestamp)
                    
            except Exception as e:
                log_error("TIME", f"处理性能事件错误: {e}")
    
    def _handle_vad_speech_start(self, timestamp: float):
        """处理VAD语音开始事件"""
        # 新的对话轮次开始，重置性能数据
        self.perf_data.reset()
        self.event_timeline.clear()
        self._analysis_output = False
        self.perf_data.vad_speech_start_time = timestamp
    
    def _handle_vad_speech_end(self, timestamp: float):
        """处理VAD语音结束事件"""
        self.perf_data.vad_speech_end_time = timestamp
    
    def _handle_asr_recognition_start(self, timestamp: float):
        """处理ASR识别开始事件"""
        self.perf_data.asr_recognition_start_time = timestamp
    
    def _handle_asr_model_inference(self, timestamp: float):
        """处理ASR模型推理事件"""
        self.perf_data.asr_model_inference_time = timestamp
    
    def _handle_asr_final_result(self, timestamp: float):
        """处理ASR最终结果事件"""
        self.perf_data.asr_final_result_time = timestamp
    
    def _handle_asr_recognition_end(self, timestamp: float):
        """处理ASR识别结束事件"""
        self.perf_data.asr_recognition_end_time = timestamp
    
    def _handle_llm_generation_start(self, timestamp: float):
        """处理LLM生成开始事件"""
        self.perf_data.llm_generation_start_time = timestamp
    
    def _handle_llm_request_sent(self, timestamp: float):
        """处理LLM请求发送事件"""
        self.perf_data.llm_request_sent_time = timestamp
    
    def _handle_llm_stream_start(self, timestamp: float):
        """处理LLM流开始事件"""
        self.perf_data.llm_stream_start_time = timestamp
    
    def _handle_llm_first_sentence(self, timestamp: float):
        """处理LLM第一句事件"""
        if self.perf_data.llm_first_sentence_time == 0:
            self.perf_data.llm_first_sentence_time = timestamp
    
    def _handle_llm_generation_complete(self, timestamp: float):
        """处理LLM生成完成事件"""
        self.perf_data.llm_generation_complete_time = timestamp
    
    def _handle_tts_generation_start(self, timestamp: float):
        """处理TTS生成开始事件"""
        if self.perf_data.tts_generation_start_time == 0:
            self.perf_data.tts_generation_start_time = timestamp
    
    def _handle_tts_cache_hit(self, timestamp: float):
        """处理TTS缓存命中事件"""
        self.perf_data.tts_cache_hit = True
    
    def _handle_tts_first_audio_ready(self, timestamp: float, data: Dict[str, Any]):
        """处理TTS第一个音频就绪事件"""
        if self.perf_data.tts_first_audio_ready_time == 0:
            self.perf_data.tts_first_audio_ready_time = timestamp
        
        # 记录TTS生成和处理时间
        generation_time = data.get("generation_time", 0)
        processing_time = data.get("processing_time", 0)
        if generation_time > 0:
            self.perf_data.tts_generation_time = generation_time
        if processing_time > 0:
            self.perf_data.tts_processing_time = processing_time
    
    def _handle_tts_generation_complete(self, timestamp: float):
        """处理TTS生成完成事件"""
        pass  # 目前不需要特殊处理
    
    def _handle_audio_play_start(self, timestamp: float):
        """处理音频播放开始事件"""
        if self.perf_data.audio_play_start_time == 0:
            self.perf_data.audio_play_start_time = timestamp
            # 输出响应耗时分析
            if not self._analysis_output:
                self.calculate_and_output()
                self._analysis_output = True
    
    def _handle_audio_play_end(self, timestamp: float):
        """处理音频播放结束事件"""
        self.perf_data.audio_play_end_time = timestamp
    
    def calculate_and_output(self):
        """
        计算并输出响应耗时分析

        整块输出独占控制台（先闭合活动流式行），避免与日志/流式刷新交错

        使用树形结构展示6个关键时间节点和阶段耗时：
        T0: VAD语音结束
        T1: ASR结果就绪
        T2: LLM线程开始
        T3: LLM第一句完成
        T4: TTS音频就绪
        T5: 开始播放
        """
        with console_exclusive():
            self._calculate_and_output_impl()

    def _calculate_and_output_impl(self):
        """响应耗时分析的实际计算与输出实现"""
        if not self.enable_output:
            return
        
        perf = self.perf_data
        
        # ========== 确定关键时间节点 ==========
        
        # T0: VAD语音结束（基准时间）
        t0 = perf.vad_speech_end_time
        if t0 == 0:
            return
        
        # T5: 开始播放（终点时间）
        t5 = perf.audio_play_start_time
        if t5 == 0:
            return
        
        # 计算总响应耗时
        total_latency = t5 - t0
        if total_latency <= 0:
            return
        
        # T1: ASR结果就绪
        t1 = perf.asr_final_result_time
        if t1 == 0:
            t1 = perf.asr_recognition_end_time
        
        # T2: LLM线程开始
        t2 = perf.llm_generation_start_time
        
        # T3: LLM第一句完成
        t3 = perf.llm_first_sentence_time
        
        # T4: TTS音频就绪
        t4 = perf.tts_first_audio_ready_time
        
        # ========== 计算阶段耗时 ==========
        
        # 阶段1: ASR阶段 (T0 → T1)
        asr_stage_time = (t1 - t0) if t1 > t0 else 0
        
        # 阶段2: 调度阶段 (T1 → T2)
        schedule_stage_time = (t2 - t1) if (t2 > 0 and t1 > 0 and t2 > t1) else 0
        
        # 阶段3: LLM首句生成 (T2 → T3)
        llm_stage_time = (t3 - t2) if (t3 > 0 and t2 > 0 and t3 > t2) else 0
        
        # 阶段4: TTS首句生成 (T3 → T4)
        tts_stage_time = (t4 - t3) if (t4 > 0 and t3 > 0 and t4 > t3) else 0
        
        # 阶段5: 播放启动 (T4 → T5)
        play_start_time = (t5 - t4) if (t5 > 0 and t4 > 0 and t5 > t4) else 0
        
        # ========== 计算内部细节耗时 ==========
        
        # ASR内部: 模型推理时间
        asr_inference_time = 0
        if perf.asr_model_inference_time > 0 and perf.asr_recognition_start_time > 0:
            asr_inference_time = perf.asr_model_inference_time - perf.asr_recognition_start_time
        asr_result_process_time = max(0, asr_stage_time - asr_inference_time) if asr_inference_time > 0 else 0
        
        # LLM内部: API请求发送、等待首token、生成首句
        llm_request_send_time = 0
        llm_wait_first_token_time = 0
        llm_first_sentence_gen_time = 0
        
        if t2 > 0 and perf.llm_request_sent_time > 0:
            llm_request_send_time = perf.llm_request_sent_time - t2
        
        if perf.llm_request_sent_time > 0 and perf.llm_stream_start_time > 0:
            llm_wait_first_token_time = perf.llm_stream_start_time - perf.llm_request_sent_time
        
        if perf.llm_stream_start_time > 0 and t3 > 0:
            llm_first_sentence_gen_time = t3 - perf.llm_stream_start_time
        
        # TTS内部: 生成时间、处理时间
        tts_gen_time = perf.tts_generation_time
        tts_proc_time = perf.tts_processing_time
        
        # ========== 辅助函数 ==========
        
        def ms(seconds: float) -> int:
            """转换为毫秒"""
            return int(seconds * 1000)
        
        def relative_ms(timestamp: float) -> int:
            """相对于T0的毫秒数"""
            return ms(timestamp - t0) if timestamp > 0 else 0
        
        def format_stage(name: str, duration: float, width: int = 38) -> str:
            """格式化阶段名称和耗时"""
            duration_str = f"{ms(duration)}ms"
            padding = width - len(name) - len(duration_str)
            return f"{name} {'─' * max(1, padding)} {duration_str}"
        
        # ========== 输出 ==========
        
        # ANSI颜色
        BOLD = "\033[1m"
        DIM = "\033[2m"
        RED = "\033[31m"
        GREEN = "\033[32m"
        YELLOW = "\033[33m"
        BLUE = "\033[34m"
        MAGENTA = "\033[35m"
        CYAN = "\033[36m"
        WHITE = "\033[37m"
        RESET = "\033[0m"
        
        line = "=" * 72
        
        print(f"\n{RED}{line}{RESET}")
        print(f"{RED}{BOLD}[响应耗时分析]{RESET} 总耗时: {YELLOW}{ms(total_latency)}ms{RESET} (VAD语音结束 → 开始播放)")
        print(f"{RED}{line}{RESET}")
        
        # T0: VAD语音结束
        print(f"\n{WHITE}[T0] VAD语音结束{RESET}                              {DIM}0ms{RESET}")
        
        # ASR阶段
        if asr_stage_time > 0:
            stage_label = format_stage("  ├── ASR阶段", asr_stage_time)
            print(f"{BLUE}{stage_label}{RESET}")
            if asr_inference_time > 0:
                print(f"{BLUE}  │     ├─ 模型推理: {ms(asr_inference_time)}ms{RESET}")
            if asr_result_process_time > 0:
                print(f"{BLUE}  │     └─ 结果处理: {ms(asr_result_process_time)}ms (最终结果){RESET}")
        
        # T1: ASR结果就绪
        t1_label = "ASR最终结果"
        print(f"{WHITE}[T1] {t1_label}{RESET}                          {DIM}{relative_ms(t1)}ms{RESET}")
        
        # 调度阶段
        if schedule_stage_time > 0:
            stage_label = format_stage("  ├── 调度阶段", schedule_stage_time)
            print(f"{DIM}{stage_label}{RESET}")
        
        # T2: LLM线程开始
        if t2 > 0:
            print(f"{WHITE}[T2] LLM线程开始{RESET}                            {DIM}{relative_ms(t2)}ms{RESET}")
        
        # LLM首句生成阶段
        if llm_stage_time > 0:
            stage_label = format_stage("  ├── LLM首句生成", llm_stage_time)
            print(f"{GREEN}{stage_label}{RESET}")
            if llm_request_send_time > 0:
                print(f"{GREEN}  │     ├─ API请求发送: {ms(llm_request_send_time)}ms{RESET}")
            if llm_wait_first_token_time > 0:
                print(f"{GREEN}  │     ├─ 等待首个token: {ms(llm_wait_first_token_time)}ms{RESET}")
            if llm_first_sentence_gen_time > 0:
                print(f"{GREEN}  │     └─ 生成第一句: {ms(llm_first_sentence_gen_time)}ms{RESET}")
        
        # T3: LLM第一句完成
        if t3 > 0:
            print(f"{WHITE}[T3] LLM第一句完成{RESET}                          {DIM}{relative_ms(t3)}ms{RESET}")
        
        # TTS首句生成阶段
        if tts_stage_time > 0:
            stage_label = format_stage("  ├── TTS首句生成", tts_stage_time)
            print(f"{MAGENTA}{stage_label}{RESET}")
            if perf.tts_cache_hit:
                print(f"{MAGENTA}  │     └─ 缓存命中: {ms(tts_gen_time)}ms{RESET}")
            else:
                if tts_gen_time > 0:
                    print(f"{MAGENTA}  │     ├─ edge_tts生成: {ms(tts_gen_time)}ms{RESET}")
                if tts_proc_time > 0:
                    print(f"{MAGENTA}  │     └─ 静音裁剪: {ms(tts_proc_time)}ms{RESET}")
        
        # T4: TTS音频就绪
        if t4 > 0:
            print(f"{WHITE}[T4] TTS音频就绪{RESET}                           {DIM}{relative_ms(t4)}ms{RESET}")
        
        # 播放启动阶段
        if play_start_time > 0:
            stage_label = format_stage("  ├── 播放启动", play_start_time)
            print(f"{CYAN}{stage_label}{RESET}")
        
        # T5: 开始播放
        print(f"{WHITE}[T5] 开始播放{RESET}                              {YELLOW}{BOLD}{relative_ms(t5)}ms{RESET}")
        
        print(f"\n{RED}{line}{RESET}\n")
    
    def get_event_filter(self) -> EventFilter:
        """
        获取事件过滤器（用于订阅相关事件）
        
        Returns:
            事件过滤器
        """
        event_types = [
            EventType.VAD_SPEECH_START,
            EventType.VAD_SPEECH_END,
            EventType.ASR_MODEL_INFERENCE,
            EventType.ASR_FINAL_RESULT,
            EventType.LLM_REQUEST_SENT,
            EventType.LLM_STREAM_START,
            EventType.LLM_FIRST_SENTENCE,
            EventType.TTS_CACHE_HIT,
            EventType.AUDIO_PLAY_START,
            EventType.AUDIO_PLAY_END,
        ]
        
        # 添加旧架构事件类型（如果存在）
        if hasattr(EventType, 'ASR_RECOGNITION_START'):
            event_types.extend([
                EventType.ASR_RECOGNITION_START,
                EventType.ASR_RECOGNITION_END,
                EventType.LLM_GENERATION_START,
                EventType.LLM_GENERATION_COMPLETE,
                EventType.TTS_GENERATION_START,
                EventType.TTS_FIRST_AUDIO_READY,
                EventType.TTS_GENERATION_COMPLETE,
            ])
        
        # 添加新架构事件类型（如果存在）
        if hasattr(EventType, 'ASR_START'):
            event_types.extend([
                EventType.ASR_START,
                EventType.ASR_END,
                EventType.LLM_START,
                EventType.LLM_COMPLETE,
                EventType.TTS_START,
                EventType.TTS_FIRST_AUDIO,
                EventType.TTS_COMPLETE,
            ])
        
        return EventFilter(event_types=event_types)
    
    def get_timeline(self) -> List[Event]:
        """获取事件时间轴"""
        with self._lock:
            return self.event_timeline.copy()
    
    def get_perf_data(self) -> PerformanceData:
        """获取性能数据"""
        with self._lock:
            return self.perf_data
