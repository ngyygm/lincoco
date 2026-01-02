"""
核心调度器模块（v3.0 架构）

DialogueManager 是语音对话系统的核心调度器，负责协调所有模块和线程的工作。
参考：THREAD_SCHEDULING.md, PROJECT_OUTLINE.md
"""

import threading
import time
import queue
import traceback
from typing import Optional, Callable, Dict, Any, List
from enum import Enum

import numpy as np

from voice_chatbot.dialogue.state import MainSystemState, SubSystemState
from voice_chatbot.dialogue.state_manager import StateManager
from voice_chatbot.dialogue.context_manager import ContextManager
from voice_chatbot.dialogue.thread_manager import ThreadManager
from voice_chatbot.dialogue.task_context import TaskContext, TaskCancelledException
from voice_chatbot.dialogue.events import EventType, EventBus, EventFilter, create_event
from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.utils.timer import PerformanceTimer
from voice_chatbot.tts.audio_player import AudioPlayer, PlaybackStatus


class TraceableLock:
    """可追踪的锁，用于调试死锁问题
    
    记录锁的获取和释放，包括：
    - 哪个线程持有锁
    - 获取锁时的调用栈
    - 哪个线程在等待锁
    """
    def __init__(self, name: str = "Lock"):
        self._lock = threading.RLock()
        self._name = name
        self._owner_thread_id: Optional[int] = None
        self._owner_thread_name: Optional[str] = None
        self._acquire_time: Optional[float] = None
        self._acquire_stack: Optional[str] = None
        self._waiting_threads: Dict[int, float] = {}  # thread_id -> wait_start_time
        self._lock_info = threading.Lock()  # 保护追踪信息的锁
        
    def acquire(self, blocking: bool = True, timeout: float = -1):
        """获取锁"""
        thread_id = threading.get_ident()
        thread_name = threading.current_thread().name
        
        # 记录等待开始时间
        if blocking:
            with self._lock_info:
                if self._owner_thread_id is not None:
                    self._waiting_threads[thread_id] = time.time()
                    log_warn("MANAGER", f"[LOCK-TRACE] 线程 {thread_name} (ID:{thread_id}) 正在等待 {self._name}，当前持有者: 线程 {self._owner_thread_name} (ID:{self._owner_thread_id})，持有时间: {time.time() - self._acquire_time:.2f}秒", indent=0)
                    if self._acquire_stack:
                        log_warn("MANAGER", f"[LOCK-TRACE] 持有者获取锁时的调用栈:\n{self._acquire_stack}", indent=1)
        
        try:
            if timeout < 0:
                result = self._lock.acquire(blocking=blocking)
            else:
                result = self._lock.acquire(blocking=blocking, timeout=timeout)
            
            if result:
                # 成功获取锁
                current_stack = ''.join(traceback.format_stack()[:-1])  # 去掉当前帧
                with self._lock_info:
                    self._owner_thread_id = thread_id
                    self._owner_thread_name = thread_name
                    self._acquire_time = time.time()
                    self._acquire_stack = current_stack
                    if thread_id in self._waiting_threads:
                        wait_duration = time.time() - self._waiting_threads[thread_id]
                        log_info("MANAGER", f"[LOCK-TRACE] 线程 {thread_name} (ID:{thread_id}) 成功获取 {self._name}，等待时间: {wait_duration:.2f}秒", indent=0)
                        del self._waiting_threads[thread_id]
                    else:
                        pass
                        # log_info("MANAGER", f"[LOCK-TRACE] 线程 {thread_name} (ID:{thread_id}) 获取 {self._name}", indent=0)
            
            return result
            
        except Exception as e:
            # 获取失败，从等待列表中移除
            with self._lock_info:
                if thread_id in self._waiting_threads:
                    del self._waiting_threads[thread_id]
            raise
    
    def release(self):
        """释放锁"""
        thread_id = threading.get_ident()
        thread_name = threading.current_thread().name
        
        with self._lock_info:
            if self._owner_thread_id != thread_id:
                log_error("MANAGER", f"[LOCK-TRACE] 警告：线程 {thread_name} (ID:{thread_id}) 尝试释放 {self._name}，但锁的持有者是线程 {self._owner_thread_name} (ID:{self._owner_thread_id})", indent=0)
            
            if self._acquire_time:
                hold_duration = time.time() - self._acquire_time
                if hold_duration > 0.1:  # 持有时间超过100ms才记录
                    log_info("MANAGER", f"[LOCK-TRACE] 线程 {thread_name} (ID:{thread_id}) 释放 {self._name}，持有时间: {hold_duration:.2f}秒", indent=0)
            
            self._owner_thread_id = None
            self._owner_thread_name = None
            self._acquire_time = None
            self._acquire_stack = None
        
        self._lock.release()
    
    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
    
    def get_owner_info(self) -> Optional[Dict[str, Any]]:
        """获取当前持有锁的线程信息（用于调试）"""
        with self._lock_info:
            if self._owner_thread_id is None:
                return None
            return {
                "thread_id": self._owner_thread_id,
                "thread_name": self._owner_thread_name,
                "acquire_time": self._acquire_time,
                "hold_duration": time.time() - self._acquire_time if self._acquire_time else 0,
                "stack": self._acquire_stack,
                "waiting_count": len(self._waiting_threads)
            }


class DialogueManager:
    """核心调度器类（v3.0 架构）
    
    负责协调所有模块和线程的工作，包括：
    - 模块初始化与协调
    - 线程生命周期管理（通过 ThreadManager）
    - 状态管理（通过 StateManager）
    - 任务上下文管理（通过 ContextManager）
    - 事件驱动的日志输出
    """
    
    def __init__(self, config):
        """
        初始化核心调度器
        
        Args:
            config: 配置对象（Config实例）
        """
        self.config = config
        
        # 初始化事件总线（先创建，后续模块需要）
        self.event_bus = EventBus()
        
        # 初始化核心管理器组件（v3.0 新架构）
        self.state_manager = StateManager(event_bus=self.event_bus)
        self.context_manager = ContextManager(event_bus=self.event_bus)
        self.thread_manager = ThreadManager(event_bus=self.event_bus)
        
        # 从配置获取关键参数
        self.no_speech_threshold = config.get("dialogue.no_speech_threshold", 1.2)
        # 注意：文档中默认值为0.3秒，但代码中使用0.5秒作为更保守的默认值（减少误判）
        # 可通过配置文件dialogue.audio_stop_delay覆盖默认值
        self.audio_stop_delay = config.get("dialogue.audio_stop_delay", 0.5)
        self.min_play_duration_to_record = config.get("dialogue.min_play_duration_to_record", 0.5)
        self.invalid_recognition_words = config.get("dialogue.invalid_recognition_words", ["", "i", "嗯", "啊", "呃", "哦", "额"])
        self.min_audio_length = config.get("dialogue.min_audio_length", 0.5)
        self.interrupt_check_interval = config.get("dialogue.interrupt_check_interval", 0.1)
        self.min_silent_duration = config.get("dialogue.min_silent_duration", 0.8)
        self.sentence_queue_maxsize = config.get("dialogue.sentence_queue_maxsize", 100)
        self.audio_play_queue_maxsize = config.get("dialogue.audio_play_queue_maxsize", 50)
        self.llm_timeout = config.get("dialogue.llm_timeout", 300.0)
        self.tts_timeout = config.get("dialogue.tts_timeout", 5.0)
        self.player_timeout = config.get("dialogue.player_timeout", 10.0)
        self.thread_timeout = config.get("dialogue.thread_timeout", 1.0)
        self.max_history_length = config.get("dialogue.max_history_length", 50)  # 最大历史记录长度（对话轮数）
        self.enable_latency_analysis = config.get("dialogue.enable_latency_analysis", False)  # 是否启用响应耗时详细分析
        
        # 初始化性能计时器
        self.performance_timer = PerformanceTimer(enable_output=self.enable_latency_analysis)
        
        # 模块实例（将在 initialize_modules 中初始化）
        self.microphone: Optional[Any] = None
        self.vad: Optional[Any] = None
        self.asr: Optional[Any] = None
        self.llm: Optional[Any] = None
        self.tts_engine: Optional[Any] = None
        self.audio_player: Optional[Any] = None
        self.persona_manager: Optional[Any] = None
        
        # 状态变量
        self.running = False
        self.pending_interrupt = False  # 用户打断标记
        self.last_active_time = 0.0  # 最后活动时间（用于NO_SPEECH_THRESHOLD检测）
        self.asr_thread_active = False  # ASR线程是否活跃
        
        # 任务相关变量
        self.conversation_history: List[Dict[str, str]] = []
        self.history_lock = threading.Lock()
        
        # 打断相关变量
        self.interrupt_lock = TraceableLock("interrupt_lock")
        self.pending_interrupt = False
        self.interrupt_timer: Optional[threading.Timer] = None
        
        # 线程启动标志（用于LLM+TTS流程）
        self.thread_started_lock = threading.Lock()  # 保护线程启动标志的锁
        self.tts_thread_started = False
        self.player_thread_started = False
        
        # 延迟验证相关
        self.voice_check_timer: Optional[threading.Timer] = None
        self.paused_sub_state: Optional[SubSystemState] = None  # 暂停前的副系统状态（用于场景判断）
        
        # ASR线程管理
        self.asr_thread_lock = threading.Lock()
        self.asr_task_id = 0  # ASR任务ID（独立于主任务ID）
        
        # 快速连续输入相关
        self.accumulated_input = ""  # 累积的用户输入（支持多次合并）
        self.voice_activity_start_time = 0.0  # 新语音活动检测到的时间点（用于判断"什么都没说过"）
        self.nothing_said_yet = False  # 标记当前任务是否被判定为"什么都没说过"
        
        
        log_info("MANAGER", "核心调度器初始化完成", indent=0)
    
    def _limit_history_length(self):
        """
        限制历史记录长度，删除最旧的记录以保持不超过最大长度
        注意：此方法应在持有 history_lock 的情况下调用
        """
        # max_history_length是对话轮数，每轮包含用户和助手两条消息
        max_messages = self.max_history_length * 2
        if len(self.conversation_history) > max_messages:
            # 删除最旧的记录，保持历史记录长度不超过限制
            removed_count = len(self.conversation_history) - max_messages
            self.conversation_history = self.conversation_history[removed_count:]
            log_debug("MANAGER", f"历史记录超过最大长度({self.max_history_length}轮)，已删除最旧的{removed_count}条记录，当前历史记录数: {len(self.conversation_history)}", indent=1)
    
    def _get_pending_interrupt_safe(self) -> bool:
        """
        线程安全地获取 pending_interrupt 标志
        
        Returns:
            pending_interrupt 的当前值
        """
        with self.interrupt_lock:
            return self.pending_interrupt
    
    def _get_player_thread_started_safe(self) -> bool:
        """
        线程安全地获取 player_thread_started 标志
        
        Returns:
            player_thread_started 的当前值
        """
        with self.thread_started_lock:
            return self.player_thread_started
    
    def _set_player_thread_started_safe(self, value: bool):
        """
        线程安全地设置 player_thread_started 标志
        
        Args:
            value: 要设置的值
        """
        with self.thread_started_lock:
            old_value = self.player_thread_started
            self.player_thread_started = value
            log_debug("MANAGER", f"player_thread_started: {old_value} -> {value}", indent=2)
    
    def _log_state_transition(self, transition_type: str, from_state: str, to_state: str, reason: str, caller: str):
        """
        记录状态转换的详细日志
        
        Args:
            transition_type: 转换类型 ("main", "sub", "both")
            from_state: 原状态
            to_state: 目标状态
            reason: 转换原因
            caller: 调用者函数名
        """
        import inspect
        frame = inspect.currentframe().f_back
        caller_info = f"{caller}()"
        if frame and frame.f_back:
            caller_info = f"{frame.f_back.f_code.co_name}() -> {caller_info}"
        
        log_info("MANAGER", f"[STATE_TRANS-{transition_type.upper()}] {from_state} -> {to_state} | 原因: {reason} | 调用者: {caller_info}", indent=1)
    
    def initialize_modules(self):
        """
        初始化所有模块
        
        包括：
        - EventBus 事件总线处理线程（线程3）
        - MicrophoneRecorder（音频采集）
        - WebRTCVAD（VAD检测）
        - FunASRStream（ASR识别）
        - LLM（OpenAI流式生成）
        - TTS引擎（EdgeTTS或CosyVoice3）
        - AudioPlayer（音频播放）
        - PersonaManager（人设管理）
        """
        log_info("MANAGER", "开始初始化模块...", indent=0)
        
        # 启动事件总线处理线程（线程3）
        self.event_bus.start_processing()
        log_info("MANAGER", "事件总线处理线程已启动", indent=1)
        
        # 初始化音频采集模块
        from voice_chatbot.audio.microphone import MicrophoneRecorder
        self.microphone = MicrophoneRecorder(
            config=self.config,
            on_audio_data_callback=self._on_audio_data
        )
        log_info("MANAGER", "音频采集模块初始化完成", indent=1)
        
        # 初始化VAD模块
        from voice_chatbot.audio.webrtc_vad import WebRTCVAD
        self.vad = WebRTCVAD(
            config=self.config,
            on_speech_start=self._on_vad_speech_start,
            on_speech_end=self._on_vad_speech_end,
            on_speech_active=self._on_vad_speech_active,
            event_bus=self.event_bus
        )
        log_info("MANAGER", "VAD模块初始化完成", indent=1)
        
        # 初始化ASR模块
        from voice_chatbot.audio.fun_asr_stream import FunASRStream, CircularAudioBuffer
        pre_buffer = CircularAudioBuffer(
            self.config.get("asr.pre_buffer_seconds", 0.5),
            self.config.get("audio.sample_rate", 16000)
        )
        self.asr = FunASRStream(
            config=self.config,
            pre_buffer=pre_buffer,
            on_partial_result=self._on_asr_partial_result,
            on_final_result=self._on_asr_final_result,
            event_bus=self.event_bus
        )
        log_info("MANAGER", "ASR模块初始化完成", indent=1)
        
        # 初始化人设管理器（在LLM之前，以便传递给LLM模块）
        from voice_chatbot.llm.persona_manager import PersonaManager
        self.persona_manager = PersonaManager(config=self.config)
        log_info("MANAGER", "人设管理器初始化完成", indent=1)
        
        # 初始化LLM模块
        # 注意：LLM模块需要task_lock，但在v3.0架构中我们使用TaskContext
        # 为了兼容，创建一个锁，但实际检查任务取消时使用get_current_task_id
        from voice_chatbot.llm.llm_openai_stream import LLMStreamGenerator
        import threading
        llm_task_lock = threading.Lock()
        self.llm = LLMStreamGenerator(
            config=self.config,
            conversation_history=self.conversation_history,
            task_lock=llm_task_lock,  # LLM模块内部需要锁
            get_current_task_id=lambda: self.context_manager.get_current_task_id(),
            memory_manager=None,  # TODO: 可选
            rag_manager=None,  # TODO: 可选
            persona_manager=self.persona_manager  # 使用共享的PersonaManager实例
        )
        log_info("MANAGER", "LLM模块初始化完成", indent=1)
        
        # 初始化TTS引擎（根据配置选择）
        tts_engine_type = self.config.get("tts.default_engine", "edgetts")
        # 兼容旧的配置值（edge_tts -> edgetts）
        if tts_engine_type == "edge_tts":
            tts_engine_type = "edgetts"
        
        # 规范化引擎类型名称（用于配置读取）
        engine_config_key = tts_engine_type
        if tts_engine_type in ("melotts", "melo_tts"):
            engine_config_key = "melo_tts"
        elif tts_engine_type == "edgetts":
            engine_config_key = "edge_tts"
        
        # 保存引擎类型（用于后续判断流式/非流式）
        self.tts_engine_type = engine_config_key
        
        # 处理 MeloTTS 引擎
        if tts_engine_type in ("melotts", "melo_tts"):
            try:
                from voice_chatbot.tts import MeloTTSEngine, HAS_MELOTTS
                if HAS_MELOTTS:
                    self.tts_engine = MeloTTSEngine(config=self.config, event_bus=self.event_bus)
                    log_info("MANAGER", f"TTS引擎初始化完成 ({tts_engine_type})", indent=1)
                else:
                    log_warn("MANAGER", "MeloTTS模块未安装，使用EdgeTTS", indent=1)
                    from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                    self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
                    self.tts_engine_type = "edge_tts"
                    log_info("MANAGER", "TTS引擎初始化完成 (edge_tts)", indent=1)
            except ImportError:
                log_warn("MANAGER", "MeloTTS模块导入失败，使用EdgeTTS", indent=1)
                from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
                self.tts_engine_type = "edge_tts"
                log_info("MANAGER", "TTS引擎初始化完成 (edge_tts)", indent=1)
        # 处理 CosyVoice3 引擎
        elif tts_engine_type == "cosyvoice3":
            try:
                from voice_chatbot.tts import CosyVoice3Engine, HAS_COSYVOICE3
                if HAS_COSYVOICE3:
                    self.tts_engine = CosyVoice3Engine(config=self.config, event_bus=self.event_bus)
                    log_info("MANAGER", f"TTS引擎初始化完成 ({tts_engine_type})", indent=1)
                else:
                    log_warn("MANAGER", "CosyVoice3模块未安装，使用EdgeTTS", indent=1)
                    from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                    self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
                    self.tts_engine_type = "edge_tts"
                    log_info("MANAGER", "TTS引擎初始化完成 (edge_tts)", indent=1)
            except ImportError:
                log_warn("MANAGER", "CosyVoice3模块导入失败，使用EdgeTTS", indent=1)
                from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
                self.tts_engine_type = "edge_tts"
                log_info("MANAGER", "TTS引擎初始化完成 (edge_tts)", indent=1)
        # 默认使用 EdgeTTS
        else:
            from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
            self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
            log_info("MANAGER", f"TTS引擎初始化完成 ({tts_engine_type})", indent=1)
        
        # 初始化音频播放器
        # 注意：AudioPlayer需要task_lock，但在v3.0架构中我们使用TaskContext
        # 为了兼容，创建一个锁，但实际检查任务取消时使用get_current_task_id
        import threading
        player_task_lock = threading.Lock()
        self.audio_player = AudioPlayer(
            config=self.config,
            task_lock=player_task_lock,  # AudioPlayer内部需要锁
            get_current_task_id=lambda: self.context_manager.get_current_task_id(),
            get_interrupt_flag=lambda: self._get_pending_interrupt_safe(),  # 使用线程安全的方式读取
            event_bus=self.event_bus
        )
        log_info("MANAGER", "音频播放器初始化完成", indent=1)
        
        # 设置事件订阅
        self._setup_event_subscriptions()
        
        # 发布系统初始化事件
        init_event = create_event(
            EventType.SYSTEM_INIT,
            data={"modules": ["microphone", "vad", "asr", "llm", "tts", "audio_player", "persona_manager"]},
            source="DialogueManager"
        )
        self.event_bus.publish(init_event)
        
        log_info("MANAGER", "所有模块初始化完成", indent=0)
    
    def _setup_event_subscriptions(self):
        """设置事件订阅"""
        
        # 订阅播放进度事件（用于实时更新已播放文本）
        progress_filter = EventFilter(event_types=[EventType.AUDIO_PLAY_PROGRESS])
        self.event_bus.subscribe(self._on_playback_progress, progress_filter)
        
        # 订阅ASR部分识别结果事件（用于实时日志输出）
        asr_partial_filter = EventFilter(event_types=[EventType.ASR_PARTIAL_RESULT])
        self.event_bus.subscribe(self._on_asr_partial_result, asr_partial_filter)
        
        # 订阅其他需要处理的事件...
        # TODO: 根据需要添加更多事件订阅
    
   
    
    def start(self):
        """启动系统"""
        if self.running:
            log_warn("MANAGER", "系统已经在运行中", indent=0)
            return
        
        log_info("MANAGER", "启动系统...", indent=0)
        self.running = True
        
        # 启动音频采集线程（线程1，通过 ThreadManager）
        def audio_recorder_thread():
            """音频采集线程（线程1）"""
            if self.microphone:
                self.microphone.start()
                # 持续运行，直到running=False
                # 注意：NO_SPEECH_THRESHOLD检查已移至_on_audio_data回调中实时进行
                # 使用更小的sleep间隔以更快响应停止信号
                while self.running:
                    time.sleep(0.05)  # 从0.1秒减少到0.05秒，更快响应
                self.microphone.stop()
        
        self.thread_manager.start_thread(
            name="Audio-Recorder",
            target=audio_recorder_thread,
            context=None,
            thread_type="resident",
            daemon=True
        )
        log_info("MANAGER", "音频采集线程已启动", indent=1)
        
        # 启动VAD后台线程（线程2，通过 ThreadManager）
        if self.vad:
            self.vad.start_background_thread()
        log_info("MANAGER", "VAD后台线程已启动", indent=1)
        
        # 发布系统启动事件
        start_event = create_event(
            EventType.SYSTEM_START,
            data={},
            source="DialogueManager"
        )
        self.event_bus.publish(start_event)
        
        # 状态初始化（IDLE + NONE）
        self.state_manager.transition_both(
            MainSystemState.IDLE,
            SubSystemState.NONE,
            reason="系统启动"
        )
        
        log_info("MANAGER", "系统启动完成", indent=0)
    
    def stop(self):
        """停止系统"""
        if not self.running:
            return
        
        log_info("MANAGER", "停止系统...", indent=0)
        self.running = False
        
        # 发布系统停止事件
        log_info("MANAGER", "发布系统停止事件", indent=1)
        stop_event = create_event(
            EventType.SYSTEM_STOP,
            data={},
            source="DialogueManager"
        )
        self.event_bus.publish(stop_event)
        log_info("MANAGER", "系统停止事件已发布", indent=1)
        
        # 取消当前任务
        log_info("MANAGER", "取消当前任务", indent=1)
        self.context_manager.cancel_current_context()
        log_info("MANAGER", "当前任务已取消", indent=1)
        
        # 停止ASR线程（如果正在运行）
        if self.asr_thread_active:
            log_info("MANAGER", "停止ASR线程", indent=1)
            self._stop_asr_recognition()
            log_info("MANAGER", "ASR线程已停止", indent=1)
        
        # 停止所有常驻线程（通过 ThreadManager）
        log_info("MANAGER", "停止所有线程（超时: {}秒）".format(self.thread_timeout), indent=1)
        stopped_count = self.thread_manager.stop_all_threads(timeout=self.thread_timeout)
        log_info("MANAGER", "线程停止完成（成功: {}个）".format(stopped_count), indent=1)
        
        # 停止VAD后台线程
        if self.vad:
            log_info("MANAGER", "停止VAD后台线程", indent=1)
            self.vad.stop_background_thread()
            log_info("MANAGER", "VAD后台线程已停止", indent=1)
        
        # 停止事件总线处理
        log_info("MANAGER", "停止事件总线处理", indent=1)
        self.event_bus.stop_processing()
        log_info("MANAGER", "事件总线处理已停止", indent=1)
        
        # 清理资源
        log_info("MANAGER", "清理资源", indent=1)
        if self.microphone:
            self.microphone.stop()
        if self.audio_player:
            self.audio_player.cleanup()
        if self.tts_engine:
            self.tts_engine.cleanup()
        if self.asr:
            # ASR清理
            pass
        log_info("MANAGER", "资源清理完成", indent=1)
        
        log_info("MANAGER", "系统已停止", indent=0)
    
    
    
    def _on_audio_data(self, audio_data: np.ndarray):
        """
        音频数据回调（由线程1调用）
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        # 发送给VAD队列
        if self.vad:
            self.vad.feed_audio(audio_data)
        
        # 发送给ASR预缓存（如果ASR已初始化）
        if self.asr and hasattr(self.asr, 'pre_buffer') and self.asr.pre_buffer:
            try:
                self.asr.pre_buffer.append(audio_data)
            except Exception as e:
                log_debug("MANAGER", f"预缓存添加失败: {e}", indent=2)
        
        # 发送给ASR识别队列（如果正在识别）
        if self.asr:
            self.asr.feed_audio(audio_data)
        
        # 注意：last_active_time 只由 VAD 的 _on_vad_speech_start() 回调更新
        # 不在这里更新，避免音频数据持续输入时误判
        
        # 检查NO_SPEECH_THRESHOLD，触发ASR停止
        if (self.last_active_time > 0 and 
            time.time() - self.last_active_time > self.no_speech_threshold):
            log_debug("MANAGER", f"检查NO_SPEECH_THRESHOLD: last_active_time={self.last_active_time:.3f}, 距离上次活动={time.time() - self.last_active_time:.3f}秒, 阈值={self.no_speech_threshold}秒", indent=2)
            # 检查ASR是否正在识别
            if self.asr:
                if hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
                    with self.asr.state_lock:
                        is_recognizing = self.asr.recognizing
                    if is_recognizing:
                        # 触发ASR停止（通过_save_audio_video逻辑）
                        self._save_audio_video()
    
    def _save_audio_video(self):
        """
        保存音频并等待ASR识别完成
        当用户停止说话超过NO_SPEECH_THRESHOLD时调用
        
        注意：ASR结果已经通过on_final_result回调处理，这里只需要等待识别完成即可
        """
        if not self.asr:
            return
        
        # 检查ASR是否正在识别
        if hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
            with self.asr.state_lock:
                is_recognizing = self.asr.recognizing
        else:
            return
        
        if not is_recognizing:
            return
        
        log_info("MANAGER", "用户停止说话，等待ASR识别完成", indent=0)
        
        # 等待ASR识别完成（结果已经通过on_final_result回调处理，这里只需要等待线程完成）
        # 注意：不调用_handle_asr_result，因为结果已经通过回调处理了
        if hasattr(self.asr, 'wait_for_result'):
            self.asr.wait_for_result(
                timeout=self.config.get("asr.result_timeout", 30.0)
            )
    
    def _on_asr_partial_result(self, text, confidence=None):
        """ASR部分结果回调"""
        # 可选：可以在这里处理部分结果（如实时显示）
        log_debug("MANAGER", f"部分识别结果: {text}", indent=0)
    
    def _on_asr_final_result(self, text, confidence=None):
        """ASR最终结果回调"""
        log_info("ASR", f"最终识别结果: {text}", indent=0)
        
        # 记录ASR识别完成时间
        log_info("TIME", "记录ASR识别完成时间", indent=1)
        self.performance_timer.record_user_speech_end()
        
        self._handle_asr_result(text, confidence)
    
    def _is_nothing_said_yet(self) -> bool:
        """
        判断当前任务是否被判定为"什么都没说过"
        
        判断条件：
        1. 播放线程还没启动（player_thread_started == False）
        2. 或截止新语音活动开始时，播放时长 < min_audio_length
        
        Returns:
            True表示什么都没说过，False表示已经说过
        """
        log_info("MANAGER", "[NOTHING_SAID-001] 开始判断是否'什么都没说过'", indent=1)
        
        # 条件1: 播放线程还没启动
        player_thread_started = self._get_player_thread_started_safe()
        log_info("MANAGER", f"[NOTHING_SAID-002] 检查播放线程状态: player_thread_started={player_thread_started}", indent=2)
        
        if not player_thread_started:
            log_info("MANAGER", "[NOTHING_SAID-002] 播放线程未启动，判定为'什么都没说过'", indent=2)
            log_info("MANAGER", "[NOTHING_SAID-001] 判断结果: True（什么都没说过）", indent=1)
            return True
        
        # 条件2: 播放时长不足（需要从TaskContext获取播放开始时间）
        # 注意：在v3.0架构中，播放开始时间应该存储在TaskContext中
        # 这里简化处理，如果voice_activity_start_time > 0，说明有语音活动
        log_info("MANAGER", f"[NOTHING_SAID-003] 播放线程已启动，检查播放时长: voice_activity_start_time={self.voice_activity_start_time}, min_audio_length={self.min_audio_length}秒", indent=2)
        
        if self.voice_activity_start_time > 0:
            # 尝试从当前任务上下文获取播放开始时间
            current_context = self.context_manager.get_current_context()
            log_info("MANAGER", f"[NOTHING_SAID-003] 获取当前上下文: context={'存在' if current_context else 'None'}, is_valid={current_context.is_valid if current_context else False}", indent=3)
            
            if current_context and current_context.is_valid:
                play_start_time = current_context.get_extra_data("play_start_time", 0.0)
                log_info("MANAGER", f"[NOTHING_SAID-003] 播放开始时间: play_start_time={play_start_time}", indent=3)
                
                if play_start_time > 0:
                    # 计算截止新语音活动开始时的播放时长
                    play_duration = self.voice_activity_start_time - play_start_time
                    log_info("MANAGER", f"[NOTHING_SAID-003] 计算播放时长: {play_duration:.3f}秒 (voice_activity_start_time={self.voice_activity_start_time:.3f} - play_start_time={play_start_time:.3f})", indent=3)
                    
                    if play_duration < self.min_audio_length:
                        log_info("MANAGER", f"[NOTHING_SAID-003] 播放时长不足 ({play_duration:.3f}秒 < {self.min_audio_length}秒)，判定为'什么都没说过'", indent=3)
                        log_info("MANAGER", "[NOTHING_SAID-001] 判断结果: True（什么都没说过）", indent=1)
                        return True
                    else:
                        log_info("MANAGER", f"[NOTHING_SAID-003] 播放时长足够 ({play_duration:.3f}秒 >= {self.min_audio_length}秒)，判定为'已经说过'", indent=3)
                        log_info("MANAGER", "[NOTHING_SAID-001] 判断结果: False（已经说过）", indent=1)
                        return False
                else:
                    # 播放线程已启动但还没开始播放
                    log_info("MANAGER", "[NOTHING_SAID-003] 播放开始时间为0，播放线程已启动但还未开始播放，判定为'什么都没说过'", indent=3)
                    log_info("MANAGER", "[NOTHING_SAID-001] 判断结果: True（什么都没说过）", indent=1)
                    return True
            else:
                log_info("MANAGER", "[NOTHING_SAID-003] 当前上下文无效或为空，无法获取播放开始时间", indent=3)
        else:
            log_info("MANAGER", f"[NOTHING_SAID-003] voice_activity_start_time={self.voice_activity_start_time} <= 0，无法计算播放时长", indent=3)
        
        log_info("MANAGER", "[NOTHING_SAID-001] 判断结果: False（已经说过或无法判断）", indent=1)
        return False
    
    def _confirm_real_interrupt(self):
        """
        确认真实打断并停止播放
        
        当ASR验证成功（有效输入）时调用，正式打断当前播放或处理
        
        根据THREAD_SCHEDULING.md场景2，应该：
        1. 停止播放
        2. 检查播放时长
        3. 如果播放时长 >= MIN_PLAY_DURATION_TO_RECORD，记录已播放内容到对话历史
        4. 清理播放状态
        5. 清除 pending_interrupt
        
        注意：如果遇到打断，响应耗时分析的初始时间点要重新计算，从当前句子VAD结束时间开始
        """
        log_info("MANAGER", "确认真实打断，停止播放并处理新输入", indent=0)
        
        try:
            # 重置性能计时器，重新开始计时（从当前VAD结束时间开始）
            log_info("MANAGER", "[PERF_TIMER] 打断时重置性能计时器，重新开始计时", indent=1)
            log_info("TIME", "打断时重置性能计时器", indent=1)
            self.performance_timer.reset()
            # 重新记录当前VAD结束时间作为新的起点（ASR识别完成时VAD应该已经结束）
            log_info("TIME", "打断时重新记录VAD结束时间作为新的起点", indent=1)
            self.performance_timer.record_vad_speech_end()
            log_info("MANAGER", "[PERF_TIMER] 已重新记录VAD结束时间作为新的起点", indent=1)
            self.performance_timer.set_paused_reset_flag(value=True)
            log_info("TIME", "打断时设置暂停重置标记为True", indent=1)

            # 获取当前任务上下文
            current_context = self.context_manager.get_current_context()
            current_task_id = self.context_manager.get_current_task_id()
            
            # 停止音频播放（在获取进度之前停止，因为播放器可能已经暂停）
            # 注意：如果播放器已经暂停，get_progress()在stop()之前仍然能返回已播放时长
            played_duration = 0.0
            if self.audio_player:
                # 检查当前任务是否真的开始播放了
                # 如果任务ID不匹配或状态不是PLAYING/PAUSED，说明当前任务还没有开始播放
                audio_status = self.audio_player.get_status()
                audio_task_id = self.audio_player.get_current_task_id()
                
                if audio_task_id == current_task_id and audio_status in [PlaybackStatus.PLAYING, PlaybackStatus.PAUSED]:
                    # 当前任务正在播放或已暂停，获取播放进度
                    progress = self.audio_player.get_progress()
                    played_duration = progress.get("elapsed_time", 0.0)
                    log_info("MANAGER", f"[HISTORY_CHECK-000] 当前任务正在播放/暂停，获取播放时长: {played_duration:.2f}秒", indent=1)
                else:
                    # 当前任务还没有开始播放，播放时长为0
                    log_info("MANAGER", f"[HISTORY_CHECK-000] 当前任务尚未开始播放 (音频任务ID={audio_task_id}, 当前任务ID={current_task_id}, 状态={audio_status.value})，播放时长为0", indent=1)
                    played_duration = 0.0
                
                # 停止播放
                log_info("MANAGER", f"[AUDIO_STOP-001] _confirm_real_interrupt() 准备调用 audio_player.stop(task_id={current_task_id})", indent=1)
                log_info("MANAGER", f"[AUDIO_STOP-001] 调用前状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                stop_result = self.audio_player.stop(task_id=current_task_id)
                log_info("MANAGER", f"[AUDIO_STOP-001] audio_player.stop() 返回: {stop_result}", indent=1)
        
            # 检查播放时长，决定是否记录历史
            log_info("MANAGER", f"[HISTORY_CHECK-001] 开始检查播放时长: played_duration={played_duration:.2f}秒, 阈值={self.min_play_duration_to_record:.2f}秒", indent=1)
            
            if played_duration >= self.min_play_duration_to_record:
                log_info("MANAGER", f"[HISTORY_CHECK-002] 播放时长达到阈值，准备记录对话历史", indent=1)
                
                # 播放时长达到阈值，记录已播放内容到对话历史
                if current_context and current_context.is_valid:
                    log_info("MANAGER", f"[HISTORY_CHECK-003] 当前上下文有效，开始获取对话内容", indent=1)
                    
                    user_input = current_context.user_input
                    log_info("MANAGER", f"[HISTORY_CHECK-004] 获取用户输入: 长度={len(user_input) if user_input else 0}字符", indent=1)
                    
                    played_text = current_context.get_extra_data("played_response_text", "")
                    log_info("MANAGER", f"[HISTORY_CHECK-005] 获取已播放文本: 长度={len(played_text) if played_text else 0}字符", indent=1)
                    
                    if not played_text:
                        # 如果没有实时更新，使用完整回答文本
                        log_info("MANAGER", f"[HISTORY_CHECK-006] 已播放文本为空，使用完整回答文本", indent=1)
                        played_text = current_context.response_text
                        log_info("MANAGER", f"[HISTORY_CHECK-007] 完整回答文本长度: {len(played_text) if played_text else 0}字符", indent=1)
                    else:
                        log_info("MANAGER", f"[HISTORY_CHECK-006] 使用已播放文本（实时更新）", indent=1)
                    
                    # 记录到对话历史
                    log_info("MANAGER", f"[HISTORY_CHECK-008] 准备获取 history_lock 并记录到对话历史", indent=1)
                    with self.history_lock:
                        history_before = len(self.conversation_history)
                        self.conversation_history.append({"role": "user", "content": user_input})
                        log_info("MANAGER", f"[HISTORY_CHECK-009] 已添加用户消息到历史，历史记录数: {history_before} -> {len(self.conversation_history)}", indent=1)
                        
                        if played_text:
                            # 去除前导空白字符（包括换行符、空格等）
                            played_text_trimmed = played_text.lstrip()
                            self.conversation_history.append({"role": "assistant", "content": played_text_trimmed})
                            log_info("MANAGER", f"[HISTORY_CHECK-010] 已添加助手消息到历史，历史记录数: {len(self.conversation_history)}", indent=1)
                        else:
                            log_warn("MANAGER", f"[HISTORY_CHECK-010] 播放文本为空，未添加助手消息", indent=1)
                        
                        # 限制历史记录长度
                        self._limit_history_length()
                    
                    log_info("MANAGER", f"[HISTORY_CHECK-011] 对话历史记录完成（播放时长: {played_duration:.2f}秒，历史记录总数: {len(self.conversation_history)}）", indent=1)
                    
                    # 任务被打断但已记录到对话历史，清空累积输入（避免已处理的输入在后续合并中被重复使用）
                    if self.accumulated_input:
                        log_info("MANAGER", f"[HISTORY_CHECK-011] 清空累积输入（打断但已记录）: '{self.accumulated_input}'", indent=2)
                        self.accumulated_input = ""
                    
                    # 设置标记，避免播放线程重复记录
                    log_info("MANAGER", f"[HISTORY_CHECK-012] 准备设置历史记录标记 history_recorded_by_interrupt=True", indent=1)
                    try:
                        current_context.set_extra_data("history_recorded_by_interrupt", True)
                        log_info("MANAGER", f"[HISTORY_CHECK-013] 历史记录标记设置成功", indent=1)
                    except Exception as e:
                        log_warn("MANAGER", f"[HISTORY_CHECK-013] 设置历史记录标记失败: {e}", indent=1)
                else:
                    log_warn("MANAGER", f"[HISTORY_CHECK-003] 当前上下文无效或为空，无法记录对话历史", indent=1)
            else:
                log_info("MANAGER", f"[HISTORY_CHECK-002] 播放时长未达到阈值，不记录对话历史", indent=1)
            
            log_info("MANAGER", "步骤A: 准备清除pending_interrupt", indent=1)
            # 清除pending_interrupt标志  【这里有点问题】
            owner_info = self.interrupt_lock.get_owner_info()
            if owner_info:
                log_warn("MANAGER", f"[LOCK-001] _confirm_real_interrupt() 尝试获取 interrupt_lock，但锁已被占用:", indent=1)
                log_warn("MANAGER", f"  持有线程: {owner_info['thread_name']} (ID:{owner_info['thread_id']})", indent=1)
                log_warn("MANAGER", f"  持有时间: {owner_info['hold_duration']:.2f}秒", indent=1)
                log_warn("MANAGER", f"  等待线程数: {owner_info['waiting_count']}", indent=1)
            log_info("MANAGER", f"[LOCK-001] _confirm_real_interrupt() 正在获取 interrupt_lock, pending_interrupt={self.pending_interrupt}", indent=1)
            with self.interrupt_lock:
                self.pending_interrupt = False
                log_info("MANAGER", "步骤B: pending_interrupt 已被设置为False", indent=1)
            
            log_info("MANAGER", "步骤C: pending_interrupt已清除，获取状态", indent=1)
            
            # 更新状态
            sub_state = self.state_manager.get_sub_state()
            main_state = self.state_manager.get_main_state()
            
            log_info("MANAGER", f"步骤D: 当前状态 主={main_state.value}, 副={sub_state.value}", indent=1)
            
            if sub_state == SubSystemState.PAUSED:
                # 从PAUSED状态恢复，准备处理新输入
                log_info("MANAGER", f"[STATE_TRANS-CONFIRM_INTERRUPT] _confirm_real_interrupt() 准备从PAUSED转换到NONE", indent=1)
                log_info("MANAGER", f"[STATE_TRANS-CONFIRM_INTERRUPT] 转换前状态: 主={main_state.value}, 副={sub_state.value}", indent=1)
                self.state_manager.transition_sub_state(SubSystemState.NONE, "确认真实打断，准备处理新输入")
                log_info("MANAGER", f"[STATE_TRANS-CONFIRM_INTERRUPT] 转换后状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
            
            log_info("MANAGER", "_confirm_real_interrupt() 完成", indent=1)
        except Exception as e:
            log_error("MANAGER", f"_confirm_real_interrupt() 发生异常: {e}", indent=0)
            import traceback
            log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=0)
            raise
    
    def _handle_asr_result(self, text: str, confidence: float):
        """
        处理ASR识别结果
        
        Args:
            text: 识别文本
            confidence: 识别置信度
        """
        # 记录当前状态和输入
        sub_state = self.state_manager.get_sub_state()
        main_state = self.state_manager.get_main_state()
        log_info("MANAGER", f"处理ASR结果: '{text}' (主系统={main_state.value}, 副系统={sub_state.value})", indent=0)
        
        # 检查是否已经处理过（避免重复处理）
        if main_state == MainSystemState.IDLE and sub_state == SubSystemState.PROCESSING:
            # 如果状态已经是 idle + processing，说明已经处理过了，直接返回
            log_debug("MANAGER", f"识别结果已处理，跳过: '{text}'", indent=0)
            return
        
        # 验证输入有效性
        if not self._validate_input(text):
            log_warn("MANAGER", f"识别结果无效: '{text}'", indent=0)
            
            # 根据系统状态处理无效输入
            sub_state = self.state_manager.get_sub_state()
            main_state = self.state_manager.get_main_state()
            log_info("MANAGER", f"无效输入处理: 进入场景5/场景6判断 (主系统={main_state.value}, 副系统={sub_state.value})", indent=0)
            
            # 场景5/场景6: 根据系统状态处理无效输入
            if sub_state == SubSystemState.PAUSED:
                # 场景6: PAUSED状态，恢复播放或恢复处理
                log_info("MANAGER", "场景6: 无效输入，PAUSED状态，进入场景6处理", indent=0)
                paused_before_state = self.paused_sub_state
                
                if paused_before_state == SubSystemState.PROCESSING:
                    # 延迟取消情况：播放线程未启动，恢复PROCESSING状态
                    log_info("MANAGER", "场景6: 无效输入，PAUSED状态（延迟取消），恢复PROCESSING状态", indent=0)
                    
                    # 发布打断取消事件（误判）
                    if self.event_bus:
                        current_task_id = self.context_manager.get_current_task_id()
                        event = create_event(
                            EventType.INTERRUPT_CANCELLED,
                            data={
                                "task_id": current_task_id,
                                "previous_state": "processing",
                                "reason": "invalid_input"
                            },
                            source="DialogueManager"
                        )
                        self.event_bus.publish(event)
                    
                    # 清除所有打断相关标记
                    log_info("MANAGER", "[LOCK-002] _handle_asr_result() 场景6-延迟取消 正在获取 interrupt_lock", indent=0)
                    with self.interrupt_lock:
                        self.pending_interrupt = False
                    self.paused_sub_state = None
                    self.nothing_said_yet = False
                    # 主系统状态: LISTENING → IDLE
                    self.state_manager.transition_main_state(MainSystemState.IDLE, "ASR验证失败（误判），恢复正常状态")
                    # 副系统状态: PAUSED → PROCESSING
                    self.state_manager.transition_sub_state(SubSystemState.PROCESSING, "ASR验证失败（误判），恢复处理")
                else:
                    # 普通暂停情况：正在播放时被暂停，恢复播放
                    log_info("MANAGER", "场景6: 无效输入，PAUSED状态（普通暂停），恢复播放", indent=0)
                    current_task_id = self.context_manager.get_current_task_id()
                    
                    # 发布打断取消事件（误判）
                    if self.event_bus:
                        event = create_event(
                            EventType.INTERRUPT_CANCELLED,
                            data={
                                "task_id": current_task_id,
                                "previous_state": "speaking",
                                "reason": "invalid_input"
                            },
                            source="DialogueManager"
                        )
                        self.event_bus.publish(event)
                    
                    # 检查audio_player的状态并尝试恢复播放
                    resume_success = False
                    if self.audio_player and hasattr(self.audio_player, 'is_paused') and self.audio_player.is_paused():
                        # 恢复播放（从暂停位置继续）
                        if hasattr(self.audio_player, 'resume'):
                            resume_success = self.audio_player.resume(task_id=current_task_id)
                    
                    if resume_success:
                        # 恢复成功，清除标志并更新状态
                        log_info("MANAGER", "场景6: 播放已恢复，从暂停位置继续", indent=0)
                        log_info("MANAGER", "[LOCK-003] _handle_asr_result() 场景6-恢复播放成功 正在获取 interrupt_lock", indent=0)
                        with self.interrupt_lock:
                            self.pending_interrupt = False
                        self.paused_sub_state = None
                        self.nothing_said_yet = False
                        # 主系统状态: LISTENING → ROBOT_SPEAKING
                        self.state_manager.transition_main_state(MainSystemState.ROBOT_SPEAKING, "ASR验证失败（误判），恢复播放状态")
                        # 副系统状态: PAUSED → SPEAKING
                        self.state_manager.transition_sub_state(SubSystemState.SPEAKING, "ASR验证失败，恢复播放")
                    else:
                        # 恢复失败，保留标志并处理异常情况
                        log_error("MANAGER", "场景6: 恢复播放失败，播放器可能已停止", indent=0)
                        # 清除标志但状态转为NONE（播放器已停止，无法恢复）
                        log_info("MANAGER", "[LOCK-004] _handle_asr_result() 场景6-恢复播放失败 正在获取 interrupt_lock", indent=0)
                        with self.interrupt_lock:
                            self.pending_interrupt = False
                        self.paused_sub_state = None
                        self.nothing_said_yet = False
                        # 主系统状态: LISTENING → IDLE
                        self.state_manager.transition_main_state(MainSystemState.IDLE, "ASR验证失败但播放器已停止")
                        # 副系统状态: PAUSED → NONE
                        self.state_manager.transition_sub_state(SubSystemState.NONE, "ASR验证失败但播放器已停止")
            elif sub_state == SubSystemState.PROCESSING and main_state == MainSystemState.LISTENING:
                # 场景5: PROCESSING + LISTENING状态，取消当前任务
                log_info("MANAGER", "场景5: 无效输入，PROCESSING + LISTENING状态，取消任务", indent=0)
                # 取消当前任务
                self._cancel_current_task()
                
                # 清除打断相关标记
                log_info("MANAGER", "[LOCK-005] _handle_asr_result() 场景5-无效输入 正在获取 interrupt_lock", indent=0)
                with self.interrupt_lock:
                    self.pending_interrupt = False
                self.paused_sub_state = None
                self.nothing_said_yet = False
                
                # 场景5特殊处理：无效输入时，副系统状态应该直接恢复为None
                current_sub_state = self.state_manager.get_sub_state()
                if current_sub_state == SubSystemState.PAUSED:
                    self.state_manager.transition_sub_state(SubSystemState.NONE, "无效输入，取消任务")
                # 确保主系统状态也更新为IDLE
                self.state_manager.transition_main_state(MainSystemState.IDLE, "无效输入，取消任务")
            else:
                # 其他情况（包括普通LISTENING状态），也需要恢复为IDLE
                log_info("MANAGER", "无效输入，恢复IDLE状态", indent=0)
                # 清除打断相关标记
                log_info("MANAGER", "[LOCK-006] _handle_asr_result() 无效输入-其他情况 正在获取 interrupt_lock", indent=0)
                with self.interrupt_lock:
                    self.pending_interrupt = False
                self.paused_sub_state = None
                self.nothing_said_yet = False
                self.state_manager.transition_main_state(MainSystemState.IDLE, "无效输入")
                self.state_manager.transition_sub_state(SubSystemState.NONE, "无效输入")
            
            return
        
        # 有效输入，根据系统状态处理
        sub_state = self.state_manager.get_sub_state()
        main_state = self.state_manager.get_main_state()
        
        if sub_state == SubSystemState.PAUSED:
            # PAUSED状态可能来自两种情况：
            # 1. 场景2: SPEAKING → PAUSED（正在播放时被打断）
            # 2. 场景3/4: PROCESSING → PAUSED（正在处理时被打断）
            log_info("MANAGER", "进入PAUSED状态处理: 有效输入，判断是场景2还是场景3/4", indent=0)
            paused_before_state = self.paused_sub_state
            if paused_before_state is None:
                # 没有记录暂停前的状态，通过audio_player状态判断
                if self.audio_player:
                    is_playing = hasattr(self.audio_player, 'is_playing') and self.audio_player.is_playing()
                    is_paused = hasattr(self.audio_player, 'is_paused') and self.audio_player.is_paused()
                    if is_playing or is_paused:
                        paused_before_state = SubSystemState.SPEAKING
                    else:
                        paused_before_state = SubSystemState.PROCESSING
                else:
                    # 使用主系统状态判断
                    if main_state == MainSystemState.LISTENING:
                        paused_before_state = SubSystemState.PROCESSING
                    else:
                        paused_before_state = SubSystemState.SPEAKING
            
            if paused_before_state == SubSystemState.PROCESSING:
                # 场景3/4: 正在处理时被打断（已暂停）
                log_info("MANAGER", "[INPUT_MERGE-001] 进入场景3/4判断: 暂停前状态为PROCESSING（LLM/TTS处理中但未开始播放）", indent=0)
                log_info("MANAGER", f"[INPUT_MERGE-001] 当前状态: 主系统={main_state.value}, 副系统={sub_state.value}, 暂停前状态={paused_before_state.value}", indent=1)
                log_info("MANAGER", f"[INPUT_MERGE-001] 新ASR识别结果: '{text}'", indent=1)
                
                # 快速连续输入场景：判断是否"什么都没说过"
                log_info("MANAGER", "[INPUT_MERGE-002] 开始判断是否'什么都没说过'", indent=1)
                is_nothing_said = self._is_nothing_said_yet()
                log_info("MANAGER", f"[INPUT_MERGE-002] 判断结果: is_nothing_said={is_nothing_said}", indent=1)
                
                # 获取当前任务上下文
                current_context = self.context_manager.get_current_context()
                has_current_input = current_context and current_context.is_valid and bool(current_context.user_input)
                log_info("MANAGER", f"[INPUT_MERGE-003] 获取当前任务上下文: task_id={current_context.task_id if current_context else None}, has_current_input={has_current_input}", indent=1)
                if has_current_input and current_context:
                    log_info("MANAGER", f"[INPUT_MERGE-003] 当前任务用户输入: '{current_context.user_input}'", indent=2)
                
                if is_nothing_said:
                    # 什么都没说过：累积输入，取消当前任务，合并处理
                    log_info("MANAGER", "[INPUT_MERGE-004] 分支1: 快速连续输入场景（什么都没说过），开始累积输入并取消当前任务", indent=1)
                    
                    # 重置性能计时器，重新开始计时（从当前VAD结束时间开始）
                    log_info("MANAGER", "[PERF_TIMER] 什么都没说过场景，重置性能计时器，重新开始计时", indent=1)
                    log_info("TIME", "什么都没说过场景重置性能计时器", indent=1)
                    self.performance_timer.reset()
                    # 重新记录当前VAD结束时间作为新的起点（ASR识别完成时VAD应该已经结束）
                    log_info("TIME", "什么都没说过场景重新记录VAD结束时间作为新的起点", indent=1)
                    self.performance_timer.record_vad_speech_end()
                    log_info("MANAGER", "[PERF_TIMER] 已重新记录VAD结束时间作为新的起点", indent=1)
                    self.performance_timer.set_paused_reset_flag(value=True)
                    log_info("TIME", "什么都没说过场景设置暂停重置", indent=1)
                    
                    # 累积当前输入到accumulated_input
                    if has_current_input and current_context:
                        old_input = current_context.user_input
                        log_info("MANAGER", f"[INPUT_MERGE-004] 获取旧输入: '{old_input}'", indent=2)
                        
                        if self.accumulated_input:
                            log_info("MANAGER", f"[INPUT_MERGE-004] 已有累积输入: '{self.accumulated_input}'，开始合并", indent=2)
                            self.accumulated_input = self.accumulated_input.strip() + " " + old_input.strip()
                            log_info("MANAGER", f"[INPUT_MERGE-004] 合并后累积输入: '{self.accumulated_input}'", indent=2)
                        else:
                            log_info("MANAGER", f"[INPUT_MERGE-004] 无累积输入，直接设置: '{old_input}'", indent=2)
                            self.accumulated_input = old_input.strip()
                        log_info("MANAGER", f"[INPUT_MERGE-004] 最终累积输入: '{self.accumulated_input}'", indent=2)
                    else:
                        log_info("MANAGER", "[INPUT_MERGE-004] 当前任务无有效输入，跳过累积", indent=2)
                    
                    # 取消当前任务
                    log_info("MANAGER", f"[INPUT_MERGE-005] 准备取消当前任务: task_id={current_context.task_id if current_context else None}", indent=2)
                    # 在取消前保存旧输入
                    old_input_to_merge = current_context.user_input if current_context and current_context.is_valid else ""
                    if old_input_to_merge:
                        log_info("MANAGER", f"[INPUT_MERGE-005] 保存旧输入（取消前）: '{old_input_to_merge}'", indent=3)
                    self.context_manager.cancel_current_context()
                    log_info("MANAGER", "[INPUT_MERGE-005] 当前任务已取消，上下文已失效", indent=2)
                    
                    # 清除打断相关标记
                    log_info("MANAGER", "[INPUT_MERGE-006] 准备清除打断相关标记", indent=2)
                    log_info("MANAGER", "[LOCK-007] _handle_asr_result() 快速连续输入-什么都没说过 正在获取 interrupt_lock", indent=0)
                    with self.interrupt_lock:
                        old_pending = self.pending_interrupt
                        self.pending_interrupt = False
                        log_info("MANAGER", f"[INPUT_MERGE-006] pending_interrupt: {old_pending} -> False", indent=3)
                    self.paused_sub_state = None
                    self.nothing_said_yet = False
                    log_info("MANAGER", "[INPUT_MERGE-006] 打断相关标记已清除", indent=2)
                    
                    # 副系统状态: PAUSED → NONE
                    log_info("MANAGER", "[INPUT_MERGE-007] 准备转换副系统状态: PAUSED -> NONE", indent=2)
                    self.state_manager.transition_sub_state(SubSystemState.NONE, "什么都没说过，取消任务准备合并输入")
                    log_info("MANAGER", "[INPUT_MERGE-007] 状态转换完成", indent=2)
                    
                    # 处理新输入（合并输入）
                    log_info("MANAGER", f"[INPUT_MERGE-008] 准备处理新输入（合并模式）: 新输入='{text}', 累积输入='{self.accumulated_input}'", indent=2)
                    # 传递已保存的旧输入（如果有）
                    old_input_to_merge = self.accumulated_input if self.accumulated_input else ""
                    self._process_user_input(text, is_merge=True, old_input=old_input_to_merge)
                    log_info("MANAGER", "[INPUT_MERGE-008] 新输入处理完成", indent=2)
                else:
                    # 延迟取消或普通取消情况
                    log_info("MANAGER", "[INPUT_MERGE-009] 分支2: 延迟取消或普通取消情况，开始取消任务并合并输入", indent=1)
                    log_info("MANAGER", f"[INPUT_MERGE-009] 当前任务信息: task_id={current_context.task_id if current_context else None}", indent=2)
                    if has_current_input and current_context:
                        log_info("MANAGER", f"[INPUT_MERGE-009] 当前任务用户输入: '{current_context.user_input}'", indent=2)
                    
                    # 取消当前任务
                    log_info("MANAGER", f"[INPUT_MERGE-010] 准备取消当前任务: task_id={current_context.task_id if current_context else None}", indent=2)
                    # 在取消前保存旧输入
                    old_input_to_merge = current_context.user_input if current_context and current_context.is_valid else ""
                    if old_input_to_merge:
                        log_info("MANAGER", f"[INPUT_MERGE-010] 保存旧输入（取消前）: '{old_input_to_merge}'", indent=3)
                    self.context_manager.cancel_current_context()
                    log_info("MANAGER", "[INPUT_MERGE-010] 当前任务已取消，上下文已失效", indent=2)
                    
                    # 清除打断相关标记
                    log_info("MANAGER", "[INPUT_MERGE-011] 准备清除打断相关标记", indent=2)
                    log_info("MANAGER", "[LOCK-008] _handle_asr_result() 场景3/4-延迟取消或普通取消 正在获取 interrupt_lock", indent=0)
                    with self.interrupt_lock:
                        old_pending = self.pending_interrupt
                        self.pending_interrupt = False
                        log_info("MANAGER", f"[INPUT_MERGE-011] pending_interrupt: {old_pending} -> False", indent=3)
                    self.paused_sub_state = None
                    self.nothing_said_yet = False
                    log_info("MANAGER", "[INPUT_MERGE-011] 打断相关标记已清除", indent=2)
                    
                    # 副系统状态: PAUSED → NONE
                    log_info("MANAGER", "[INPUT_MERGE-012] 准备转换副系统状态: PAUSED -> NONE", indent=2)
                    self.state_manager.transition_sub_state(SubSystemState.NONE, "确认真实打断，准备处理新输入（场景3/4）")
                    log_info("MANAGER", "[INPUT_MERGE-012] 状态转换完成", indent=2)
                    
                    # 处理新输入（合并输入）
                    log_info("MANAGER", f"[INPUT_MERGE-013] 准备处理新输入（合并模式）: 新输入='{text}'", indent=2)
                    # 传递已保存的旧输入（如果有）
                    old_input_to_merge = current_context.user_input if has_current_input and current_context else ""
                    if old_input_to_merge:
                        log_info("MANAGER", f"[INPUT_MERGE-013] 将合并已保存的旧输入: '{old_input_to_merge}'", indent=2)
                    self._process_user_input(text, is_merge=True, old_input=old_input_to_merge)
                    log_info("MANAGER", "[INPUT_MERGE-013] 新输入处理完成", indent=2)
            else:
                # 场景2: 正在播放时被打断（已暂停），正式打断并处理新输入
                log_info("MANAGER", "场景2: PAUSED状态（正在播放时被打断），确认真实打断", indent=0)
                self.paused_sub_state = None
                try:
                    # 确认真实打断（包含历史记录逻辑）
                    self._confirm_real_interrupt()
                    log_info("MANAGER", "场景2: 开始处理新输入", indent=0)
                    # 处理新输入
                    self._process_user_input(text, is_merge=False)
                except Exception as e:
                    log_error("MANAGER", f"场景2处理失败: {e}", indent=0)
                    import traceback
                    log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=0)
                    raise
        elif sub_state == SubSystemState.SPEAKING and main_state == MainSystemState.LISTENING:
            # 正在播放时被打断（还在播放），正式打断并处理新输入
            log_info("MANAGER", "场景2变体: SPEAKING+LISTENING状态，确认真实打断", indent=0)
            try:
                self._confirm_real_interrupt()
                log_info("MANAGER", "场景2变体: 开始处理新输入", indent=0)
                self._process_user_input(text, is_merge=False)
            except Exception as e:
                log_error("MANAGER", f"场景2变体处理失败: {e}", indent=0)
                import traceback
                log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=0)
                raise
        elif sub_state == SubSystemState.PROCESSING and main_state == MainSystemState.LISTENING:
            # 正在处理时被打断，需要先检查音频是否已经开始播放
            log_info("MANAGER", "场景3/4判断: PROCESSING + LISTENING状态，检查音频是否已开始播放", indent=0)
            log_info("MANAGER", f"[INPUT_MERGE-201] 当前状态: 主系统={main_state.value}, 副系统={sub_state.value}", indent=1)
            log_info("MANAGER", f"[INPUT_MERGE-201] 新ASR识别结果: '{text}'", indent=1)
            
            # 检查音频是否已经开始播放
            is_audio_playing = False
            is_audio_paused = False
            if self.audio_player:
                is_audio_playing = hasattr(self.audio_player, 'is_playing') and self.audio_player.is_playing()
                is_audio_paused = hasattr(self.audio_player, 'is_paused') and self.audio_player.is_paused()
            log_info("MANAGER", f"[INPUT_MERGE-201] 音频播放状态检查: is_playing={is_audio_playing}, is_paused={is_audio_paused}", indent=1)
            
            # 如果音频已经开始播放（或已暂停），应该按照场景2处理（正在播放时被打断）
            if is_audio_playing or is_audio_paused:
                log_info("MANAGER", "[INPUT_MERGE-201] 音频已开始播放，按照场景2处理（正在播放时被打断）", indent=1)
                try:
                    # 确认真实打断（包含历史记录逻辑）
                    self._confirm_real_interrupt()
                    log_info("MANAGER", "[INPUT_MERGE-201] 场景2处理: 开始处理新输入", indent=1)
                    # 处理新输入（不合并，因为已经在播放了）
                    self._process_user_input(text, is_merge=False)
                except Exception as e:
                    log_error("MANAGER", f"场景2处理失败: {e}", indent=1)
                    import traceback
                    log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
                    raise
            else:
                # 音频还没开始播放，取消任务并合并输入
                log_info("MANAGER", "[INPUT_MERGE-201] 音频未开始播放，取消任务并合并输入", indent=1)
                
                # 快速连续输入场景：判断是否"什么都没说过"
                log_info("MANAGER", "[INPUT_MERGE-202] 开始判断是否'什么都没说过'", indent=1)
                is_nothing_said = self._is_nothing_said_yet()
                log_info("MANAGER", f"[INPUT_MERGE-202] 判断结果: is_nothing_said={is_nothing_said}", indent=1)
                
                # 重置性能计时器，重新开始计时（从当前VAD结束时间开始）
                log_info("MANAGER", "[PERF_TIMER] 音频未开始播放场景，重置性能计时器，重新开始计时", indent=1)
                log_info("TIME", "音频未开始播放场景重置性能计时器", indent=1)
                self.performance_timer.reset()
                # 重新记录当前VAD结束时间作为新的起点（ASR识别完成时VAD应该已经结束）
                log_info("TIME", "音频未开始播放场景重新记录VAD结束时间作为新的起点", indent=1)
                self.performance_timer.record_vad_speech_end()
                log_info("MANAGER", "[PERF_TIMER] 已重新记录VAD结束时间作为新的起点", indent=1)
                self.performance_timer.set_paused_reset_flag(value=True)
                log_info("TIME", "音频未开始播放场景设置暂停重置", indent=1)
                
                # 获取当前任务上下文（在取消之前先保存旧输入）
                current_context = self.context_manager.get_current_context()
                log_info("MANAGER", f"[INPUT_MERGE-203] 获取当前任务上下文: task_id={current_context.task_id if current_context else None}", indent=1)
                
                # 在取消任务之前，先保存旧输入
                old_input_to_merge = ""
                if current_context and current_context.is_valid:
                    old_input_to_merge = current_context.user_input
                    log_info("MANAGER", f"[INPUT_MERGE-203] 保存旧输入（取消前）: '{old_input_to_merge}'", indent=2)
                else:
                    log_info("MANAGER", "[INPUT_MERGE-203] 当前上下文无效或为空，无旧输入可保存", indent=2)
                
                # 取消当前任务
                log_info("MANAGER", f"[INPUT_MERGE-204] 准备取消当前任务: task_id={current_context.task_id if current_context else None}", indent=1)
                self.context_manager.cancel_current_context()
                log_info("MANAGER", "[INPUT_MERGE-204] 当前任务已取消，上下文已失效", indent=1)
                
                # 处理新输入（合并输入）
                log_info("MANAGER", f"[INPUT_MERGE-205] 准备处理新输入（合并模式）: 新输入='{text}'", indent=1)
                if old_input_to_merge:
                    log_info("MANAGER", f"[INPUT_MERGE-205] 将合并已保存的旧输入: '{old_input_to_merge}'", indent=2)
                self._process_user_input(text, is_merge=True, old_input=old_input_to_merge)
                log_info("MANAGER", "[INPUT_MERGE-205] 新输入处理完成", indent=1)
        elif main_state == MainSystemState.LISTENING:
            # 检查是否有待处理的打断
            with self.interrupt_lock:
                has_pending_interrupt = self.pending_interrupt
            
            if has_pending_interrupt:
                # 有pending_interrupt，说明这是打断场景，但状态已经转换了
                log_info("MANAGER", "打断场景: LISTENING状态，但检测到pending_interrupt=True，确认真实打断", indent=0)
                self._confirm_real_interrupt()
                self._process_user_input(text, is_merge=False)
            else:
                # 正常情况，处理新输入
                log_info("MANAGER", "正常场景: LISTENING状态，处理新输入", indent=0)
                self._process_user_input(text, is_merge=False)
        elif main_state == MainSystemState.IDLE and sub_state == SubSystemState.SPEAKING:
            # 误判恢复后的状态：主系统=idle, 副系统=speaking
            # 播放还在进行，但ASR识别完成说明用户确实在说话，应该确认打断并处理新输入
            log_info("MANAGER", "误判恢复场景: IDLE+SPEAKING状态（误判恢复后），确认真实打断并处理新输入", indent=0)
            self._confirm_real_interrupt()
            self._process_user_input(text, is_merge=False)
        else:
            log_warn("MANAGER", f"意外状态组合: 主系统={main_state.value}, 副系统={sub_state.value}, text='{text}'", indent=0)
            # 尝试处理输入
            log_info("MANAGER", "尝试处理输入（意外状态组合情况）", indent=0)
            self._process_user_input(text, is_merge=False)
    
    def _validate_input(self, text: str) -> bool:
        """
        验证输入有效性
        
        Args:
            text: 输入文本
            
        Returns:
            True表示有效，False表示无效
        """
        if not text:
            return False
        
        text_trimmed = text.strip()
        if not text_trimmed:
            return False
        
        # 检查是否在无效词列表中
        if text_trimmed.lower() in [word.lower() for word in self.invalid_recognition_words]:
            return False
        
        return True
    
    def _process_user_input(self, user_input: str, is_merge: bool = False, old_input: str = ""):
        """
        处理用户输入
        
        Args:
            user_input: 用户输入文本
            is_merge: 是否为合并输入
            old_input: 可选的旧输入（如果提供，将直接使用，不从上下文获取）
        """
        log_info("MANAGER", f"开始处理用户输入: '{user_input}' (is_merge={is_merge})", indent=0)
        
        # 输入合并逻辑
        if is_merge:
            log_info("MANAGER", "[INPUT_MERGE-101] 进入输入合并逻辑", indent=1)
            log_info("MANAGER", f"[INPUT_MERGE-101] 新输入: '{user_input}'", indent=2)
            
            # 优先使用传入的old_input参数（如果提供）
            # 其次使用accumulated_input（快速连续输入场景）
            # 最后使用current_context.user_input（普通合并场景）
            old_input_final = ""
            log_info("MANAGER", "[INPUT_MERGE-102] 开始获取旧输入", indent=2)
            
            if old_input and old_input.strip():
                # 优先使用传入的old_input参数
                old_input_final = old_input.strip()
                log_info("MANAGER", f"[INPUT_MERGE-102] 使用传入的旧输入参数: '{old_input_final}'", indent=3)
            elif self.accumulated_input and self.accumulated_input.strip():
                # 其次使用accumulated_input
                old_input_final = self.accumulated_input.strip()
                log_info("MANAGER", f"[INPUT_MERGE-102] 找到累积输入: '{old_input_final}'", indent=3)
                # 清空累积输入
                self.accumulated_input = ""
                log_info("MANAGER", "[INPUT_MERGE-102] 累积输入已清空", indent=3)
            else:
                # 最后尝试从当前上下文获取
                log_info("MANAGER", "[INPUT_MERGE-102] 无传入参数和累积输入，尝试从当前上下文获取", indent=3)
                current_context = self.context_manager.get_current_context()
                log_info("MANAGER", f"[INPUT_MERGE-102] 获取上下文结果: context={'存在' if current_context else 'None'}, is_valid={current_context.is_valid if current_context else False}", indent=3)
                if current_context and current_context.is_valid:
                    old_input_final = current_context.user_input
                    log_info("MANAGER", f"[INPUT_MERGE-102] 从当前上下文获取旧输入: '{old_input_final}' (task_id={current_context.task_id})", indent=3)
                else:
                    if current_context:
                        log_info("MANAGER", f"[INPUT_MERGE-102] 当前上下文已失效（is_valid=False），无法获取旧输入", indent=3)
                    else:
                        log_info("MANAGER", "[INPUT_MERGE-102] 当前上下文为空，无旧输入", indent=3)
            
            old_input = old_input_final
            
            log_info("MANAGER", f"[INPUT_MERGE-103] 旧输入获取完成: '{old_input if old_input else '(空)'}'", indent=2)
            
            if old_input:
                log_info("MANAGER", "[INPUT_MERGE-104] 检测到旧输入，开始合并流程", indent=2)
                
                # 检查是否重复（避免相同输入重复合并）
                old_input_stripped = old_input.strip()
                user_input_stripped = user_input.strip()
                log_info("MANAGER", f"[INPUT_MERGE-104] 检查输入是否重复: 旧输入='{old_input_stripped}', 新输入='{user_input_stripped}'", indent=3)
                
                if old_input_stripped == user_input_stripped:
                    log_warn("MANAGER", f"[INPUT_MERGE-104] 检测到重复输入，跳过合并: '{user_input}'", indent=3)
                    # 取消当前任务，但不合并
                    log_info("MANAGER", "[INPUT_MERGE-104] 准备取消当前任务（重复输入）", indent=3)
                    self.context_manager.cancel_current_context()
                    log_info("MANAGER", "[INPUT_MERGE-104] 当前任务已取消，直接使用新输入（不合并）", indent=3)
                    # 直接使用新输入，不合并
                else:
                    # 合并输入（用空格连接）
                    log_info("MANAGER", "[INPUT_MERGE-105] 输入不重复，开始合并", indent=3)
                    merged_input = f"{old_input} {user_input}" if old_input else user_input
                    log_info("MANAGER", f"[INPUT_MERGE-105] 合并结果: '{old_input}' + '{user_input}' = '{merged_input}'", indent=3)
                    log_info("MANAGER", f"[INPUT_MERGE-105] 合并后输入长度: {len(merged_input)}字符", indent=3)
                    user_input = merged_input
                    
                    # 取消当前任务（合并时取消旧任务）
                    log_info("MANAGER", "[INPUT_MERGE-105] 准备取消当前任务（合并输入）", indent=3)
                    self.context_manager.cancel_current_context()
                    log_info("MANAGER", "[INPUT_MERGE-105] 当前任务已取消，将使用合并后的输入", indent=3)
            else:
                # 无旧输入或旧输入为空，直接使用新输入
                log_info("MANAGER", f"[INPUT_MERGE-106] 无待合并输入，直接使用新输入: '{user_input}'", indent=2)
            
            log_info("MANAGER", f"[INPUT_MERGE-107] 合并逻辑完成，最终输入: '{user_input}'", indent=2)
        
        # 创建新任务上下文（自动使旧任务失效）
        old_task_id = self.context_manager.get_current_task_id()
        context = self.context_manager.create_context(user_input=user_input)
        new_task_id = context.task_id
        
        # 检查任务ID是否变化，如果变化则重置性能计时器
        if old_task_id != new_task_id:
            log_info("MANAGER", f"[PERF_TIMER] 任务ID变化 ({old_task_id} -> {new_task_id})，重置性能计时器", indent=0)
            log_info("TIME", "任务ID变化，重置性能计时器", indent=1)
            self.performance_timer.reset()
        
        # 转换状态
        # 主系统: LISTENING → IDLE
        self.state_manager.transition_main_state(
            MainSystemState.IDLE,
            reason="开始处理用户输入"
        )
        
        # 副系统: None/PAUSED → PROCESSING
        current_sub_state = self.state_manager.get_sub_state()
        if current_sub_state == SubSystemState.PAUSED:
            self.state_manager.transition_sub_state(
                SubSystemState.PROCESSING,
                reason="开始处理用户输入（合并后）"
            )
        else:
            self.state_manager.transition_sub_state(
                SubSystemState.PROCESSING,
                reason="开始处理用户输入"
            )
        
        # 启动LLM+TTS处理线程（线程6）
        self._start_inference_flow(context)
        log_info("MANAGER", f"用户输入处理流程已启动: 任务ID={context.task_id}", indent=0)
    
    def _on_vad_speech_start(self):
        """VAD检测到语音开始回调（由线程2调用）"""
        # 首先检查ASR线程是否在运行（无论当前状态如何，避免重复启动）
        with self.asr_thread_lock:
            asr_thread_info = self.thread_manager.get_thread_info("ASR-Recognition")
            asr_thread_alive = asr_thread_info and asr_thread_info.is_alive and asr_thread_info.thread.is_alive()
            asr_recognizing = False
            if self.asr and hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
                with self.asr.state_lock:
                    asr_recognizing = self.asr.recognizing
        
        log_debug("MANAGER", f"检测到语音开始，ASR线程状态: 线程存活={asr_thread_alive}, 识别中={asr_recognizing}", indent=1)
        
        if asr_thread_alive and asr_recognizing:
            log_debug("MANAGER", "ASR线程正在运行，不重复启动", indent=0)
            return
        
        main_state = self.state_manager.get_main_state()
        sub_state = self.state_manager.get_sub_state()
        
        # 如果主系统状态为LISTENING且ASR线程不在运行，可能是异常情况，恢复状态
        if main_state == MainSystemState.LISTENING and not (asr_thread_alive and asr_recognizing):
            log_warn("MANAGER", "状态为LISTENING但ASR线程未运行，重置状态为IDLE", indent=0)
            self.state_manager.transition_main_state(MainSystemState.IDLE, "ASR线程未运行，恢复IDLE状态")
            # 更新main_state，因为状态已改变
            main_state = MainSystemState.IDLE
        
        # 检查是否可以启动ASR
        # 主系统状态为IDLE、ROBOT_SPEAKING时，或副系统状态为PROCESSING、SPEAKING、PAUSED时
        can_start_asr = (
            main_state in [MainSystemState.IDLE, MainSystemState.ROBOT_SPEAKING] or
            sub_state in [SubSystemState.PROCESSING, SubSystemState.SPEAKING, SubSystemState.PAUSED]
        )
        
        if not can_start_asr:
            log_debug("MANAGER", f"当前状态不允许启动ASR: 主系统={main_state.value}, 副系统={sub_state.value}", indent=0)
            return
        
        log_info("MANAGER", "检测到语音开始", indent=0)

        # 更新last_active_time（VAD检测到语音活动时立即更新，不受check_interval影响）
        self.last_active_time = time.time()
        log_debug("MANAGER", f"VAD检测到语音开始，更新last_active_time={self.last_active_time:.3f}", indent=1)
        
        # 场景1特殊处理: 如果主系统状态为IDLE且副系统状态为None，这是正常对话流程，不触发打断逻辑
        if main_state == MainSystemState.IDLE and sub_state == SubSystemState.NONE:
            log_debug("MANAGER", "场景1: 正常对话流程，不触发打断逻辑", indent=0)
            # 直接启动ASR线程，不设置pending_interrupt
            self._start_asr_thread()
            return
        
        # 如果副系统状态为SPEAKING或PROCESSING，设置pending_interrupt并启动延迟验证（场景2和场景3）
        if sub_state in [SubSystemState.SPEAKING, SubSystemState.PROCESSING]:
            log_debug("MANAGER", "场景2和场景3: 如果副系统状态为SPEAKING或PROCESSING，设置pending_interrupt并启动延迟验证", indent=0)
            # 场景2: 如果主系统状态为ROBOT_SPEAKING，更新为LISTENING
            if main_state == MainSystemState.ROBOT_SPEAKING:
                log_debug("MANAGER", "场景2: 更新主系统状态为LISTENING", indent=0)
                self.state_manager.transition_main_state(MainSystemState.LISTENING, "检测到可能打断，开始监听")
            # 场景3: 如果主系统状态为IDLE，更新为LISTENING
            elif main_state == MainSystemState.IDLE:
                log_debug("MANAGER", "场景3: 更新主系统状态为LISTENING", indent=0)
                self.state_manager.transition_main_state(MainSystemState.LISTENING, "检测到可能打断，开始监听")
            
            log_info("MANAGER", "[LOCK-009] _on_vad_speech_start() 检测到可能打断 正在获取 interrupt_lock", indent=0)
            with self.interrupt_lock:
                self.pending_interrupt = True
            
            # 记录新语音活动开始时间（用于判断"什么都没说过"）
            self.voice_activity_start_time = time.time()
            log_info("MANAGER", f"检测到可能打断，启动延迟验证（语音活动时间: {self.voice_activity_start_time:.3f}）", indent=0)
            # 启动延迟验证（通过定时器）
            self._pause_for_voice_check()
        
        # 启动ASR线程（线程4）
        self._start_asr_thread()
    
    def _on_vad_speech_active(self):
        """VAD持续检测到语音活动回调（由线程2调用）"""
        # 持续更新last_active时间，防止误判用户停止说话
        self.last_active_time = time.time()
        log_debug("MANAGER", f"VAD持续语音活动，更新last_active_time={self.last_active_time:.3f}", indent=1)

    def _on_vad_speech_end(self):
        """VAD检测到语音结束回调（由线程2调用）"""
        log_debug("MANAGER", "检测到语音结束", indent=0)
        # 注意：VAD_SPEECH_END事件已由VAD模块发送，PerformanceMonitor会处理
        # 注意：ASR的结束不是由VAD直接控制，而是由NO_SPEECH_THRESHOLD控制

        # 重置性能计时器（清除之前的所有时间戳）
        # 然后记录VAD结束时间作为新的起点
        log_info("MANAGER", "[PERF_TIMER] VAD结束，重置性能计时器并记录VAD结束时间", indent=1)
        if not self.performance_timer.is_paused_reset:
            if self.performance_timer.asr_recognition_end_time > 0:
                log_info("TIME", "ASR识别已完成，跳过重置", indent=1)
                self.performance_timer.record_vad_speech_end()
            else:
                log_info("TIME", "ASR识别未完成，重置性能计时器并记录VAD结束时间", indent=1)
                self.performance_timer.reset()
                log_info("TIME", "VAD检测的记录VAD结束时间", indent=1)
                self.performance_timer.record_vad_speech_end()
        
    
    def _on_asr_partial_result(self, event):
        """
        处理ASR部分识别结果（实时显示）
        
        Args:
            event: ASR_PARTIAL_RESULT事件
        """
        # 获取事件数据
        event_data = event.data if hasattr(event, 'data') else {}
        partial_text = event_data.get("partial_text", "")
        
        if partial_text:
            # 使用inline模式输出实时识别结果（同一行更新）
            log_info("ASR", f"[识别中] {partial_text}", indent=0, inline=True)
    
    def _on_playback_progress(self, event):
        """
        播放进度事件处理（用于实时更新已播放文本）
        
        参考DEVELOPMENT_LOG.md中的实现
        """
        # 获取事件数据
        event_data = event.data if hasattr(event, 'data') else {}
        task_id = event_data.get("task_id", 0)
        elapsed_time = event_data.get("elapsed_time", 0.0)
        current_sentence_text = event_data.get("current_sentence_text", "")
        current_sentence_position = event_data.get("current_sentence_position", 0)
        
        # 检查任务ID是否匹配
        current_task_id = self.context_manager.get_current_task_id()
        if task_id != current_task_id:
            return
        
        # 获取当前任务上下文
        context = self.context_manager.get_current_context()
        if not context or not context.is_valid:
            return
        
        # 首次播放时，检查播放时长是否达到阈值，达到时立即进行状态转换
        is_first_audio = context.get_extra_data("first_audio_state_transitioned", None) is None
        if is_first_audio and elapsed_time >= self.min_play_duration_to_record:
            # 播放时长达到阈值，进行状态转换
            current_sub = self.state_manager.get_sub_state()
            current_main = self.state_manager.get_main_state()
            
            # 检查是否已经转换过（避免重复转换）
            if current_sub != SubSystemState.SPEAKING:
                log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 播放进度事件：播放时长达到阈值({elapsed_time:.2f}秒 >= {self.min_play_duration_to_record:.2f}秒)，开始转换状态", indent=1)
                log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换前状态: 主={current_main.value}, 副={current_sub.value}", indent=1)
                
                # 副系统: PROCESSING → SPEAKING
                self.state_manager.transition_sub_state(
                    SubSystemState.SPEAKING,
                    reason=f"开始播放音频（播放时长{elapsed_time:.2f}秒达到阈值）"
                )
                # 主系统状态转换（仅在IDLE时转换，LISTENING时保持不变）
                main_state = self.state_manager.get_main_state()
                if main_state == MainSystemState.IDLE:
                    self.state_manager.transition_main_state(
                        MainSystemState.ROBOT_SPEAKING,
                        reason=f"开始播放音频（播放时长{elapsed_time:.2f}秒达到阈值）"
                    )
                # 注意：如果主系统状态是LISTENING，保持不变，等待ASR验证
                log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换后状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                
                
                # 标记状态已转换
                context.set_extra_data("first_audio_state_transitioned", True)
        
        # 句子跟踪机制（从TaskContext获取或初始化）
        completed_sentences = context.get_extra_data("completed_sentences", [])
        last_sentence_text = context.get_extra_data("last_sentence_text", "")
        
        # 检测句子切换
        if current_sentence_text != last_sentence_text:
            # 句子切换，将上一个句子标记为完成
            if last_sentence_text:
                completed_sentences.append(last_sentence_text)
            context.set_extra_data("last_sentence_text", current_sentence_text)
        
        # 检测句子完成
        if current_sentence_position >= len(current_sentence_text):
            # 当前句子播放完成
            if current_sentence_text and current_sentence_text not in completed_sentences:
                completed_sentences.append(current_sentence_text)
                context.set_extra_data("completed_sentences", completed_sentences)
                context.set_extra_data("last_sentence_text", "")
        
        # 计算已播放文本
        # 使用空格连接句子，保留每个句子的完整内容（包括标点符号）
        completed_text = "".join(completed_sentences)
        current_playing_text = completed_text + current_sentence_text[:current_sentence_position]
        
        # 更新TaskContext中的已播放文本
        context.set_extra_data("played_response_text", current_playing_text)
        
        # 输出实时播放文字日志（使用inline模式，同一行更新）
        if current_playing_text:
            log_info("TTS", f"[播放中] {current_playing_text}", indent=0, inline=True)
    
    def _start_asr_thread(self):
        """启动ASR识别线程（线程4）"""
        # 记录当前状态（用于调试）
        main_state = self.state_manager.get_main_state()
        sub_state = self.state_manager.get_sub_state()
        log_info("MANAGER", f"启动ASR线程，当前状态: main={main_state.value}, sub={sub_state.value}", indent=1)
        
        with self.asr_thread_lock:
            # 检查ASR线程是否已在运行
            asr_thread_info = self.thread_manager.get_thread_info("ASR-Recognition")
            if asr_thread_info:
                # 检查线程是否真的还在运行（不等待，直接检查）
                thread_actually_alive = asr_thread_info.thread.is_alive() if asr_thread_info.thread else False
                thread_info_is_alive = asr_thread_info.is_alive
                thread_id = asr_thread_info.thread.ident if asr_thread_info.thread else None
                
                log_info("MANAGER", f"检查ASR线程状态: thread_info.is_alive={thread_info_is_alive}, thread.is_alive()={thread_actually_alive}, thread_id={thread_id}", indent=1)
                
                if thread_actually_alive:
                    # 线程实际还在运行，检查ASR是否真的在识别
                    asr_recognizing = False
                    if self.asr and hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
                        with self.asr.state_lock:
                            asr_recognizing = self.asr.recognizing
                    
                    log_info("MANAGER", f"ASR状态检查: recognizing={asr_recognizing}", indent=1)
                    
                    if asr_recognizing:
                        log_info("MANAGER", "ASR线程已在运行且正在识别，跳过启动", indent=1)
                        return
                    else:
                        # 线程还在运行但ASR不在识别，说明线程可能正在清理中
                        # 允许启动新线程，ThreadManager会处理冲突（如果线程真的还在运行会抛出异常）
                        log_info("MANAGER", "ASR线程还在运行但ASR不在识别，可能是清理中，尝试启动新线程", indent=1)
                else:
                    # 线程已结束，但记录可能还在
                    # ThreadManager会在start_thread时自动清理，无需等待
                    log_info("MANAGER", "检测到已结束的ASR线程记录，ThreadManager将自动清理", indent=1)
            
            # 递增ASR任务ID
            self.asr_task_id += 1
            current_asr_task_id = self.asr_task_id
            
            # 通过ThreadManager启动ASR识别线程
            def asr_recognition_thread():
                """ASR识别线程（线程4）"""
                log_info("MANAGER", f"ASR识别线程启动（任务ID: {current_asr_task_id}）", indent=0)
                
                try:
                    # 检查任务是否被取消（在启动前检查）
                    if current_asr_task_id != self.asr_task_id:
                        log_warn("MANAGER", f"ASR任务 {current_asr_task_id} 在启动前被取消（当前任务ID: {self.asr_task_id}）", indent=0)
                        self.state_manager.transition_main_state(MainSystemState.IDLE, f"ASR任务 {current_asr_task_id} 被取消")
                        return
                    
                    # 设置ASR的当前任务ID（用于事件中）
                    if self.asr and hasattr(self.asr, 'current_task_id'):
                        self.asr.current_task_id = current_asr_task_id
                    
                    # 启动ASR识别（这会启动线程5：ASR内部识别线程）
                    if not self.asr or not hasattr(self.asr, 'start_recognition') or not self.asr.start_recognition():
                        log_error("MANAGER", f"启动ASR识别失败（任务ID: {current_asr_task_id}）", indent=0)
                        # 检查任务是否被取消
                        if current_asr_task_id != self.asr_task_id:
                            log_warn("MANAGER", f"ASR任务 {current_asr_task_id} 在启动失败后被取消（当前任务ID: {self.asr_task_id}）", indent=0)
                        else:
                            self.state_manager.transition_main_state(MainSystemState.IDLE, f"ASR启动失败（任务ID: {current_asr_task_id}）")
                        return
                    
                    # 等待ASR识别完成
                    # 在等待期间，定期检查任务是否被取消或系统是否停止
                    check_interval = 0.1  # 100ms检查一次
                    while True:
                        # 检查系统是否停止
                        if not self.running:
                            log_info("MANAGER", f"系统已停止，退出ASR识别线程（任务ID: {current_asr_task_id}）", indent=0)
                            # 停止ASR识别
                            if self.asr and hasattr(self.asr, 'stop_recognition'):
                                self.asr.stop_recognition()
                            return
                        
                        # 检查任务是否被取消
                        if current_asr_task_id != self.asr_task_id:
                            log_warn("MANAGER", f"ASR任务 {current_asr_task_id} 在识别过程中被取消（当前任务ID: {self.asr_task_id}）", indent=0)
                            # 停止ASR识别
                            if self.asr and hasattr(self.asr, 'stop_recognition'):
                                self.asr.stop_recognition()
                            self.state_manager.transition_main_state(MainSystemState.IDLE, f"ASR任务 {current_asr_task_id} 被取消")
                            return
                        
                        # 检查ASR是否还在识别
                        if self.asr and hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
                            with self.asr.state_lock:
                                if not self.asr.recognizing:
                                    # ASR识别已结束，退出循环
                                    break
                        else:
                            # ASR对象不存在或不支持状态检查，退出循环
                            break
                        
                        # 等待一段时间后再次检查
                        time.sleep(check_interval)
                    
                    # 更新ASR线程活跃标志
                    self.asr_thread_active = False
                    
                except Exception as e:
                    log_error("MANAGER", f"ASR识别线程错误（任务ID: {current_asr_task_id}）: {e}", indent=0)
                    # 检查任务是否被取消
                    if current_asr_task_id != self.asr_task_id:
                        log_warn("MANAGER", f"ASR任务 {current_asr_task_id} 在错误后被取消（当前任务ID: {self.asr_task_id}）", indent=0)
                    else:
                        self.state_manager.transition_main_state(MainSystemState.IDLE, f"ASR识别线程错误（任务ID: {current_asr_task_id}）")
                    self.asr_thread_active = False
                finally:
                    log_info("MANAGER", f"ASR识别线程结束（任务ID: {current_asr_task_id}）", indent=0)
            
            # 启动ASR识别线程
            try:
                self.thread_manager.start_thread(
                    name="ASR-Recognition",
                    target=asr_recognition_thread,
                    context=None,
                    thread_type="on_demand",
                    daemon=True
                )
                log_info("MANAGER", f"启动ASR识别线程（任务ID: {current_asr_task_id}）", indent=0)
            except ValueError as e:
                # ThreadManager检测到线程冲突
                log_error("MANAGER", f"启动ASR识别线程失败: {e}", indent=0)
                # 再次检查线程状态
                asr_thread_info = self.thread_manager.get_thread_info("ASR-Recognition")
                if asr_thread_info:
                    thread_actually_alive = asr_thread_info.thread.is_alive() if asr_thread_info.thread else False
                    asr_recognizing = False
                    if self.asr and hasattr(self.asr, 'state_lock') and hasattr(self.asr, 'recognizing'):
                        with self.asr.state_lock:
                            asr_recognizing = self.asr.recognizing
                    log_error("MANAGER", f"线程冲突详情: thread.is_alive()={thread_actually_alive}, ASR.recognizing={asr_recognizing}", indent=1)
                return
            
            # 等待一小段时间，确认线程启动成功
            time.sleep(0.05)  # 50ms，给线程启动时间
            
            # 检查线程是否还在运行（如果立即失败，线程可能已经结束）
            asr_thread_info = self.thread_manager.get_thread_info("ASR-Recognition")
            if not asr_thread_info or not asr_thread_info.is_alive or not asr_thread_info.thread.is_alive():
                log_error("MANAGER", "ASR线程启动后立即失败", indent=0)
                return
            
            # 线程启动成功，更新主系统状态为LISTENING
            self.state_manager.transition_main_state(
                MainSystemState.LISTENING,
                f"VAD检测到语音开始（ASR任务ID: {current_asr_task_id}）"
            )
            self.asr_thread_active = True
    
    def _stop_asr_recognition(self):
        """停止ASR识别"""
        if not self.asr:
            return
        
        # 递增ASR任务ID，使正在运行的ASR线程检测到任务被取消
        with self.asr_thread_lock:
            self.asr_task_id += 1
            log_info("MANAGER", f"取消ASR任务（新ASR任务ID: {self.asr_task_id}）", indent=0)
        
        # 停止ASR识别（如果正在识别）
        if hasattr(self.asr, 'stop_recognition'):
            self.asr.stop_recognition()
        
        # 更新ASR线程活跃标志
        self.asr_thread_active = False
        
        # 更新主系统状态为IDLE
        self.state_manager.transition_main_state(MainSystemState.IDLE, "停止ASR识别")
    
    def _pause_for_voice_check(self):
        """
        延迟验证机制（检测到语音活动后延迟AUDIO_STOP_DELAY秒再暂停）
        
        根据THREAD_SCHEDULING.md：
        - 延迟验证期间，所有线程继续正常运行，完全不受影响
        - 持续检查用户是否还在说话
        - 如果检测到持续静音 → 判断为误判，取消打断
        - 如果持续说话 → 延迟验证通过，暂停播放或取消任务
        """
        def voice_check_timer_func():
            """延迟验证定时器函数"""
            check_interval = self.interrupt_check_interval
            elapsed_time = 0.0
            silent_start_time = None
            
            # 记录延迟验证开始时的状态，用于日志
            initial_sub_state = self.state_manager.get_sub_state()
            log_info("MANAGER", f"延迟验证开始，等待 {self.audio_stop_delay} 秒（初始状态: {initial_sub_state.value}）", indent=1)
            
            # 延迟期间持续检查
            while elapsed_time < self.audio_stop_delay:
                time.sleep(check_interval)
                elapsed_time += check_interval
                
                # 检查是否还在等待打断
                log_info("MANAGER", "[LOCK-010] _pause_for_voice_check() 延迟验证期间检查 正在获取 interrupt_lock", indent=2)
                with self.interrupt_lock:
                    if not self.pending_interrupt:
                        log_info("MANAGER", "延迟期间待打断标记已取消，误判", indent=2)
                        return
                
                # 检查VAD状态（是否还在说话）
                if self.vad:
                    # 尝试获取VAD状态（兼容不同的VAD实现）
                    if hasattr(self.vad, 'get_state'):
                        is_speaking = self.vad.get_state()
                    elif hasattr(self.vad, 'is_speaking'):
                        is_speaking = self.vad.is_speaking
                    else:
                        is_speaking = False
                    
                    if not is_speaking:
                        # 检测到静音
                        if silent_start_time is None:
                            silent_start_time = time.time()
                        else:
                            # 如果静音持续时间足够长，认为是误判
                            silent_duration = time.time() - silent_start_time
                            if silent_duration >= self.min_silent_duration:
                                log_info("MANAGER", "[LOCK-011] _pause_for_voice_check() 延迟期间检测到持续静音 正在获取 interrupt_lock", indent=2)
                                with self.interrupt_lock:
                                    self.pending_interrupt = False
                                log_info("MANAGER", f"延迟期间检测到持续静音 {silent_duration:.2f}秒，判断为误判", indent=2)
                                return
                    else:
                        # 恢复语音活动，重置静音计时
                        silent_start_time = None
                
                # 再次检查状态（可能在延迟期间状态已变化）
                sub_state = self.state_manager.get_sub_state()
                if sub_state not in [SubSystemState.SPEAKING, SubSystemState.PROCESSING, SubSystemState.PAUSED]:
                    # 状态已变为NONE或其他状态，取消延迟验证
                    log_debug("MANAGER", f"延迟验证期间状态已变化为{sub_state.value}，取消延迟验证", indent=2)
                    return
            
            # 延迟时间到，检查最终状态
            log_info("MANAGER", "[LOCK-012] _pause_for_voice_check() 延迟时间到检查最终状态 正在获取 interrupt_lock", indent=2)
            with self.interrupt_lock:
                if not self.pending_interrupt:
                    log_info("MANAGER", "延迟结束时待打断标记已取消，误判", indent=2)
                    return
                
                # 检查当前是否为静音状态
                if self.vad:
                    if hasattr(self.vad, 'get_state'):
                        is_speaking = self.vad.get_state()
                    elif hasattr(self.vad, 'is_speaking'):
                        is_speaking = self.vad.is_speaking
                    else:
                        is_speaking = False
                    
                    if not is_speaking:
                        log_info("MANAGER", "延迟结束时检测到静音状态，判断为误判", indent=2)
                        self.pending_interrupt = False
                        return
                
                # 延迟验证通过，暂停播放或取消任务
                sub_state = self.state_manager.get_sub_state()
                main_state = self.state_manager.get_main_state()
                
                # 如果状态从PROCESSING变为SPEAKING，记录日志
                if initial_sub_state == SubSystemState.PROCESSING and sub_state == SubSystemState.SPEAKING:
                    log_info("MANAGER", "延迟验证期间状态从PROCESSING变为SPEAKING（TTS已开始播放），按SPEAKING处理（场景2）", indent=2)
                
                # 发布打断检测事件
                if self.event_bus:
                    current_task_id = self.context_manager.get_current_task_id()
                    event = create_event(
                        EventType.INTERRUPT_DETECTED,
                        data={
                            "task_id": current_task_id,
                            "sub_state": sub_state.value,
                            "initial_sub_state": initial_sub_state.value
                        },
                        source="DialogueManager"
                    )
                    self.event_bus.publish(event)
                
                if sub_state == SubSystemState.SPEAKING:
                    # 场景2: 正在播放，暂停播放
                    log_info("MANAGER", "延迟验证通过，暂停播放等待ASR验证", indent=2)
                    
                    if self.audio_player:
                        current_task_id = self.context_manager.get_current_task_id()
                        # 检查播放器实际状态
                        is_paused = hasattr(self.audio_player, 'is_paused') and self.audio_player.is_paused()
                        is_playing = hasattr(self.audio_player, 'is_playing') and self.audio_player.is_playing()
                        log_info("MANAGER", f"播放器状态: is_paused={is_paused}, is_playing={is_playing}", indent=2)
                        
                        pause_success = False
                        if is_paused:
                            # 播放器已经暂停（由audio_player.play()内部暂停），直接标记成功
                            pause_success = True
                            log_info("MANAGER", "播放器已暂停，直接标记成功", indent=2)
                        elif is_playing and hasattr(self.audio_player, 'pause'):
                            # 播放器还在播放，尝试暂停
                            log_info("MANAGER", "尝试暂停播放器...", indent=2)
                            pause_success = self.audio_player.pause(task_id=current_task_id)
                            log_info("MANAGER", f"暂停结果: {pause_success}", indent=2)
                        else:
                            log_warn("MANAGER", "播放器不在播放或暂停状态，跳过暂停", indent=2)
                        
                        if pause_success:
                            # 暂停成功，清除标志并更新状态
                            log_info("MANAGER", "[LOCK-013] _pause_for_voice_check() 场景2-暂停播放成功 清除 pending_interrupt", indent=2)
                            self.pending_interrupt = False
                            # 记录暂停前的副系统状态，用于场景判断
                            self.paused_sub_state = sub_state
                            self.state_manager.transition_sub_state(
                                SubSystemState.PAUSED,
                                reason="延迟验证通过，暂停播放"
                            )
                        else:
                            # 暂停失败，保留pending_interrupt标志，让后续逻辑处理
                            log_warn("MANAGER", "暂停播放失败，保留打断标记", indent=2)
                            self.state_manager.transition_sub_state(SubSystemState.NONE, "延迟验证通过但播放器暂停失败")
                    else:
                        log_warn("MANAGER", "播放器未初始化，无法暂停", indent=2)
                        # 保留pending_interrupt标志，但状态转为NONE
                        log_info("MANAGER", "[LOCK-014] _pause_for_voice_check() 场景2-播放器未初始化 清除 pending_interrupt", indent=2)
                        self.pending_interrupt = False
                        self.state_manager.transition_sub_state(SubSystemState.NONE, "延迟验证通过但播放器未初始化")
                elif sub_state == SubSystemState.PROCESSING:
                    # 检查播放线程是否已启动
                    # 如果播放线程已启动，说明音频已准备好，应该按SPEAKING处理（暂停播放）
                    # 如果播放线程未启动，说明还在处理中，应该按PROCESSING处理（延迟取消）
                    player_started = self._get_player_thread_started_safe()
                    log_info("MANAGER", f"[状态检查] player_thread_started={player_started}", indent=2)
                    if player_started:
                        # 播放线程已启动，音频已准备好，按SPEAKING处理（场景2：暂停播放）
                        log_info("MANAGER", "延迟验证通过，播放线程已启动，按SPEAKING处理（暂停播放）", indent=2)
                        
                        if self.audio_player:
                            current_task_id = self.context_manager.get_current_task_id()
                            is_paused = hasattr(self.audio_player, 'is_paused') and self.audio_player.is_paused()
                            is_playing = hasattr(self.audio_player, 'is_playing') and self.audio_player.is_playing()
                            
                            pause_success = False
                            if is_paused:
                                # 播放器已经暂停，直接标记成功
                                pause_success = True
                            elif is_playing and hasattr(self.audio_player, 'pause'):
                                # 播放器还在播放，尝试暂停
                                pause_success = self.audio_player.pause(task_id=current_task_id)
                            else:
                                log_warn("MANAGER", "播放器不在播放或暂停状态，跳过暂停", indent=2)
                            
                            if pause_success:
                                # 暂停成功，清除标志并更新状态
                                log_info("MANAGER", "[LOCK-015] _pause_for_voice_check() 场景2变体-暂停播放成功 清除 pending_interrupt", indent=2)
                                self.pending_interrupt = False
                                self.paused_sub_state = sub_state
                                self.state_manager.transition_sub_state(
                                    SubSystemState.PAUSED,
                                    reason="延迟验证通过，暂停播放"
                                )
                            else:
                                # 暂停失败，保留pending_interrupt标志，让后续逻辑处理
                                log_warn("MANAGER", "暂停播放失败，保留打断标记", indent=2)
                                self.state_manager.transition_sub_state(SubSystemState.NONE, "延迟验证通过但播放器暂停失败")
                        else:
                            log_warn("MANAGER", "播放器未初始化，无法暂停", indent=2)
                            # 保留pending_interrupt标志，但状态转为NONE
                            log_info("MANAGER", "[LOCK-016] _pause_for_voice_check() 场景2变体-播放器未初始化 清除 pending_interrupt", indent=2)
                            self.pending_interrupt = False
                            self.state_manager.transition_sub_state(SubSystemState.NONE, "延迟验证通过但播放器未初始化")
                    else:
                        # 播放线程未启动，还在处理中，采用延迟取消策略（场景3：延迟取消任务）
                        log_info("MANAGER", "延迟验证通过，标记为延迟取消状态等待ASR验证（LLM继续生成，TTS不播放）", indent=2)
                        # 记录暂停前的副系统状态，用于场景判断
                        self.paused_sub_state = sub_state
                        # 标记当前任务可能"什么都没说过"（需要在ASR结果处理时验证）
                        self.nothing_said_yet = True
                        # 只更新状态为PAUSED，不调用_cancel_current_task（不递增task_id）
                        # 这样LLM和TTS线程可以继续运行，等待ASR结果
                        # 播放线程会检测到PAUSED状态，在开始播放前等待状态变化
                        self.state_manager.transition_sub_state(
                            SubSystemState.PAUSED,
                            reason="延迟取消等待ASR验证（播放线程未启动，LLM继续）"
                        )
        
        # 启动延迟验证定时器
        self.voice_check_timer = threading.Timer(self.audio_stop_delay, voice_check_timer_func)
        self.voice_check_timer.start()
    
    def _cancel_current_task(self):
        """
        取消当前任务
        
        根据THREAD_SCHEDULING.md：
        - 通过 ContextManager 取消当前任务（自动使上下文失效）
        - 检查线程启动状态（如果线程还没启动，直接取消）
        - 转换副系统状态: PROCESSING/SPEAKING → PAUSED
        - 停止音频播放
        - 检查播放时长，决定是否记录历史
        """
        # 获取当前任务上下文
        current_context = self.context_manager.get_current_context()
        if not current_context or not current_context.is_valid:
            log_debug("MANAGER", "当前没有活动的任务，无需取消", indent=0)
            return
        
        # 取消当前任务（使上下文失效）
        self.context_manager.cancel_current_context()
        
        # 检查线程启动状态（如果线程还没启动，直接取消）
        player_started = self._get_player_thread_started_safe()
        if not self.tts_thread_started and not player_started:
            log_info("MANAGER", "TTS和播放线程都未启动，任务已直接取消", indent=1)
        else:
            # 重置线程启动标志
            self.tts_thread_started = False
            self._set_player_thread_started_safe(False)
        
        # 转换副系统状态: PROCESSING/SPEAKING → PAUSED
        sub_state = self.state_manager.get_sub_state()
        if sub_state == SubSystemState.PROCESSING or sub_state == SubSystemState.SPEAKING:
            self.state_manager.transition_sub_state(
                SubSystemState.PAUSED,
                reason="检测到用户打断，取消当前任务"
            )
        
        # 停止音频播放
        if self.audio_player:
            task_id = current_context.task_id
            log_info("MANAGER", f"[AUDIO_STOP-002] _cancel_current_task() 准备调用 audio_player.stop(task_id={task_id})", indent=1)
            log_info("MANAGER", f"[AUDIO_STOP-002] 调用前状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
            stop_result = self.audio_player.stop(task_id=task_id)
            log_info("MANAGER", f"[AUDIO_STOP-002] audio_player.stop() 返回: {stop_result}", indent=1)
        
        # 检查播放时长，决定是否记录历史
        # 根据THREAD_SCHEDULING.md场景2，当播放时被打断，如果播放时长 >= MIN_PLAY_DURATION_TO_RECORD，应记录已播放内容
        sub_state = self.state_manager.get_sub_state()
        if sub_state == SubSystemState.SPEAKING:
            # 正在播放时被打断，检查播放时长
            if self.audio_player:
                progress = self.audio_player.get_progress()
                played_duration = progress.get("elapsed_time", 0.0)
                
                if played_duration >= self.min_play_duration_to_record:
                    # 播放时长达到阈值，记录已播放内容到对话历史
                    user_input = current_context.user_input
                    played_text = current_context.get_extra_data("played_response_text", "")
                    if not played_text:
                        # 如果没有实时更新，使用完整回答文本
                        played_text = current_context.response_text
                    
                    # 记录到对话历史
                    with self.history_lock:
                        self.conversation_history.append({"role": "user", "content": user_input})
                        if played_text:
                            # 去除前导空白字符（包括换行符、空格等）
                            played_text_trimmed = played_text.lstrip()
                            self.conversation_history.append({"role": "assistant", "content": played_text_trimmed})
                        # 限制历史记录长度
                        self._limit_history_length()
                    
                    log_info("MANAGER", f"任务取消时已记录对话历史（播放时长: {played_duration:.2f}秒）", indent=1)
                    # 任务被打断但已记录到对话历史，清空累积输入（避免已处理的输入在后续合并中被重复使用）
                    if self.accumulated_input:
                        log_info("MANAGER", f"清空累积输入（任务打断但已记录）: '{self.accumulated_input}'", indent=1)
                        self.accumulated_input = ""
        # 如果副系统状态为 PROCESSING，说明还未开始播放，不需要记录历史
        
        log_info("MANAGER", f"任务已取消: ID={current_context.task_id}", indent=0)
    
    def _start_inference_flow(self, context: TaskContext):
        """
        启动LLM+TTS处理流程（线程6）
        
        Args:
            context: 任务上下文
        """
        def inference_flow(context: TaskContext):
            """LLM+TTS处理流程（线程6）"""
            try:
                # 检查任务有效性
                context.check_valid()
                
                # 重置线程启动标志
                self.tts_thread_started = False
                self._set_player_thread_started_safe(False)
                
                # 获取用户输入
                user_input = context.user_input
                
                # 获取LLM和TTS引擎
                if not self.llm or not self.tts_engine:
                    log_error("MANAGER", "LLM或TTS引擎未初始化", indent=1)
                    return
                
                # 创建队列
                sentence_queue = queue.Queue(maxsize=self.sentence_queue_maxsize)
                audio_play_queue = queue.Queue(maxsize=self.audio_play_queue_maxsize)
                
                # 存储到TaskContext
                context.set_extra_data("sentence_queue", sentence_queue)
                context.set_extra_data("audio_play_queue", audio_play_queue)
                context.set_extra_data("stream_generation_done", False)
                context.set_extra_data("tts_generation_done", False)
                
                # 启动LLM流式输出线程（线程7）
                def llm_generator_thread(context: TaskContext):
                    """LLM流式输出线程（线程7）"""
                    try:
                        # 检查任务有效性
                        context.check_valid()
                        
                        # 跟踪是否是第一句
                        first_sentence = True
                        
                        # 调用LLM流式生成
                        def on_sentence_callback(sentence: str):
                            """句子回调函数"""
                            nonlocal first_sentence
                            try:
                                context.check_valid()
                                sentence_queue.put(sentence, timeout=1.0)
                                
                                # 记录第一句生成时间
                                if first_sentence:
                                    log_info("TIME", "记录LLM第一句生成时间", indent=2)
                                    self.performance_timer.record_llm_first_sentence()
                                    first_sentence = False
                                
                                # 第一个句子生成时启动TTS生成线程（线程8）
                                if not self.tts_thread_started:
                                    self._start_tts_generator_thread(context, sentence_queue, audio_play_queue)
                                    self.tts_thread_started = True
                            except TaskCancelledException:
                                # 任务被取消，停止生成
                                pass
                            except Exception as e:
                                log_error("MANAGER", f"句子回调错误: {e}", indent=2)
                        
                        full_response_text = self.llm.generate_stream(
                            user_input=user_input,
                            task_id=context.task_id,
                            on_sentence_callback=on_sentence_callback
                        )
                        
                        # 更新TaskContext
                        context.update_response(full_response_text)
                        context.set_extra_data("stream_generation_done", True)
                    except TaskCancelledException:
                        log_info("MANAGER", "LLM生成被取消", indent=1)
                    except Exception as e:
                        log_error("MANAGER", f"LLM生成错误: {e}", indent=1)
                        import traceback
                        log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=2)
                
                # 启动LLM线程
                self.thread_manager.start_thread(
                    name=f"LLM-Generator-{context.task_id}",
                    target=llm_generator_thread,
                    context=context,
                    thread_type="on_demand",
                    daemon=True
                )
                
                # 等待所有线程完成
                # 这里需要等待LLM、TTS、播放线程都完成
                # 由于线程都是daemon，这里只需要等待播放完成即可
                # 播放完成会在_audio_player_thread中处理
                
            except TaskCancelledException:
                log_info("MANAGER", "推理流程被取消", indent=1)
            except Exception as e:
                log_error("MANAGER", f"推理流程错误: {e}", indent=1)
                import traceback
                log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=2)
        
        # 记录LLM线程开始时间
        log_info("TIME", "记录LLM线程开始时间", indent=1)
        self.performance_timer.record_llm_thread_start()

        # 启动推理流程线程
        self.thread_manager.start_thread(
            name=f"LLM+TTS-{context.task_id}",
            target=inference_flow,
            context=context,
            thread_type="on_demand",
            daemon=True
        )
        log_info("MANAGER", f"LLM+TTS处理流程已启动: 任务ID={context.task_id}", indent=0)
    
    def _start_tts_generator_thread(self, context: TaskContext, sentence_queue: queue.Queue, audio_play_queue: queue.Queue):
        """
        启动TTS生成线程（线程8）
        
        Args:
            context: 任务上下文
            sentence_queue: 句子队列
            audio_play_queue: 音频播放队列
        """
        def tts_generator_thread(context: TaskContext):
            """TTS生成线程（线程8）"""
            try:
                # 检查任务有效性
                context.check_valid()
                
                # 记录TTS线程开始时间
                log_info("TIME", "记录TTS线程开始时间", indent=1)
                self.performance_timer.record_tts_thread_start()
                
                # 从配置判断TTS引擎类型（是否支持流式）
                engine_config_key = getattr(self, 'tts_engine_type', 'edge_tts')
                is_streaming = self.config.get(f"tts.engines.{engine_config_key}.is_streaming", False)
                
                if is_streaming:
                    # 流式模式（CosyVoice3）
                    # TODO: 实现流式TTS生成
                    # 
                    # 根据THREAD_SCHEDULING.md，流式模式应该实现以下功能：
                    # 1. 直接对接LLM流式输出，不等待完整句子
                    # 2. 实时接收LLM生成的文本token流
                    # 3. 累积文本到一定长度（如10-20字符）或时间间隔后生成音频块
                    # 4. 调用tts.generate_stream()获取音频块生成器，遍历音频块：
                    #    - 首个块：转换并放入队列（包含累积文本）→ 启动播放线程
                    #    - 后续块：转换并连续放入队列（文本为空）
                    # 5. 每个块生成前后检查task_id，支持任务取消
                    # 6. 实现真正的pipeline模式：LLM、TTS、播放三者并行工作
                    #
                    # 注意：当前实现使用非流式模式，需要完整实现流式TTS生成逻辑
                    log_info("MANAGER", f"使用流式TTS模式（{engine_config_key}）- 待实现", indent=1)
                    # 暂时使用非流式模式作为fallback
                    is_streaming = False
                else:
                    # 非流式模式（EdgeTTS）
                    log_info("MANAGER", f"使用非流式TTS模式（{engine_config_key}）", indent=1)
                    
                    # 从句子队列获取句子并生成TTS
                    first_audio = True
                    while True:
                        try:
                            context.check_valid()
                            
                            # 从队列获取句子（带超时）
                            try:
                                sentence = sentence_queue.get(timeout=0.1)
                            except queue.Empty:
                                # 检查是否LLM生成完成
                                if context.get_extra_data("stream_generation_done", False):
                                    # 检查队列是否为空
                                    if sentence_queue.empty():
                                        break
                                continue
                            
                            # 生成TTS音频
                            # 记录TTS生成开始时间（仅第一句）
                            if first_audio:
                                log_info("TIME", "记录TTS生成开始时间", indent=2)
                                self.performance_timer.record_tts_start()
                            
                            # 调用TTS生成，获取耗时信息（仅第一句）
                            if first_audio:
                                result = self.tts_engine.generate(
                                    text=sentence,
                                    task_id=context.task_id,
                                    return_timing=True
                                )
                                if result and isinstance(result, tuple):
                                    audio_segment, generation_time, processing_time = result
                                    # 记录第一句的生成和处理时间
                                    log_info("TIME", f"记录TTS生成时间: {generation_time*1000:.1f}ms", indent=2)
                                    self.performance_timer.record_tts_generation_time(generation_time)
                                    log_info("TIME", f"记录TTS处理时间: {processing_time*1000:.1f}ms", indent=2)
                                    self.performance_timer.record_tts_processing_time(processing_time)
                                    log_info("TIME", "记录TTS生成结束时间", indent=2)
                                    self.performance_timer.record_tts_end()
                                else:
                                    audio_segment = result
                            else:
                                audio_segment = self.tts_engine.generate(
                                    text=sentence,
                                    task_id=context.task_id
                                )
                            
                            if audio_segment:
                                # 放入播放队列
                                audio_play_queue.put((audio_segment, sentence), timeout=1.0)
                                
                                # 第一个音频生成后启动播放线程（线程9）
                                if first_audio:
                                    self._start_audio_player_thread(context, audio_play_queue)
                                    self._set_player_thread_started_safe(True)
                                    first_audio = False
                        except TaskCancelledException:
                            break
                        except queue.Full:
                            log_warn("MANAGER", "音频播放队列已满，等待空间", indent=2)
                            time.sleep(0.1)
                            continue
                        except Exception as e:
                            log_error("MANAGER", f"TTS生成错误: {e}", indent=2)
                            continue
                    
                    context.set_extra_data("tts_generation_done", True)
            except TaskCancelledException:
                log_info("MANAGER", "TTS生成被取消", indent=1)
            except Exception as e:
                log_error("MANAGER", f"TTS生成线程错误: {e}", indent=1)
                import traceback
                log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=2)
        
        # 启动TTS线程
        self.thread_manager.start_thread(
            name=f"TTS-Generator-{context.task_id}",
            target=tts_generator_thread,
            context=context,
            thread_type="on_demand",
            daemon=True
        )
        log_info("MANAGER", f"TTS生成线程已启动: 任务ID={context.task_id}", indent=0)
    
    def _start_audio_player_thread(self, context: TaskContext, audio_play_queue: queue.Queue):
        """
        启动音频播放线程（线程9）
        
        Args:
            context: 任务上下文
            audio_play_queue: 音频播放队列
        """
        def audio_player_thread(context: TaskContext):
            """音频播放线程（线程9）"""
            try:
                # 检查任务有效性
                context.check_valid()
                
                # 首次播放前检查主系统状态
                main_state = self.state_manager.get_main_state()
                if main_state == MainSystemState.LISTENING:
                    # 如果主系统状态是LISTENING，等待ASR验证
                    # 这里需要等待ASR验证完成（通过pending_interrupt检查）
                    # 如果pending_interrupt为True且ASR验证通过，则不播放
                    # 如果pending_interrupt为False或ASR验证失败，则继续播放
                    log_info("MANAGER", "首次播放前检测到LISTENING状态，等待ASR验证", indent=1)
                    # 等待一小段时间，让ASR验证完成
                    time.sleep(0.1)
                    
                    # 检查是否应该继续播放（如果pending_interrupt为True，说明被打断，不播放）
                    log_info("MANAGER", "[LOCK-017] _start_audio_player_thread() 首次播放前检查打断 正在获取 interrupt_lock", indent=1)
                    with self.interrupt_lock:
                        if self.pending_interrupt:
                            log_info("MANAGER", "检测到用户打断，取消播放", indent=1)
                            return
                
                # 首次播放时转换状态
                first_audio = True
                played_duration = 0.0
                
                while True:
                    try:
                        context.check_valid()
                        
                        # 从队列获取音频（带超时）
                        try:
                            audio_item = audio_play_queue.get(timeout=0.1)
                            if isinstance(audio_item, tuple):
                                audio_segment, sentence_text = audio_item
                            else:
                                audio_segment = audio_item
                                sentence_text = ""
                        except queue.Empty:
                            # 检查是否所有音频都已播放完成
                            # 根据THREAD_SCHEDULING.md，播放完成必须同时满足以下所有条件：
                            # 1. LLM生成完成
                            stream_done = context.get_extra_data("stream_generation_done", False)
                            # 2. TTS生成完成
                            tts_done = context.get_extra_data("tts_generation_done", False)
                            # 3. 播放队列为空
                            queue_empty = audio_play_queue.empty()
                            # 4. 音频播放器不在播放
                            is_still_playing = self.audio_player.is_playing() if self.audio_player else False
                            # 5. 至少播放过第一个音频
                            if stream_done and tts_done and queue_empty and not is_still_playing and not first_audio:
                                # 所有音频都已播放完成
                                break
                            continue
                        
                        # 首次播放时，标记需要延迟状态转换（通过播放进度事件触发）
                        if first_audio:
                            log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] Audio-Player首次播放，将通过播放进度事件在播放时长达到阈值({self.min_play_duration_to_record:.2f}秒)后转换状态", indent=1)
                            # 初始化标记，表示首次播放状态转换还未完成
                            context.set_extra_data("first_audio_state_transitioned", None)
                            # 记录第一个音频播放时间（在实际播放开始之前）
                            first_audio_play_time_recorded = context.get_extra_data("first_audio_play_time_recorded", False)
                            if not first_audio_play_time_recorded:
                                log_info("TIME", "记录第一个音频播放时间", indent=2)
                                self.performance_timer.record_audio_play()
                                # 输出耗时分析（播放时间已在首次播放时记录）
                                log_info("TIME", "计算并输出响应耗时分析", indent=2)
                                self.performance_timer.calculate_and_output()
                                context.set_extra_data("first_audio_play_time_recorded", True)
                        
                        # 播放音频
                        def on_play_complete(was_interrupted: bool, interrupt_reason):
                            """播放完成回调"""
                            nonlocal played_duration, first_audio
                            
                            # 累加播放时长
                            if not was_interrupted:
                                # 播放未被打断，累加完整音频时长
                                audio_duration = len(audio_segment) / 1000.0
                                played_duration += audio_duration
                            else:
                                # 播放被打断，使用get_progress()获取实际播放时长
                                # 注意：get_progress()返回的是当前音频段的播放进度，不是累计的
                                # 但在这个回调中，我们只需要累加当前音频段的实际播放时长
                                if self.audio_player:
                                    progress = self.audio_player.get_progress()
                                    actual_elapsed = progress.get("elapsed_time", 0.0)
                                    # 如果get_progress()返回的时长大于0，累加实际播放时长
                                    # 否则累加完整音频时长（作为fallback）
                                    if actual_elapsed > 0:
                                        played_duration += actual_elapsed
                                        log_info("MANAGER", f"播放被打断，累加实际播放时长: {actual_elapsed:.2f}秒", indent=2)
                                    else:
                                        # fallback：使用完整音频时长
                                        audio_duration = len(audio_segment) / 1000.0
                                        played_duration += audio_duration
                                        log_info("MANAGER", f"播放被打断，get_progress()返回0，使用完整音频时长: {audio_duration:.2f}秒", indent=2)
                            
                            # 如果首次播放时状态转换还未完成（播放时长不足阈值就被打断），检查是否需要转换
                            if first_audio:
                                first_audio_state_transitioned = context.get_extra_data("first_audio_state_transitioned", None)
                                if first_audio_state_transitioned is None:
                                    # 状态转换还未完成，检查播放时长
                                    if was_interrupted and self.audio_player:
                                        # 播放被打断，使用get_progress()获取实际播放时长
                                        progress = self.audio_player.get_progress()
                                        first_audio_play_duration = progress.get("elapsed_time", 0.0)
                                        if first_audio_play_duration == 0:
                                            # fallback：使用完整音频时长
                                            first_audio_play_duration = len(audio_segment) / 1000.0
                                    else:
                                        # 播放完成，使用完整音频时长
                                        first_audio_play_duration = len(audio_segment) / 1000.0
                                    
                                    # 检查播放时长是否达到阈值
                                    if first_audio_play_duration >= self.min_play_duration_to_record:
                                        # 播放时长达到阈值，进行状态转换
                                        current_sub = self.state_manager.get_sub_state()
                                        current_main = self.state_manager.get_main_state()
                                        log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 播放完成/打断时检查：播放时长达到阈值({first_audio_play_duration:.2f}秒 >= {self.min_play_duration_to_record:.2f}秒)，开始转换状态", indent=1)
                                        log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换前状态: 主={current_main.value}, 副={current_sub.value}", indent=1)
                                        
                                        # 副系统: PROCESSING → SPEAKING
                                        self.state_manager.transition_sub_state(
                                            SubSystemState.SPEAKING,
                                            reason=f"开始播放音频（播放时长{first_audio_play_duration:.2f}秒达到阈值）"
                                        )
                                        # 主系统状态转换（仅在IDLE时转换，LISTENING时保持不变）
                                        main_state = self.state_manager.get_main_state()
                                        if main_state == MainSystemState.IDLE:
                                            self.state_manager.transition_main_state(
                                                MainSystemState.ROBOT_SPEAKING,
                                                reason=f"开始播放音频（播放时长{first_audio_play_duration:.2f}秒达到阈值）"
                                            )
                                        # 注意：如果主系统状态是LISTENING，保持不变，等待ASR验证
                                        log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换后状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                                        
                                        # 输出耗时分析（播放时间已在首次播放时记录）
                                        log_info("TIME", "计算并输出响应耗时分析", indent=2)
                                        self.performance_timer.calculate_and_output()
                                        
                                        # 标记状态已转换
                                        context.set_extra_data("first_audio_state_transitioned", True)
                                    else:
                                        # 播放时长不足阈值，不进行状态转换
                                        log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 播放时长不足阈值({first_audio_play_duration:.2f}秒 < {self.min_play_duration_to_record:.2f}秒)，不转换状态（保持PROCESSING）", indent=1)
                                        # 标记状态转换已完成（虽然未转换，但已检查过）
                                        context.set_extra_data("first_audio_state_transitioned", False)
                                
                                first_audio = False
                        
                        # 启动播放
                        was_completed = self.audio_player.play(
                            audio_segment=audio_segment,
                            task_id=context.task_id,
                            on_complete_callback=on_play_complete,
                            current_sentence_text=sentence_text
                        )
                        
                        # 检查是否被打断
                        if not was_completed:
                            log_info("MANAGER", "播放被中断", indent=1)
                            break
                        
                        # 检查任务是否被取消
                        context.check_valid()
                        
                    except TaskCancelledException:
                        log_info("MANAGER", "播放被取消", indent=1)
                        break
                    except Exception as e:
                        log_error("MANAGER", f"播放错误: {e}", indent=2)
                        continue
                
                # 播放完成，转换状态
                # 副系统: SPEAKING → None
                current_sub = self.state_manager.get_sub_state()
                current_main = self.state_manager.get_main_state()
                log_info("MANAGER", f"[STATE_TRANS-AUDIO_THREAD] Audio-Player线程结束，准备转换状态", indent=1)
                log_info("MANAGER", f"[STATE_TRANS-AUDIO_THREAD] 当前状态: 主={current_main.value}, 副={current_sub.value}", indent=1)
                self.state_manager.transition_sub_state(
                    SubSystemState.NONE,
                    reason="播放完成"
                )
                # 主系统: ROBOT_SPEAKING → IDLE
                self.state_manager.transition_main_state(
                    MainSystemState.IDLE,
                    reason="播放完成"
                )
                log_info("MANAGER", f"[STATE_TRANS-AUDIO_THREAD] 状态转换完成: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                
                # 播放完成，输出换行以保持日志清晰（因为之前使用了inline模式）
                print()  # 输出换行
                
                # 记录对话历史（如果播放时长>=MIN_PLAY_DURATION_TO_RECORD）
                # 检查是否已经被打断处理逻辑记录过
                history_recorded = context.get_extra_data("history_recorded_by_interrupt", False)
                # 注意：played_duration在on_play_complete回调中累加，无论是否被打断都会正确累加
                if not history_recorded and played_duration >= self.min_play_duration_to_record:
                    # 获取用户输入和播放内容
                    user_input = context.user_input
                    # 优先使用完整回答文本（包含完整标点符号），而不是实时更新的played_response_text
                    # 因为played_response_text可能因为句子拼接问题导致标点丢失或重复
                    played_text = context.response_text
                    if not played_text:
                        # 如果没有完整回答文本，使用实时更新的文本作为fallback
                        played_text = context.get_extra_data("played_response_text", "")
                    
                    # 记录到对话历史
                    with self.history_lock:
                        self.conversation_history.append({"role": "user", "content": user_input})
                        if played_text:
                            # 去除前导空白字符（包括换行符、空格等）
                            played_text_trimmed = played_text.lstrip()
                            self.conversation_history.append({"role": "assistant", "content": played_text_trimmed})
                        # 限制历史记录长度
                        self._limit_history_length()
                    
                    log_info("MANAGER", f"对话历史已记录（播放时长: {played_duration:.2f}秒）", indent=0)
                    # 任务成功完成并记录到对话历史，清空累积输入（避免已处理的输入在后续合并中被重复使用）
                    if self.accumulated_input:
                        log_info("MANAGER", f"清空累积输入（任务完成）: '{self.accumulated_input}'", indent=1)
                        self.accumulated_input = ""
                
                # 发布任务完成事件
                completed_event = create_event(
                    EventType.TASK_COMPLETED,
                    data={"task_id": context.task_id},
                    source="DialogueManager"
                )
                self.event_bus.publish(completed_event)
                
            except TaskCancelledException:
                log_info("MANAGER", "音频播放线程被取消", indent=1)
            except Exception as e:
                log_error("MANAGER", f"音频播放线程错误: {e}", indent=1)
                import traceback
                log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=2)
            finally:
                # 重置线程启动标志（确保线程结束时标志被重置）
                log_info("MANAGER", f"音频播放线程结束，重置 player_thread_started 标志", indent=1)
                self._set_player_thread_started_safe(False)
        
        # 启动播放线程
        self.thread_manager.start_thread(
            name=f"Audio-Player-{context.task_id}",
            target=audio_player_thread,
            context=context,
            thread_type="on_demand",
            daemon=True
        )
        log_info("MANAGER", f"音频播放线程已启动: 任务ID={context.task_id}", indent=0)
    
    def switch_persona(self, name: str) -> bool:
        """切换人设"""
        if self.persona_manager:
            success = self.persona_manager.set_persona(name)
            if success:
                # 发布人设切换事件
                persona_event = create_event(
                    EventType.LOG_INFO,  # 使用日志事件记录人设切换
                    data={"message": f"人设已切换为: {name}"},
                    source="DialogueManager"
                )
                self.event_bus.publish(persona_event)
                log_info("MANAGER", f"人设已切换为: {name}", indent=0)
            return success
        return False
    
    def get_current_persona_name(self) -> Optional[str]:
        """获取当前人设名称"""
        if self.persona_manager:
            return self.persona_manager.get_current_persona_name()
        return None
