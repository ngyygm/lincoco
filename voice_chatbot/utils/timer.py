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
    
    def __init__(self, enable_output=True):
        """
        初始化性能计时器
        
        Args:
            enable_output: 是否启用性能统计输出
        """
        self.enable_output = enable_output
        self.vad_speech_last_end_time = 0
        self.asr_recognition_last_end_time = 0  # ASR识别完成的时间点
        self.reset()
    
    def reset(self):
        """重置所有时间戳"""
        self.is_paused_reset = False # 是否是暂停重置
        self.vad_speech_end_time = self.vad_speech_last_end_time  # VAD检测到语音活动结束的时间点（响应耗时分析的起点）
        self.asr_recognition_end_time = self.asr_recognition_last_end_time  # ASR识别完成的时间点
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
            self.vad_speech_end_time = self.asr_recognition_end_time
        else:
            self.vad_speech_end_time = time.time()
        self.vad_speech_last_end_time = time.time()
        self.is_paused_reset = False
    
    def record_user_speech_end(self):
        """记录ASR识别完成的时间（用户结束语音的时间点）"""
        self.asr_recognition_end_time = time.time()
        self.asr_recognition_last_end_time = time.time()
    
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
        
        每次调用都会输出（如果数据完整），输出后自动重置以便下次使用
        """
        if not self.enable_output:
            return
        
        # 确定响应耗时分析的起点：优先使用vad_speech_end_time，否则回退到asr_recognition_end_time
        start_time = 0
        start_time_label = ""
        if self.vad_speech_end_time > 0:
            start_time = self.vad_speech_end_time
            start_time_label = "VAD检测到语音停止"
        elif self.asr_recognition_end_time > 0:
            start_time = self.asr_recognition_end_time
            start_time_label = "ASR识别完成"
        else:
            return  # 没有起点时间，无法计算
        
        if self.first_audio_play_time == 0:
            return  # 没有播放时间，无法计算
        
        # 计算总响应耗时
        total_latency = self.first_audio_play_time - start_time
        
        # 阶段1：VAD结束 -> LLM线程开始
        stage1_time = 0
        asr_time = 0
        thread_startup_time = 0
        
        # 确定阶段1的起点：优先使用vad_speech_end_time，如果没有则使用asr_recognition_end_time
        # 【未打印】stage1_start_time：仅用于内部计算，不单独打印（已包含在阶段1的起始事件中）
        stage1_start_time = self.vad_speech_end_time if self.vad_speech_end_time > 0 else self.asr_recognition_end_time
        
        # ASR识别耗时：从VAD检测到语音活动结束到ASR识别完成
        if self.vad_speech_end_time > 0 and self.asr_recognition_end_time > 0:
            asr_time = self.asr_recognition_end_time - self.vad_speech_end_time
            if asr_time < 0:
                asr_time = 0
        
        # LLM线程启动时间：从ASR识别完成到LLM线程开始
        # 【未打印】asr_end_time：仅用于内部计算thread_startup_time，不单独打印（已在ASR识别部分打印）
        asr_end_time = self.asr_recognition_end_time
        # 验证时间戳的有效性：优先使用asr_end_time，如果没有则使用stage1_start_time
        if self.llm_thread_start_time > 0 and self.llm_thread_start_time >= start_time:
            if asr_end_time > 0 and asr_end_time >= start_time:
                # 使用ASR结束时间计算
                thread_startup_time = self.llm_thread_start_time - asr_end_time
                if thread_startup_time < 0:
                    thread_startup_time = 0
            elif stage1_start_time > 0 and stage1_start_time >= start_time:
                # 如果没有ASR结束时间，使用stage1_start_time计算（从阶段1起点到LLM线程开始）
                thread_startup_time = self.llm_thread_start_time - stage1_start_time
                if thread_startup_time < 0:
                    thread_startup_time = 0
            else:
                # 如果连stage1_start_time都没有，使用start_time计算
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
        if total_latency > 0:
            stage1_percent = (stage1_time / total_latency) * 100 if stage1_time > 0 else 0
            stage2_percent = (stage2_time / total_latency) * 100 if stage2_time > 0 else 0
            stage3_percent = (stage3_time / total_latency) * 100 if stage3_time > 0 else 0
            asr_percent = (asr_time / total_latency) * 100 if asr_time > 0 else 0
            thread_startup_percent = (thread_startup_time / total_latency) * 100 if thread_startup_time > 0 else 0
            llm_gen_percent = (llm_generation_time / total_latency) * 100 if llm_generation_time > 0 else 0
            queue_wait_percent = (queue_wait_time / total_latency) * 100 if queue_wait_time > 0 else 0
            tts_gen_percent = (tts_generation_time / total_latency) * 100 if tts_generation_time > 0 else 0
            tts_proc_percent = (tts_processing_time / total_latency) * 100 if tts_processing_time > 0 else 0
            playback_startup_percent = (playback_startup_time / total_latency) * 100 if playback_startup_time > 0 else 0
            
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
            
            print(f"\n{red_start}{'='*80}{reset}")
            print(f"{red_start}[响应耗时详细分析]{reset}")
            print(f"{red_start}{'='*80}{reset}")
            print(f"{yellow_start}总响应耗时: {total_latency*1000:.1f}ms ({start_time_label} → 机器人说出第一个字){reset}")
            print(f"{yellow_start}  起始事件:  {format_timestamp(start_time)} - {start_time_label}{reset}")
            print(f"{yellow_start}  结束事件:  {format_timestamp(self.first_audio_play_time)} - 机器人说出第一个字{reset}\n")
            
            # 阶段1：VAD结束 -> LLM线程开始
            if stage1_time > 0:
                # 确定阶段1的起始事件标签和时间戳
                stage1_start_label = "VAD检测到语音停止"
                stage1_start_ts = self.vad_speech_end_time
                if stage1_start_ts <= 0:
                    # 如果VAD结束时间没有记录，使用ASR识别完成时间
                    stage1_start_ts = self.asr_recognition_end_time
                    stage1_start_label = "ASR识别完成"
                
                print(f"{blue_start}阶段1: VAD结束 → LLM线程开始 ({stage1_time*1000:.1f}ms, {stage1_percent:.1f}%){reset}")
                print(f"{blue_start}  起始事件:  {format_timestamp(stage1_start_ts)} - {stage1_start_label}{reset}")
                print(f"{blue_start}  结束事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                if asr_time > 0:
                    asr_start = self.vad_speech_end_time
                    asr_end = self.asr_recognition_end_time
                    print(f"{blue_start}  ├─ ASR识别: {asr_time*1000:.1f}ms ({asr_percent:.1f}%){reset}")
                    print(f"{blue_start}  │  起始事件:  {format_timestamp(asr_start)} - VAD检测到语音停止{reset}")
                    print(f"{blue_start}  │  结束事件:  {format_timestamp(asr_end)} - ASR识别完成{reset}")
                if thread_startup_time > 0:
                    thread_start = self.asr_recognition_end_time
                    thread_end = self.llm_thread_start_time
                    if asr_time > 0:
                        print(f"{cyan_start}  └─ 线程启动/等待: {thread_startup_time*1000:.1f}ms ({thread_startup_percent:.1f}%){reset}")
                        print(f"{cyan_start}     起始事件:  {format_timestamp(thread_start)} - ASR识别完成{reset}")
                        print(f"{cyan_start}     结束事件:  {format_timestamp(thread_end)} - LLM线程开始{reset}")
                    else:
                        print(f"{cyan_start}  └─ 线程启动/等待: {thread_startup_time*1000:.1f}ms ({thread_startup_percent:.1f}%){reset}")
                        print(f"{cyan_start}     起始事件:  {format_timestamp(thread_start)} - VAD检测到语音停止{reset}")
                        print(f"{cyan_start}     结束事件:  {format_timestamp(thread_end)} - LLM线程开始{reset}")
                print()
            
            # 阶段2：LLM线程开始 -> 第一句话传给TTS
            if stage2_time > 0:
                # 【未打印】stage2_end_time：仅用于打印阶段2的结束事件时间戳，不作为独立指标打印
                stage2_end_time = self.tts_thread_start_time if self.tts_thread_start_time > 0 else self.llm_first_sentence_time
                print(f"{green_start}阶段2: LLM线程开始 → 第一句话传给TTS ({stage2_time*1000:.1f}ms, {stage2_percent:.1f}%){reset}")
                print(f"{green_start}  起始事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                print(f"{green_start}  结束事件:  {format_timestamp(stage2_end_time)} - 第一句话传给TTS{reset}")
                if llm_generation_time > 0:
                    print(f"{green_start}  ├─ LLM生成(第一句): {llm_generation_time*1000:.1f}ms ({llm_gen_percent:.1f}%){reset}")
                    print(f"{green_start}  │  起始事件:  {format_timestamp(self.llm_thread_start_time)} - LLM线程开始{reset}")
                    print(f"{green_start}  │  结束事件:  {format_timestamp(self.llm_first_sentence_time)} - LLM生成第一句{reset}")
                if queue_wait_time > 0:
                    print(f"{cyan_start}  └─ 队列等待: {queue_wait_time*1000:.1f}ms ({queue_wait_percent:.1f}%){reset}")
                    print(f"{cyan_start}     起始事件:  {format_timestamp(self.llm_first_sentence_time)} - LLM生成第一句{reset}")
                    print(f"{cyan_start}     结束事件:  {format_timestamp(self.tts_thread_start_time)} - TTS线程开始{reset}")
                elif queue_wait_time < 0:
                    # 【未打印的情况4】queue_wait_time < 0 时不显示
                    #   原因：负数表示时间戳异常（TTS线程启动时间早于LLM第一句生成时间），数据无效，不应显示
                    pass
                print()
            
            # 阶段3：TTS线程开始 -> 第一句话开始播放
            if stage3_time > 0:
                print(f"{magenta_start}阶段3: TTS线程开始 → 第一句话开始播放 ({stage3_time*1000:.1f}ms, {stage3_percent:.1f}%){reset}")
                print(f"{magenta_start}  起始事件:  {format_timestamp(self.tts_thread_start_time)} - TTS线程开始{reset}")
                print(f"{magenta_start}  结束事件:  {format_timestamp(self.first_audio_play_time)} - 第一句话开始播放{reset}")
                if tts_generation_time > 0:
                    print(f"{magenta_start}  ├─ TTS生成(edge_tts): {tts_generation_time*1000:.1f}ms ({tts_gen_percent:.1f}%){reset}")
                    if self.tts_first_sentence_start_time > 0 and self.tts_first_sentence_end_time > 0:
                        print(f"{magenta_start}  │  起始事件:  {format_timestamp(self.tts_first_sentence_start_time)} - TTS生成开始{reset}")
                        print(f"{magenta_start}  │  结束事件:  {format_timestamp(self.tts_first_sentence_end_time)} - TTS生成结束{reset}")
                    else:
                        print(f"{magenta_start}  │  起始事件:  [N/A] - TTS生成开始 (使用相对时间){reset}")
                        print(f"{magenta_start}  │  结束事件:  [N/A] - TTS生成结束 (使用相对时间){reset}")
                if tts_processing_time > 0:
                    print(f"{magenta_start}  ├─ TTS处理(裁剪静音): {tts_processing_time*1000:.1f}ms ({tts_proc_percent:.1f}%){reset}")
                    if self.tts_first_sentence_end_time > 0:
                        # 【未打印】tts_proc_start：仅用于打印时间戳，不作为独立指标打印（已包含在TTS处理的起始事件中）
                        tts_proc_start = self.tts_first_sentence_end_time - tts_processing_time
                        print(f"{magenta_start}  │  起始事件:  {format_timestamp(tts_proc_start)} - TTS生成结束{reset}")
                        print(f"{magenta_start}  │  结束事件:  {format_timestamp(self.tts_first_sentence_end_time)} - TTS处理完成{reset}")
                    else:
                        print(f"{magenta_start}  │  起始事件:  [N/A] - TTS生成结束 (使用相对时间){reset}")
                        print(f"{magenta_start}  │  结束事件:  [N/A] - TTS处理完成 (使用相对时间){reset}")
                if playback_startup_time > 0 or (tts_processing_time > 0 and playback_startup_time == 0):
                    print(f"{yellow_start}  └─ 播放启动: {playback_startup_time*1000:.1f}ms ({playback_startup_percent:.1f}%){reset}")
                    # 【未打印】playback_start：仅用于打印时间戳，不作为独立指标打印（已包含在播放启动的起始事件中）
                    playback_start = self.tts_first_sentence_end_time if self.tts_first_sentence_end_time > 0 else (self.tts_first_sentence_start_time + tts_generation_time + tts_processing_time if self.tts_first_sentence_start_time > 0 else self.tts_thread_start_time + tts_generation_time + tts_processing_time)
                    if playback_start > 0:
                        print(f"{yellow_start}     起始事件:  {format_timestamp(playback_start)} - TTS处理完成{reset}")
                    else:
                        print(f"{yellow_start}     起始事件:  [N/A] - TTS处理完成 (使用相对时间){reset}")
                    print(f"{yellow_start}     结束事件:  {format_timestamp(self.first_audio_play_time)} - 第一句话开始播放{reset}")
            
            print(f"{red_start}{'='*80}{reset}\n")
        
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

