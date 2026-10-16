"""
通用 WebSocket 服务端

支持多种消息类型的 WebSocket 服务器，提供消息广播和回调机制
可用于音频传输、日志传输等功能
"""

import sys
import os

# 添加项目根目录到 Python 路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import asyncio
import json
import websockets
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Callable, List
import threading

# 日志函数缓存（延迟导入，避免与 logger 模块产生导入顺序问题）
_log_funcs = None


def _ws_log(level: str, message: str):
    """通过统一 logger 输出 WebSocket 服务器内部日志（替代裸 print）"""
    global _log_funcs
    if _log_funcs is None:
        try:
            from voice_chatbot.utils.logger import log_info, log_warn, log_error
            _log_funcs = {"INFO": log_info, "WARN": log_warn, "ERROR": log_error}
        except ImportError:
            _log_funcs = {}
    func = _log_funcs.get(level) or _log_funcs.get("INFO")
    if func is not None:
        func("WS", message, indent=0)
    else:
        print(f"[WebSocketServer] {message}")


class WebSocketServer:
    """通用 WebSocket 服务端"""

    def __init__(self, config, host: str = "0.0.0.0", port: int = 8766):
        """
        初始化 WebSocket 服务端

        Args:
            config: 配置对象
            host: 监听地址
            port: 监听端口
        """
        self.config = config
        self.host = host
        self.port = port

        # 客户端管理
        self.clients: Dict[str, dict] = {}  # {client_id: {websocket, client_addr, connected_time}}
        self.lock = threading.Lock()

        # 消息处理器注册表 {message_type: [callback1, callback2, ...]}
        self.message_handlers: Dict[str, List[Callable]] = {}

        # 服务状态
        self.server = None
        self.running = False

        # 音频参数（兼容旧代码）
        self.sample_rate = config.get("audio.sample_rate", 16000)

        # 保存事件循环引用（用于跨线程通信）
        self.event_loop: Optional[asyncio.AbstractEventLoop] = None
        self.event_loop_ready = threading.Event()  # 事件循环就绪信号

        # 注册为全局实例（这样 get_websocket_server() 能返回这个实例）
        global _global_server
        with _server_lock:
            if _global_server is None:
                _global_server = self

    async def handle_client(self, websocket):
        """处理客户端连接"""
        # 生成客户端 ID
        client_id = f"client_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        client_addr = websocket.remote_address

        _ws_log("INFO", f"客户端已连接: {client_addr} -> ID: {client_id}")

        # 初始化客户端
        with self.lock:
            self.clients[client_id] = {
                'websocket': websocket,
                'connected_time': datetime.now(),
                'client_addr': client_addr
            }

        try:
            # 发送欢迎消息和配置
            config_info = {
                "type": "welcome",
                "client_id": client_id,
                "sample_rate": self.sample_rate,
                "message": "欢迎连接到 WebSocket 服务端"
            }
            await websocket.send(json.dumps(config_info))

            # 接收消息
            async for message in websocket:
                try:
                    if isinstance(message, bytes):
                        # 二进制数据
                        await self._handle_binary_data(client_id, message)
                    elif isinstance(message, str):
                        # 文本消息
                        await self._handle_text_message(client_id, message)

                except Exception as e:
                    _ws_log("ERROR", f"处理客户端消息错误: {e}")

        except websockets.exceptions.ConnectionClosed:
            _ws_log("INFO", f"客户端断开连接: {client_id}")
        except Exception as e:
            _ws_log("ERROR", f"处理客户端连接错误: {e}")
        finally:
            # 清理客户端
            with self.lock:
                if client_id in self.clients:
                    del self.clients[client_id]
            _ws_log("INFO", f"客户端已清理: {client_id}")

    async def _handle_binary_data(self, client_id: str, data: bytes):
        """处理二进制数据"""
        # 调用注册的二进制数据处理器
        handlers = self.message_handlers.get("binary", [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(client_id, data)
                else:
                    handler(client_id, data)
            except Exception as e:
                _ws_log("ERROR", f"二进制处理器错误: {e}")

    async def _handle_text_message(self, client_id: str, message: str):
        """处理文本消息"""
        try:
            data = json.loads(message)
            msg_type = data.get("type", "unknown")

            # 调用注册的消息处理器
            handlers = self.message_handlers.get(msg_type, [])
            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(client_id, data)
                    else:
                        handler(client_id, data)
                except Exception as e:
                    _ws_log("ERROR", f"{msg_type} 消息处理器错误: {e}")

        except json.JSONDecodeError:
            _ws_log("WARN", f"收到非 JSON 文本消息: {message}")

    def register_handler(self, message_type: str, callback: Callable):
        """
        注册消息处理器

        Args:
            message_type: 消息类型（如 "audio", "start_recording", "log" 等）
            callback: 回调函数，签名: callback(client_id: str, message)
        """
        if message_type not in self.message_handlers:
            self.message_handlers[message_type] = []
        self.message_handlers[message_type].append(callback)

    def unregister_handler(self, message_type: str, callback: Callable):
        """取消注册消息处理器"""
        if message_type in self.message_handlers:
            if callback in self.message_handlers[message_type]:
                self.message_handlers[message_type].remove(callback)

    async def send_to_client(self, client_id: str, message: dict):
        """
        发送消息到指定客户端

        Args:
            client_id: 客户端 ID
            message: 消息字典（会自动转换为 JSON）
        """
        with self.lock:
            if client_id not in self.clients:
                return False

            websocket = self.clients[client_id]['websocket']

        try:
            await websocket.send(json.dumps(message))
            return True
        except Exception as e:
            _ws_log("WARN", f"发送消息到客户端 {client_id} 失败: {e}")
            return False

    async def send_to_all_clients(self, message: dict, exclude_client_ids: List[str] = None):
        """
        广播消息到所有客户端

        Args:
            message: 消息字典（会自动转换为 JSON）
            exclude_client_ids: 要排除的客户端 ID 列表
        """
        exclude_ids = set(exclude_client_ids or [])
        client_ids = list(self.clients.keys())

        for client_id in client_ids:
            if client_id in exclude_ids:
                continue
            await self.send_to_client(client_id, message)

    async def send_to_client_bytes(self, client_id: str, data: bytes):
        """
        发送二进制数据到指定客户端

        Args:
            client_id: 客户端 ID
            data: 二进制数据
        """
        with self.lock:
            if client_id not in self.clients:
                return False

            websocket = self.clients[client_id]['websocket']

        try:
            await websocket.send(data)
            return True
        except Exception as e:
            _ws_log("WARN", f"发送二进制数据到客户端 {client_id} 失败: {e}")
            return False

    def send_to_all_clients_sync(self, message: dict, exclude_client_ids: List[str] = None):
        """
        同步版本：广播消息到所有客户端（在事件循环中运行）

        Args:
            message: 消息字典（会自动转换为 JSON）
            exclude_client_ids: 要排除的客户端 ID 列表
        """
        # 等待事件循环就绪（最多等待5秒）
        if not self.event_loop_ready.wait(timeout=5.0):
            # 事件循环未就绪，静默失败
            return

        # 使用保存的事件循环
        if self.event_loop and self.event_loop.is_running():
            # 事件循环正在运行，使用 call_soon_threadsafe
            asyncio.run_coroutine_threadsafe(
                self.send_to_all_clients(message, exclude_client_ids),
                self.event_loop
            )
        else:
            # 事件循环未运行，静默失败
            pass

    def broadcast_log(self, level: str, category: str, message: str,
                     timestamp: str = None, source: str = None, inline: bool = False,
                     stream: str = None, stream_action: str = None,
                     stream_label: str = None, indent: int = 0):
        """
        广播日志消息到所有客户端

        Args:
            level: 日志级别 (INFO, WARN, ERROR, DEBUG)
            category: 日志类别
            message: 日志消息
            timestamp: 时间戳（可选，不提供则自动生成）
            source: 代码来源（可选）
            inline: 是否为 inline 日志（流式更新，兼容旧协议）
            stream: 流式通道名（如 play_text / asr_partial，同通道在网页端固定一行原地更新）
            stream_action: 流式动作（"update"=更新中 / "end"=结束并落定）
            stream_label: 流式标签（如 "播放文本" / "识别中"，网页端显示）
            indent: 缩进级别（网页端按层级缩进）
        """
        if timestamp is None:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        log_message = {
            "type": "log",
            "level": level,
            "category": category,
            "message": message,
            "timestamp": timestamp,
            "source": source,
            "inline": inline,  # 兼容旧 inline 标记
            "stream": stream,
            "stream_action": stream_action,
            "stream_label": stream_label,
            "indent": indent
        }

        self.send_to_all_clients_sync(log_message)

    async def start(self):
        """启动服务器"""
        self.running = True

        # 保存当前事件循环的引用（用于跨线程通信）
        self.event_loop = asyncio.get_running_loop()
        self.event_loop_ready.set()  # 标记事件循环已就绪

        _ws_log("INFO", f"启动 WebSocket 服务器: {self.host}:{self.port}")
        _ws_log("INFO", "等待客户端连接...")

        # 启动 WebSocket 服务器
        self.server = await websockets.serve(
            self.handle_client,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=20,
            max_size=10 * 1024 * 1024
        )

        _ws_log("INFO", f"服务器已启动，监听 {self.host}:{self.port}")

    async def stop(self):
        """停止服务器"""
        self.running = False

        # 清理事件循环就绪信号
        self.event_loop_ready.clear()
        self.event_loop = None

        # 关闭所有客户端连接
        client_ids = list(self.clients.keys())
        for client_id in client_ids:
            with self.lock:
                if client_id in self.clients:
                    websocket = self.clients[client_id]['websocket']
                    try:
                        await websocket.close()
                    except:
                        pass
                    del self.clients[client_id]

        # 关闭服务器
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        _ws_log("INFO", "服务器已停止")

    def get_statistics(self) -> dict:
        """获取统计信息"""
        with self.lock:
            return {
                "active_clients": len(self.clients),
                "client_ids": list(self.clients.keys()),
            }

    def get_client_count(self) -> int:
        """获取客户端数量（同步方法）"""
        with self.lock:
            return len(self.clients)


# 全局服务器实例（延迟初始化）
_global_server = None
_server_lock = threading.Lock()


def get_websocket_server(config=None, host: str = "0.0.0.0", port: int = 8766) -> Optional[WebSocketServer]:
    """
    获取全局 WebSocket 服务器实例（单例模式）

    Args:
        config: 配置对象（如果已设置全局实例，此参数会被忽略）
        host: 监听地址（如果已设置全局实例，此参数会被忽略）
        port: 监听端口（如果已设置全局实例，此参数会被忽略）

    Returns:
        WebSocketServer 实例（如果已设置），否则返回新创建的实例
    """
    global _global_server

    with _server_lock:
        # 如果全局实例已存在，直接返回
        if _global_server is not None:
            return _global_server

        # 否则创建新实例（仅当提供了 config 时）
        if config is not None:
            _global_server = WebSocketServer(config, host, port)
            return _global_server

        # 既没有全局实例，也没有提供 config
        return None


def reset_websocket_server():
    """重置全局 WebSocket 服务器实例（用于测试）"""
    global _global_server
    with _server_lock:
        _global_server = None


def set_global_server_instance(server):
    """
    直接设置全局 WebSocket 服务器实例

    用于从外部（如 logger.py）设置实例

    Args:
        server: WebSocketServer 实例
    """
    global _global_server
    with _server_lock:
        _global_server = server


async def main_async():
    """异步主函数（仅用于测试）"""
    from voice_chatbot.utils.config import Config

    config = Config()

    server = WebSocketServer(
        config=config,
        host="0.0.0.0",
        port=8766
    )

    # 注册音频处理器
    def handle_audio(client_id, data):
        print(f"收到客户端 {client_id} 的音频数据: {len(data)} 字节")

    server.register_handler("binary", handle_audio)

    # 注册文本消息处理器
    def handle_text(client_id, data):
        print(f"收到客户端 {client_id} 的文本消息: {data}")

    server.register_handler("start_recording", handle_text)
    server.register_handler("stop_recording", handle_text)

    await server.start()

    # 测试广播日志
    print("\n开始测试日志广播（每5秒发送一条日志）...")
    try:
        count = 0
        while True:
            await asyncio.sleep(5)
            count += 1
            server.broadcast_log("INFO", "TEST", f"这是测试日志 #{count}")
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    import sys
    try:
        print("注意：此模式仅用于测试 WebSocket 服务器，实际使用请通过主程序启动")
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("[WebSocketServer] 收到中断信号，正在停止...")
        sys.exit(0)
