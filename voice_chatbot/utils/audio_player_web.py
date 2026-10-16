"""
Web 音频播放器模块（基于 WebSocket）

提供 WebSocket 音频流式播放、暂停/恢复、停止、播放完成回调、音量控制、播放进度跟踪等功能
参考：audio_player.py 的本地播放器实现
"""

import sys
import os

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import io
import time
import numpy as np
from typing import Optional, Callable, Dict, Any
from enum import Enum
import threading

from pydub import AudioSegment

from voice_chatbot.utils.logger import log_info, log_warn, log_error, log_debug, log_stream, log_stream_end
from voice_chatbot.utils.websocket_server import WebSocketServer, get_websocket_server

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


class AudioPlayerWeb:
    """Web 音频播放器（基于 WebSocket）"""

    def __init__(self, config, ws_server: WebSocketServer = None,
                 task_lock: Optional[threading.Lock] = None,
                 get_current_task_id: Optional[callable] = None,
                 event_bus: Optional[Any] = None):
        """
        初始化 Web 音频播放器

        Args:
            config: 配置对象（Config实例）
            ws_server: WebSocket 服务器实例（如果为 None，则创建新实例）
            task_lock: 任务锁（可选，用于任务取消检查）
            get_current_task_id: 获取当前任务ID的函数（可选）
            event_bus: 事件总线（可选，用于发送播放进度事件）
        """
        self.config = config
        self.task_lock = task_lock
        self.get_current_task_id = get_current_task_id
        self.event_bus = event_bus

        # 从配置获取参数
        self.check_interval = config.get("tts.playback_check_interval", 0.05)
        self.status_update_interval = config.get("tts.playback_status_update_interval", 0.1)

        # WebSocket 服务器
        if ws_server is not None:
            self.ws_server = ws_server
            log_info("PLAY", "使用外部 WebSocket 服务器实例", indent=1)
        else:
            host = config.get("system.websocket.host", "0.0.0.0")
            port = config.get("system.websocket.port", 8766)
            self.ws_server = get_websocket_server(config, host, port)
            log_info("PLAY", f"创建新 WebSocket 服务器实例 - {host}:{port}", indent=1)

        # 播放状态
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

        # 音频数据缓冲区（用于流式传输）
        self._audio_buffer = []
        self._buffer_lock = threading.Lock()

        # 客户端统计信息
        self.client_stats: Dict[str, dict] = {}  # {client_id: {total_bytes, start_time}}

        log_debug("PLAY", "Web 音频播放器初始化完成")
        log_info("PLAY", "音频数据将实时传输到客户端", indent=1)

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

    def set_volume(self, volume: float):
        """
        设置音量

        Args:
            volume: 音量值（0.0-1.0）
        """
        volume = max(0.0, min(1.0, volume))  # 限制在0.0-1.0范围
        self._volume = volume

        # 发送音量控制消息到所有客户端
        volume_message = {
            "type": "volume",
            "volume": volume
        }
        self.ws_server.send_to_all_clients_sync(volume_message)
        log_debug("PLAY", f"音量设置为: {volume:.2f}", indent=1)

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

            self._status = PlaybackStatus.PAUSED
            self._pause_start_time = time.time()

            # 发送暂停消息到所有客户端
            pause_message = {
                "type": "pause"
            }
            self.ws_server.send_to_all_clients_sync(pause_message)
            log_debug("PLAY", "播放已暂停", indent=1)
            return True

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

            self._status = PlaybackStatus.PLAYING

            # 更新累计暂停时长
            if self._pause_start_time:
                pause_duration = time.time() - self._pause_start_time
                self._total_pause_duration += pause_duration
                self._pause_start_time = None

            # 发送恢复消息到所有客户端
            resume_message = {
                "type": "resume"
            }
            self.ws_server.send_to_all_clients_sync(resume_message)
            log_debug("PLAY", "播放已恢复", indent=1)
            return True

        return False

    def stop(self, task_id: Optional[int] = None) -> bool:
        """
        停止播放

        Args:
            task_id: 任务ID（可选，用于验证）

        Returns:
            True表示停止成功，False表示失败
        """
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

            # 立即设置状态为STOPPED，使play()循环中的检查能够立即生效
            self._status = PlaybackStatus.STOPPED
            self._play_start_time = None
            self._pause_start_time = None
            self._total_pause_duration = 0.0

            # 清空音频缓冲区
            with self._buffer_lock:
                self._audio_buffer.clear()

            log_debug("PLAY", "播放已停止（状态已更新）", indent=1)

        # 发送停止消息到所有客户端（在锁外发送，避免阻塞）
        try:
            stop_message = {
                "type": "stop"
            }
            self.ws_server.send_to_all_clients_sync(stop_message)
            log_debug("PLAY", "停止消息已发送到客户端", indent=1)
        except Exception as e:
            log_warn("PLAY", f"发送停止消息失败: {e}", indent=1)

        return True

    def cleanup(self):
        """
        清理资源

        停止当前播放，清理资源
        """
        try:
            log_debug("PLAY", "开始清理音频播放器资源", indent=0)

            # 停止当前播放
            with self._state_lock:
                self._status = PlaybackStatus.IDLE
                self._current_task_id = None
                self._play_start_time = None
                self._pause_start_time = None
                self._total_pause_duration = 0.0
                self._audio_duration = None
                self._last_logged_text = ""

            # 清空音频缓冲区
            with self._buffer_lock:
                self._audio_buffer.clear()

            # 发送停止消息到所有客户端
            stop_message = {
                "type": "stop"
            }
            self.ws_server.send_to_all_clients_sync(stop_message)

            # 清理客户端统计
            with self._state_lock:
                self.client_stats.clear()

            log_debug("PLAY", "音频播放器资源清理完成", indent=0)
        except Exception as e:
            log_debug("PLAY", f"清理音频播放器资源时出错: {e}", indent=1)
            import traceback
            log_debug("PLAY", f"异常堆栈:\n{traceback.format_exc()}", indent=2)

    def _send_audio_chunk(self, audio_data: bytes):
        """
        发送音频数据块到所有客户端

        Args:
            audio_data: 音频数据（bytes）
        """
        # 发送二进制音频数据到所有客户端
        client_ids = list(self.ws_server.clients.keys())

        for client_id in client_ids:
            try:
                # 使用 asyncio 在事件循环中发送
                import asyncio
                if self.ws_server.event_loop and self.ws_server.event_loop.is_running():
                    asyncio_future = asyncio.run_coroutine_threadsafe(
                        self.ws_server.send_to_client_bytes(client_id, audio_data),
                        self.ws_server.event_loop
                    )
                    asyncio_future.result(timeout=1.0)  # 等待1秒

                    # 更新统计信息
                    with self._state_lock:
                        if client_id not in self.client_stats:
                            self.client_stats[client_id] = {
                                'total_bytes': 0,
                                'start_time': time.time()
                            }
                        self.client_stats[client_id]['total_bytes'] += len(audio_data)
            except Exception as e:
                log_debug("PLAY", f"发送音频数据到客户端 {client_id} 失败: {e}", indent=2)

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
        import asyncio  # 延迟导入，避免循环依赖

        play_start_time = time.time()
        interrupt_reason: Optional[InterruptReason] = None

        # 使用传入的更新间隔，如果没有则使用配置值
        update_interval = status_update_interval if status_update_interval is not None else self.status_update_interval
        last_status_update_time = 0.0  # 上次发送状态事件的时间

        try:
            # 检查任务是否被取消
            if self._check_task_cancelled(task_id):
                log_warn("PLAY", f"任务 {task_id} 在开始播放前被取消", indent=1)
                if on_complete_callback:
                    try:
                        on_complete_callback(True, InterruptReason.TASK_CANCELLED)
                    except Exception as e:
                        log_error("PLAY", f"播放完成回调错误: {e}", indent=1)
                return False

            # 记录音频总时长
            self._audio_duration = len(audio_segment) / 1000.0  # 转换为秒

            # 更新状态
            with self._state_lock:
                self._current_task_id = task_id
                self._status = PlaybackStatus.PLAYING
                self._play_start_time = play_start_time
                self._pause_start_time = None
                self._total_pause_duration = 0.0
                self._last_logged_text = ""  # 重置流式输出状态（开始新的播放）

            log_debug("PLAY", f"开始播放 [任务ID: {task_id}, 时长: {self._audio_duration:.2f}秒]", indent=1)

            # 将 AudioSegment 转换为 WAV 格式
            # 使用 16kHz 采样率，单声道
            wav_buffer = io.BytesIO()
            audio_segment = audio_segment.set_frame_rate(16000)
            audio_segment = audio_segment.set_channels(1)
            audio_segment.export(wav_buffer, format="wav")
            wav_buffer.seek(0)

            # 读取 WAV 数据并跳过 WAV 头（44字节）
            wav_data = wav_buffer.read()
            pcm_data = wav_data[44:]  # 跳过 WAV 头，只保留 PCM 数据

            # 将音频数据分块发送
            chunk_size = 4096  # 每块 4096 样本（4KB）
            total_chunks = (len(pcm_data) + chunk_size - 1) // chunk_size

            for chunk_idx in range(total_chunks):
                # 计算当前块的起始和结束位置
                start_pos = chunk_idx * chunk_size
                end_pos = min(start_pos + chunk_size, len(pcm_data))
                chunk = pcm_data[start_pos:end_pos]

                # 检查任务是否被取消
                if self._check_task_cancelled(task_id):
                    self.stop()
                    interrupt_reason = InterruptReason.TASK_CANCELLED
                    log_warn("PLAY", f"任务 {task_id} 被取消，停止播放", indent=1)
                    break

                # 检查是否处于暂停或停止状态
                is_paused = False
                is_stopped = False
                with self._state_lock:
                    if self._status == PlaybackStatus.PAUSED:
                        is_paused = True
                    elif self._status == PlaybackStatus.STOPPED:
                        is_stopped = True
                        interrupt_reason = InterruptReason.MANUAL_STOP

                if is_stopped:
                    log_warn("PLAY", "播放被手动停止", indent=1)
                    break

                if is_paused:
                    # 音频被暂停，等待恢复或停止（在锁外 sleep）
                    time.sleep(self.check_interval)
                    # 在恢复时，重新检查是否被停止
                    with self._state_lock:
                        if self._status == PlaybackStatus.STOPPED:
                            is_stopped = True
                            interrupt_reason = InterruptReason.MANUAL_STOP
                    if is_stopped:
                        log_warn("PLAY", "播放被手动停止（暂停期间）", indent=1)
                        break
                    # 音频被暂停，等待恢复或停止（在锁外 sleep）
                    time.sleep(self.check_interval)
                    # 重新发送当前块（在恢复时）
                    chunk_idx -= 1  # 重新发送当前块
                    continue

                # 发送音频块到客户端
                self._send_audio_chunk(chunk)

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
                            # 当进度接近100%时，确保显示完整文本
                            # 降低阈值到98%，避免因时间计算误差导致文本截断
                            if progress_percent >= 98.0:
                                current_sentence_position = len(current_sentence_text)
                            else:
                                # 增加偏移量到3，补偿估算误差
                                position = int(len(current_sentence_text) * progress_percent / 100) + 3
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
                            source="AudioPlayerWeb"
                        )
                        self.event_bus.publish(event)

                        # 输出实时播放文本日志（流式通道，同通道原地更新）
                        if current_sentence_text and current_sentence_position is not None:
                            played_text = current_sentence_text[:current_sentence_position]
                            if played_text and played_text != self._last_logged_text:
                                log_stream('play_text', played_text, label='播放文本', category='PLAY')
                                self._last_logged_text = played_text

                        last_status_update_time = current_time
                    except Exception as e:
                        log_debug("PLAY", f"发送播放进度事件失败: {e}", indent=2)

                # 发送进度消息到所有客户端（用于客户端显示）
                if current_time - last_status_update_time >= update_interval:
                    # 计算播放进度
                    elapsed_chunks = chunk_idx + 1
                    progress_percent = (elapsed_chunks / total_chunks) * 100.0
                    elapsed_time = (elapsed_chunks / total_chunks) * self._audio_duration

                    progress_message = {
                        "type": "progress",
                        "elapsed_time": elapsed_time,
                        "total_duration": self._audio_duration,
                        "progress_percent": progress_percent,
                        "current_sentence_text": current_sentence_text
                    }
                    self.ws_server.send_to_all_clients_sync(progress_message)

                # 控制发送速率，模拟实际播放时间
                chunk_duration = len(chunk) / 2 / 16000  # 每个样本 2 字节，采样率 16kHz
                # 在 sleep 之前再次检查状态，以便更快响应停止操作
                with self._state_lock:
                    if self._status == PlaybackStatus.STOPPED:
                        log_warn("PLAY", "检测到停止状态，提前退出播放循环", indent=1)
                        interrupt_reason = InterruptReason.MANUAL_STOP
                        break
                time.sleep(chunk_duration * 0.8)  # 稍快一点，避免缓冲

                # 发送完每个块后再次检查状态，确保在块之间也能及时响应停止
                with self._state_lock:
                    if self._status == PlaybackStatus.STOPPED:
                        log_warn("PLAY", "检测到停止状态，提前退出播放循环（块间检查）", indent=1)
                        interrupt_reason = InterruptReason.MANUAL_STOP
                        break

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
                elif not was_interrupted:
                    self._status = PlaybackStatus.IDLE

            # 播放完成或中断时结束流式通道（需要在清理状态之前取最终文本）
            # 注意：需要在清理状态之前检查，因为_last_logged_text会被重置
            final_stream_text = ""
            with self._state_lock:
                if self._last_logged_text:
                    final_stream_text = self._last_logged_text

            # 发送播放完成消息到所有客户端
            complete_message = {
                "type": "complete",
                "was_interrupted": was_interrupted,
                "interrupt_reason": interrupt_reason.value if interrupt_reason else None
            }
            self.ws_server.send_to_all_clients_sync(complete_message)

            # 结束播放文本流式通道（控制台换行落定 + 网页端移除固定行）
            # 正常完成时以完整句子为准（避免流式更新中的文本截断），中断时落定已播部分
            final_text = final_stream_text
            if not was_interrupted and current_sentence_text:
                final_text = current_sentence_text
            if final_text:
                log_stream_end('play_text', final_text, label='播放文本', category='PLAY')

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

    def get_statistics(self) -> dict:
        """获取统计信息"""
        with self._state_lock:
            total_bytes = sum(stats['total_bytes'] for stats in self.client_stats.values())
            sample_rate = self.config.get("audio.sample_rate", 16000)
            total_duration = total_bytes / (2 * sample_rate)  # 每个样本2字节

            return {
                "active_clients": len(self.client_stats),
                "total_bytes": total_bytes,
                "total_duration": total_duration,
            }


async def main_async():
    """异步主函数（仅用于测试）"""
    from voice_chatbot.utils.config import Config

    config = Config()

    player = AudioPlayerWeb(
        config=config,
        host="0.0.0.0",
        port=8766
    )

    # 启动 WebSocket 服务器
    await player.ws_server.start()

    # 生成测试音频
    log_info("PLAY", "生成测试音频...", indent=0)
    sample_rate = 16000
    duration = 3  # 3秒
    frequency = 440  # 440Hz (A4音)

    t = np.linspace(0, duration, int(sample_rate * duration), False)
    audio_data = np.sin(2 * np.pi * frequency * t) * 0.3  # 正弦波，音量0.3
    audio_data = (audio_data * 32767).astype(np.int16)  # 转换为16位PCM

    # 转换为 AudioSegment
    audio_segment = AudioSegment(
        audio_data.tobytes(),
        frame_rate=sample_rate,
        sample_width=audio_data.dtype.itemsize,
        channels=1
    )

    # 等待客户端连接
    log_info("PLAY", "等待客户端连接...", indent=0)
    await asyncio.sleep(5)

    # 播放测试音频
    log_info("PLAY", "开始播放测试音频...", indent=0)
    await asyncio.to_thread(
        player.play,
        audio_segment,
        task_id=1
    )

    # 打印统计信息
    stats = player.get_statistics()
    log_info("PLAY", f"统计: {stats['active_clients']} 活跃客户端, "
            f"总时长: {stats['total_duration']:.1f}秒", indent=0)

    # 保持运行
    try:
        while True:
            await asyncio.sleep(10)
            stats = player.get_statistics()
            log_info("PLAY", f"统计: {stats['active_clients']} 活跃客户端, "
                    f"总时长: {stats['total_duration']:.1f}秒", indent=0)
    except asyncio.CancelledError:
        pass
    finally:
        player.cleanup()
        await player.ws_server.stop()


if __name__ == "__main__":
    import sys
    import asyncio
    try:
        print("注意：此模式仅用于测试 Web 音频播放器，实际使用请通过主程序启动")
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log_info("PLAY", "收到中断信号，正在停止...", indent=0)
        sys.exit(0)
