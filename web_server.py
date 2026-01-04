#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebSocket 服务器 - 为 lincoco 提供网页端实时语音交互

参考 RealtimeVoiceChat 项目的实现方式
"""

import sys
import asyncio
import struct
import json
import time
import base64
import threading
from pathlib import Path
from typing import Optional, Dict, Any
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from starlette.responses import Response

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from voice_chatbot.utils import Config, init_logger, log_info, log_error, log_warn, log_debug
from voice_chatbot.dialogue import DialogueManager
from pydub import AudioSegment
import io
import wave


# 全局变量
dialogue_manager: Optional[DialogueManager] = None
melo_tts_process = None


class NoCacheStaticFiles(StaticFiles):
    """提供静态文件服务，禁用客户端缓存"""
    async def get_response(self, path: str, scope: Dict[str, Any]) -> Response:
        response: Response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        if "etag" in response.headers:
            response.headers.__delitem__("etag")
        if "last-modified" in response.headers:
            response.headers.__delitem__("last-modified")
        return response


class WebSocketAudioHandler:
    """处理 WebSocket 音频数据的类"""
    
    def __init__(self, dialogue_manager: DialogueManager, message_queue: asyncio.Queue):
        """
        初始化 WebSocket 音频处理器
        
        Args:
            dialogue_manager: DialogueManager 实例
            message_queue: 用于发送消息到客户端的队列
        """
        self.dialogue_manager = dialogue_manager
        self.message_queue = message_queue
        self.sample_rate = dialogue_manager.config.get("audio.sample_rate", 16000)
        self.event_loop = None  # 将在首次使用时获取
    
    async def process_audio_chunk(self, pcm_data: bytes, timestamp_ms: int, flags: int):
        """
        处理从客户端接收的音频数据块
        
        Args:
            pcm_data: PCM 音频数据（Int16 格式）
            timestamp_ms: 客户端时间戳（毫秒）
            flags: 标志位（例如：是否正在播放 TTS）
        """
        try:
            # 检查是否需要触发打断逻辑
            # 如果 flags 表示 TTS 正在播放，或者检测到系统状态是 SPEAKING/PROCESSING
            if self.dialogue_manager:
                from voice_chatbot.dialogue.state import MainSystemState, SubSystemState
                
                main_state = self.dialogue_manager.state_manager.get_main_state()
                sub_state = self.dialogue_manager.state_manager.get_sub_state()
                
                # 检查TTS是否正在播放
                is_playing = False
                if self.dialogue_manager.audio_player:
                    is_playing = hasattr(self.dialogue_manager.audio_player, 'is_playing') and self.dialogue_manager.audio_player.is_playing()
                
                # 检查是否需要触发打断（首次检测到音频数据且TTS正在播放）
                should_trigger_interrupt = (
                    (sub_state in [SubSystemState.SPEAKING, SubSystemState.PROCESSING] or
                     is_playing or
                     main_state == MainSystemState.ROBOT_SPEAKING) and
                    not self.dialogue_manager._get_pending_interrupt_safe()
                )
                
                if should_trigger_interrupt:
                    log_info("WEBSOCKET", "检测到音频输入且TTS正在播放，触发打断逻辑", indent=1)
                    
                    # 立即发送停止TTS消息给前端
                    try:
                        if self.event_loop is None:
                            try:
                                self.event_loop = asyncio.get_running_loop()
                            except RuntimeError:
                                pass
                        
                        if self.event_loop:
                            asyncio.run_coroutine_threadsafe(
                                self.message_queue.put({
                                    "type": "stop_tts"
                                }),
                                self.event_loop
                            )
                            log_info("WEBSOCKET", "已发送 stop_tts 消息给前端（音频输入打断）", indent=1)
                    except Exception as e:
                        log_error("WEBSOCKET", f"发送 stop_tts 消息失败: {e}", indent=1)
                    
                    # 触发打断逻辑：模拟 VAD 检测到语音开始
                    # 1. 更新状态为 LISTENING
                    if main_state != MainSystemState.LISTENING:
                        self.dialogue_manager.state_manager.transition_main_state(
                            MainSystemState.LISTENING, 
                            "WebSocket音频输入触发打断，开始监听"
                        )
                    
                    # 2. 设置 pending_interrupt
                    with self.dialogue_manager.interrupt_lock:
                        self.dialogue_manager.pending_interrupt = True
                    
                    # 3. 记录语音活动开始时间
                    self.dialogue_manager.voice_activity_start_time = time.time()
                    
                    # 4. 启动延迟验证
                    self.dialogue_manager._pause_for_voice_check()
                    
                    log_info("WEBSOCKET", "打断逻辑已触发，pending_interrupt=True", indent=1)
            
            # 将 Int16 PCM 转换为 float32 numpy 数组
            int16_array = np.frombuffer(pcm_data, dtype=np.int16)
            float32_array = int16_array.astype(np.float32) / 32768.0
            
            # 调用 DialogueManager 的音频数据回调
            if self.dialogue_manager:
                self.dialogue_manager._on_audio_data(float32_array)
        except Exception as e:
            log_error("WEBSOCKET", f"处理音频数据块时出错: {e}", indent=1)
    


@asynccontextmanager
async def lifespan(app: FastAPI):
    """管理应用生命周期"""
    global dialogue_manager, melo_tts_process
    
    log_info("WEBSERVER", "Web 服务器启动中...", indent=0)
    
    # 加载配置
    config = Config()
    
    # 初始化日志系统（传递 config 以读取过滤和颜色配置）
    init_logger(
        log_level=config.get("logging.level", "INFO"),
        log_file_enabled=True,
        log_dir=config.get("logging.log_dir", "logs"),
        config=config  # 传递 config 以读取 filtered_categories 和 category_colors
    )
    
    # 如果使用 MeloTTS，启动 API 服务（复用 main.py 的逻辑）
    try:
        from main import start_melo_tts_api
        melo_tts_process = start_melo_tts_api(config)
    except Exception as e:
        log_warn("WEBSERVER", f"启动 MeloTTS API 服务失败: {e}", indent=1)
        melo_tts_process = None
    
    # 创建 DialogueManager 实例
    dialogue_manager = DialogueManager(config=config)
    dialogue_manager.initialize_modules()
    
    # 启动系统（但不启动麦克风，因为我们将通过 WebSocket 接收音频）
    # 注意：microphone.start() 会在 start() 中调用，我们需要阻止它
    dialogue_manager.start()
    
    # 停止麦克风（因为我们将通过 WebSocket 接收音频）
    if dialogue_manager.microphone:
        dialogue_manager.microphone.stop()
        log_info("WEBSERVER", "已停止本地麦克风（使用 WebSocket 音频输入）", indent=1)
    
    # 将 dialogue_manager 存储到 app.state
    app.state.dialogue_manager = dialogue_manager
    
    log_info("WEBSERVER", "Web 服务器已启动", indent=0)
    
    yield
    
    # 清理资源
    log_info("WEBSERVER", "Web 服务器关闭中...", indent=0)
    if dialogue_manager:
        dialogue_manager.stop()
    if melo_tts_process:
        try:
            melo_tts_process.terminate()
            melo_tts_process.wait(timeout=5)
        except:
            pass
    log_info("WEBSERVER", "Web 服务器已关闭", indent=0)


# 创建 FastAPI 应用
app = FastAPI(lifespan=lifespan)

# 启用 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
static_dir = project_root / "web" / "static"
if static_dir.exists():
    app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")


@app.get("/favicon.ico")
async def favicon():
    """提供 favicon"""
    favicon_path = static_dir / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    return Response(status_code=404)


@app.get("/")
async def get_index() -> HTMLResponse:
    """提供主页面"""
    index_path = static_dir / "index.html"
    if index_path.exists():
        with open(index_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    return HTMLResponse(content="<h1>Error: index.html not found</h1>", status_code=404)


def parse_json_message(text: str) -> dict:
    """安全地解析 JSON 消息"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log_warn("WEBSOCKET", "收到无效的 JSON 消息", indent=1)
        return {}


async def process_incoming_data(
    ws: WebSocket,
    app: FastAPI,
    incoming_chunks: asyncio.Queue,
    audio_handler: WebSocketAudioHandler,
    message_queue: asyncio.Queue
) -> None:
    """
    处理从客户端接收的数据
    
    Args:
        ws: WebSocket 连接
        app: FastAPI 应用实例
        incoming_chunks: 音频数据块队列
        audio_handler: 音频处理器
    """
    try:
        while True:
            msg = await ws.receive()
            
            # 处理二进制消息（音频数据）
            if "bytes" in msg and msg["bytes"]:
                raw = msg["bytes"]
                
                # 确保至少有 8 字节的头部：4 字节时间戳 + 4 字节标志位
                if len(raw) < 8:
                    log_warn("WEBSOCKET", "收到的数据包太短，缺少 8 字节头部", indent=1)
                    continue
                
                # 解析头部（大端序 uint32 时间戳和 uint32 标志位）
                timestamp_ms, flags = struct.unpack("!II", raw[:8])
                
                # 提取 PCM 数据
                pcm_data = raw[8:]
                
                # 处理音频数据
                await audio_handler.process_audio_chunk(pcm_data, timestamp_ms, flags)
            
            # 处理文本消息（JSON）
            elif "text" in msg and msg["text"]:
                data = parse_json_message(msg["text"])
                msg_type = data.get("type")
                
                log_info("WEBSOCKET", f"收到客户端消息: {data}", indent=1)
                
                if msg_type == "tts_start":
                    log_info("WEBSOCKET", "客户端开始播放 TTS", indent=1)
                elif msg_type == "tts_stop":
                    log_info("WEBSOCKET", "客户端停止播放 TTS", indent=1)
                elif msg_type == "clear_history":
                    log_info("WEBSOCKET", "客户端请求清空历史", indent=1)
                    if app.state.dialogue_manager:
                        with app.state.dialogue_manager.history_lock:
                            app.state.dialogue_manager.conversation_history = []
                elif msg_type == "text_input":
                    text_content = data.get("content", "").strip()
                    if text_content:
                        log_info("WEBSOCKET", f"收到文本输入: {text_content}", indent=1)
                        dialogue_manager = app.state.dialogue_manager
                        if dialogue_manager:
                            try:
                                from voice_chatbot.dialogue.state import MainSystemState, SubSystemState
                                
                                main_state = dialogue_manager.state_manager.get_main_state()
                                sub_state = dialogue_manager.state_manager.get_sub_state()
                                
                                # 检查是否需要触发打断逻辑
                                # 如果TTS正在播放（SPEAKING状态）或正在处理（PROCESSING状态），应该触发打断
                                is_playing = False
                                if dialogue_manager.audio_player:
                                    is_playing = hasattr(dialogue_manager.audio_player, 'is_playing') and dialogue_manager.audio_player.is_playing()
                                
                                should_interrupt = (
                                    sub_state in [SubSystemState.SPEAKING, SubSystemState.PROCESSING] or
                                    is_playing or
                                    main_state == MainSystemState.ROBOT_SPEAKING
                                )
                                
                                # 在发送新用户输入之前，先检查并发送旧任务的最终结果（如果有）
                                # 这样可以确保消息顺序：final_assistant_answer（旧任务） -> final_user_request（新输入）
                                # 注意：需要在调用 _handle_asr_result 之前获取旧上下文，因为调用后会创建新任务
                                old_context = dialogue_manager.context_manager.get_current_context()
                                old_task_id = dialogue_manager.context_manager.get_current_task_id()
                                
                                # 保存旧上下文信息（在创建新任务之前）
                                old_played_text = ""
                                old_response_text = ""
                                old_final_answer_sent = False
                                if old_context and old_context.is_valid:
                                    old_played_text = old_context.get_extra_data("played_response_text", "")
                                    old_response_text = old_context.response_text
                                    # 检查旧任务是否已经发送过最终结果（TTS正常播放完成时已发送）
                                    old_final_answer_sent = old_context.get_extra_data("final_answer_sent_to_frontend", False)
                                    log_info("WEBSOCKET", f"获取旧任务上下文: task_id={old_task_id}, played_text长度={len(old_played_text)}, response_text长度={len(old_response_text)}, 已发送最终结果={old_final_answer_sent}", indent=1)
                                
                                # 如果旧任务已经发送过最终结果（TTS正常播放完成），就不需要再发送了
                                if old_final_answer_sent:
                                    log_info("WEBSOCKET", "旧任务已发送过最终结果（TTS正常播放完成），跳过重复发送", indent=1)
                                else:
                                    # 如果从上下文无法获取，尝试从对话历史获取最后一个助手消息
                                    if not old_played_text and not old_response_text:
                                        with dialogue_manager.history_lock:
                                            history = dialogue_manager.conversation_history.copy()
                                            # 从后往前查找最后一个助手消息
                                            for msg in reversed(history):
                                                if msg.get("role") == "assistant":
                                                    old_response_text = msg.get("content", "")
                                                    if old_response_text:
                                                        log_info("WEBSOCKET", f"从对话历史获取旧任务回答: {len(old_response_text)} 字符", indent=1)
                                                    break
                                    
                                    # 确定要发送的最终文本
                                    final_text_to_send = None
                                    if old_played_text:
                                        # 如果有已播放内容，使用已播放内容
                                        final_text_to_send = old_played_text
                                        log_info("WEBSOCKET", f"使用旧任务已播放内容: {len(old_played_text)} 字符", indent=1)
                                    elif old_response_text:
                                        # 如果没有已播放内容，但response_text有内容，使用response_text
                                        final_text_to_send = old_response_text
                                        log_info("WEBSOCKET", f"使用旧任务回答内容（未播放）: {len(old_response_text)} 字符", indent=1)
                                    
                                    # 如果找到了要发送的文本，先发送最终结果
                                    # 使用 await 确保消息已进入队列
                                    if final_text_to_send:
                                        await message_queue.put({
                                            "type": "final_assistant_answer",
                                            "content": final_text_to_send
                                        })
                                        log_info("WEBSOCKET", f"发送旧任务最终结果: {len(final_text_to_send)} 字符", indent=1)
                                        # 等待更长时间，确保前端UI更新完成（气泡更新结束）
                                        await asyncio.sleep(0.05)  # 50ms 延迟，确保前端UI更新完成
                                    else:
                                        log_info("WEBSOCKET", "未找到旧任务内容，跳过发送 final_assistant_answer", indent=1)
                                
                                if should_interrupt:
                                    log_info("WEBSOCKET", "检测到TTS正在播放或处理，触发打断逻辑", indent=1)
                                    
                                    # 立即发送停止TTS消息给前端
                                    await message_queue.put({
                                        "type": "stop_tts"
                                    })
                                    log_info("WEBSOCKET", "已发送 stop_tts 消息给前端", indent=1)
                                    
                                    # 触发打断逻辑：模拟 VAD 检测到语音开始
                                    # 1. 更新状态为 LISTENING
                                    if main_state != MainSystemState.LISTENING:
                                        dialogue_manager.state_manager.transition_main_state(
                                            MainSystemState.LISTENING, 
                                            "WebSocket文本输入触发打断，开始监听"
                                        )
                                    
                                    # 2. 设置 pending_interrupt
                                    with dialogue_manager.interrupt_lock:
                                        dialogue_manager.pending_interrupt = True
                                    
                                    # 3. 记录语音活动开始时间
                                    dialogue_manager.voice_activity_start_time = time.time()
                                    
                                    # 4. 先确认真实打断并记录旧任务的对话历史（这样新任务的LLM messages会包含第一轮对话）
                                    # 注意：_confirm_real_interrupt 会停止播放并记录对话历史，并清除 pending_interrupt
                                    log_info("WEBSOCKET", "确认真实打断并记录旧任务对话历史", indent=1)
                                    dialogue_manager._confirm_real_interrupt()
                                    log_info("WEBSOCKET", "已确认打断并记录旧任务对话历史", indent=1)
                                    
                                    # 注意：_confirm_real_interrupt 已经清除了 pending_interrupt，所以不需要再调用 _pause_for_voice_check
                                    # 对于文本输入，不需要延迟验证，直接处理即可
                                    log_info("WEBSOCKET", "打断逻辑已触发，旧任务对话历史已记录", indent=1)
                                
                                # 如果系统处于 LISTENING 状态，先停止 ASR 识别
                                if main_state == MainSystemState.LISTENING:
                                    log_info("WEBSOCKET", "系统处于 LISTENING 状态，停止 ASR 识别", indent=1)
                                    # 停止 ASR 识别
                                    if hasattr(dialogue_manager, 'asr_thread_active') and dialogue_manager.asr_thread_active:
                                        dialogue_manager._stop_asr_recognition()
                                        log_info("WEBSOCKET", "ASR 识别已停止", indent=1)
                                
                                # 验证文本输入是否有效（非空且有意义）
                                if not text_content or len(text_content.strip()) == 0:
                                    log_warn("WEBSOCKET", "文本输入为空，跳过处理", indent=1)
                                    continue
                                
                                # 记录性能计时器
                                # Web端点击发送按钮，等同于VAD检测到语音活动结束和ASR识别完成
                                # 先记录ASR识别完成时间（等同于ASR识别完成）
                                dialogue_manager.performance_timer.record_user_speech_end()
                                log_info("WEBSOCKET", "[PERF_TIMER] 文本输入：记录ASR识别完成时间（等同于点击发送按钮）", indent=1)
                                # 然后记录VAD结束时间（响应耗时分析的起点，会使用ASR识别完成时间，确保时间点一致）
                                dialogue_manager.performance_timer.record_vad_speech_end()
                                log_info("WEBSOCKET", "[PERF_TIMER] 文本输入：记录VAD结束时间（使用ASR识别完成时间）", indent=1)
                                
                                # 标记文本输入已处理，避免 ASR 事件重复发送
                                if not hasattr(dialogue_manager, '_text_input_handled'):
                                    dialogue_manager._text_input_handled = set()
                                dialogue_manager._text_input_handled.add(text_content)
                                
                                # 在调用 _handle_asr_result 之前，先发送 final_user_request
                                # 这样可以确保消息顺序：final_assistant_answer（旧任务） -> final_user_request（新输入）
                                # 注意：已经等待了 final_assistant_answer 的UI更新完成
                                await message_queue.put({
                                    "type": "final_user_request",
                                    "content": text_content
                                })
                                log_info("WEBSOCKET", f"已验证并发送用户输入最终结果: {text_content[:50]}...", indent=1)
                                
                                # 直接调用 _handle_asr_result，让系统按正常流程处理
                                # confidence=1.0 表示文本输入是确定的（不需要 ASR 识别）
                                dialogue_manager._handle_asr_result(text_content, confidence=1.0)
                                log_info("WEBSOCKET", "文本输入已处理", indent=1)
                            except Exception as e:
                                log_error("WEBSOCKET", f"处理文本输入时出错: {e}", indent=1)
                                import traceback
                                log_error("WEBSOCKET", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
                        else:
                            log_error("WEBSOCKET", "DialogueManager 未初始化", indent=1)
    
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        log_info("WEBSOCKET", "WebSocket 连接断开", indent=1)
    except Exception as e:
        log_error("WEBSOCKET", f"处理接收数据时出错: {e}", indent=1)


async def send_text_messages(ws: WebSocket, message_queue: asyncio.Queue) -> None:
    """持续发送文本消息到客户端"""
    try:
        tts_chunk_count = 0
        while True:
            await asyncio.sleep(0.001)  # 让出控制权
            data = await message_queue.get()
            msg_type = data.get("type")
            if msg_type != "tts_chunk":  # TTS 音频块不记录日志（太频繁）
                log_info("WEBSOCKET", f"发送消息到客户端: {data}", indent=1)
            else:
                tts_chunk_count += 1
                if tts_chunk_count % 50 == 0:  # 每50个块记录一次
                    log_debug("WEBSOCKET", f"已发送 {tts_chunk_count} 个 TTS 音频块", indent=1)
            
            try:
                await ws.send_json(data)
            except Exception as e:
                log_error("WEBSOCKET", f"发送消息失败: {e}, 消息类型: {msg_type}", indent=1)
                raise
    except asyncio.CancelledError:
        pass
    except WebSocketDisconnect:
        log_info("WEBSOCKET", "WebSocket 连接断开", indent=1)
    except Exception as e:
        log_error("WEBSOCKET", f"发送消息时出错: {e}", indent=1)


async def send_tts_audio_to_websocket(audio_segment: AudioSegment, message_queue: asyncio.Queue, sample_rate: int = 24000):
    """
    将 TTS 音频发送到 WebSocket
    
    Args:
        audio_segment: pydub AudioSegment 对象
        message_queue: 消息队列
        sample_rate: 目标采样率
    """
    try:
        log_info("WEBSOCKET", f"[send_tts_audio_to_websocket] 开始处理 TTS 音频", indent=1)
        if audio_segment is None:
            log_error("WEBSOCKET", "audio_segment 为 None，无法发送", indent=1)
            return
        
        log_info("WEBSOCKET", f"开始处理 TTS 音频: 原始采样率={audio_segment.frame_rate}Hz, 声道数={audio_segment.channels}, 时长={len(audio_segment)/1000.0:.2f}秒", indent=1)
        
        # 确保采样率为目标采样率
        if audio_segment.frame_rate != sample_rate:
            log_debug("WEBSOCKET", f"转换采样率: {audio_segment.frame_rate}Hz -> {sample_rate}Hz", indent=1)
            audio_segment = audio_segment.set_frame_rate(sample_rate)
        
        # 转换为单声道
        if audio_segment.channels > 1:
            log_debug("WEBSOCKET", f"转换为单声道: {audio_segment.channels} -> 1", indent=1)
            audio_segment = audio_segment.set_channels(1)
        
        # 转换为 Int16 PCM
        # 注意：pydub 的 raw_data 返回的是原始字节数据
        # 需要根据 sample_width 来确定数据类型
        sample_width = audio_segment.sample_width  # 字节数（1=8bit, 2=16bit, 4=32bit）
        
        if sample_width == 2:
            # 16-bit (Int16)
            raw_audio = audio_segment.raw_data
            int16_array = np.frombuffer(raw_audio, dtype=np.int16)
        elif sample_width == 4:
            # 32-bit (Int32 或 Float32)
            # 先尝试作为 Int32，如果不行再作为 Float32
            raw_audio = audio_segment.raw_data
            try:
                # 尝试作为 Int32
                int32_array = np.frombuffer(raw_audio, dtype=np.int32)
                # 转换为 Int16（可能需要缩放）
                int16_array = (int32_array / 65536).astype(np.int16)
            except:
                # 尝试作为 Float32
                float32_array = np.frombuffer(raw_audio, dtype=np.float32)
                # 转换为 Int16
                int16_array = (float32_array * 32767).astype(np.int16)
        else:
            # 其他格式，先导出为 WAV 再读取
            log_warn("WEBSOCKET", f"不支持的 sample_width: {sample_width}，尝试导出为 WAV", indent=1)
            wav_buffer = io.BytesIO()
            audio_segment.export(wav_buffer, format="wav")
            wav_buffer.seek(0)
            # 重新读取为 AudioSegment（确保是 16-bit）
            audio_segment = AudioSegment.from_wav(wav_buffer)
            raw_audio = audio_segment.raw_data
            int16_array = np.frombuffer(raw_audio, dtype=np.int16)
        
        if len(int16_array) == 0:
            log_error("WEBSOCKET", "转换后的音频数组为空", indent=1)
            return
        
        log_info("WEBSOCKET", f"准备发送 TTS 音频: {len(int16_array)} 个样本, 采样率={sample_rate}Hz, 时长={len(int16_array)/sample_rate:.2f}秒", indent=1)
        
        # 将数据分块发送（每块 2048 个样本）
        chunk_size = 2048
        chunk_count = 0
        for i in range(0, len(int16_array), chunk_size):
            chunk = int16_array[i:i + chunk_size]
            # 转换为 base64 编码
            base64_chunk = base64.b64encode(chunk.tobytes()).decode('utf-8')
            
            await message_queue.put({
                "type": "tts_chunk",
                "content": base64_chunk
            })
            chunk_count += 1
        
        log_info("WEBSOCKET", f"TTS 音频已发送: {chunk_count} 个音频块", indent=1)
    except Exception as e:
        log_error("WEBSOCKET", f"发送 TTS 音频时出错: {e}", indent=1)
        import traceback
        log_error("WEBSOCKET", f"异常堆栈:\n{traceback.format_exc()}", indent=1)


async def monitor_dialogue_events(
    app: FastAPI,
    message_queue: asyncio.Queue,
    audio_handler: WebSocketAudioHandler
) -> None:
    """
    监控 DialogueManager 的事件，将结果发送到客户端
    
    这个函数需要监控：
    1. ASR 部分结果 -> partial_user_request
    2. ASR 最终结果 -> final_user_request
    3. LLM 部分结果 -> partial_assistant_answer (通过事件系统)
    4. LLM 最终结果 -> final_assistant_answer (通过对话历史)
    5. TTS 音频 -> tts_chunk (通过拦截 audio_play_queue)
    """
    dialogue_manager = app.state.dialogue_manager
    
    # 保存原始的 audio_player
    original_audio_player = dialogue_manager.audio_player
    
    # 获取当前事件循环（用于从线程中安全调用异步函数）
    event_loop = asyncio.get_running_loop()
    
    # 不再需要累积LLM流式输出文本（文字输出完全通过TTS播放进度同步，不显示预览）
    
    # 创建一个包装器来拦截音频播放
    class WebSocketAudioPlayerWrapper:
        """包装 AudioPlayer，拦截播放调用并发送到 WebSocket"""
        def __init__(self, original_player, message_queue, event_loop):
            self.original_player = original_player
            self.message_queue = message_queue
            self.event_loop = event_loop
            # 播放队列状态跟踪
            self._play_queue = []  # 待播放的音频队列 [(audio_segment, sentence_text, task_id, duration, callback), ...]
            self._current_playing_index = -1  # 当前正在播放的音频索引
            self._total_elapsed_time = 0.0  # 累计播放时长
            self._play_start_time = None  # 当前音频开始播放的时间
            self._play_lock = threading.Lock()  # 保护播放队列的锁
            self._progress_thread = None  # 播放进度模拟线程
            self._current_task_id = None  # 当前任务ID（用于检测任务切换）
            import time  # 用于get_progress计算时间
            # 复制原始播放器的所有属性
            for attr in dir(original_player):
                if not attr.startswith('_') and attr != 'play':
                    try:
                        setattr(self, attr, getattr(original_player, attr))
                    except:
                        pass
        
        def play(self, audio_segment, task_id, on_complete_callback=None, current_sentence_text=None, status_update_interval=None):
            """拦截播放调用，发送到 WebSocket，并按顺序模拟播放进度事件"""
            import threading
            import time
            from voice_chatbot.dialogue.events import EventType, create_event
            
            # 异步发送到 WebSocket
            try:
                if audio_segment is None:
                    log_error("WEBSOCKET", "audio_segment 为 None，无法播放", indent=1)
                    if on_complete_callback:
                        on_complete_callback(False, None)
                    return False
                
                audio_duration = len(audio_segment) / 1000.0
                log_info("WEBSOCKET", f"收到 TTS 音频播放请求: 任务ID={task_id}, 时长={audio_duration:.2f}秒, 句子: {current_sentence_text[:30] if current_sentence_text else 'N/A'}...", indent=1)
                
                # 检测任务切换：如果任务ID变化，清空旧队列并重置状态
                with self._play_lock:
                    if self._current_task_id is not None and self._current_task_id != task_id:
                        log_info("WEBSOCKET", f"检测到任务切换：task_id {self._current_task_id} -> {task_id}，清空旧队列并重置状态", indent=1)
                        self._play_queue.clear()
                        self._current_playing_index = -1
                        self._total_elapsed_time = 0.0
                        self._play_start_time = None
                        # 停止旧的播放进度线程（如果存在）
                        if self._progress_thread and self._progress_thread.is_alive():
                            log_debug("WEBSOCKET", "停止旧的播放进度线程", indent=1)
                    
                    self._current_task_id = task_id
                    queue_index = len(self._play_queue)
                    self._play_queue.append((audio_segment, current_sentence_text or "", task_id, audio_duration, on_complete_callback))
                    log_debug("WEBSOCKET", f"音频已添加到队列，索引={queue_index}, 队列长度={len(self._play_queue)}, 任务ID={task_id}", indent=1)
                
                # 从线程中安全地调用异步函数发送音频到WebSocket
                future = asyncio.run_coroutine_threadsafe(
                    send_tts_audio_to_websocket(
                        audio_segment, 
                        self.message_queue,
                        sample_rate=24000
                    ),
                    self.event_loop
                )
                
                def on_task_done(fut):
                    try:
                        fut.result()
                        log_debug("WEBSOCKET", f"TTS 音频发送完成，队列索引={queue_index}, 任务ID={task_id}", indent=1)
                        
                        # 如果是队列中的第一个音频，启动播放进度模拟线程
                        with self._play_lock:
                            if queue_index == 0 and self._current_playing_index == -1:
                                # 确保没有旧的播放进度线程在运行
                                if self._progress_thread and self._progress_thread.is_alive():
                                    log_warn("WEBSOCKET", "检测到旧的播放进度线程仍在运行，等待其结束", indent=1)
                                else:
                                    self._start_playback_progress_simulation()
                    except Exception as e:
                        log_error("WEBSOCKET", f"TTS 音频发送失败: {e}", indent=1)
                
                future.add_done_callback(on_task_done)
                
            except Exception as e:
                log_error("WEBSOCKET", f"发送 TTS 音频到 WebSocket 时出错: {e}", indent=1)
                import traceback
                log_error("WEBSOCKET", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
                if on_complete_callback:
                    try:
                        on_complete_callback(False, None)
                    except:
                        pass
                return False
            
            return True
        
        def _start_playback_progress_simulation(self):
            """启动播放进度模拟线程（只启动一次，处理整个队列）"""
            import threading
            import time
            from voice_chatbot.dialogue.events import EventType, create_event
            
            def simulate_queue_playback():
                """模拟整个队列的播放进度"""
                try:
                    update_interval = 0.1  # 默认100ms
                    last_update_time = 0.0
                    
                    # 获取事件总线
                    event_bus = dialogue_manager.event_bus if dialogue_manager else None
                    if not event_bus:
                        log_warn("WEBSOCKET", "无法获取事件总线，无法发送播放进度事件", indent=1)
                        return
                    
                    while True:
                        with self._play_lock:
                            if self._current_playing_index >= len(self._play_queue):
                                # 队列播放完成（已在最后一个音频播放完成时发布AUDIO_PLAY_END事件）
                                break
                            
                            if self._current_playing_index == -1:
                                # 开始播放第一个音频
                                if len(self._play_queue) > 0:
                                    self._current_playing_index = 0
                                    self._play_start_time = time.time()
                                    self._total_elapsed_time = 0.0
                                    log_debug("WEBSOCKET", f"开始播放队列，当前索引={self._current_playing_index}", indent=1)
                            
                            if self._current_playing_index < len(self._play_queue):
                                audio_segment, current_sentence_text, task_id, audio_duration, on_complete_callback = self._play_queue[self._current_playing_index]
                                
                                # 计算当前音频的播放进度
                                current_time = time.time()
                                if self._play_start_time is None:
                                    self._play_start_time = current_time
                                
                                # 当前音频已播放的时间
                                current_audio_elapsed = current_time - self._play_start_time
                                
                                # 检查当前音频是否播放完成
                                if current_audio_elapsed >= audio_duration:
                                    # 发送最后一次进度事件（100%），确保包含最后一个字符
                                    if current_sentence_text:
                                        # 确保位置包含整个句子（字符数+1，但不超过句子长度）
                                        sentence_position = min(len(current_sentence_text), len(current_sentence_text))
                                        event_data = {
                                            "elapsed_time": self._total_elapsed_time + audio_duration,
                                            "total_duration": audio_duration,
                                            "progress_percent": 100.0,
                                            "current_sentence_text": current_sentence_text,
                                            "current_sentence_position": sentence_position,
                                            "task_id": task_id
                                        }
                                        event = create_event(
                                            EventType.AUDIO_PLAY_PROGRESS,
                                            data=event_data,
                                            source="WebSocketAudioPlayerWrapper"
                                        )
                                        event_bus.publish(event)
                                    
                                    # 调用完成回调
                                    if on_complete_callback:
                                        try:
                                            on_complete_callback(False, None)
                                        except Exception as e:
                                            log_error("WEBSOCKET", f"调用播放完成回调时出错: {e}", indent=1)
                                    
                                    # 累加播放时长
                                    self._total_elapsed_time += audio_duration
                                    
                                    # 移动到下一个音频
                                    self._current_playing_index += 1
                                    if self._current_playing_index < len(self._play_queue):
                                        # 开始播放下一个音频
                                        self._play_start_time = time.time()
                                        log_debug("WEBSOCKET", f"切换到下一个音频，索引={self._current_playing_index}", indent=1)
                                    else:
                                        # 队列播放完成，发布AUDIO_PLAY_END事件
                                        log_debug("WEBSOCKET", "队列播放完成，发布AUDIO_PLAY_END事件", indent=1)
                                        end_event_data = {
                                            "task_id": task_id,
                                            "total_duration": self._total_elapsed_time
                                        }
                                        end_event = create_event(
                                            EventType.AUDIO_PLAY_END,
                                            data=end_event_data,
                                            source="WebSocketAudioPlayerWrapper"
                                        )
                                        event_bus.publish(end_event)
                                        break
                                else:
                                    # 定期发送播放进度事件
                                    if current_time - last_update_time >= update_interval:
                                        progress_percent = (current_audio_elapsed / audio_duration) * 100.0 if audio_duration > 0 else 0.0
                                        
                                        # 估算文本位置
                                        current_sentence_position = None
                                        if current_sentence_text:
                                            position = int(len(current_sentence_text) * progress_percent / 100.0)
                                            # 限制在有效范围内，但确保在接近完成时包含整个句子
                                            if progress_percent >= 99.0:
                                                # 接近完成时，确保包含整个句子（字符数+1，但不超过句子长度）
                                                current_sentence_position = len(current_sentence_text)
                                            else:
                                                current_sentence_position = min(position, len(current_sentence_text))
                                        
                                        # 创建并发送播放进度事件
                                        event_data = {
                                            "elapsed_time": self._total_elapsed_time + current_audio_elapsed,
                                            "total_duration": audio_duration,
                                            "progress_percent": progress_percent,
                                            "current_sentence_text": current_sentence_text or "",
                                            "current_sentence_position": current_sentence_position,
                                            "task_id": task_id
                                        }
                                        
                                        event = create_event(
                                            EventType.AUDIO_PLAY_PROGRESS,
                                            data=event_data,
                                            source="WebSocketAudioPlayerWrapper"
                                        )
                                        event_bus.publish(event)
                                        
                                        last_update_time = current_time
                        
                        time.sleep(0.05)  # 每50ms检查一次
                        
                except Exception as e:
                    log_error("WEBSOCKET", f"模拟播放进度时出错: {e}", indent=1)
                    import traceback
                    log_error("WEBSOCKET", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
                finally:
                    # 清理状态
                    with self._play_lock:
                        self._current_playing_index = -1
                        self._play_start_time = None
                        self._total_elapsed_time = 0.0
                        self._play_queue.clear()
            
            # 启动模拟播放进度的线程（只启动一次）
            progress_thread = threading.Thread(target=simulate_queue_playback, daemon=True)
            progress_thread.start()
            self._progress_thread = progress_thread  # 保存线程引用
            log_debug("WEBSOCKET", "播放进度模拟线程已启动", indent=1)
        
        def get_progress(self):
            """获取播放进度（WebSocket模式下使用模拟的播放进度）"""
            import time
            with self._play_lock:
                if self._play_start_time is None or self._current_playing_index < 0:
                    # 还没有开始播放
                    return {
                        "elapsed_time": 0.0,
                        "total_duration": None,
                        "progress_percent": None,
                        "paused_duration": 0.0
                    }
                
                # 计算当前累计播放时长
                current_time = time.time()
                if self._current_playing_index < len(self._play_queue):
                    # 当前音频还在播放
                    audio_segment, current_sentence_text, task_id, audio_duration, on_complete_callback = self._play_queue[self._current_playing_index]
                    current_audio_elapsed = current_time - self._play_start_time
                    # 限制在当前音频时长内
                    current_audio_elapsed = min(max(0.0, current_audio_elapsed), audio_duration)
                    total_elapsed = self._total_elapsed_time + current_audio_elapsed
                else:
                    # 所有音频都已播放完成
                    total_elapsed = self._total_elapsed_time
                
                return {
                    "elapsed_time": total_elapsed,
                    "total_duration": None,  # WebSocket模式下不跟踪总时长
                    "progress_percent": None,
                    "paused_duration": 0.0
                }
        
        def get_status(self):
            """获取播放状态"""
            with self._play_lock:
                if self._current_playing_index >= 0 and self._current_playing_index < len(self._play_queue):
                    # 有音频在播放队列中
                    from voice_chatbot.tts.audio_player import PlaybackStatus
                    return PlaybackStatus.PLAYING
                else:
                    from voice_chatbot.tts.audio_player import PlaybackStatus
                    return PlaybackStatus.STOPPED
        
        def get_current_task_id(self):
            """获取当前任务ID"""
            with self._play_lock:
                return self._current_task_id
        
        def stop(self, task_id=None):
            """拦截停止调用，发送停止消息到 WebSocket"""
            log_info("WEBSOCKET", f"收到停止播放请求: 任务ID={task_id}", indent=1)
            
            # 停止播放进度模拟线程
            with self._play_lock:
                # 标记为停止，让模拟线程退出
                if self._current_playing_index >= 0:
                    self._current_playing_index = len(self._play_queue)  # 设置为超出范围，让线程退出
                    log_debug("WEBSOCKET", "已标记停止播放，播放进度模拟线程将退出", indent=1)
            
            # 立即发送停止TTS消息给前端
            try:
                asyncio.run_coroutine_threadsafe(
                    self.message_queue.put({
                        "type": "stop_tts"
                    }),
                    self.event_loop
                )
                log_info("WEBSOCKET", "已发送 stop_tts 消息给前端（停止播放）", indent=1)
            except Exception as e:
                log_error("WEBSOCKET", f"发送 stop_tts 消息失败: {e}", indent=1)
            
            # 调用原始播放器的stop方法（虽然它不会真正播放，但需要更新状态）
            if hasattr(self.original_player, 'stop'):
                return self.original_player.stop(task_id)
            return True
        
        def pause(self, task_id=None):
            """拦截暂停调用，发送停止消息到 WebSocket"""
            log_info("WEBSOCKET", f"收到暂停播放请求: 任务ID={task_id}", indent=1)
            
            # 立即发送停止TTS消息给前端
            try:
                asyncio.run_coroutine_threadsafe(
                    self.message_queue.put({
                        "type": "stop_tts"
                    }),
                    self.event_loop
                )
                log_info("WEBSOCKET", "已发送 stop_tts 消息给前端（暂停播放）", indent=1)
            except Exception as e:
                log_error("WEBSOCKET", f"发送 stop_tts 消息失败: {e}", indent=1)
            
            # 调用原始播放器的pause方法
            if hasattr(self.original_player, 'pause'):
                return self.original_player.pause(task_id)
            return True
        
        def __getattr__(self, name):
            # 对于其他未定义的属性，从原始播放器获取
            return getattr(self.original_player, name)
    
    # 替换 audio_player（仅在 WebSocket 模式下）
    if original_audio_player:
        wrapper = WebSocketAudioPlayerWrapper(
            original_audio_player,
            message_queue,
            event_loop
        )
        dialogue_manager.audio_player = wrapper
        log_info("WEBSOCKET", "已设置 WebSocket 音频播放器包装器", indent=1)
        log_info("WEBSOCKET", f"原始 audio_player 类型: {type(original_audio_player)}", indent=1)
        log_info("WEBSOCKET", f"包装器类型: {type(wrapper)}", indent=1)
        
        # 验证包装器是否正确设置
        if dialogue_manager.audio_player == wrapper:
            log_info("WEBSOCKET", "包装器设置成功", indent=1)
        else:
            log_error("WEBSOCKET", "包装器设置失败！", indent=1)
    
    # 订阅事件系统来获取流式输出
    from voice_chatbot.dialogue.events import EventType, EventFilter
    
    # 不再订阅LLM句子事件，文字输出完全通过TTS播放进度同步，不显示预览
    # def on_llm_sentence(event):
    #     """处理 LLM 句子生成事件（已移除，不再显示预览）"""
    #     pass
    
    # 用于记录上次发送的文本，避免重复发送相同内容
    last_sent_text = ""
    last_sent_task_id = None
    final_answer_sent = False  # 标记是否已发送最终结果
    
    def on_audio_play_progress(event):
        """处理 TTS 音频播放进度事件，根据播放进度发送已播放的文字"""
        nonlocal last_sent_text, last_sent_task_id, final_answer_sent
        try:
            # 如果已经发送了最终结果，不再发送部分结果
            if final_answer_sent:
                return
            
            data = event.data if hasattr(event, 'data') else {}
            task_id = data.get("task_id", 0)
            current_sentence_text = data.get("current_sentence_text", "")
            current_sentence_position = data.get("current_sentence_position", 0)
            
            # 验证任务ID是否匹配当前任务
            current_task_id = dialogue_manager.context_manager.get_current_task_id()
            if task_id != current_task_id:
                return
            
            # 任务切换时重置上次发送的文本和最终结果标志
            if last_sent_task_id != task_id:
                last_sent_text = ""
                last_sent_task_id = task_id
                final_answer_sent = False  # 新任务开始，重置最终结果标志
                log_debug("WEBSOCKET", f"任务切换：task_id {last_sent_task_id} -> {task_id}，重置状态", indent=1)
            
            # 获取当前任务上下文
            context = dialogue_manager.context_manager.get_current_context()
            if not context or not context.is_valid:
                return
            
            # 优先从TaskContext获取已播放文本（由_on_playback_progress计算并存储）
            played_text = context.get_extra_data("played_response_text", "")
            
            # 如果无法获取，则根据事件数据计算
            if not played_text:
                completed_sentences = context.get_extra_data("completed_sentences", [])
                completed_text = "".join(completed_sentences)
                current_playing_text = completed_text + current_sentence_text[:current_sentence_position]
                played_text = current_playing_text
            
            # 只有当文本内容发生变化时才发送到前端（去重）
            if played_text and played_text != last_sent_text:
                # 记录变化前的长度，用于日志
                prev_length = len(last_sent_text) if last_sent_text else 0
                current_length = len(played_text)
                
                # 异步发送部分结果
                asyncio.run_coroutine_threadsafe(
                    message_queue.put({
                        "type": "partial_assistant_answer",
                        "content": played_text
                    }),
                    event_loop
                )
                last_sent_text = played_text
                
                # 减少日志输出频率，只在文本长度变化较大时输出（避免重复日志）
                if current_length % 10 == 0 or (current_length - prev_length) >= 5:
                    log_debug("WEBSOCKET", f"发送 TTS 播放进度文字: {current_length} 字符", indent=1)
        except Exception as e:
            log_error("WEBSOCKET", f"处理 TTS 播放进度事件时出错: {e}", indent=1)
    
    def on_asr_partial(event):
        """处理 ASR 部分识别结果事件"""
        try:
            data = event.data
            text = data.get("text", "")
            if text:
                # 异步发送部分结果
                asyncio.run_coroutine_threadsafe(
                    message_queue.put({
                        "type": "partial_user_request",
                        "content": text
                    }),
                    event_loop
                )
        except Exception as e:
            log_error("WEBSOCKET", f"处理 ASR 部分结果事件时出错: {e}", indent=1)
    
    # 用于记录已通过事件发送的用户消息，避免与对话历史重复
    sent_user_messages = set()
    # 用于标记文本输入已处理，避免 ASR 事件重复发送
    # 使用 dialogue_manager 的属性来存储，以便在 process_incoming_data 中访问
    if not hasattr(dialogue_manager, '_text_input_handled'):
        dialogue_manager._text_input_handled = set()
    
    def on_asr_final(event):
        """处理 ASR 最终识别结果事件"""
        try:
            data = event.data
            text = data.get("text", "")
            
            # 如果这个文本已经通过文本输入处理过，跳过（避免重复发送）
            if hasattr(dialogue_manager, '_text_input_handled') and text in dialogue_manager._text_input_handled:
                log_debug("WEBSOCKET", f"跳过 ASR 事件（文本输入已处理）: {text[:50]}...", indent=1)
                return
            
            # 验证文本是否有效（非空且有意义）
            if not text or len(text.strip()) == 0:
                log_debug("WEBSOCKET", "ASR识别结果为空，跳过发送", indent=1)
                return
            
            # 检查是否已发送过（避免重复）
            if text not in sent_user_messages:
                # 异步发送最终结果
                asyncio.run_coroutine_threadsafe(
                    message_queue.put({
                        "type": "final_user_request",
                        "content": text
                    }),
                    event_loop
                )
                sent_user_messages.add(text)
                log_info("WEBSOCKET", f"已验证并发送ASR识别结果: {text[:50]}...", indent=1)
                
                # 限制集合大小
                if len(sent_user_messages) > 50:
                    # 保留最近的消息
                    sent_user_messages.clear()
                    sent_user_messages.add(text)
        except Exception as e:
            log_error("WEBSOCKET", f"处理 ASR 最终结果事件时出错: {e}", indent=1)
    
    # 订阅事件
    # 不再订阅LLM句子事件，文字输出完全通过TTS播放进度同步，不显示预览
    # llm_sentence_filter = EventFilter(event_types=[EventType.LLM_SENTENCE_GENERATED])
    # dialogue_manager.event_bus.subscribe(on_llm_sentence, llm_sentence_filter)
    
    asr_partial_filter = EventFilter(event_types=[EventType.ASR_PARTIAL_RESULT])
    dialogue_manager.event_bus.subscribe(on_asr_partial, asr_partial_filter)
    
    asr_final_filter = EventFilter(event_types=[EventType.ASR_FINAL_RESULT])
    dialogue_manager.event_bus.subscribe(on_asr_final, asr_final_filter)
    
    # 订阅 TTS 播放进度事件，用于同步文字输出
    audio_play_progress_filter = EventFilter(event_types=[EventType.AUDIO_PLAY_PROGRESS])
    dialogue_manager.event_bus.subscribe(on_audio_play_progress, audio_play_progress_filter)
    
    def on_audio_play_end(event):
        """处理音频播放结束事件，发送最终结果（只发送已播放的内容）"""
        nonlocal final_answer_sent
        try:
            data = event.data if hasattr(event, 'data') else {}
            task_id = data.get("task_id", 0)
            
            # 验证任务ID是否匹配当前任务
            current_task_id = dialogue_manager.context_manager.get_current_task_id()
            if task_id != current_task_id:
                return
            
            # 如果已经发送过最终结果，不再重复发送
            if final_answer_sent:
                return
            
            # 获取当前任务上下文
            context = dialogue_manager.context_manager.get_current_context()
            if not context or not context.is_valid:
                return
            
            # 优先使用played_response_text（已播放的内容），确保只显示说出来的内容
            final_text = context.get_extra_data("played_response_text", "")
            
            # 如果played_response_text不完整，检查是否有未完成的句子并补充完整
            if final_text:
                completed_sentences = context.get_extra_data("completed_sentences", [])
                last_sentence_text = context.get_extra_data("last_sentence_text", "")
                
                # 如果有未完成的句子，补充完整（确保包含最后一个字符）
                if last_sentence_text:
                    # 计算应该包含的完整文本
                    expected_text = "".join(completed_sentences) + last_sentence_text
                    # 如果final_text不完整，补充完整
                    if len(final_text) < len(expected_text):
                        final_text = expected_text
                    # 如果final_text没有以last_sentence_text结尾，说明最后一句不完整，补充完整
                    elif not final_text.endswith(last_sentence_text):
                        # 检查是否部分包含最后一句
                        completed_text = "".join(completed_sentences)
                        if final_text.startswith(completed_text):
                            # 已包含已完成句子，补充最后一句的剩余部分
                            remaining = last_sentence_text[len(final_text) - len(completed_text):]
                            final_text = final_text + remaining
                        else:
                            # 结构不匹配，使用完整文本
                            final_text = expected_text
            
            if not final_text:
                # 如果没有已播放文本，使用response_text作为fallback
                final_text = context.response_text
            
            # 如果有最终文本，发送final_assistant_answer
            if final_text:
                asyncio.run_coroutine_threadsafe(
                    message_queue.put({
                        "type": "final_assistant_answer",
                        "content": final_text
                    }),
                    event_loop
                )
                final_answer_sent = True  # 标记已发送最终结果
                # 在TaskContext中标记该任务已发送过最终结果（用于避免在用户输入新内容时重复发送）
                if context:
                    context.set_extra_data("final_answer_sent_to_frontend", True)
                log_info("WEBSOCKET", f"播放完成，发送最终结果（已播放内容）: {len(final_text)} 字符", indent=1)
        except Exception as e:
            log_error("WEBSOCKET", f"处理音频播放结束事件时出错: {e}", indent=1)
    
    def on_user_interrupt(event):
        """处理用户打断事件，发送已播放内容的最终结果"""
        nonlocal final_answer_sent, last_sent_task_id
        try:
            data = event.data if hasattr(event, 'data') else {}
            task_id = data.get("task_id", 0)
            
            # 获取被打断的任务上下文（可能是旧任务，因为新任务可能已经创建）
            # 尝试获取当前上下文，如果无效，说明任务已经被取消
            context = dialogue_manager.context_manager.get_current_context()
            
            # 如果当前上下文无效，可能是任务已经被取消，尝试从事件数据获取任务ID
            if not context or not context.is_valid:
                # 如果task_id为0，可能是通用事件，无法确定具体任务
                if task_id == 0:
                    return
                # 尝试通过task_id查找上下文（但ContextManager可能已经清空了）
                # 这种情况下，打断时的最终结果应该在打断逻辑中已经发送
                log_debug("WEBSOCKET", f"打断事件：任务上下文已失效，可能已在打断逻辑中处理", indent=1)
                return
            
            # 验证任务ID是否匹配（允许task_id为0的通用事件）
            current_task_id = dialogue_manager.context_manager.get_current_task_id()
            if task_id != 0 and task_id != current_task_id:
                # 如果task_id不匹配且不是0，说明是旧任务的事件，可能已经在打断逻辑中处理
                log_debug("WEBSOCKET", f"打断事件：任务ID不匹配（事件task_id={task_id}, 当前task_id={current_task_id}），可能已在打断逻辑中处理", indent=1)
                return
            
            # 如果已经发送过最终结果，不再重复发送
            if final_answer_sent and last_sent_task_id == task_id:
                return
            
            # 只使用played_response_text（已播放的内容），确保只显示说出来的内容
            played_text = context.get_extra_data("played_response_text", "")
            
            # 如果有已播放文本，发送final_assistant_answer（截至到打断位置）
            if played_text:
                asyncio.run_coroutine_threadsafe(
                    message_queue.put({
                        "type": "final_assistant_answer",
                        "content": played_text
                    }),
                    event_loop
                )
                final_answer_sent = True  # 标记已发送最终结果
                last_sent_task_id = task_id  # 记录任务ID
                # 在TaskContext中标记该任务已发送过最终结果（用于避免在用户输入新内容时重复发送）
                if context:
                    context.set_extra_data("final_answer_sent_to_frontend", True)
                log_info("WEBSOCKET", f"用户打断事件，发送最终结果（已播放内容）: {len(played_text)} 字符", indent=1)
        except Exception as e:
            log_error("WEBSOCKET", f"处理用户打断事件时出错: {e}", indent=1)
    
    # 订阅音频播放结束事件
    audio_play_end_filter = EventFilter(event_types=[EventType.AUDIO_PLAY_END])
    dialogue_manager.event_bus.subscribe(on_audio_play_end, audio_play_end_filter)
    
    # 订阅用户打断事件，在打断时发送已播放内容的最终结果
    user_interrupt_filter = EventFilter(event_types=[EventType.USER_INTERRUPT, EventType.INTERRUPT_VALIDATED])
    dialogue_manager.event_bus.subscribe(on_user_interrupt, user_interrupt_filter)
    
    # 订阅任务取消事件，在任务取消时发送已播放内容的最终结果（作为兜底）
    def on_task_cancelled(event):
        """处理任务取消事件，发送已播放内容的最终结果（兜底）"""
        nonlocal final_answer_sent, last_sent_task_id
        try:
            data = event.data if hasattr(event, 'data') else {}
            cancelled_task_id = data.get("task_id", 0)
            
            # 如果已经发送过最终结果，不再重复发送
            if final_answer_sent and last_sent_task_id == cancelled_task_id:
                return
            
            # 尝试获取被打断的任务上下文
            # 注意：任务取消时，上下文可能已经被清空，所以这个主要是兜底
            # 主要的处理应该在打断逻辑中完成
            log_debug("WEBSOCKET", f"任务取消事件（task_id={cancelled_task_id}），可能已在打断逻辑中处理", indent=1)
        except Exception as e:
            log_error("WEBSOCKET", f"处理任务取消事件时出错: {e}", indent=1)
    
    task_cancelled_filter = EventFilter(event_types=[EventType.TASK_CANCELLED])
    dialogue_manager.event_bus.subscribe(on_task_cancelled, task_cancelled_filter)
    
    # 订阅任务创建事件，在新任务开始时重置状态
    def on_task_created(event):
        """处理任务创建事件，重置状态以允许新任务发送最终结果"""
        nonlocal final_answer_sent, last_sent_text, last_sent_task_id
        try:
            data = event.data if hasattr(event, 'data') else {}
            new_task_id = data.get("task_id", 0)
            
            # 新任务创建，重置所有状态
            if new_task_id > 0:
                final_answer_sent = False
                last_sent_text = ""
                last_sent_task_id = new_task_id
                log_debug("WEBSOCKET", f"新任务创建（task_id={new_task_id}），重置状态", indent=1)
        except Exception as e:
            log_error("WEBSOCKET", f"处理任务创建事件时出错: {e}", indent=1)
    
    task_created_filter = EventFilter(event_types=[EventType.TASK_CREATED])
    dialogue_manager.event_bus.subscribe(on_task_created, task_created_filter)
    
    log_info("WEBSOCKET", "已订阅事件系统（ASR结果、TTS播放进度、音频播放结束、用户打断、任务创建/取消）", indent=1)
    
    # 使用轮询方式检查对话历史（仅用于助手最终结果，用户消息通过事件发送）
    # 记录已发送的消息，避免重复
    sent_messages = set()  # 使用集合记录已发送的消息 (role, content) 元组
    last_history_length = 0
    
    try:
        while True:
            await asyncio.sleep(0.1)  # 每 100ms 检查一次
            
            if not dialogue_manager:
                continue
            
            # 检查对话历史是否有更新
            with dialogue_manager.history_lock:
                current_history = dialogue_manager.conversation_history.copy()
            
            if len(current_history) > last_history_length:
                # 有新消息，只处理助手消息（用户消息已通过 ASR 事件发送）
                new_messages = current_history[last_history_length:]
                for msg in new_messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    
                    # 创建消息标识符
                    msg_key = (role, content)
                    
                    # 只发送助手消息（用户消息通过 ASR 事件已发送，避免重复）
                    if role == "assistant" and msg_key not in sent_messages:
                        # 助手消息：不再通过对话历史发送final_assistant_answer
                        # 因为final_assistant_answer现在通过AUDIO_PLAY_END事件发送，确保在TTS播放完成后才发送
                        # 只记录已发送，不实际发送（避免在TTS播放完成前发送）
                        sent_messages.add(msg_key)
                        log_debug("WEBSOCKET", f"检测到助手消息已添加到对话历史（等待播放完成事件）: {content[:50]}...", indent=1)
                    elif role == "user":
                        # 用户消息：记录已发送（通过 ASR 事件），但不重复发送
                        # 用户新消息，重置最终结果标志（新任务开始）
                        if msg_key not in sent_messages:
                            # 重置最终结果标志，允许新的对话发送最终结果
                            final_answer_sent = False
                            # 注意：用户消息应该已经在打断逻辑中发送了，这里只作为兜底
                            # 如果用户消息还没通过事件发送，则发送（兜底）
                            await message_queue.put({
                                "type": "final_user_request",
                                "content": content
                            })
                            sent_messages.add(msg_key)
                
                last_history_length = len(current_history)
                
                # 限制 sent_messages 集合大小，避免内存泄漏
                if len(sent_messages) > 100:
                    # 保留最近的消息，删除最旧的
                    # 简单策略：清空并重新构建（基于当前历史）
                    sent_messages = {
                        (msg.get("role"), msg.get("content"))
                        for msg in current_history[-50:]  # 保留最近50条
                    }
    
    except asyncio.CancelledError:
        pass
    except Exception as e:
        log_error("WEBSOCKET", f"监控对话事件时出错: {e}", indent=1)
    finally:
        # 恢复原始的 audio_player
        if dialogue_manager and original_audio_player:
            dialogue_manager.audio_player = original_audio_player
            log_info("WEBSOCKET", "已恢复原始音频播放器", indent=1)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket 端点，处理实时语音交互"""
    await ws.accept()
    log_info("WEBSOCKET", "客户端已连接", indent=0)
    
    dialogue_manager = app.state.dialogue_manager
    if not dialogue_manager:
        await ws.close(code=1008, reason="DialogueManager not initialized")
        return
    
    message_queue = asyncio.Queue()
    audio_chunks = asyncio.Queue()
    
    # 创建音频处理器
    audio_handler = WebSocketAudioHandler(dialogue_manager, message_queue)
    # 设置事件循环引用（用于在音频处理中发送消息）
    audio_handler.event_loop = asyncio.get_running_loop()
    
    # 创建任务
    tasks = [
        asyncio.create_task(process_incoming_data(ws, app, audio_chunks, audio_handler, message_queue)),
        asyncio.create_task(send_text_messages(ws, message_queue)),
        asyncio.create_task(monitor_dialogue_events(app, message_queue, audio_handler)),
    ]
    
    try:
        # 等待任一任务完成（通常是连接断开）
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        # 取消所有待处理的任务
        for task in pending:
            task.cancel()
        
        # 等待所有任务完成清理
        await asyncio.gather(*tasks, return_exceptions=True)
    
    except Exception as e:
        log_error("WEBSOCKET", f"WebSocket 会话出错: {e}", indent=0)
    finally:
        log_info("WEBSOCKET", "WebSocket 会话结束", indent=0)


if __name__ == "__main__":
    import uvicorn
    
    # 设置 Windows 事件循环策略
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    log_info("WEBSERVER", "启动 Web 服务器...", indent=0)
    log_info("WEBSERVER", "访问 http://localhost:8000 使用网页端界面", indent=0)
    
    uvicorn.run(
        "web_server:app",
        host="0.0.0.0",
        port=8000,
        log_config=None
    )
