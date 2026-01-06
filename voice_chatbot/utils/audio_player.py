"""
音频播放器模块

提供Pygame音频播放、暂停/恢复、停止、打断检测、播放完成回调、音量控制、播放进度跟踪等功能
参考：56_Lincoco_streamASR_openai_edgetts_realTime.py
"""

import io
import time
import threading
from typing import Optional, Callable, Dict, Any
from enum import Enum

import pygame
from pydub import AudioSegment

from voice_chatbot.utils import log_info, log_warn, log_error, log_debug

# 延迟导入 dialogue.events 以避免循环导入
# from voice_chatbot.dialogue.events import EventBus, EventType, create_event


class PlaybackStatus(Enum):
    """播放状态枚举"""
    IDLE = "idle"           # 空闲
    PLAYING = "playing"     # 正在播放
    PAUSED = "paused"       # 已暂停
    STOPPED = "stopped"     # 已停止


class InterruptReason(Enum):
    """打断原因枚举"""
    TASK_CANCELLED = "task_cancelled"      # 任务被取消
    USER_INTERRUPTED = "user_interrupted"  # 用户打断
    MANUAL_STOP = "manual_stop"            # 手动停止


class AudioPlayer:
    """音频播放器"""
    
    def __init__(self, config, task_lock: Optional[threading.Lock] = None,
                 get_current_task_id: Optional[callable] = None,
                 get_interrupt_flag: Optional[callable] = None,
                 event_bus: Optional[Any] = None):
        """
        初始化音频播放器
        
        Args:
            config: 配置对象（Config实例）
            task_lock: 任务锁（可选，用于任务取消检查）
            get_current_task_id: 获取当前任务ID的函数（可选）
            get_interrupt_flag: 获取用户打断标记的函数（可选，返回bool）
            event_bus: 事件总线（可选，用于发送播放进度事件）
        """
        self.config = config
        self.task_lock = task_lock
        self.get_current_task_id = get_current_task_id
        self.get_interrupt_flag = get_interrupt_flag
        self.event_bus = event_bus
        
        # 从配置获取参数
        self.check_interval = config.get("tts.playback_check_interval", 0.05)
        self.status_update_interval = config.get("tts.playback_status_update_interval", 0.1)
        
        # 播放状态
        self._pygame_mixer_initialized = False
        self._current_channel: Optional[pygame.mixer.Channel] = None
        self._current_sound: Optional[pygame.mixer.Sound] = None
        self._current_task_id: Optional[int] = None
        self._status = PlaybackStatus.IDLE
        self._play_start_time: Optional[float] = None
        self._pause_start_time: Optional[float] = None
        self._total_pause_duration = 0.0  # 累计暂停时长
        self._audio_duration: Optional[float] = None  # 音频总时长（秒）
        self._volume = 1.0  # 默认音量
        
        # 状态锁
        self._state_lock = threading.Lock()
        
        # 流式输出状态（用于同一行更新）
        self._last_logged_text = ""  # 上次输出的文本
        self._full_played_text = ""  # 完整的已播放文本（所有已完成句子 + 当前句子已播放部分）
        
        log_debug("PLAY", "音频播放器初始化完成")
    
    def _init_mixer(self):
        """初始化pygame.mixer（只初始化一次）"""
        if not self._pygame_mixer_initialized:
            try:
                pygame.mixer.init()
                self._pygame_mixer_initialized = True
                log_debug("PLAY", "pygame.mixer初始化完成", indent=1)
            except Exception as e:
                log_error("PLAY", "pygame.mixer初始化失败: {e}", indent=1)
                raise
    
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
    
    def _check_user_interrupt(self) -> bool:
        """
        检查用户打断标记
        
        Returns:
            True表示用户打断，False表示未打断
        """
        if self.get_interrupt_flag is None:
            return False
        
        try:
            return self.get_interrupt_flag()
        except:
            return False
    
    def set_volume(self, volume: float):
        """
        设置音量
        
        Args:
            volume: 音量值（0.0-1.0）
        """
        volume = max(0.0, min(1.0, volume))  # 限制在0.0-1.0范围
        self._volume = volume
        
        with self._state_lock:
            if self._current_channel:
                try:
                    self._current_channel.set_volume(volume)
                    log_debug("PLAY", "音量设置为: {volume:.2f}", indent=1)
                except Exception as e:
                    log_warn("PLAY", f"设置音量失败: {e}", indent=1)
    
    def get_volume(self) -> float:
        """
        获取当前音量
        
        Returns:
            音量值（0.0-1.0）
        """
        return self._volume
    
    def is_playing(self) -> bool:
        """
        检查是否正在播放
        
        Returns:
            True表示正在播放，False表示未播放
        """
        with self._state_lock:
            return self._status == PlaybackStatus.PLAYING
    
    def is_paused(self) -> bool:
        """
        检查是否暂停
        
        Returns:
            True表示已暂停，False表示未暂停
        """
        with self._state_lock:
            return self._status == PlaybackStatus.PAUSED
    
    def get_status(self) -> PlaybackStatus:
        """
        获取播放状态
        
        Returns:
            播放状态枚举
        """
        with self._state_lock:
            return self._status
    
    def get_current_task_id(self) -> Optional[int]:
        """
        获取当前任务ID
        
        Returns:
            当前任务ID，如果没有任务则返回None
        """
        with self._state_lock:
            return self._current_task_id
    
    def get_progress(self) -> Dict[str, Any]:
        """
        获取播放进度
        
        Returns:
            包含进度信息的字典：
            - elapsed_time: 已播放时长（秒，不包括暂停时间）
            - total_duration: 音频总时长（秒，如果知道）
            - progress_percent: 播放进度百分比（0-100，如果知道总时长）
            - paused_duration: 累计暂停时长（秒）
        """
        with self._state_lock:
            if self._play_start_time is None:
                return {
                    "elapsed_time": 0.0,
                    "total_duration": None,
                    "progress_percent": None,
                    "paused_duration": self._total_pause_duration
                }
            
            # 计算已播放时长（不包括暂停时间）
            current_time = time.time()
            if self._status == PlaybackStatus.PAUSED and self._pause_start_time:
                # 暂停状态，暂停时间不计入播放时长
                elapsed_time = (self._pause_start_time - self._play_start_time) - self._total_pause_duration
            else:
                elapsed_time = (current_time - self._play_start_time) - self._total_pause_duration
            
            elapsed_time = max(0.0, elapsed_time)
            
            # 计算进度百分比
            progress_percent = None
            if self._audio_duration and self._audio_duration > 0:
                progress_percent = min(100.0, (elapsed_time / self._audio_duration) * 100)
            
            return {
                "elapsed_time": elapsed_time,
                "total_duration": self._audio_duration,
                "progress_percent": progress_percent,
                "paused_duration": self._total_pause_duration
            }
    
    def get_audio_duration(self) -> Optional[float]:
        """获取当前音频的时长（秒）"""
        with self._state_lock:
            return self._audio_duration
    
    def pause(self, task_id: Optional[int] = None) -> bool:
        """
        暂停播放
        
        Args:
            task_id: 任务ID（可选，用于验证）
            
        Returns:
            True表示暂停成功，False表示失败
        """
        with self._state_lock:
            if self._status != PlaybackStatus.PLAYING:
                status_desc = {
                    PlaybackStatus.IDLE: "空闲",
                    PlaybackStatus.PAUSED: "已暂停",
                    PlaybackStatus.STOPPED: "已停止"
                }.get(self._status, f"未知状态({self._status.value})")
                log_warn("PLAY", f"当前未在播放，无法暂停（当前状态: {status_desc}）", indent=1)
                return False
            
            if task_id is not None and task_id != self._current_task_id:
                log_warn("PLAY", f"任务ID不匹配，无法暂停（当前任务ID: {self._current_task_id}, 请求任务ID: {task_id}）", indent=1)
                return False
            
            try:
                if self._current_channel:
                    self._current_channel.pause()
                    self._status = PlaybackStatus.PAUSED
                    self._pause_start_time = time.time()
                    log_debug("PLAY", "播放已暂停", indent=1)
                    return True
            except Exception as e:
                log_error("PLAY", f"暂停播放失败: {e}", indent=1)
                return False
        
        return False
    
    def resume(self, task_id: Optional[int] = None) -> bool:
        """
        恢复播放
        
        Args:
            task_id: 任务ID（可选，用于验证）
            
        Returns:
            True表示恢复成功，False表示失败
        """
        with self._state_lock:
            if self._status != PlaybackStatus.PAUSED:
                status_desc = {
                    PlaybackStatus.IDLE: "空闲",
                    PlaybackStatus.PLAYING: "正在播放",
                    PlaybackStatus.STOPPED: "已停止"
                }.get(self._status, f"未知状态({self._status.value})")
                log_warn("PLAY", f"当前未暂停，无法恢复（当前状态: {status_desc}）", indent=1)
                return False
            
            if task_id is not None and task_id != self._current_task_id:
                log_warn("PLAY", f"任务ID不匹配，无法恢复（当前任务ID: {self._current_task_id}, 请求任务ID: {task_id}）", indent=1)
                return False
            
            try:
                if self._current_channel:
                    self._current_channel.unpause()
                    self._status = PlaybackStatus.PLAYING
                    
                    # 更新累计暂停时长
                    if self._pause_start_time:
                        pause_duration = time.time() - self._pause_start_time
                        self._total_pause_duration += pause_duration
                        self._pause_start_time = None
                    
                    log_debug("PLAY", "播放已恢复", indent=1)
                    return True
            except Exception as e:
                log_error("PLAY", f"恢复播放失败: {e}", indent=1)
                return False
        
        return False
    
    def stop(self, task_id: Optional[int] = None) -> bool:
        """
        停止播放
        
        Args:
            task_id: 任务ID（可选，用于验证）
            
        Returns:
            True表示停止成功，False表示失败
            
        注意：此方法使用 _state_lock 保护，锁只在方法内部短暂持有，不会阻塞其他操作。
        channel.stop() 是非阻塞调用。
        """
        import threading
        current_thread = threading.current_thread().name
        log_debug("PLAY", f"[AUDIO_STOP] stop() 被调用 (task_id={task_id}, 线程={current_thread})", indent=1)
        with self._state_lock:
            if self._status not in [PlaybackStatus.PLAYING, PlaybackStatus.PAUSED]:
                status_desc = {
                    PlaybackStatus.IDLE: "空闲",
                    PlaybackStatus.STOPPED: "已停止"
                }.get(self._status, f"未知状态({self._status.value})")
                log_warn("PLAY", f"当前未在播放或暂停，无法停止（当前状态: {status_desc}）", indent=1)
                return False
            
            if task_id is not None and task_id != self._current_task_id:
                log_warn("PLAY", f"任务ID不匹配，无法停止（当前任务ID: {self._current_task_id}, 请求任务ID: {task_id}）", indent=1)
                return False
            
            try:
                if self._current_channel:
                    self._current_channel.stop()
                # 重置播放状态变量，避免get_progress()计算错误的播放时长
                self._status = PlaybackStatus.STOPPED
                self._play_start_time = None
                self._pause_start_time = None
                self._total_pause_duration = 0.0
                log_debug("PLAY", "播放已停止", indent=1)
                return True
            except Exception as e:
                log_error("PLAY", f"停止播放失败: {e}", indent=1)
                return False
        
        return False
    
    def cleanup(self):
        """
        清理资源
        
        停止当前播放，清理pygame.mixer资源
        """
        try:
            log_debug("PLAY", "开始清理音频播放器资源", indent=0)
            
            # 停止当前播放
            with self._state_lock:
                if self._current_channel:
                    try:
                        self._current_channel.stop()
                    except Exception as e:
                        log_warn("PLAY", f"停止播放通道时出错: {e}", indent=1)
                    self._current_channel = None
                if self._current_sound:
                    self._current_sound = None
                self._status = PlaybackStatus.IDLE
                self._current_task_id = None
                self._play_start_time = None
                self._pause_start_time = None
                self._total_pause_duration = 0.0
                self._audio_duration = None
                self._last_logged_text = ""
            
            # 清理pygame.mixer（可选，因为可能被其他地方使用）
            # 注意：不调用 pygame.mixer.quit()，因为可能影响其他模块
            
            log_debug("PLAY", "音频播放器资源清理完成", indent=0)
        except Exception as e:
            log_debug("PLAY", f"清理音频播放器资源时出错: {e}", indent=1)
            import traceback
            log_debug("PLAY", f"异常堆栈:\n{traceback.format_exc()}", indent=2)
    
    def play(self, audio_segment: AudioSegment, task_id: int,
             on_complete_callback: Optional[Callable[[bool, Optional[InterruptReason]], None]] = None,
             current_sentence_text: Optional[str] = None,
             status_update_interval: Optional[float] = None) -> bool:
        """
        播放音频（AudioSegment对象）
        
        Args:
            audio_segment: 音频段对象
            task_id: 任务ID（用于取消检查）
            on_complete_callback: 播放完成回调函数
                - 参数1: was_interrupted (bool) - 是否被打断
                - 参数2: interrupt_reason (InterruptReason | None) - 打断原因（如果被打断）
            current_sentence_text: 当前句子文本（可选，用于文本位置估算）
            status_update_interval: 状态更新间隔（秒，可选，如果为None则使用配置值）
        
        Returns:
            True表示播放完成（未被打断），False表示被打断
        """
        play_start_time = time.time()
        interrupt_reason: Optional[InterruptReason] = None
        
        # 使用传入的更新间隔，如果没有则使用配置值
        update_interval = status_update_interval if status_update_interval is not None else self.status_update_interval
        last_status_update_time = 0.0  # 上次发送状态事件的时间
        
        try:
            # 初始化mixer（只初始化一次）
            self._init_mixer()
            
            # 检查任务是否被取消
            if self._check_task_cancelled(task_id):
                log_warn("PLAY", f"任务 {task_id} 在开始播放前被取消", indent=1)
                if on_complete_callback:
                    try:
                        on_complete_callback(True, InterruptReason.TASK_CANCELLED)
                    except Exception as e:
                        log_error("PLAY", f"播放完成回调错误: {e}", indent=1)
                return False
            
            # 将AudioSegment转换为WAV格式的bytes（pygame支持WAV）
            wav_buffer = io.BytesIO()
            audio_segment.export(wav_buffer, format="wav")
            wav_buffer.seek(0)
            
            # 记录音频总时长
            self._audio_duration = len(audio_segment) / 1000.0  # 转换为秒
            
            # 使用pygame.mixer.Sound从内存加载并播放（避免文件I/O）
            sound = pygame.mixer.Sound(wav_buffer)
            channel = sound.play()
            
            # 设置音量
            channel.set_volume(self._volume)
            
            # 更新状态
            with self._state_lock:
                self._current_channel = channel
                self._current_sound = sound  # 保持引用，避免被垃圾回收导致播放中断
                self._current_task_id = task_id
                self._status = PlaybackStatus.PLAYING
                self._play_start_time = play_start_time
                self._pause_start_time = None
                self._total_pause_duration = 0.0
                self._last_logged_text = ""  # 重置流式输出状态（开始新的播放）
            
            log_debug("PLAY", f"开始播放 [任务ID: {task_id}, 时长: {self._audio_duration:.2f}秒]", indent=1)
            
            # 等待播放完成或被打断
            while True:
                # 检查是否正在播放
                is_busy = channel.get_busy() if channel else False
                
                # 检查任务是否被取消
                if self._check_task_cancelled(task_id):
                    if channel:
                        channel.stop()
                    interrupt_reason = InterruptReason.TASK_CANCELLED
                    log_warn("PLAY", f"任务 {task_id} 被取消，停止播放", indent=1)
                    break
                
                # 检查用户打断
                # 注意：检测到pending_interrupt时，不立即暂停，而是继续播放
                # 延迟验证会决定是否暂停，如果延迟验证通过，外部会调用pause()方法
                # 如果延迟验证失败，pending_interrupt会被清除，播放器继续播放
                if self._check_user_interrupt():
                    # 检测到pending_interrupt，但不立即暂停
                    # 继续播放，等待延迟验证结果（由外部决定是暂停还是继续）
                    # 延迟验证通过后，外部会调用pause()方法暂停
                    # 延迟验证失败时，pending_interrupt会被清除，这里会继续正常播放
                    log_debug("PLAY", "检测到可能打断标记，继续播放等待延迟验证结果", indent=1)
                    # 继续正常播放循环，不暂停
                
                # 检查是否处于暂停状态
                is_paused = False
                is_stopped = False
                with self._state_lock:
                    if self._status == PlaybackStatus.PAUSED:
                        is_paused = True
                    elif self._status == PlaybackStatus.STOPPED:
                        is_stopped = True
                        interrupt_reason = InterruptReason.MANUAL_STOP
                
                if is_paused:
                    # 音频被暂停，等待恢复或停止（在锁外 sleep）
                    time.sleep(self.check_interval)
                    continue
                
                if is_stopped:
                    log_warn("PLAY", "播放被手动停止", indent=1)
                    break
                
                # 如果不在播放且不是暂停状态，说明播放完成
                if not is_busy:
                    break
                
                # 发送播放进度事件（如果事件总线可用且距离上次发送时间超过更新间隔）
                current_time = time.time()
                if (self.event_bus is not None and 
                    current_time - last_status_update_time >= update_interval):
                    try:
                        # 获取播放进度
                        progress = self.get_progress()
                        
                        # 估算文本位置
                        current_sentence_position = None
                        if current_sentence_text and progress.get("progress_percent") is not None:
                            progress_percent = progress["progress_percent"]
                            # 根据播放进度百分比估算字符位置
                            position = int(len(current_sentence_text) * progress_percent / 100)
                            # 限制在有效范围内
                            current_sentence_position = min(position, len(current_sentence_text))
                        
                        # 创建并发送播放进度事件
                        # 延迟导入以避免循环导入
                        from voice_chatbot.dialogue.events import EventType, create_event
                        
                        event_data = {
                            "elapsed_time": progress.get("elapsed_time", 0.0),
                            "total_duration": progress.get("total_duration"),
                            "progress_percent": progress.get("progress_percent"),
                            "current_sentence_text": current_sentence_text,
                            "current_sentence_position": current_sentence_position,
                            "task_id": task_id
                        }
                        
                        event = create_event(
                            EventType.AUDIO_PLAY_PROGRESS,
                            data=event_data,
                            source="AudioPlayer"
                        )
                        self.event_bus.publish(event)
                        
                        # 输出实时播放文本日志（同一行更新）
                        if current_sentence_text and current_sentence_position is not None:
                            played_text = current_sentence_text[:current_sentence_position]
                            if played_text and played_text != self._last_logged_text:
                                # 使用inline=True在同一行更新
                                log_info("PLAY", f"[播放文本] {played_text}", indent=1, inline=True)
                                self._last_logged_text = played_text
                        
                        last_status_update_time = current_time
                    except Exception as e:
                        log_debug("PLAY", f"发送播放进度事件失败: {e}", indent=2)
                
                time.sleep(self.check_interval)
            
            # 检查最终状态
            was_interrupted = interrupt_reason is not None
            
            # 如果暂停状态，也算作被打断（需要外部调用stop）
            with self._state_lock:
                if self._status == PlaybackStatus.PAUSED:
                    was_interrupted = True
                    if interrupt_reason is None:
                        interrupt_reason = InterruptReason.USER_INTERRUPTED
                elif self._status == PlaybackStatus.STOPPED:
                    was_interrupted = True
                    if interrupt_reason is None:
                        interrupt_reason = InterruptReason.MANUAL_STOP
            
            # 播放完成或中断时输出换行（如果使用了inline输出）
            # 注意：需要在清理状态之前检查，因为_last_logged_text会被重置
            need_newline = False
            with self._state_lock:
                if self._last_logged_text:
                    need_newline = True
            
            # 更新状态
            with self._state_lock:
                if not was_interrupted:
                    self._status = PlaybackStatus.IDLE
                else:
                    self._status = PlaybackStatus.STOPPED
                
                # 清理引用
                self._current_channel = None
                self._current_sound = None
                self._current_task_id = None
                self._last_logged_text = ""  # 重置流式输出状态
            
            # 输出换行，结束流式输出
            if need_newline:
                print()  # 换行，结束流式输出
            
            play_elapsed = time.time() - play_start_time
            if was_interrupted:
                log_warn("PLAY", f"播放中断 [耗时: {play_elapsed*1000:.1f}ms, 原因: {interrupt_reason.value}]", indent=1)
            else:
                log_debug("PLAY", f"播放完成 [耗时: {play_elapsed*1000:.1f}ms]", indent=1)
            
            # 调用回调函数
            if on_complete_callback:
                try:
                    on_complete_callback(was_interrupted, interrupt_reason)
                except Exception as e:
                    log_error("PLAY", f"播放完成回调错误: {e}", indent=1)
            
            return not was_interrupted
            
        except Exception as e:
            play_elapsed = time.time() - play_start_time
            log_error("PLAY", f"播放音频错误: {e} [耗时: {play_elapsed*1000:.1f}ms]", indent=1)
            
            # 更新状态
            with self._state_lock:
                self._status = PlaybackStatus.IDLE
                self._current_channel = None
                self._current_sound = None
                self._current_task_id = None
                self._last_logged_text = ""  # 重置流式输出状态
            
            # 调用回调函数（错误情况）
            if on_complete_callback:
                try:
                    on_complete_callback(True, None)
                except Exception as e:
                    log_error("PLAY", f"播放完成回调错误: {e}", indent=1)
            
            return False
    
    def get_visualization_data(self) -> Optional[Dict[str, Any]]:
        """
        获取音频可视化数据（预留接口）
        
        Returns:
            可视化数据字典（当前返回None，后续可扩展）
        """
        # 预留接口，可以后续实现音频波形数据提取
        return None

