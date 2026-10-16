"""
核心调度器模块（v3.0 架构）

DialogueManager 是语音对话系统的核心调度器，负责协调所有模块和线程的工作。
参考：THREAD_SCHEDULING.md, PROJECT_OUTLINE.md
"""

import threading
import time
import queue
import re
import traceback
from typing import Optional, Callable, Dict, Any, List
from enum import Enum

import numpy as np

from voice_chatbot.dialogue.state import MainSystemState, SubSystemState
from voice_chatbot.dialogue.conversation_history import ConversationHistory
from voice_chatbot.dialogue.state_manager import StateManager
from voice_chatbot.dialogue.context_manager import ContextManager
from voice_chatbot.dialogue.thread_manager import ThreadManager
from voice_chatbot.dialogue.task_context import TaskContext, TaskCancelledException
from voice_chatbot.dialogue.events import EventType, EventBus, EventFilter, create_event
from voice_chatbot.dialogue.endpoint_debouncer import EndpointDebouncer
from voice_chatbot.utils import log_info, log_warn, log_error, log_debug
from voice_chatbot.utils.logger import log_stream, log_stream_end
from voice_chatbot.utils.timer import PerformanceTimer


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

    麦克风模式：
    - 本地麦克风模式：使用 MicrophoneRecorder 直接采集音频
    - Web 麦克风模式：使用 WebMicrophoneServer 接收 WebSocket 实时音频流
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
        self.no_speech_threshold = config.get("asr.no_speech_threshold", 0.8)
        self.invalid_recognition_words = config.get(
            "asr.invalid_recognition_words",
            ["", "i", "嗯", "啊", "呃", "哦", "额", "嗯哼", "嘻嘻", "呵呵", "嗯呐", "啊这", "对对对", "好的好的", "好好好"])
        # 归一化后的无效词集合（去标点/空白、转小写），用于匹配标点恢复产生的变体（如 'i.' → 'i'、'嗯。' → '嗯'）
        self._normalized_invalid_words = {
            self._normalize_input_text(word).lower()
            for word in self.invalid_recognition_words if word
        }
        # 无实义附和词字符集合（从配置读取，用于过滤纯语气词识别结果如"嗯嗯""啊哦"）
        self._backchannel_chars = set(config.get("asr.backchannel_chars", "嗯啊哦呃额噢唉呀哈嘿诶欸唔"))
        self.thread_timeout = config.get("system.thread_timeout", 1.0)

        # 麦克风类型配置：根据 WebSocket 是否启用自动选择
        # system.websocket.enabled = True -> Web麦克风，False -> 本地麦克风
        ws_enabled = config.get("system.websocket.enabled", False)
        self.microphone_type = "web" if ws_enabled else "local"

        # 播放器类型配置：根据 WebSocket 是否启用自动选择
        # system.websocket.enabled = True -> Web播放器，False -> 本地播放器
        self.player_type = "web" if ws_enabled else "local"
        
        # 性能分析配置
        performance_analysis_config = config.get("logging.performance_analysis", {})
        self.enable_latency_analysis = performance_analysis_config.get("enabled", True) if isinstance(performance_analysis_config, dict) else True
        self.performance_show_details = performance_analysis_config.get("show_details", True) if isinstance(performance_analysis_config, dict) else True
        
        # 初始化性能计时器（log_func 使耗时分析走日志系统，从而广播到Web端显示）
        self.performance_timer = PerformanceTimer(
            enable_output=self.enable_latency_analysis,
            show_details=self.performance_show_details,
            log_func=lambda msg: log_info("TIME", msg)
        )
        
        # 模块实例（将在 initialize_modules 中初始化）
        self.microphone: Optional[Any] = None
        self.vad: Optional[Any] = None
        self.asr: Optional[Any] = None
        self.llm: Optional[Any] = None
        self.tts_engine: Optional[Any] = None
        self.audio_player: Optional[Any] = None
        self.persona_manager: Optional[Any] = None
        self.punctuation_restorer: Optional[Any] = None  # 标点恢复器
        self.websocket_server: Optional[Any] = None  # WebSocket 服务器（统一管理）

        # 状态变量
        self.running = False
        self.last_active_time = 0.0  # 最后活动时间（用于NO_SPEECH_THRESHOLD检测）
        self.asr_thread_active = False  # ASR线程是否活跃
        
        # 任务相关变量
        # 对话历史唯一所有者：同句合并/裁剪/清空/快照职责全部收敛到 ConversationHistory
        # （内部自带锁；manager侧不再维护history_lock/_limit_history_length/_last_user_record_time）
        self.history = ConversationHistory(
            max_records=config.get("llm.max_history_length", 50),  # 最大历史记录长度（对话轮数）
            merge_window_s=config.get("asr.input_merge_window_seconds", 10.0),  # 用户输入合并窗口（秒）
            max_merged_chars=config.get("asr.max_merged_input_chars", 200),  # 合并后最大字符数
        )

        # 打断相关变量
        self.interrupt_lock = threading.Lock()  # 保护打断操作的锁
        self.interrupted_task_id = None  # 记录已经处理过真实打断的任务ID，避免重复打断
        self._last_real_interrupt_time = 0.0  # 上次确认真打断的时间戳（跨任务冷却用，interrupt.cooldown_seconds）
        self._interrupt_paused_task_id = None  # 确认真打断后被暂停播放的任务ID（误判恢复用，None表示无可恢复的暂停）
        # 打断确认路径与播放线程路径互斥记录历史用的窄锁：
        # 只保护"检查并置位 history_recorded_by_interrupt 标志"这一恰好记录一次的临界区
        # （历史列表本身的线程安全已由 ConversationHistory 内部锁承担）
        self._history_record_guard = threading.Lock()

        # 终句debounce（P3：源头治理VAD过切分）：final碎片先缓冲，静默窗口内无新语音
        # （新的partial/final/VAD重新开始说话都会重置计时）才触发一轮"记录+处理"，
        # 避免每个碎片都启动一整轮LLM->TTS->播放；ConversationHistory的同句合并仍作为第二道防线保留。
        # asr.endpoint_debounce_ms=0时完全关闭（行为与旧版一致）；
        # 打断语义（SPEAKING/已确认打断）下始终绕过debounce立即处理，打断延迟不受影响
        self._endpoint_debounce_delay_s = max(0.0, float(config.get("asr.endpoint_debounce_ms", 500))) / 1000.0
        self._endpoint_debouncer = EndpointDebouncer(
            delay_s=self._endpoint_debounce_delay_s,
            fire_callback=self._on_endpoint_debounce_fire,
            # 缓冲碎片的最大保留间隔与历史合并窗口对齐（同一"同句切分"判定口径），
            # 防止长时间挂起的碎片并入后续新话轮
            max_gap_s=config.get("asr.input_merge_window_seconds", 10.0),
        )
        
        # 线程启动标志（用于LLM+TTS流程）
        self.thread_started_lock = threading.Lock()  # 保护线程启动标志的锁
        self.tts_thread_started = False
        self.player_thread_started = False
        
        # ASR线程管理
        self.asr_thread_lock = threading.Lock()
        self.asr_task_id = 0  # ASR任务ID（独立于主任务ID）
        self.current_asr_thread_name = None  # 当前活动的ASR线程名
        
        # TTS播放文本实时跟踪（用于记录对话历史）
        # 注意：不再需要current_played_text_lock，因为已播放文本直接从播放进度百分比计算
        # 完整文本存储在context.response_text中，播放进度百分比存储在context的playback_progress_percent中
        
        log_info("MANAGER", "核心调度器初始化完成", indent=0)
    
    @property
    def conversation_history(self) -> List[Dict[str, str]]:
        """只读兼容入口：返回全部对话历史的快照（副本）

        历史的写入只允许经由 self.history（record_user/record_assistant/clear）；
        全库已核对：无任何调用方经由本属性写入（LLM侧已改用 history_provider）
        """
        return self.history.snapshot_all()

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
        - WebSocket 服务器（如果启用，用于日志广播和音频传输）
        - MicrophoneRecorder（音频采集）或 WebMicrophoneAdapter（Web麦克风）
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

        # ====== 统一启动 WebSocket 服务器 ======
        # 如果启用了 WebSocket，统一在全局启动，所有模块共享
        ws_enabled = self.config.get("system.websocket.enabled", True)
        if ws_enabled:
            log_info("MANAGER", "WebSocket 服务器功能已启用", indent=1)

            from voice_chatbot.utils.websocket_server import WebSocketServer
            from voice_chatbot.utils.logger import set_websocket_server

            ws_config = self.config.get("system.websocket", {})
            self.websocket_server = WebSocketServer(
                config=self.config,
                host=ws_config.get("host", "0.0.0.0"),
                port=ws_config.get("port", 8766)
            )

            # 将 WebSocket 服务器设置到 logger，用于广播日志到 Web 端
            # 注意：必须在创建服务器后立即调用，否则日志无法广播
            set_websocket_server(self.websocket_server)

            # 注册"清空对话历史"处理器（Web端按钮触发，用于演示/换人时重置上下文，
            # 避免被无关旁白污染的历史导致回复跑偏/复读）
            def _handle_clear_history(client_id: str, data: dict):
                log_info("MANAGER", f"收到客户端 {client_id} 的清空对话请求", indent=1)
                self.clear_conversation_history()
            self.websocket_server.register_handler("clear_history", _handle_clear_history)

            # ===== 运行时热设置：Web端动态调整日志显示/打断判定/识别间隔等参数 =====
            from voice_chatbot.utils.runtime_settings import get_settings_state, apply_settings

            def _reply_to(client_id: str, message: dict):
                """向指定客户端回执消息（handler在事件循环线程被同步调用）"""
                import asyncio
                loop = self.websocket_server.event_loop
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self.websocket_server.send_to_client(client_id, message), loop)

            def _handle_get_settings(client_id: str, data: dict):
                _reply_to(client_id, {
                    "type": "settings_state",
                    "settings": get_settings_state(self.config),
                })

            def _handle_set_settings(client_id: str, data: dict):
                changes = (data or {}).get("changes", {})
                result = apply_settings(self, changes)
                # 回执变更结果，并向所有客户端广播最新状态（多端保持一致）
                _reply_to(client_id, {"type": "settings_result", **result})
                if result.get("applied"):
                    self.websocket_server.send_to_all_clients_sync({
                        "type": "settings_state",
                        "settings": get_settings_state(self.config),
                    })

            self.websocket_server.register_handler("get_settings", _handle_get_settings)
            self.websocket_server.register_handler("set_settings", _handle_set_settings)

            log_info("MANAGER", "WebSocket 服务器初始化完成", indent=1)
            log_info("MANAGER", "日志广播已启用，Web 端将显示系统日志", indent=1)

            # 在后台启动 WebSocket 服务器
            import asyncio
            import threading
            def start_websocket_server():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.websocket_server.start())
                try:
                    loop.run_forever()
                except asyncio.CancelledError:
                    pass
                finally:
                    loop.run_until_complete(self.websocket_server.stop())
                    loop.close()

            self.websocket_server_thread = threading.Thread(
                target=start_websocket_server,
                name="WebSocket-Server",
                daemon=True
            )
            self.websocket_server_thread.start()
            log_info("MANAGER", "WebSocket 服务器线程已启动", indent=1)
        else:
            log_info("MANAGER", "WebSocket 服务器功能未启用", indent=1)

        # 初始化音频采集模块（根据 system.websocket.enabled 自动选择）
        # system.websocket.enabled = True -> Web麦克风
        # system.websocket.enabled = False -> 本地麦克风
        if self.microphone_type == "web":
            # 使用 Web 麦克风
            if not ws_enabled:
                raise ValueError("Web 麦克风模式需要启用 WebSocket 服务器（system.websocket.enabled = true）")

            log_info("MANAGER", "麦克风类型: Web麦克风（通过浏览器远程采集）", indent=1)

            # 使用已启动的 WebSocket 服务器
            from voice_chatbot.utils.microphone_web import WebMicrophoneServer
            self.microphone = WebMicrophoneServer(
                config=self.config,
                ws_server=self.websocket_server,  # 复用已启动的服务器
                on_audio_callback=self._on_audio_data  # 实时音频回调
            )
            log_info("MANAGER", "Web 麦克风模块初始化完成（复用 WebSocket 服务器）", indent=1)

        else:
            # 使用本地麦克风
            log_info("MANAGER", "麦克风类型: 本地麦克风", indent=1)
            from voice_chatbot.utils.microphone import MicrophoneRecorder
            self.microphone = MicrophoneRecorder(
                config=self.config,
                on_audio_data_callback=self._on_audio_data
            )
            log_info("MANAGER", "音频采集模块初始化完成", indent=1)
        
        # 初始化VAD模块
        from voice_chatbot.asr.webrtc_vad import WebRTCVAD
        self.vad = WebRTCVAD(
            config=self.config,
            on_speech_start=self._on_vad_speech_start,
            on_speech_end=self._on_vad_speech_end,
            on_speech_active=self._on_vad_speech_active,
            event_bus=self.event_bus
        )
        log_info("MANAGER", "VAD模块初始化完成", indent=1)
        
        # 初始化ASR模块
        from voice_chatbot.asr.fun_asr_stream import FunASRStream, CircularAudioBuffer
        pre_buffer = CircularAudioBuffer(
            self.config.get("asr.pre_buffer_seconds", 1),
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
        
        # 初始化标点恢复器（如果启用）
        punctuation_restore_config = self.config.get("asr.punctuation_restore", {})
        enable_punctuation_restore = punctuation_restore_config.get("enabled", False) if isinstance(punctuation_restore_config, dict) else False
        if enable_punctuation_restore:
            try:
                from voice_chatbot.asr.punctuation_restore import PunctuationRestorer
                # 从配置读取模型名称，默认为"ct-punc"
                model_name = punctuation_restore_config.get("model", "ct-punc") if isinstance(punctuation_restore_config, dict) else "ct-punc"
                self.punctuation_restorer = PunctuationRestorer(model_name=model_name)
                # 立即加载模型（系统启动时预加载）
                log_info("MANAGER", f"正在加载标点恢复模型: {model_name}...", indent=1)
                self.punctuation_restorer.initialize()
                log_info("MANAGER", f"标点恢复器初始化完成（模型: {model_name}，已预加载）", indent=1)
            except Exception as e:
                log_error("MANAGER", f"标点恢复器初始化失败: {e}，将禁用标点恢复功能", indent=1)
                import traceback
                log_error("MANAGER", f"异常堆栈: {traceback.format_exc()}", indent=1)
                self.punctuation_restorer = None
        else:
            log_info("MANAGER", "标点恢复功能已禁用", indent=1)
        
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
            history_provider=lambda: self.history.snapshot_for_llm(),  # 历史快照提供函数（已排除当前输入）
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
        # 处理 CosyVoice TTS 引擎（通过HTTP API调用）
        elif tts_engine_type in ("cosyvoice_tts", "cosyvoice"):
            try:
                from voice_chatbot.tts import CosyVoiceTTSEngine, HAS_COSYVOICE_TTS
                if HAS_COSYVOICE_TTS:
                    self.tts_engine = CosyVoiceTTSEngine(config=self.config, event_bus=self.event_bus)
                    log_info("MANAGER", f"TTS引擎初始化完成 ({tts_engine_type})", indent=1)
                else:
                    log_warn("MANAGER", "CosyVoice TTS模块未安装，使用EdgeTTS", indent=1)
                    from voice_chatbot.tts.edge_tts_engine import EdgeTTSEngine
                    self.tts_engine = EdgeTTSEngine(config=self.config, event_bus=self.event_bus)
                    self.tts_engine_type = "edge_tts"
                    log_info("MANAGER", "TTS引擎初始化完成 (edge_tts)", indent=1)
            except ImportError:
                log_warn("MANAGER", "CosyVoice TTS模块导入失败，使用EdgeTTS", indent=1)
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
        # 根据播放器类型选择本地播放器或 Web 播放器
        import threading
        player_task_lock = threading.Lock()

        if self.player_type == "web":
            # Web 播放器模式
            from voice_chatbot.utils.audio_player_web import AudioPlayerWeb, PlaybackStatus
            self.PlaybackStatus = PlaybackStatus
            self.audio_player = AudioPlayerWeb(
                config=self.config,
                ws_server=self.websocket_server,  # 复用现有的 WebSocket 服务器
                task_lock=player_task_lock,
                get_current_task_id=lambda: self.context_manager.get_current_task_id(),
                event_bus=self.event_bus
            )
            log_info("MANAGER", "Web 音频播放器初始化完成", indent=1)
        else:
            # 本地播放器模式（默认）
            from voice_chatbot.utils.audio_player import AudioPlayer, PlaybackStatus
            self.PlaybackStatus = PlaybackStatus
            self.audio_player = AudioPlayer(
                config=self.config,
                task_lock=player_task_lock,  # AudioPlayer内部需要锁
                get_current_task_id=lambda: self.context_manager.get_current_task_id(),
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
            # 只有本地麦克风模式下才需要启动 microphone
            if self.microphone:
                self.microphone.start()
                # 持续运行，直到running=False
                # 注意：NO_SPEECH_THRESHOLD检查已移至_on_audio_data回调中实时进行
                # 使用更小的sleep间隔以更快响应停止信号
                while self.running:
                    time.sleep(0.05)  # 从0.1秒减少到0.05秒，更快响应
                self.microphone.stop()
            # Web 麦克风模式下，服务器独立运行，只需要等待停止信号
            else:
                while self.running:
                    time.sleep(0.05)

        # 只有本地麦克风模式下才启动音频采集线程
        if self.microphone:
            self.thread_manager.start_thread(
                name="Audio-Recorder",
                target=audio_recorder_thread,
                context=None,
                thread_type="resident",
                daemon=True
            )
            log_info("MANAGER", "音频采集线程已启动", indent=1)
        else:
            log_info("MANAGER", "Web麦克风模式，使用独立服务器线程", indent=1)
        
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

        # P3 终句debounce：取消pending的终句计时器并清空缓冲，
        # 确保系统关闭后debounce的timer线程不会再触发LLM->TTS->播放管线
        dropped = self._endpoint_debouncer.reset()
        if dropped:
            log_info("MANAGER", f"[DEBOUNCE] 系统停止，丢弃未触发的终句缓冲: '{dropped}'", indent=1)

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
        # 统一处理两种模式的麦克风清理
        if self.microphone:
            if self.microphone_type == "local":
                # 本地麦克风：调用 stop() 方法
                self.microphone.stop()
            elif self.microphone_type == "web":
                # Web 麦克风：调用 stop() 方法（WebMicrophoneServer 也实现了 stop()）
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.microphone.stop())
                except Exception as e:
                    log_error("MANAGER", f"停止 Web 麦克风时出错: {e}", indent=1)
                finally:
                    loop.close()
        if self.audio_player:
            self.audio_player.cleanup()
        if self.tts_engine:
            self.tts_engine.cleanup()
        if self.asr:
            # ASR清理
            pass
        
        # 清理标点恢复器
        if self.punctuation_restorer:
            try:
                self.punctuation_restorer.cleanup()
            except Exception as e:
                log_error("MANAGER", f"清理标点恢复器时发生错误: {e}", indent=1)
        log_info("MANAGER", "资源清理完成", indent=1)
        
        log_info("MANAGER", "系统已停止", indent=0)
    
    
    
    def _on_audio_data(self, audio_data: np.ndarray):
        """
        音频数据回调（由线程1调用）
        
        Args:
            audio_data: 音频数据（numpy数组，float32格式）
        """
        # 计算音频统计信息（定期输出，避免日志过多）
        if not hasattr(self, '_audio_log_counter'):
            self._audio_log_counter = 0
        self._audio_log_counter += 1
        
        # 每100次输出一次统计信息（约每2秒，假设每次约20ms）
        if self._audio_log_counter % 100 == 0:
            audio_length = len(audio_data)
            sample_rate = self.config.get("audio.sample_rate", 16000)
            duration = audio_length / sample_rate
            rms = np.sqrt(np.mean(audio_data ** 2))
            max_amp = np.max(np.abs(audio_data))
            min_amp = np.min(np.abs(audio_data))
            mean_amp = np.mean(np.abs(audio_data))
            
            # 获取预缓存状态
            pre_buffer_info = ""
            if self.asr and hasattr(self.asr, 'pre_buffer') and self.asr.pre_buffer:
                pre_buffer_samples = len(self.asr.pre_buffer.get_all())
                pre_buffer_duration = pre_buffer_samples / sample_rate
                pre_buffer_info = f", 预缓存: {pre_buffer_samples}采样点({pre_buffer_duration:.2f}秒)"
            
            log_debug("MANAGER", 
                     f"音频数据统计: 长度={audio_length}采样点({duration:.3f}秒), "
                     f"RMS={rms:.4f}, 最大振幅={max_amp:.4f}, 最小振幅={min_amp:.4f}, "
                     f"平均振幅={mean_amp:.4f}{pre_buffer_info}", 
                     indent=2)
        
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
            # log_debug("MANAGER", f"检查NO_SPEECH_THRESHOLD: last_active_time={self.last_active_time:.3f}, 距离上次活动={time.time() - self.last_active_time:.3f}秒, 阈值={self.no_speech_threshold}秒", indent=2)
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
    
    # 注意：旧的_on_asr_partial_result回调方法已被事件处理版本替代
    # 保留此方法仅用于向后兼容，实际使用事件处理版本（第1195行）
    
    def _on_asr_final_result(self, text):
        """ASR最终结果回调"""
        # 记录ASR识别完成时间
        try:
            log_info("TIME", "记录ASR识别完成时间", indent=1)
            self.performance_timer.record_user_speech_end()
        except Exception as e:
            log_error("ASR", f"记录ASR识别完成时间时发生错误: {e}", indent=1)
            import traceback
            log_error("ASR", f"异常堆栈: {traceback.format_exc()}", indent=1)
        
        # final已到达：结束"识别中"流式通道（无final_text，最终内容由下方常规日志承载）
        log_stream_end('asr_partial')

        if text:
            log_info("MANAGER", f"最终识别结果: {text}", indent=0)
            # 如果启用了标点恢复，先进行标点恢复处理
            if self.punctuation_restorer and text:
                try:
                    original_text = text
                    text = self.punctuation_restorer.restore(text)
                    if text != original_text:
                        log_info("PUN", f"标点恢复: '{original_text}' -> '{text}'", indent=1)
                    else:
                        log_debug("PUN", "标点恢复后文本未变化", indent=1)
                except Exception as e:
                    log_error("PUN", f"标点恢复处理失败: {e}，使用原始文本", indent=1)
                    import traceback
                    log_error("PUN", f"异常堆栈: {traceback.format_exc()}", indent=1)
            
        
        # P3 终句debounce：非打断语义且debounce开启时，final先进入缓冲并（重）启计时，
        # 静默窗口内出现新的partial/VAD重新开始说话会取消计时（缓冲保留），
        # 新final到来时并入缓冲重新offer——窗口到期才用合并文本触发一轮"记录+处理"，
        # 从源头避免VAD过切分的每个碎片都启动一整轮LLM->TTS->播放
        if (self._endpoint_debounce_delay_s > 0
                and not self._in_interrupt_semantics()
                and text and text.strip()):
            self._endpoint_debouncer.offer(text)
            log_info("MANAGER",
                     f"[DEBOUNCE] final进入终句缓冲（窗口{self._endpoint_debounce_delay_s * 1000:.0f}ms）: "
                     f"'{text}'，当前合并缓冲: '{self._endpoint_debouncer.pending_text()}'",
                     indent=0)
            return

        # 绕过debounce立即处理（打断流保证打断延迟不受影响；debounce=0行为与旧版完全一致）：
        # 仅打断语义下丢弃未触发的缓冲碎片（打断语音优先，陈旧碎片不应回流后续话轮）；
        # 空final/debounce=0的绕过路径不丢弃——缓冲属于尚未结束的话轮，静默到期自然触发
        if self._in_interrupt_semantics() and self._endpoint_debouncer.is_pending():
            dropped = self._endpoint_debouncer.reset()
            log_info("MANAGER", f"[DEBOUNCE] 打断流绕过debounce，丢弃未触发的终句缓冲: '{dropped}'", indent=1)

        # 立即处理：走与debounce触发共用的"记录+处理"路径
        self._dispatch_final_text(text)

    def _in_interrupt_semantics(self) -> bool:
        """
        判断当前是否处于打断语义（final必须立即处理，不能进入debounce缓冲）

        打断语义 = 满足以下任一条件：
        1. 机器人正在播放（副系统SPEAKING）：用户可能正在打断，final是打断语音，
           必须立即处理以保证打断延迟
        2. 已确认的真实打断尚未被final结果消化（interrupted_task_id等于当前任务，
           或存在等待误判恢复的暂停任务）：final是该打断的判定结果，同样必须立即处理

        Returns:
            True表示处于打断语义（绕过debounce立即处理）
        """
        if self.state_manager.get_sub_state() == SubSystemState.SPEAKING:
            return True
        with self.interrupt_lock:
            if self._interrupt_paused_task_id is not None:
                return True
            if (self.interrupted_task_id is not None
                    and self.interrupted_task_id == self.context_manager.get_current_task_id()):
                return True
        return False

    def _dispatch_final_text(self, text: str):
        """
        final结果的"记录+处理"路径（立即处理与debounce到期触发共用）

        先将用户输入写入对话历史（含同句切分合并），再启动处理流程。
        注意顺序：LLM构建消息时通过history_provider读取历史并按身份排除"待定当前输入"，
        必须先入史后启动LLM，否则LLM线程可能读到缺少当前输入的历史（竞态）

        Args:
            text: final文本（debounce触发时为窗口内碎片的合并文本）
        """
        effective_text = text
        if self._validate_input(text):
            try:
                effective_text = self._record_user_input(text)
            except Exception as history_error:
                log_error("ASR", f"记录用户输入到对话历史时发生错误: {history_error}", indent=1)

        # 处理ASR结果（使用合并后的文本，确保LLM拿到完整的用户输入）
        try:
            log_debug("ASR", f"准备调用_handle_asr_result处理ASR结果: '{effective_text}'", indent=1)
            self._handle_asr_result(effective_text)
            log_debug("ASR", f"_handle_asr_result调用完成", indent=1)
        except Exception as e:
            log_error("ASR", f"处理ASR结果时发生错误: {e}", indent=1)
            import traceback
            log_error("ASR", f"异常堆栈: {traceback.format_exc()}", indent=1)

    def _on_endpoint_debounce_fire(self, merged_text: str):
        """
        终句debounce静默窗口到期回调（由EndpointDebouncer的timer线程调用）

        用窗口内合并后的文本走现有"记录+处理"路径（_dispatch_final_text），
        recorder（ConversationHistory）的同句合并作为第二道防线保留。
        """
        if not self.running:
            # 系统已停止（stop()已reset计时器，此处为迟到触发的兜底防护），不再触发管线
            log_info("MANAGER", f"[DEBOUNCE] 系统已停止，丢弃debounce合并文本: '{merged_text}'", indent=1)
            return
        log_info("MANAGER", f"[DEBOUNCE] 终句静默窗口到期，合并文本触发处理: '{merged_text}'", indent=0)
        self._dispatch_final_text(merged_text)

    def _should_real_interrupt(self, partial_text: str, confidence: Optional[float] = None) -> bool:
        """
        唯一的真打断判定点：ASR部分识别结果是否构成真实打断

        四个条件全部满足才判定为True（任一不满足均不触发打断）：
        1. _validate_input(partial_text)：归一化非空且不在无效词表/纯语气词中
        2. 归一化后有效字符数 >= interrupt.min_text_chars（默认2，单字"对/好"不再触发）
        3. 置信度门槛：confidence为None或<=0（字段缺失/不可用）时放行，
           否则需 >= interrupt.min_confidence（默认0.3）
        4. 距上次确认真打断 >= interrupt.cooldown_seconds（默认1.0秒，跨任务冷却，
           防止ASR抖动/重复识别引发打断风暴）

        判定为True时更新_last_real_interrupt_time（冷却计时起点）

        Args:
            partial_text: ASR部分识别文本
            confidence: ASR部分识别置信度（None表示字段缺失）

        Returns:
            True表示真实打断，False表示不构成打断
        """
        # 条件1：输入有效性（归一化+无效词表+附和词过滤）
        if not self._validate_input(partial_text):
            return False

        # 条件2：最小有效字符数（归一化后），过滤单字应答（"对""好"等）
        cleaned = self._normalize_input_text(partial_text)
        min_text_chars = self.config.get("interrupt.min_text_chars", 2)
        if len(cleaned) < min_text_chars:
            log_debug("MANAGER", f"[INTERRUPT] 有效字符数不足({len(cleaned)}<{min_text_chars})，不构成打断: '{cleaned}'", indent=1)
            return False

        # 条件3：置信度门槛（字段缺失None或<=0时放行，兼容不输出置信度的ASR）
        if confidence is not None and confidence > 0:
            min_confidence = self.config.get("interrupt.min_confidence", 0.3)
            if confidence < min_confidence:
                log_debug("MANAGER", f"[INTERRUPT] 置信度不足({confidence:.2f}<{min_confidence})，不构成打断: '{cleaned}'", indent=1)
                return False

        # 条件4：跨任务冷却（与上次确认真打断的间隔），防止打断风暴
        cooldown_seconds = self.config.get("interrupt.cooldown_seconds", 1.0)
        with self.interrupt_lock:
            now = time.time()
            if now - self._last_real_interrupt_time < cooldown_seconds:
                log_debug("MANAGER", f"[INTERRUPT] 打断冷却中(距上次{now - self._last_real_interrupt_time:.2f}秒<{cooldown_seconds}秒)，不构成打断", indent=1)
                return False
            # 判定通过，更新冷却计时起点
            self._last_real_interrupt_time = now

        return True

    def _recover_misclassified_interrupt(self, reason: str):
        """
        误判恢复：确认打断后final结果无效时，恢复被暂停的播放（假打断必须能继续播放）

        _confirm_real_interrupt对播放只做pause()（保留队列与播放线程），真打断的硬停止
        由新任务路径（_process_user_input创建新任务时）负责；若最终ASR结果归一化后无效
        （_handle_asr_result提前return、不会建新任务），则在此处resume()续播。

        由interrupt.recover_on_misclassify开关控制（默认true）：
        - 开启：resume()续播，副系统状态回到SPEAKING
        - 关闭：退回旧行为，硬停止被暂停的播放（播放线程按打断路径记录已播部分）

        Args:
            reason: 恢复原因描述（用于日志与事件观测）
        """
        # 原子取出并清除待恢复任务标记（无论走恢复还是终止路径都只消费一次）
        with self.interrupt_lock:
            paused_task_id = self._interrupt_paused_task_id
            self._interrupt_paused_task_id = None

        if paused_task_id is None:
            # 本次final无效但此前没有暂停中的播放（未在播放中触发打断），无需恢复
            return

        if not self.config.get("interrupt.recover_on_misclassify", True):
            # 关闭误判恢复：硬停止被暂停的播放，避免播放器长期滞留在暂停状态
            log_info("MANAGER", f"[INTERRUPT] 误判恢复已关闭(interrupt.recover_on_misclassify=false)，停止被暂停的播放: 任务{paused_task_id} ({reason})", indent=1)
            if self.audio_player:
                try:
                    # 播放器空闲时stop()仅记录警告并返回False，调用是安全的
                    self.audio_player.stop()
                except Exception as e:
                    log_warn("MANAGER", f"误判恢复关闭时停止播放器出错: {e}", indent=1)
            return

        # 仅当被暂停的任务仍是当前任务时才恢复（若已被新任务取代，播放已随之终止）
        if paused_task_id != self.context_manager.get_current_task_id():
            log_info("MANAGER", f"[INTERRUPT] 被暂停的任务{paused_task_id}已不是当前任务，无需恢复播放", indent=1)
            return

        if not self.audio_player:
            return

        try:
            resumed = self.audio_player.resume()
            if resumed:
                log_info("MANAGER", f"[INTERRUPT] 误判恢复：{reason}，恢复任务{paused_task_id}的播放", indent=0)
                # 状态机同步：打断确认时副系统已转为NONE，恢复播放后回到SPEAKING
                if self.state_manager.get_sub_state() != SubSystemState.SPEAKING:
                    self.state_manager.transition_sub_state(SubSystemState.SPEAKING, "误判打断恢复，继续播放")
                # 发布打断取消事件（误判），便于日志/网页端观测
                cancel_event = create_event(
                    EventType.INTERRUPT_CANCELLED,
                    data={"task_id": paused_task_id, "reason": reason},
                    source="DialogueManager"
                )
                self.event_bus.publish(cancel_event)
            else:
                log_warn("MANAGER", f"[INTERRUPT] 误判恢复失败：播放器不在暂停状态(任务{paused_task_id})，可能已随任务切换终止", indent=1)
        except Exception as e:
            log_error("MANAGER", f"误判恢复resume()出错: {e}", indent=1)
            # 恢复异常时兜底硬停止，避免播放器长期滞留在暂停状态
            try:
                self.audio_player.stop()
            except Exception:
                pass

    def _confirm_real_interrupt(self):
        """
        确认真实打断并暂停播放/取消处理

        当ASR验证成功（有效输入）时调用，正式打断当前播放或处理

        操作流程：
        1. 如果在播放中（SPEAKING）：暂停播放（pause()保留播放队列与播放线程，
           final结果无效时可resume()续播实现误判恢复）；真打断的硬停止延迟到
           新任务路径（_process_user_input创建新任务时stop()并借任务取消终止播放）
        2. 如果在处理中（PROCESSING）：取消当前任务，停止LLM/TTS线程
        3. 转换系统状态到NONE

        注意：
        - full_played_text 在 _on_playback_progress 中实时计算并保存到 context
        - 使用锁保护，避免连续打断时的竞争条件
        """
        # 使用锁保护打断操作，避免连续打断时的竞争条件
        with self.interrupt_lock:
            log_info("MANAGER", "确认真实打断，停止播放并处理新输入", indent=0)

            try:
                # 获取当前任务上下文
                current_context = self.context_manager.get_current_context()
                current_task_id = self.context_manager.get_current_task_id()
                sub_state = self.state_manager.get_sub_state()

                # 如果没有当前任务，可能是状态异常，尝试重置状态
                if current_context is None:
                    log_warn("MANAGER", "[INTERRUPT] 当前没有活动任务，但检测到打断，尝试重置状态", indent=1)
                    if sub_state != SubSystemState.NONE:
                        self.state_manager.transition_sub_state(SubSystemState.NONE, "打断时发现无活动任务，重置状态")
                    # 重置打断标记，确保后续可以正常处理
                    self.interrupted_task_id = None
                    return

                # 检查当前任务是否已经被打断过，避免重复打断
                if self.interrupted_task_id == current_task_id:
                    log_info("MANAGER", f"[INTERRUPT] 任务{current_task_id}已处理过打断，跳过重复打断", indent=1)
                    return

                # 记录已经处理过真实打断的任务ID，避免重复打断
                self.interrupted_task_id = current_task_id
                log_info("MANAGER", f"[INTERRUPT] 记录已打断任务ID: {current_task_id}，副系统状态: {sub_state.value}", indent=1)

                # 注意：SPEAKING分支改为暂停语义后，此处完全不再写入
                # history_recorded_by_interrupt标志，已播文本的历史记录统一延迟到
                # 播放线程终态路径（误判恢复后自然完成记全文/真打断被取消记已播部分），
                # 由_history_record_guard保证恰好记录一次

                # 根据副系统状态处理
                if sub_state == SubSystemState.SPEAKING:
                    # 正在播放：暂停播放（保留播放队列与播放线程，误判恢复时可直接resume()续播）
                    # 真打断的硬停止由新任务路径负责（_process_user_input创建新任务时stop()，
                    # 且create_context使旧任务失效后播放循环经任务取消检查自然退出并flush队列）
                    log_info("MANAGER", f"[INTERRUPT] 副系统状态为SPEAKING，暂停播放等待final结果判定", indent=1)

                    paused = False
                    if self.audio_player:
                        audio_task_id = self.audio_player.get_current_task_id()
                        if audio_task_id == current_task_id:
                            # 暂停当前任务的播放（保留队列与播放进度）
                            paused = self.audio_player.pause(task_id=current_task_id)
                        else:
                            # 段间IDLE间隙（play()结束后_current_task_id会被清空）或任务已切换：
                            # 不依赖status==PLAYING，直接无task_id调用pause()兜底；
                            # 播放器空闲时pause()仅记录警告并返回False，调用是安全的
                            paused = self.audio_player.pause()

                    if paused:
                        # 记录待恢复任务：final结果无效时由_handle_asr_result恢复播放（误判恢复），
                        # final结果有效时由新任务路径清除该标记并硬停止播放
                        self._interrupt_paused_task_id = current_task_id
                        log_info("MANAGER", f"[INTERRUPT] 播放已暂停（保留队列），任务{current_task_id}等待final结果判定", indent=1)
                    else:
                        # 暂停失败（段间间隙等）：播放器当前空闲或已切换任务，无正在播放的音频可暂停，
                        # 后续音频由队列继续供给，真打断时随新任务取消自然终止
                        log_info("MANAGER", f"[INTERRUPT] 播放器当前不可暂停（段间间隙或已切换任务），跳过暂停", indent=1)

                elif sub_state == SubSystemState.PROCESSING:
                    # 正在处理（LLM生成或TTS生成）：取消任务，停止线程
                    # 注意：本分支不设置history_recorded_by_interrupt（它没有记录任何内容），
                    # 播放线程路径对空played_text已有跳过逻辑，会自行决定是否记录
                    log_info("MANAGER", f"[INTERRUPT] 副系统状态为PROCESSING，取消当前任务", indent=1)

                    # 取消当前任务（使上下文失效，线程会自动停止）
                    if current_context:
                        self.context_manager.cancel_current_context()
                        log_info("MANAGER", f"[INTERRUPT] 已取消上下文，LLM/TTS线程将自动停止", indent=1)

                        # 重置线程启动标志，确保后续可以正常启动新任务
                        self.tts_thread_started = False
                        self._set_player_thread_started_safe(False)

                # 转换系统状态到NONE
                if sub_state != SubSystemState.NONE:
                    log_info("MANAGER", f"[INTERRUPT] 从{sub_state.value}转换到NONE", indent=1)
                    self.state_manager.transition_sub_state(SubSystemState.NONE, "确认真实打断，准备处理新输入")

            except Exception as e:
                log_error("MANAGER", f"_confirm_real_interrupt() 发生异常: {e}", indent=0)
                import traceback
                log_error("MANAGER", f"异常堆栈:\n{traceback.format_exc()}", indent=0)
                # 发生异常时，尝试重置状态，确保系统可以继续工作
                try:
                    if sub_state != SubSystemState.NONE:
                        self.state_manager.transition_sub_state(SubSystemState.NONE, "打断处理异常，重置状态")
                    self.interrupted_task_id = None
                except:
                    pass
                # 不重新抛出异常，避免影响后续处理
    
    def _handle_asr_result(self, text: str):
        """
        处理ASR识别结果

        注意：
        - 完整输入验证已前移到_on_asr_final_result入口（唯一入口门，三重验证收敛），
          这里只做轻量falsy检查（拦None/空串直通）；无效但非空的文本由
          _process_user_input的防御检查兜底拦截
        - 识别结果为空时不启动LLM+TTS流程，并触发误判打断恢复
          （此前确认打断时播放仅被暂停，需恢复续播）

        Args:
            text: 识别文本
        """
        # 记录当前状态和输入
        sub_state = self.state_manager.get_sub_state()
        main_state = self.state_manager.get_main_state()
        log_info("MANAGER", f"处理ASR结果: '{text}' (主系统={main_state.value}, 副系统={sub_state.value})", indent=0)


        self.state_manager.transition_main_state(MainSystemState.IDLE, "ASR线程识别结束，恢复IDLE状态")

        # 轻量falsy检查：空/None结果不启动处理流程（完整验证在_on_asr_final_result入口）
        if not text:
            log_info("MANAGER", f"ASR识别结果为空: '{text}'，不启动LLM+TTS流程", indent=0)
            # 误判恢复：已确认打断且播放被暂停时，恢复播放（假打断必须能继续播放）
            self._recover_misclassified_interrupt("最终识别结果为空")
            return

        # 只有输入有效时才处理用户输入
        self._process_user_input(text)

    
    # 无实义附和词的字符集合与词语集合均改为配置驱动（asr.backchannel_chars /
    # asr.invalid_recognition_words），不再使用硬编码类属性

    @staticmethod
    def _normalize_input_text(text: str) -> str:
        """去除空白与常见标点，用于输入有效性判断的归一化匹配（如 'i.' → 'i'、'嗯。' → '嗯'）"""
        return re.sub(r"[\s，。！？、,.!?;；:：~～…—·\"'‘’“”()（）\[\]【】]+", "", text or "")

    def _validate_input(self, text: str) -> bool:
        """
        验证输入有效性（归一化匹配）

        规则：
        1. 去除空白和标点后为空 → 无效
        2. 归一化后命中无效词表（asr.invalid_recognition_words，含"嗯哼/对对对"等枚举词）→ 无效
        3. 归一化后是纯语气词字符（asr.backchannel_chars，如"嗯嗯""啊哦"）→ 无效

        Args:
            text: 输入文本

        Returns:
            True表示有效，False表示无效
        """
        if not text:
            return False

        cleaned = self._normalize_input_text(text)
        if not cleaned:
            return False

        if cleaned.lower() in self._normalized_invalid_words:
            return False

        # 纯附和词字符检查（字符集合从配置读取，如"嗯啊哦呃额噢唉呀哈嘿诶欸唔"）
        if all(ch in self._backchannel_chars for ch in cleaned):
            return False

        return True

    def _record_user_input(self, text: str) -> str:
        """
        记录用户输入到对话历史，返回应作为本轮处理的有效输入文本

        合并/追加/裁剪逻辑由 ConversationHistory.record_user 负责（合并条件见其docstring），
        此处只负责计算"上一话轮机器人是否实际播出过音频"（独立话轮标志）并输出日志。

        注意：LLM构建消息时通过 history_provider 读取历史并按身份排除"待定当前输入"，
        因此必须先入史后启动LLM，否则LLM线程可能读到缺少当前输入的历史（竞态）
        """
        # 检查上一任务是否实际播出过音频（此时新任务尚未创建，当前context即上一任务）
        prev_turn_audible = False
        prev_context = self.context_manager.get_current_context()
        if prev_context is not None:
            prev_played_text = (prev_context.get_extra_data("full_played_text", "") or "").strip()
            prev_played_duration = prev_context.get_extra_data("played_duration", 0.0) or 0.0
            prev_turn_audible = bool(prev_played_text) or prev_played_duration > 0

        effective_text = self.history.record_user(text, prev_turn_audible=prev_turn_audible)
        if effective_text != text:
            log_info("ASR", f"检测到连续用户输入（上轮回复未完成，疑似VAD切分），合并记录: '{effective_text}'", indent=1)
        else:
            log_debug("ASR", f"用户输入已记录到对话历史: '{text}'", indent=1)
        return effective_text

    def clear_conversation_history(self):
        """清空对话历史（Web端触发），下一轮对话从全新上下文开始

        清空必须是完整上下文重置，而不仅是清空列表：
        - 停止音频播放器（防止清空后旧任务继续出声）
        - 使当前任务失效并置其history_recorded标志（防止在播任务的播放线程
          随后把旧回复append回"干净"历史——取消后播放线程仍会走到记录流程）
        - 清空历史列表、复位合并窗口与打断标记、重置性能计时器
        """
        log_info("MANAGER", "收到清空对话请求，执行完整上下文重置", indent=0)

        # 1. 停止当前播放（播放器空闲时stop()仅记录警告并返回False，安全）
        if self.audio_player:
            try:
                self.audio_player.stop()
            except Exception as e:
                log_warn("MANAGER", f"清空历史时停止音频播放器出错: {e}", indent=1)

        # 2. 使当前任务失效并标记已记录历史（先置标志再失效，防止竞态下旧回复回流）
        current_context = self.context_manager.get_current_context()
        if current_context is not None:
            current_context.set_extra_data("history_recorded_by_interrupt", True)
            self.context_manager.cancel_current_context()

        # 2.5 清空终句debounce缓冲并取消pending计时（P3）：上下文已重置，
        #     pending碎片不应在清空后回流触发处理、污染"干净"的新历史
        dropped = self._endpoint_debouncer.reset()
        if dropped:
            log_info("MANAGER", f"[DEBOUNCE] 清空对话历史，丢弃pending的终句缓冲: '{dropped}'", indent=1)

        # 3. 清空历史列表并复位用户输入合并窗口与待定输入标记（由recorder.clear完成）
        count = len(self.history)
        self.history.clear()

        # 4. 复位打断标记（Optional[int]，初值None），使后续任务可正常打断；
        #    同时清除误判恢复标记（播放已被上方stop()终止，无需也不应再resume）
        with self.interrupt_lock:
            self.interrupted_task_id = None
            self._interrupt_paused_task_id = None

        # 5. 重置性能计时器，避免上一轮时间戳污染下一轮分析
        self.performance_timer.reset()

        log_info("MANAGER", f"对话历史已清空（移除 {count} 条记录）", indent=0)

    def _process_user_input(self, user_input: str):
        """
        处理用户输入

        Args:
            user_input: 用户输入文本
        """
        log_info("MANAGER", f"开始处理用户输入: '{user_input}'", indent=0)

        # 防御性检查（保留）：兜底拦截漏网的无效输入——完整验证已在_on_asr_final_result入口完成，
        # _handle_asr_result只做轻量falsy检查，此处拦截的是无效但非空的文本（如语气词变体）
        if not self._validate_input(user_input):
            log_warn("MANAGER", f"用户输入无效: '{user_input}'，跳过处理", indent=0)
            # 误判恢复：final结果无效（不建新任务），恢复被暂停的播放（假打断必须能继续播放）
            self._recover_misclassified_interrupt(f"最终识别结果无效: '{user_input}'")
            return

        # 新任务路径做硬停止：打断确认路径（_confirm_real_interrupt）对播放只做了pause()，
        # 此处确认真打断后彻底终止旧任务音频——stop()使暂停中的播放立即终止，
        # create_context使旧任务失效后播放循环亦会经任务取消检查退出并flush队列
        with self.interrupt_lock:
            self._interrupt_paused_task_id = None
        if self.audio_player:
            try:
                # 播放器空闲时stop()仅记录警告并返回False，调用是安全的
                self.audio_player.stop()
            except Exception as e:
                log_warn("MANAGER", f"新任务开始时停止旧播放出错: {e}", indent=1)

        # 创建新任务上下文（自动使旧任务失效）
        old_task_id = self.context_manager.get_current_task_id()
        context = self.context_manager.create_context(user_input=user_input)
        new_task_id = context.task_id
        
        # 检查任务ID是否变化，如果变化则重置性能计时器和打断标记
        if old_task_id != new_task_id:
            log_info("MANAGER", f"[PERF_TIMER] 任务ID变化 ({old_task_id} -> {new_task_id})，重置性能计时器", indent=0)
            log_info("TIME", "任务ID变化，重置性能计时器", indent=1)
            # reset()不再跨轮携带VAD/ASR结束时间戳（根治92秒类跨轮污染），
            # 但本轮的VAD/ASR结束时间在任务创建前已记录，此处显式保留，避免丢失分析起点
            vad_end = self.performance_timer.vad_speech_end_time
            asr_end = self.performance_timer.asr_recognition_end_time
            self.performance_timer.reset()
            self.performance_timer.vad_speech_end_time = vad_end
            self.performance_timer.asr_recognition_end_time = asr_end
            # 重置打断标记，新任务可以被打破（使用锁保护）
            with self.interrupt_lock:
                self.interrupted_task_id = None
            log_info("MANAGER", f"[INTERRUPT] 任务ID变化，重置打断标记，新任务{new_task_id}可以被打破", indent=1)
        
        # 转换状态
        # 主系统: LISTENING → IDLE
        self.state_manager.transition_main_state(
            MainSystemState.IDLE,
            reason="开始处理用户输入"
        )
        
        # 副系统: None → PROCESSING
        self.state_manager.transition_sub_state(
            SubSystemState.PROCESSING,
            reason="开始处理用户输入"
        )
        
        # 启动LLM+TTS处理线程（线程6）
        self._start_inference_flow(context)
        log_info("MANAGER", f"用户输入处理流程已启动: 任务ID={context.task_id}", indent=0)
    
    def _on_vad_speech_start(self):
        """VAD检测到语音开始回调（由线程2调用）"""
        # P3 终句debounce保持：用户重新开始说话，取消pending的终句计时（缓冲保留），
        # 后续新final到来时并入缓冲重新offer，避免上一碎片在说话中途被提前触发处理
        if self._endpoint_debouncer.is_pending():
            log_info("MANAGER",
                     f"[DEBOUNCE] VAD重新检测到语音，保持debounce（取消终句计时，"
                     f"缓冲保留待新final并入）: '{self._endpoint_debouncer.pending_text()}'",
                     indent=1)
            self._endpoint_debouncer.cancel()

        # 首先检查ASR线程是否在运行（无论当前状态如何，避免重复启动）
        with self.asr_thread_lock:
            asr_thread_name = self.current_asr_thread_name
            asr_thread_info = self.thread_manager.get_thread_info(asr_thread_name) if asr_thread_name else None
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
        # 主系统状态为IDLE、ROBOT_SPEAKING时，或副系统状态为PROCESSING、SPEAKING时
        can_start_asr = (
            main_state in [MainSystemState.IDLE, MainSystemState.ROBOT_SPEAKING] or
            sub_state in [SubSystemState.PROCESSING, SubSystemState.SPEAKING]
        )

        if not can_start_asr:
            log_debug("MANAGER", f"当前状态不允许启动ASR: 主系统={main_state.value}, 副系统={sub_state.value}", indent=0)
            return

        log_info("MANAGER", "检测到语音开始", indent=0)

        # 更新last_active_time（VAD检测到语音活动时立即更新，不受check_interval影响）
        self.last_active_time = time.time()
        log_debug("MANAGER", f"VAD检测到语音开始，更新last_active_time={self.last_active_time:.3f}", indent=1)

        # 场景1特殊处理: 如果主系统状态为IDLE且副系统状态为None，这是正常对话流程
        # 但如果播放器正在播放，应该能够被打断
        if main_state == MainSystemState.IDLE and sub_state == SubSystemState.NONE:
            # 检查播放器是否正在播放
            is_playing = False
            if self.audio_player:
                is_playing = hasattr(self.audio_player, 'is_playing') and self.audio_player.is_playing()

            if is_playing:
                # 播放器正在播放，应该能够被打断，触发打断逻辑
                log_info("MANAGER", "场景1变体: 状态为IDLE+NONE但播放器正在播放，触发打断逻辑", indent=0)
                # 更新主系统状态为LISTENING
                self.state_manager.transition_main_state(MainSystemState.LISTENING, "检测到可能打断（播放中），开始监听")
                # 更新副系统状态为SPEAKING（因为正在播放）
                self.state_manager.transition_sub_state(SubSystemState.SPEAKING, "检测到可能打断（播放中）")
            else:
                # 播放器未播放，这是正常对话流程，不触发打断逻辑
                log_debug("MANAGER", "场景1: 正常对话流程，不触发打断逻辑", indent=0)

        # 如果副系统状态为SPEAKING或PROCESSING（场景2和场景3）
        if sub_state in [SubSystemState.SPEAKING, SubSystemState.PROCESSING]:
            # 场景2: 如果主系统状态为ROBOT_SPEAKING，更新为LISTENING
            if main_state == MainSystemState.ROBOT_SPEAKING:
                log_debug("MANAGER", "场景2: 更新主系统状态为LISTENING", indent=0)
                self.state_manager.transition_main_state(MainSystemState.LISTENING, "检测到可能打断，开始监听")
            # 场景3: 如果主系统状态为IDLE，更新为LISTENING
            elif main_state == MainSystemState.IDLE:
                log_debug("MANAGER", "场景3: 更新主系统状态为LISTENING", indent=0)
                self.state_manager.transition_main_state(MainSystemState.LISTENING, "检测到可能打断，开始监听")

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
        处理ASR部分识别结果（实时显示 + 打断判定）

        Args:
            event: ASR_PARTIAL_RESULT事件
        """
        # 获取事件数据
        event_data = event.data if hasattr(event, 'data') else {}
        partial_text = event_data.get("partial_text", "")
        # 读取置信度（fun_asr_stream._extract_confidence：模型未输出置信度字段时为None）
        confidence = event_data.get("confidence")

        if partial_text:
            # P3 终句debounce保持：用户仍在说话（partial持续到达），取消pending的终句计时
            # （缓冲保留），等下一个final到来时并入缓冲重新计时
            if self._endpoint_debouncer.is_pending():
                log_info("MANAGER",
                         f"[DEBOUNCE] 收到新的partial，保持debounce（重置终句计时）: '{partial_text}'",
                         indent=1)
                self._endpoint_debouncer.cancel()

            # 唯一的真打断判定点：有效性+最小字数+置信度+跨任务冷却全部通过才确认打断
            # （注意：_should_real_interrupt内部会获取interrupt_lock，必须在锁外调用）
            if self._should_real_interrupt(partial_text, confidence):
                # 按任务去重：同一任务只确认一次真实打断（使用锁保护，避免竞争条件）
                should_interrupt = False
                current_task_id = None
                with self.interrupt_lock:
                    current_task_id = self.context_manager.get_current_task_id()

                    # 检查当前task_id是否已经处理过真实打断，避免重复打断
                    if self.interrupted_task_id != current_task_id:
                        should_interrupt = True
                        log_info("MANAGER", f"[有效识别] 检测到有效输入: '{partial_text}'，确认真实打断", indent=0)
                    else:
                        log_info("MANAGER", f"[有效识别] 检测到有效输入: '{partial_text}'，但任务{current_task_id}已处理过打断，跳过", indent=0)

                # 在锁外调用_confirm_real_interrupt，避免死锁
                # 注意：_confirm_real_interrupt内部已经使用interrupt_lock保护
                if should_interrupt:
                    try:
                        self._confirm_real_interrupt()
                        log_info("MANAGER", f"[有效识别] 打断成功，任务{current_task_id}已标记为已打断", indent=1)
                    except Exception as e:
                        log_error("MANAGER", f"确认打断时发生错误: {e}", indent=0)
                        import traceback
                        log_error("MANAGER", f"异常堆栈: {traceback.format_exc()}", indent=0)

            # 实时识别结果走流式通道（同通道原地刷新，Web端固定在实时状态条）
            log_stream('asr_partial', partial_text, label='识别中', category='ASR', indent=0)
    
    def _on_playback_progress(self, event):
        """
        播放进度事件处理（用于状态转换和已播放文本跟踪）

        说明：
        - audio_player.py 传递当前句子和播放位置
        - manager.py 维护 completed_sentences 列表，计算完整播放文本
        - 完整播放文本保存到 context，打断时直接使用
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

        # 首次播放时，立即进行状态转换
        is_first_audio = context.get_extra_data("first_audio_state_transitioned", None) is None
        if is_first_audio:
            # 首次播放，进行状态转换
            current_sub = self.state_manager.get_sub_state()
            current_main = self.state_manager.get_main_state()

            # 检查是否已经转换过（避免重复转换）
            if current_sub != SubSystemState.SPEAKING:
                log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 播放进度事件：首次播放({elapsed_time:.2f}秒)，开始转换状态", indent=1)
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

        # 句子跟踪机制（从context获取或初始化）
        completed_sentences = context.get_extra_data("completed_sentences", [])
        last_sentence_text = context.get_extra_data("last_sentence_text", "")

        # 检测句子切换
        if current_sentence_text != last_sentence_text:
            # 句子切换，将上一个句子标记为完成
            if last_sentence_text:
                if last_sentence_text not in completed_sentences:
                    completed_sentences.append(last_sentence_text)
                    log_debug("MANAGER", f"[PLAYBACK_PROGRESS] 句子切换，将上一个句子添加到已完成列表: '{last_sentence_text}' (总句子数: {len(completed_sentences)})", indent=1)
                # 立即保存回context
                context.set_extra_data("completed_sentences", completed_sentences)
            # 更新last_sentence_text为当前句子
            context.set_extra_data("last_sentence_text", current_sentence_text)

        # 检测句子完成（位置 >= 长度）
        if current_sentence_text and current_sentence_position >= len(current_sentence_text):
            if current_sentence_text not in completed_sentences:
                completed_sentences.append(current_sentence_text)
                log_debug("MANAGER", f"[PLAYBACK_PROGRESS] 当前句子播放完成，添加到已完成列表: '{current_sentence_text}' (总句子数: {len(completed_sentences)})", indent=1)
                context.set_extra_data("completed_sentences", completed_sentences)
            # 清空last_sentence_text
            context.set_extra_data("last_sentence_text", "")

        # 计算完整的已播放文本（已完成句子 + 当前句子已播放部分）
        completed_text = "".join(completed_sentences)
        if current_sentence_text and current_sentence_position > 0:
            # 多算一个字符，确保结尾字符也被包含
            current_end_pos = min(current_sentence_position + 1, len(current_sentence_text))
            full_played_text = completed_text + current_sentence_text[:current_end_pos]
        else:
            full_played_text = completed_text

        # 保存完整的已播放文本到context（打断时直接使用）
        context.set_extra_data("full_played_text", full_played_text)
    
    def _start_asr_thread(self):
        """启动ASR识别线程（线程4）"""
        # 记录当前状态（用于调试）
        main_state = self.state_manager.get_main_state()
        sub_state = self.state_manager.get_sub_state()
        log_info("MANAGER", f"启动ASR线程，当前状态: main={main_state.value}, sub={sub_state.value}", indent=1)
        
        with self.asr_thread_lock:
            # 检查ASR线程是否已在运行
            asr_thread_name = self.current_asr_thread_name
            asr_thread_info = self.thread_manager.get_thread_info(asr_thread_name) if asr_thread_name else None
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
            
            # 启动ASR识别线程（线程名包含任务ID，允许并发）
            try:
                asr_thread_name = f"ASR-Recognition-{current_asr_task_id}"
                self.thread_manager.start_thread(
                    name=asr_thread_name,
                    target=asr_recognition_thread,
                    context=None,
                    thread_type="on_demand",
                    daemon=True
                )
                # 保存当前线程名
                self.current_asr_thread_name = asr_thread_name
                log_info("MANAGER", f"启动ASR识别线程（任务ID: {current_asr_task_id}）", indent=0)
            except ValueError as e:
                # ThreadManager检测到线程冲突
                log_error("MANAGER", f"启动ASR识别线程失败: {e}", indent=0)
                # 再次检查线程状态
                asr_thread_name = self.current_asr_thread_name
                asr_thread_info = self.thread_manager.get_thread_info(asr_thread_name) if asr_thread_name else None
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
            asr_thread_name = self.current_asr_thread_name
            asr_thread_info = self.thread_manager.get_thread_info(asr_thread_name) if asr_thread_name else None
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
    
    
    def _cancel_current_task(self):
        """
        取消当前任务

        根据THREAD_SCHEDULING.md：
        - 通过 ContextManager 取消当前任务（自动使上下文失效）
        - 检查线程启动状态（如果线程还没启动，直接取消）
        - 转换副系统状态: PROCESSING/SPEAKING → NONE
        - 停止音频播放
        """
        # 获取当前任务上下文（在取消前获取，用于记录历史）
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

        # 停止音频播放
        if self.audio_player:
            task_id = current_context.task_id
            log_info("MANAGER", f"[AUDIO_STOP-002] _cancel_current_task() 准备调用 audio_player.stop(task_id={task_id})", indent=1)
            log_info("MANAGER", f"[AUDIO_STOP-002] 调用前状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
            stop_result = self.audio_player.stop(task_id=task_id)
            log_info("MANAGER", f"[AUDIO_STOP-002] audio_player.stop() 返回: {stop_result}", indent=1)

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
                
                # 创建队列(固定大小,无需配置化)
                sentence_queue = queue.Queue(maxsize=100)
                audio_play_queue = queue.Queue(maxsize=50)
                
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
                
                # 首次播放时转换状态
                first_audio = True
                played_duration = 0.0
                playback_interrupted = False  # 播放是否被打断/取消（打断时不能把完整回复记入历史）
                
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
                        
                        # 首次播放时，立即进行状态转换
                        if first_audio:
                            log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] Audio-Player首次播放，立即转换状态", indent=1)
                            # 初始化标记，表示首次播放状态转换还未完成
                            context.set_extra_data("first_audio_state_transitioned", None)
                            # 记录第一个音频播放时间（在实际播放开始之前）
                            first_audio_play_time_recorded = context.get_extra_data("first_audio_play_time_recorded", False)
                            if not first_audio_play_time_recorded:
                                log_info("TIME", "记录第一个音频播放时间", indent=2)
                                self.performance_timer.record_audio_play()
                                # 注意：耗时分析输出已移至第一次play()调用之后，
                                # 避免分析输出位于首音频出队与出声之间
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
                                    
                                    # 首次播放完成后，进行状态转换
                                    current_sub = self.state_manager.get_sub_state()
                                    current_main = self.state_manager.get_main_state()
                                    log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 播放完成/打断时检查：首次播放完成({first_audio_play_duration:.2f}秒)，开始转换状态", indent=1)
                                    log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换前状态: 主={current_main.value}, 副={current_sub.value}", indent=1)
                                    
                                    # 副系统: PROCESSING → SPEAKING
                                    self.state_manager.transition_sub_state(
                                        SubSystemState.SPEAKING,
                                        reason=f"开始播放音频（播放时长{first_audio_play_duration:.2f}秒）"
                                    )
                                    # 主系统状态转换（仅在IDLE时转换，LISTENING时保持不变）
                                    main_state = self.state_manager.get_main_state()
                                    if main_state == MainSystemState.IDLE:
                                        self.state_manager.transition_main_state(
                                            MainSystemState.ROBOT_SPEAKING,
                                            reason=f"开始播放音频（播放时长{first_audio_play_duration:.2f}秒）"
                                        )
                                    # 注意：如果主系统状态是LISTENING，保持不变，等待ASR验证
                                    log_info("MANAGER", f"[STATE_TRANS-FIRST_AUDIO] 转换后状态: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                                    
                                    # 输出耗时分析（罕见路径：状态转换滞后到播放完成回调时才补输出）
                                    # 用first_audio_analysis_outputed标志保证每任务仅输出一次
                                    if not context.get_extra_data("first_audio_analysis_outputed", False):
                                        log_info("TIME", "计算并输出响应耗时分析", indent=2)
                                        self.performance_timer.calculate_and_output()
                                        context.set_extra_data("first_audio_analysis_outputed", True)

                                    # 标记状态已转换
                                    context.set_extra_data("first_audio_state_transitioned", True)
                                
                                first_audio = False
                        
                        # 启动播放
                        was_completed = self.audio_player.play(
                            audio_segment=audio_segment,
                            task_id=context.task_id,
                            on_complete_callback=on_play_complete,
                            current_sentence_text=sentence_text
                        )

                        # 第一次play()调用结束后输出响应耗时分析：
                        # 时间戳已在首音频出声前记录（record_audio_play），
                        # 分析输出移到此处，不再位于首音频出队与出声之间
                        if not context.get_extra_data("first_audio_analysis_outputed", False):
                            log_info("TIME", "计算并输出响应耗时分析", indent=2)
                            self.performance_timer.calculate_and_output()
                            context.set_extra_data("first_audio_analysis_outputed", True)

                        # 检查是否被打断
                        if not was_completed:
                            log_info("MANAGER", "播放被中断", indent=1)
                            playback_interrupted = True
                            break
                        
                        # 检查任务是否被取消
                        context.check_valid()
                        
                    except TaskCancelledException:
                        log_info("MANAGER", "播放被取消", indent=1)
                        playback_interrupted = True
                        break
                    except Exception as e:
                        log_error("MANAGER", f"播放错误: {e}", indent=2)
                        continue
                
                # 记录实际播放时长到extras（供下一话轮判断"上一任务是否实际播出过音频"）
                context.set_extra_data("played_duration", played_duration)

                # 播放完成，转换状态
                # 副系统: SPEAKING → None
                current_sub = self.state_manager.get_sub_state()
                current_main = self.state_manager.get_main_state()
                log_info("MANAGER", f"[STATE_TRANS] Audio-Player线程结束，准备转换状态", indent=1)
                log_info("MANAGER", f"[STATE_TRANS] 当前状态: 主={current_main.value}, 副={current_sub.value}", indent=1)

                # 任务归属检查：真打断的硬停止自P2起延迟到final结果时间
                # （_process_user_input先stop()再create_context并置sub=PROCESSING），
                # 本线程可能在此后50-200ms才从play()轮询中退出（暂停态下0.05s轮询）；
                # 若仍无条件转换，会把新任务的PROCESSING状态覆盖为NONE/IDLE，
                # 使打断确认（_confirm_real_interrupt）在SPEAKING/PROCESSING两分支间落空。
                # 故仅当本任务仍是当前任务时才做终态转换；被新任务取代/取消时只记录历史，不动状态机
                is_final_state_owner = (
                    context.is_valid
                    and self.context_manager.get_current_task_id() == context.task_id
                )
                if is_final_state_owner:
                    self.state_manager.transition_sub_state(
                        SubSystemState.NONE,
                        reason="播放完成"
                    )
                    # 主系统: ROBOT_SPEAKING → IDLE
                    self.state_manager.transition_main_state(
                        MainSystemState.IDLE,
                        reason="播放完成"
                    )
                    log_info("MANAGER", f"[STATE_TRANS] 状态转换完成: 主={self.state_manager.get_main_state().value}, 副={self.state_manager.get_sub_state().value}", indent=1)
                else:
                    log_info("MANAGER", f"[STATE_TRANS] 任务{context.task_id}已被新任务取代/取消，跳过终态转换（避免覆盖新任务的PROCESSING状态）", indent=1)
                
                # 播放完成，输出换行以保持日志清晰（因为之前使用了inline模式）
                print()  # 输出换行
                
                # 记录对话历史（播放了多少就记录多少）
                # 检查是否已经被打断处理逻辑记录过（此处仅为日志，权威检查在_history_record_guard内）
                history_recorded = context.get_extra_data("history_recorded_by_interrupt", False)
                log_info("MANAGER", f"[播放线程完成] 检查是否需要记录历史: history_recorded={history_recorded}, played_duration={played_duration:.2f}秒", indent=1)
                # 注意：played_duration在on_play_complete回调中累加，无论是否被打断都会正确累加
                if not history_recorded:
                    if playback_interrupted:
                        # 播放被打断/被新任务取消：只记录实际播出的部分（用户真正听到的内容）
                        # full_played_text 由播放进度回调实时维护（已完成句子 + 当前句子已播部分）
                        played_text = (context.get_extra_data("full_played_text", "") or "").strip()
                        if len(played_text) < 2:
                            log_info("MANAGER", f"播放被打断且未播出有效内容(已播'{played_text}')，不记录对话历史", indent=1)
                            played_text = ""
                        else:
                            log_info("MANAGER", f"播放被打断，仅记录实际已播放文本: 长度={len(played_text)}字符", indent=1)
                    else:
                        played_text = context.response_text
                        log_info("MANAGER", f"播放完成，使用完整回答文本: 长度={len(played_text) if played_text else 0}字符", indent=1)

                    # 记录到对话历史
                    # 与打断确认路径（_confirm_real_interrupt）互斥：二者都在_history_record_guard内
                    # 检查并置位history_recorded_by_interrupt标志，保证恰好记录一次
                    # （完整播完用 interrupted=False 传全文；被打断用 interrupted=True 传实际播出部分，
                    #   recorder 会为截断的回复追加省略号标记并重置用户输入合并窗口）
                    recorded = False
                    with self._history_record_guard:
                        if (played_text
                                and not context.get_extra_data("history_recorded_by_interrupt", False)):
                            # 去除前导空白字符（包括换行符、空格等）
                            played_text_trimmed = played_text.lstrip()
                            log_debug("LLM", f"audio_player_thread 记录到对话历史: {played_text_trimmed}", indent=0)
                            recorded = self.history.record_assistant(
                                played_text_trimmed, interrupted=playback_interrupted)
                            # 置位标志，防止打断确认路径（若稍后到达）重复记录
                            context.set_extra_data("history_recorded_by_interrupt", True)

                    if recorded:
                        log_info("MANAGER", f"对话历史已记录（播放时长: {played_duration:.2f}秒）", indent=0)
                
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
