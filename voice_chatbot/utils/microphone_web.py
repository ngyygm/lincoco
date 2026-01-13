"""
Web 麦克风服务端（基于通用 WebSocket 服务器）

接收来自 Web 浏览器的音频数据并通过回调函数实时传输
支持多客户端连接，音频数据实时传输到系统处理流程
"""

import sys
import os

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import numpy as np
from typing import Optional, Callable, Dict
import threading

# 从 logger 模块导入日志函数
from voice_chatbot.utils.logger import log_info, log_warn, log_error, log_debug
# 从通用 WebSocket 服务器导入
from voice_chatbot.utils.websocket_server import WebSocketServer, get_websocket_server


class WebMicrophoneServer:
    """Web 麦克风服务端（封装通用 WebSocket 服务器）"""

    def __init__(self, config, ws_server: WebSocketServer = None,
                 host: str = "0.0.0.0", port: int = 8766,
                 on_audio_callback: Optional[Callable] = None):
        """
        初始化 Web 麦克风服务端

        Args:
            config: 配置对象
            ws_server: WebSocket 服务器实例（如果为 None，则创建新实例）
            host: 监听地址（仅在 ws_server 为 None 时使用）
            port: 监听端口（仅在 ws_server 为 None 时使用）
            on_audio_callback: 音频数据回调函数（必须提供，用于实时处理）
        """
        self.config = config
        self.host = host
        self.port = port

        # 音频回调函数（用于实时处理）
        if on_audio_callback is None:
            raise ValueError("on_audio_callback 参数必须提供")
        self.on_audio_callback = on_audio_callback

        # 客户端统计信息（独立于通用服务器的客户端管理）
        self.client_stats: Dict[str, dict] = {}  # {client_id: {total_samples, start_time}}
        self.lock = threading.Lock()

        # 音频参数
        self.sample_rate = config.get("audio.sample_rate", 16000)
        self.running = False

        # 使用传入的 WebSocket 服务器或创建新实例
        if ws_server is not None:
            self.ws_server = ws_server
            log_info("MIC", "使用外部 WebSocket 服务器实例", indent=1)
        else:
            self.ws_server = get_websocket_server(config, host, port)
            log_info("MIC", f"创建新 WebSocket 服务器实例 - {host}:{port}", indent=1)

        # 注册音频处理器
        self.ws_server.register_handler("binary", self._handle_binary_data)

        # 注册文本消息处理器
        self.ws_server.register_handler("start_recording", self._handle_text_message)
        self.ws_server.register_handler("stop_recording", self._handle_text_message)
        self.ws_server.register_handler("info", self._handle_text_message)

        log_info("MIC", "实时音频回调已启用", indent=1)
        log_info("MIC", "音频数据将实时传输到系统", indent=1)

    def _handle_binary_data(self, client_id: str, audio_data: bytes):
        """
        处理二进制数据（音频）

        Args:
            client_id: 客户端 ID
            audio_data: 音频数据（bytes）
        """
        # 将 float32 格式的音频转换为 numpy 数组
        audio_float32 = np.frombuffer(audio_data, dtype=np.float32)

        # 更新统计信息
        with self.lock:
            if client_id not in self.client_stats:
                self.client_stats[client_id] = {
                    'total_samples': 0,
                    'start_time': None
                }

            self.client_stats[client_id]['total_samples'] += len(audio_float32)

        # 调用音频回调（用于实时处理）
        if self.on_audio_callback:
            try:
                self.on_audio_callback(audio_float32)
            except Exception as e:
                log_error("MIC", f"音频回调错误: {e}", indent=1)

        # 打印统计信息（每 5 秒打印一次）
        with self.lock:
            if client_id in self.client_stats:
                total_samples = self.client_stats[client_id]['total_samples']
                if total_samples % (self.sample_rate * 5) == 0:
                    duration = total_samples / self.sample_rate
                    rms = np.sqrt(np.mean(audio_float32 ** 2))
                    log_debug("MIC",
                             f"[{client_id}] 已接收 {duration:.1f}秒音频, "
                             f"当前块RMS: {rms:.4f}", indent=1)

    def _handle_text_message(self, client_id: str, data: dict):
        """
        处理文本消息

        Args:
            client_id: 客户端 ID
            data: 消息字典
        """
        msg_type = data.get("type")

        if msg_type == "start_recording":
            log_info("MIC", f"[{client_id}] 客户端开始录音", indent=1)

        elif msg_type == "stop_recording":
            log_info("MIC", f"[{client_id}] 客户端停止录音", indent=1)

        elif msg_type == "info":
            info = data.get("info", {})
            log_info("MIC",
                    f"[{client_id}] 客户端信息: {info}", indent=1)

    async def start(self):
        """启动服务器"""
        self.running = True

        log_info("MIC", f"Web 麦克风服务端已准备就绪", indent=0)
        log_info("MIC", "注意：WebSocket 服务器由 manager.py 统一管理", indent=1)

        # 不需要启动自己的服务器，因为使用的是外部的 WebSocket 服务器

    async def stop(self):
        """停止服务器"""
        self.running = False

        # 清理客户端统计
        with self.lock:
            self.client_stats.clear()

        log_info("MIC", "服务端已停止", indent=0)

    def get_statistics(self) -> dict:
        """获取统计信息"""
        with self.lock:
            total_samples = sum(stats['total_samples'] for stats in self.client_stats.values())
            total_duration = total_samples / self.sample_rate

            return {
                "active_clients": len(self.client_stats),
                "total_samples": total_samples,
                "total_duration": total_duration,
            }


async def main_async():
    """异步主函数（仅用于测试）"""
    from voice_chatbot.utils.config import Config

    config = Config()

    # 定义一个简单的音频回调函数用于测试
    def test_audio_callback(audio_data):
        """测试音频回调"""
        print(f"接收到音频数据: {len(audio_data)} 样本")

    server = WebMicrophoneServer(
        config=config,
        host="0.0.0.0",
        port=8766,
        on_audio_callback=test_audio_callback
    )

    # 启动 WebSocket 服务器（因为没有外部提供）
    await server.ws_server.start()

    # 打印统计信息（每 10 秒）
    try:
        while True:
            await asyncio.sleep(10)
            stats = server.get_statistics()
            log_info("MIC", f"统计: {stats['active_clients']} 活跃客户端, "
                    f"总时长: {stats['total_duration']:.1f}秒", indent=0)
    except asyncio.CancelledError:
        pass
    finally:
        await server.ws_server.stop()


if __name__ == "__main__":
    import sys
    try:
        print("注意：此模式仅用于测试 Web 麦克风服务器，实际使用请通过 run_web_mic_dialogue.py 启动完整系统")
        asyncio.run(main_async())
    except KeyboardInterrupt:
        log_info("MIC", "收到中断信号，正在停止...", indent=0)
        sys.exit(0)
