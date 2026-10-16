"""
时间记录和性能统计模块

提供性能计时器和简单计时器功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py 第336-344行和第2047-2116行
"""

import time
import sys


# 启用Windows虚拟终端支持（用于ANSI转义码显示颜色）
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)  # 启用虚拟终端处理
    except:
        pass  # 如果失败，继续执行（可能不支持或已启用）


class PerformanceTimer:
    """性能计时器类 - 用于记录和统计响应耗时"""
    
    def __init__(self, enable_output=True, show_details=True, log_func=None, debug=False):
        """
        初始化性能计时器

        Args:
            enable_output: 是否启用性能统计输出
            show_details: 是否显示详细阶段信息（True=显示各阶段详情，False=只显示总耗时）
            log_func: 日志输出函数（如 log_info），设置后分析结果通过它输出（可广播到Web端）；
                      为None时直接 print 到控制台。log_func模式下整个报告压缩为一条单行摘要，
                      不输出多行刷屏/分隔线/颜色
            debug: 是否输出[PERF_TIMER_DEBUG]调试信息（默认False不输出）
        """
        self.enable_output = enable_output
        self.show_details = show_details
        self.log_func = log_func
        self.debug = debug
        self.vad_speech_last_end_time = 0  # 仅作"最近一次VAD结束"记录，不参与reset携带（见reset说明）
        self.asr_recognition_last_end_time = 0  # 仅作"最近一次ASR识别完成"记录，不参与reset携带（见reset说明）
        self.reset()

    def _emit(self, text: str = ""):
        """输出一行分析结果：优先走注入的log_func（每行一条日志），否则print到控制台"""
        if self.log_func is not None:
            import re
            clean = re.sub(r"\x1b\[[0-9;]*m", "", text).strip("\n")
            # 跳过空行和纯分隔线（逐条日志显示时它们只是噪音）
            if not clean or set(clean) <= {"=", " "}:
                return
            self.log_func(clean)
        else:
            print(text, flush=True)

    def reset(self):
        """重置所有时间戳

        注意：VAD/ASR结束时间戳不再从 *_last_end_time 跨reset携带。
        原携带逻辑会让上一轮残留的时间戳流入下一轮分析（92秒类跨轮污染的根源），
        reset后一律清零；本轮时间戳若需跨reset保留（如任务创建时的重置），
        由调用方在reset前后显式保存/恢复（见manager._process_user_input）。
        """
        self.is_paused_reset = False # 是否是暂停重置
        self.vad_speech_end_time = 0  # VAD检测到语音活动结束的时间点（响应耗时分析的起点）
        self.asr_recognition_end_time = 0  # ASR识别完成的时间点
        self.llm_thread_start_time = 0  # LLM线程开始时间
        self.llm_first_sentence_time = 0  # LLM生成第一个句子并放入队列的时间
        self.tts_thread_start_time = 0  # TTS线程开始时间
        self.tts_first_sentence_start_time = 0  # 第一个TTS生成开始的时间
        self.tts_first_sentence_end_time = 0  # 第一个TTS生成结束（包括处理）的时间
        self.tts_first_generation_time = 0  # 第一个TTS纯生成时间（edge_tts生成）
        self.tts_first_processing_time = 0  # 第一个TTS处理时间（裁剪静音等）
        self.first_audio_play_time = 0  # 第一个音频开始播放的时间
    
    def set_paused_reset_flag(self, value):
        """设置暂停重置为True"""
        self.is_paused_reset = value
    
    
    def record_vad_speech_end(self):
        """记录VAD检测到语音活动结束的时间（响应耗时分析的起点）"""
        if self.asr_recognition_end_time > 0:
            # 本轮ASR已先于VAD结束事件完成（如stop_recording/打断场景VAD结束事件晚到），
            # 以较晚的ASR完成时间为准，避免VAD晚到时把起点记到出声之后
            self.vad_speech_end_time = self.asr_recognition_end_time
        else:
            self.vad_speech_end_time = time.time()
        self.vad_speech_last_end_time = time.time()  # 仅作信息记录，不参与reset携带
        self.is_paused_reset = False

    def record_user_speech_end(self):
        """记录ASR识别完成的时间（用户结束语音的时间点）"""
        self.asr_recognition_end_time = time.time()
        self.asr_recognition_last_end_time = time.time()  # 仅作信息记录，不参与reset携带
    
    def record_llm_thread_start(self):
        """记录LLM线程开始时间"""
        # 只要还没有记录过，就记录（即使vad_speech_end_time为0）
        # 因为VAD结束事件可能晚于LLM线程启动，但时间戳仍然有效
        self.llm_thread_start_time = time.time()
    
    def record_llm_first_sentence(self):
        """记录LLM第一句生成时间"""
        # 只要还没有记录过，就记录（即使vad_speech_end_time为0）
        # 因为VAD结束事件可能晚于LLM第一句生成，但时间戳仍然有效
        self.llm_first_sentence_time = time.time()
    
    def record_tts_thread_start(self):
        """记录TTS线程开始时间"""
        # 只要还没有记录过，就记录（即使vad_speech_end_time为0）
        # 因为VAD结束事件可能晚于TTS线程启动，但时间戳仍然有效
        self.tts_thread_start_time = time.time()
    
    def record_tts_start(self):
        """记录第一个TTS生成开始时间"""
        # 检查是否在重置之后（vad_speech_end_time应该已经设置）
        self.tts_first_sentence_start_time = time.time()
    
    def record_tts_generation_time(self, generation_time):
        """记录第一个TTS纯生成时间"""
        # 检查是否在重置之后（vad_speech_end_time应该已经设置）
        self.tts_first_generation_time = generation_time
    
    def record_tts_processing_time(self, processing_time):
        """记录第一个TTS处理时间"""
        # 检查是否在重置之后（vad_speech_end_time应该已经设置）
        self.tts_first_processing_time = processing_time
    
    def record_tts_end(self):
        """记录第一个TTS生成结束时间"""
        # 检查是否在重置之后（vad_speech_end_time应该已经设置）
        self.tts_first_sentence_end_time = time.time()
    
    def record_audio_play(self):
        """记录第一个音频开始播放时间"""
        # 检查是否在重置之后（vad_speech_end_time应该已经设置）
        self.first_audio_play_time = time.time()
    
    def calculate_and_output(self):
        """
        计算并输出性能分析（按三个阶段组织）

        每次调用都会输出（如果数据完整），输出后自动重置以便下次使用。
        输出模式：
        - log_func模式：整个报告压缩为一条单行摘要（总耗时+阶段1/2/3毫秒数），
          构造行文本时不加ANSI颜色/分隔线，避免逐行日志刷屏
        - print模式：保持原有详细逐行输出（含颜色与阶段详情）
        """
        if not self.enable_output:
            # 静默跳过，不输出任何调试信息
            return

        # 确定响应耗时分析的起点：取 VAD结束 与 ASR识别完成 中较晚者
        # 说明：stop_recording/打断等场景下，本轮VAD结束事件可能晚于首音播放才到达，
        # 此时若沿用上一轮残留的VAD时间戳会得出夸大的总耗时（如92秒案例）
        start_time = 0
        start_time_label = ""
        candidates = []
        if self.vad_speech_end_time > 0:
            candidates.append((self.vad_speech_end_time, "VAD检测到语音停止"))
        if self.asr_recognition_end_time > 0:
            candidates.append((self.asr_recognition_end_time, "ASR识别完成"))
        if candidates:
            start_time, start_time_label = max(candidates, key=lambda item: item[0])
        else:
            if self.debug:
                self._emit("[PERF_TIMER_DEBUG] 跳过输出: 无起点时间 "
                           f"(vad_end={self.vad_speech_end_time:.3f}, asr_end={self.asr_recognition_end_time:.3f})")
            return  # 没有起点时间，无法计算

        if self.first_audio_play_time == 0:
            if self.debug:
                self._emit("[PERF_TIMER_DEBUG] 跳过输出: first_audio_play_time=0")
            return  # 没有播放时间，无法计算
        
        # 计算总响应耗时
        total_latency = self.first_audio_play_time - start_time
        
        # 阶段1：VAD结束 -> LLM线程开始
        stage1_time = 0
        asr_time = 0
        thread_startup_time = 0

        # 阶段锚定修正：线程启动/等待子阶段统一锚定到所选起点 start_time = max(VAD, ASR)，
        # 不再按 asr_end/stage1_start 双轨锚定（原写法在两轨不一致时会产生歧义）
        # ASR子阶段：仅当 vad_speech_end_time < asr_recognition_end_time 时显示
        # （说明ASR识别确实发生在VAD结束之后、属于本轮；reset已不跨轮携带时间戳，
        # 两个时间戳均>0即为本轮记录）
        if (self.vad_speech_end_time > 0 and self.asr_recognition_end_time > 0
                and self.vad_speech_end_time < self.asr_recognition_end_time):
            asr_time = self.asr_recognition_end_time - self.vad_speech_end_time
            if asr_time < 0:
                asr_time = 0

        # 线程启动/等待：从所选起点到LLM线程开始（仅在LLM线程开始晚于起点时计算）
        if self.llm_thread_start_time > 0 and self.llm_thread_start_time >= start_time:
            thread_startup_time = self.llm_thread_start_time - start_time
            if thread_startup_time < 0:
                thread_startup_time = 0

        stage1_time = asr_time + thread_startup_time
        # 【未打印的情况1】如果 stage1_time == 0，整个阶段1都不打印
        #   原因：阶段1总时间为0意味着该阶段可以忽略，asr_time 和 thread_startup_time 的值也不会显示
        
        # 阶段2：LLM线程开始 -> 第一句话传给TTS
        stage2_time = 0
        llm_generation_time = 0
        queue_wait_time = 0
        
        # LLM生成时间：从LLM线程开始到第一句生成
        if (self.llm_thread_start_time > 0 and self.llm_first_sentence_time > 0 and
            self.llm_thread_start_time >= start_time and self.llm_first_sentence_time >= start_time):
            llm_generation_time = self.llm_first_sentence_time - self.llm_thread_start_time
            # 确保时间差为正数
            if llm_generation_time < 0:
                llm_generation_time = 0
        
        # 队列等待时间：从LLM第一句生成到TTS线程开始
        if (self.tts_thread_start_time > 0 and self.llm_first_sentence_time > 0 and
            self.tts_thread_start_time >= start_time and self.llm_first_sentence_time >= start_time and
            self.tts_thread_start_time >= self.llm_first_sentence_time):
            queue_wait_time = self.tts_thread_start_time - self.llm_first_sentence_time
        elif (self.tts_thread_start_time > 0 and self.llm_first_sentence_time > 0 and
              self.tts_thread_start_time < self.llm_first_sentence_time):
            # TTS线程在LLM第一句之前启动（不应该发生，但处理异常情况）
            queue_wait_time = 0
        elif (self.tts_thread_start_time > 0 and self.llm_thread_start_time > 0 and
              self.tts_thread_start_time >= start_time and self.llm_thread_start_time >= start_time):
            # 如果没有llm_first_sentence_time，使用llm_thread_start_time到tts_thread_start_time的时间
            # 这种情况下，整个阶段2时间就是线程启动到TTS线程开始的时间
            if llm_generation_time == 0:
                # 如果llm_generation_time为0，说明没有记录第一句生成时间
                # 此时queue_wait_time就是整个阶段2的时间
                queue_wait_time = self.tts_thread_start_time - self.llm_thread_start_time
                if queue_wait_time < 0:
                    queue_wait_time = 0
        
        stage2_time = llm_generation_time + queue_wait_time
        # 【未打印的情况2】如果 stage2_time == 0，整个阶段2都不打印
        #   原因：阶段2总时间为0意味着该阶段可以忽略，llm_generation_time 和 queue_wait_time 的值也不会显示
        
        # 阶段3：TTS线程开始 -> 第一句话开始播放
        stage3_time = 0
        tts_generation_time = 0
        tts_processing_time = 0
        playback_startup_time = 0
        
        # 使用实际记录的TTS生成和处理时间（这些是相对时间，不是绝对时间戳）
        if self.tts_first_generation_time > 0:
            tts_generation_time = self.tts_first_generation_time
        if self.tts_first_processing_time > 0:
            tts_processing_time = self.tts_first_processing_time
        
        # 播放启动时间：从TTS处理完成到播放开始
        playback_startup_time = 0
        # 验证时间戳的有效性：所有时间戳都应该在start_time之后
        if (self.tts_thread_start_time > 0 and self.first_audio_play_time > 0 and
            self.tts_thread_start_time >= start_time and self.first_audio_play_time >= start_time):
            # 计算TTS总耗时（生成+处理）
            # 【未打印】tts_total_time：仅用于内部计算playback_startup_time，不单独打印（已分解为生成和处理两部分）
            tts_total_time = tts_generation_time + tts_processing_time
            if self.tts_first_sentence_end_time > 0 and self.tts_first_sentence_end_time >= start_time:
                # 使用结束时间计算
                playback_startup_time = self.first_audio_play_time - self.tts_first_sentence_end_time
                if playback_startup_time < 0:
                    playback_startup_time = 0
            elif self.tts_first_sentence_start_time > 0 and self.tts_first_sentence_start_time >= start_time:
                # 如果没有记录结束时间，使用开始时间+总耗时估算
                # 【未打印】estimated_end_time：仅用于内部计算playback_startup_time，不单独打印
                estimated_end_time = self.tts_first_sentence_start_time + tts_total_time
                playback_startup_time = self.first_audio_play_time - estimated_end_time
                if playback_startup_time < 0:
                    playback_startup_time = 0
            elif self.tts_thread_start_time >= start_time:
                # 如果连开始时间都没有，使用线程开始时间估算
                # 【未打印】estimated_end_time：仅用于内部计算playback_startup_time，不单独打印
                estimated_end_time = self.tts_thread_start_time + tts_total_time
                playback_startup_time = self.first_audio_play_time - estimated_end_time
                if playback_startup_time < 0:
                    playback_startup_time = 0
        
        stage3_time = tts_generation_time + tts_processing_time + playback_startup_time
        # 【未打印的情况3】如果 stage3_time == 0，整个阶段3都不打印
        #   原因：阶段3总时间为0意味着该阶段可以忽略，tts_generation_time、tts_processing_time 和 playback_startup_time 的值也不会显示
        
        # 验证阶段3时间不超过总耗时（如果超过，说明时间戳有问题，需要重新计算）
        if stage3_time > total_latency and total_latency > 0:
            # 如果阶段3时间超过总耗时，说明时间戳有问题
            # 尝试使用时间戳重新计算
            if (self.tts_thread_start_time > 0 and self.first_audio_play_time > 0 and
                self.tts_thread_start_time >= start_time and self.first_audio_play_time >= start_time):
                # 使用TTS线程开始时间到播放开始时间的差值
                # 【未打印】stage3_time_corrected：仅用于时间戳异常时的修正计算，修正后的值会更新到stage3_time并显示
                stage3_time_corrected = self.first_audio_play_time - self.tts_thread_start_time
                if stage3_time_corrected > 0 and stage3_time_corrected <= total_latency:
                    # 重新计算playback_startup_time
                    if self.tts_first_sentence_end_time > 0 and self.tts_first_sentence_end_time >= start_time:
                        playback_startup_time = self.first_audio_play_time - self.tts_first_sentence_end_time
                        if playback_startup_time < 0:
                            playback_startup_time = 0
                    # 重新分配tts_generation_time和tts_processing_time
                    if stage3_time_corrected > playback_startup_time:
                        # 【未打印】remaining_time：仅用于重新分配TTS时间，不单独打印（最终体现在tts_generation_time和tts_processing_time中）
                        remaining_time = stage3_time_corrected - playback_startup_time
                        # 如果记录了实际的生成和处理时间，使用它们；否则平均分配
                        if tts_generation_time > 0 and tts_processing_time > 0:
                            # 保持比例，但确保总和不超过remaining_time
                            total_tts_time = tts_generation_time + tts_processing_time
                            if total_tts_time > 0 and total_tts_time <= remaining_time:
                                # 保持原有比例
                                pass  # 使用已计算的tts_generation_time和tts_processing_time
                            else:
                                # 按比例缩放
                                if total_tts_time > 0:
                                    # 【未打印】scale：仅用于时间比例缩放计算，不单独打印
                                    scale = remaining_time / total_tts_time
                                    tts_generation_time = tts_generation_time * scale
                                    tts_processing_time = tts_processing_time * scale
                                else:
                                    tts_generation_time = remaining_time * 0.9  # 90%用于生成
                                    tts_processing_time = remaining_time * 0.1  # 10%用于处理
                        else:
                            tts_generation_time = remaining_time * 0.9
                            tts_processing_time = remaining_time * 0.1
                    else:
                        tts_generation_time = 0
                        tts_processing_time = 0
                        playback_startup_time = stage3_time_corrected
                    
                    stage3_time = stage3_time_corrected
        
        # 计算占比
        # 【未打印的情况5】所有百分比变量都计算了，但只有当对应的阶段/子阶段被打印时才显示
        #   原因：百分比只在有意义的阶段显示，避免显示0%的无用信息
        if total_latency <= 0:
            if self.debug:
                self._emit(f"[PERF_TIMER_DEBUG] 跳过输出: total_latency={total_latency*1000:.1f}ms "
                           f"(start={start_time:.3f} {start_time_label}, play={self.first_audio_play_time:.3f})")
            # 总耗时无效，无法计算占比；重置后返回
            self.reset()
            return

        # log_func模式：整个报告压缩成一条单行摘要（总耗时 + 阶段1/2/3毫秒数），
        # 在构造行文本时就不加ANSI颜色/分隔线（而不是事后strip），避免逐行日志刷屏
        if self.log_func is not None:
            summary = (f"[响应耗时] 总计 {total_latency*1000:.1f}ms ({start_time_label} → 机器人说出第一个字) | "
                       f"阶段1(VAD结束→LLM线程开始) {stage1_time*1000:.1f}ms | "
                       f"阶段2(LLM线程开始→首句给TTS) {stage2_time*1000:.1f}ms | "
                       f"阶段3(TTS线程开始→出声) {stage3_time*1000:.1f}ms")
            self.log_func(summary)
            # 输出后自动重置，以便下次对话轮次使用
            self.reset()
            return

        # print模式：保持原有详细逐行输出
        # 阶段百分比做合理钳制：时间戳异常时阶段时间可能超过总耗时（钳到100.0）或为负（钳到0.0）
        def _clamp_percent(part: float) -> float:
            if part <= 0:
                return 0.0
            return min(max((part / total_latency) * 100, 0.0), 100.0)

        if total_latency > 0:
            stage1_percent = _clamp_percent(stage1_time)
            stage2_percent = _clamp_percent(stage2_time)
            stage3_percent = _clamp_percent(stage3_time)
            asr_percent = _clamp_percent(asr_time)
            thread_startup_percent = _clamp_percent(thread_startup_time)
            llm_gen_percent = _clamp_percent(llm_generation_time)
            queue_wait_percent = _clamp_percent(queue_wait_time)
            tts_gen_percent = _clamp_percent(tts_generation_time)
            tts_proc_percent = _clamp_percent(tts_processing_time)
            playback_startup_percent = _clamp_percent(playback_startup_time)
            
            # 使用颜色输出（ANSI转义码）
            red_start = "\033[31m"
            yellow_start = "\033[33m"
            green_start = "\033[32m"
            cyan_start = "\033[36m"
            magenta_start = "\033[35m"
            blue_start = "\033[34m"
            reset = "\033[0m"
            
            # 格式化时间戳为可读格式
            def format_timestamp(ts):
                """格式化时间戳为可读格式 [HH:MM:SS.mmm]"""
                if ts <= 0:
                    return "[N/A]"
                import datetime
                dt = datetime.datetime.fromtimestamp(ts)
                return f"[{dt.strftime('%H:%M:%S.%f')[:-3]}]"
            
            self._emit(f"\n{red_start}{'='*80}{reset}")
            self._emit(f"{red_start}[响应耗时详细分析]{reset}")
            self._emit(f"{red_start}{'='*80}{reset}")
            self._emit(f"{yellow_start}总响应耗时: {total_latency*1000:.1f}ms ({start_time_label} → 机器人说出第一个字){reset}")
            
            # 如果不显示详细信息，只输出总耗时
            if not self.show_details:
                self._emit(f"{red_start}{'='*80}{reset}\n")
                self.reset()
                return
            
            # 显示详细信息（事件时间戳和各阶段详情）
            self._emit(f"{yellow_start}  起始事件:  {format_timestamp(start_time)} - {start_time_label}{reset}")
            self._emit(f"{yellow_start}  结束事件:  {format_timestamp(self.first_audio_play_time)} - 机器人说出第一个字{reset}\n")
            
            # 阶段1：VAD结束 -> LLM线程开始
            if stage1_time > 0:
                # 阶段1起始事件锚定到所选起点 max(VAD,ASR)（与分析起点一致，不再独立选取）
                stage1_start_ts = start_time
                stage1_start_label = start_time_label

                self._emit(f"{blue_start}阶段1: VAD结束 → LLM线程开始 ({stage1_time*1000:.1f}ms, {stage1_percent:.1f}%){reset}")
                self._emit(f"{blue_start}  起始事件:  {format_timestamp(stage1_start_ts)} - {stage1_start_label}{reset}")
                self._emit(f"{blue_start}  结束事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                if asr_time > 0:
                    # ASR子阶段：仅在VAD结束早于ASR识别完成（属于本轮）时显示
                    asr_start = self.vad_speech_end_time
                    asr_end = self.asr_recognition_end_time
                    self._emit(f"{blue_start}  ├─ ASR识别: {asr_time*1000:.1f}ms ({asr_percent:.1f}%){reset}")
                    self._emit(f"{blue_start}  │  起始事件:  {format_timestamp(asr_start)} - VAD检测到语音停止{reset}")
                    self._emit(f"{blue_start}  │  结束事件:  {format_timestamp(asr_end)} - ASR识别完成{reset}")
                if thread_startup_time > 0:
                    # 线程启动子阶段锚定到所选起点（asr_time>0时起点即ASR识别完成时间）
                    thread_start = start_time
                    thread_end = self.llm_thread_start_time
                    if asr_time > 0:
                        self._emit(f"{cyan_start}  └─ 线程启动/等待: {thread_startup_time*1000:.1f}ms ({thread_startup_percent:.1f}%){reset}")
                        self._emit(f"{cyan_start}     起始事件:  {format_timestamp(thread_start)} - ASR识别完成{reset}")
                        self._emit(f"{cyan_start}     结束事件:  {format_timestamp(thread_end)} - LLM线程开始{reset}")
                    else:
                        self._emit(f"{cyan_start}  └─ 线程启动/等待: {thread_startup_time*1000:.1f}ms ({thread_startup_percent:.1f}%){reset}")
                        self._emit(f"{cyan_start}     起始事件:  {format_timestamp(thread_start)} - {stage1_start_label}{reset}")
                        self._emit(f"{cyan_start}     结束事件:  {format_timestamp(thread_end)} - LLM线程开始{reset}")
                self._emit()
            
            # 阶段2：LLM线程开始 -> 第一句话传给TTS
            if stage2_time > 0:
                # 【未打印】stage2_end_time：仅用于打印阶段2的结束事件时间戳，不作为独立指标打印
                stage2_end_time = self.tts_thread_start_time if self.tts_thread_start_time > 0 else self.llm_first_sentence_time
                self._emit(f"{green_start}阶段2: LLM线程开始 → 第一句话传给TTS ({stage2_time*1000:.1f}ms, {stage2_percent:.1f}%){reset}")
                self._emit(f"{green_start}  起始事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                self._emit(f"{green_start}  结束事件:  {format_timestamp(stage2_end_time)} - 第一句话传给TTS{reset}")
                if llm_generation_time > 0:
                    self._emit(f"{green_start}  ├─ LLM生成(第一句): {llm_generation_time*1000:.1f}ms ({llm_gen_percent:.1f}%){reset}")
                    self._emit(f"{green_start}  │  起始事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                    self._emit(f"{green_start}  │  结束事件:  {format_timestamp(self.llm_first_sentence_time)} - LLM生成第一句{reset}")
                if queue_wait_time > 0:
                    self._emit(f"{cyan_start}  └─ 队列等待: {queue_wait_time*1000:.1f}ms ({queue_wait_percent:.1f}%){reset}")
                    self._emit(f"{cyan_start}     起始事件:  {format_timestamp(self.llm_first_sentence_time)} - LLM生成第一句{reset}")
                    self._emit(f"{cyan_start}     结束事件:  {format_timestamp(self.tts_thread_start_time)} - TTS线程开始{reset}")
                elif queue_wait_time < 0:
                    # 【未打印的情况4】queue_wait_time < 0 时不显示
                    #   原因：负数表示时间戳异常（TTS线程启动时间早于LLM第一句生成时间），数据无效，不应显示
                    pass
                self._emit()
            
            # 阶段3：TTS线程开始 -> 第一句话开始播放
            if stage3_time > 0:
                self._emit(f"{magenta_start}阶段3: TTS线程开始 → 第一句话开始播放 ({stage3_time*1000:.1f}ms, {stage3_percent:.1f}%){reset}")
                self._emit(f"{magenta_start}  起始事件:  {format_timestamp(self.tts_thread_start_time)} - TTS线程开始{reset}")
                self._emit(f"{magenta_start}  结束事件:  {format_timestamp(self.first_audio_play_time)} - 第一句话开始播放{reset}")
                if tts_generation_time > 0:
                    self._emit(f"{magenta_start}  ├─ TTS生成(edge_tts): {tts_generation_time*1000:.1f}ms ({tts_gen_percent:.1f}%){reset}")
                    if self.tts_first_sentence_start_time > 0 and self.tts_first_sentence_end_time > 0:
                        self._emit(f"{magenta_start}  │  起始事件:  {format_timestamp(self.tts_first_sentence_start_time)} - TTS生成开始{reset}")
                        self._emit(f"{magenta_start}  │  结束事件:  {format_timestamp(self.tts_first_sentence_end_time)} - TTS生成结束{reset}")
                    else:
                        self._emit(f"{magenta_start}  │  起始事件:  [N/A] - TTS生成开始 (使用相对时间){reset}")
                        self._emit(f"{magenta_start}  │  结束事件:  [N/A] - TTS生成结束 (使用相对时间){reset}")
                if tts_processing_time > 0:
                    self._emit(f"{magenta_start}  ├─ TTS处理(裁剪静音): {tts_processing_time*1000:.1f}ms ({tts_proc_percent:.1f}%){reset}")
                    if self.tts_first_sentence_end_time > 0:
                        # 【未打印】tts_proc_start：仅用于打印时间戳，不作为独立指标打印（已包含在TTS处理的起始事件中）
                        tts_proc_start = self.tts_first_sentence_end_time - tts_processing_time
                        self._emit(f"{magenta_start}  │  起始事件:  {format_timestamp(tts_proc_start)} - TTS生成结束{reset}")
                        self._emit(f"{magenta_start}  │  结束事件:  {format_timestamp(self.tts_first_sentence_end_time)} - TTS处理完成{reset}")
                    else:
                        self._emit(f"{magenta_start}  │  起始事件:  [N/A] - TTS生成结束 (使用相对时间){reset}")
                        self._emit(f"{magenta_start}  │  结束事件:  [N/A] - TTS处理完成 (使用相对时间){reset}")
                if playback_startup_time > 0 or (tts_processing_time > 0 and playback_startup_time == 0):
                    self._emit(f"{yellow_start}  └─ 播放启动: {playback_startup_time*1000:.1f}ms ({playback_startup_percent:.1f}%){reset}")
                    # 【未打印】playback_start：仅用于打印时间戳，不作为独立指标打印（已包含在播放启动的起始事件中）
                    playback_start = self.tts_first_sentence_end_time if self.tts_first_sentence_end_time > 0 else (self.tts_first_sentence_start_time + tts_generation_time + tts_processing_time if self.tts_first_sentence_start_time > 0 else self.tts_thread_start_time + tts_generation_time + tts_processing_time)
                    if playback_start > 0:
                        self._emit(f"{yellow_start}     起始事件:  {format_timestamp(playback_start)} - TTS处理完成{reset}")
                    else:
                        self._emit(f"{yellow_start}     起始事件:  [N/A] - TTS处理完成 (使用相对时间){reset}")
                    self._emit(f"{yellow_start}     结束事件:  {format_timestamp(self.first_audio_play_time)} - 第一句话开始播放{reset}")
            
            self._emit(f"{red_start}{'='*80}{reset}\n")
        
        # 输出后自动重置，以便下次对话轮次使用
        self.reset()


class Timer:
    """简单计时器类 - 用于测量代码执行时间"""
    
    def __init__(self):
        """初始化计时器"""
        self.start_time = None
        self.end_time = None
    
    def start(self):
        """开始计时"""
        self.start_time = time.time()
        self.end_time = None
    
    def stop(self):
        """结束计时"""
        if self.start_time is not None:
            self.end_time = time.time()
    
    def elapsed(self):
        """
        获取耗时（毫秒）
        
        Returns:
            耗时（毫秒），如果未开始或未结束则返回None
        """
        if self.start_time is None:
            return None
        
        end = self.end_time if self.end_time is not None else time.time()
        return (end - self.start_time) * 1000
    
    def elapsed_seconds(self):
        """
        获取耗时（秒）
        
        Returns:
            耗时（秒），如果未开始或未结束则返回None
        """
        elapsed_ms = self.elapsed()
        return elapsed_ms / 1000.0 if elapsed_ms is not None else None
    
    def reset(self):
        """重置计时器"""
        self.start_time = None
        self.end_time = None

