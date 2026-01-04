#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
语音对话系统主程序

启动和运行voice_chatbot语音对话系统
"""

import sys
import signal
import argparse
import subprocess
import time
import requests
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from voice_chatbot.utils import Config, init_logger, log_info, log_error, log_warn, update_logger_filter, update_logger_category_colors
from voice_chatbot.dialogue import DialogueManager


# 全局变量，用于信号处理
dialogue_manager = None
melo_tts_process = None


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """
    检查端口是否被占用
    
    Args:
        port: 端口号
        host: 主机地址，默认为 127.0.0.1
        
    Returns:
        bool: 如果端口被占用返回 True，否则返回 False
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex((host, port))
            return result == 0
    except Exception:
        return False


def check_melo_tts_api(port: int) -> bool:
    """
    检查指定端口是否运行着 MeloTTS API 服务
    
    Args:
        port: 端口号
        
    Returns:
        bool: 如果是 MeloTTS API 服务返回 True，否则返回 False
    """
    try:
        response = requests.get(f"http://127.0.0.1:{port}/docs", timeout=2)
        if response.status_code == 200:
            return True
    except:
        pass
    return False


def signal_handler(signum, frame):
    """
    信号处理函数（用于优雅关闭）
    
    Args:
        signum: 信号编号
        frame: 当前堆栈帧
    """
    global melo_tts_process
    log_info("MAIN", f"收到信号 {signum}，正在关闭系统...", indent=0)
    if dialogue_manager:
        try:
            dialogue_manager.stop()
        except Exception as e:
            log_error("MAIN", f"关闭系统时出错: {e}", indent=1)
    
    # 关闭 MeloTTS API 服务进程
    if melo_tts_process:
        try:
            log_info("MAIN", "正在关闭 MeloTTS API 服务...", indent=0)
            melo_tts_process.terminate()
            melo_tts_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log_warn("MAIN", "MeloTTS API 服务未响应，强制终止...", indent=1)
            melo_tts_process.kill()
        except Exception as e:
            log_error("MAIN", f"关闭 MeloTTS API 服务时出错: {e}", indent=1)
    
    sys.exit(0)


def start_melo_tts_api(config: Config):
    """
    启动 MeloTTS API 服务
    
    Args:
        config: 配置对象
        
    Returns:
        subprocess.Popen: 进程对象，如果启动失败返回 None
    """
    global melo_tts_process
    
    try:
        # 获取 MeloTTS 配置
        tts_config = config["tts"]
        default_engine = tts_config.get("default_engine", "edgetts")
        
        # 检查是否使用 MeloTTS
        if default_engine.lower() != "melotts":
            return None
        
        melo_tts_config = tts_config.get("engines", {}).get("melo_tts", {})
        api_url = melo_tts_config.get("api_url", "http://127.0.0.1:8101")
        model_language = melo_tts_config.get("model_language", "ZH")
        
        # 解析 API URL 获取端口
        parsed_url = urlparse(api_url)
        port = parsed_url.port or 8101
        
        # 先检查端口是否被占用
        if is_port_in_use(port):
            log_info("MAIN", f"端口 {port} 已被占用，检查是否为 MeloTTS API 服务...", indent=0)
            # 检查是否是 MeloTTS API 服务
            if check_melo_tts_api(port):
                log_info("MAIN", f"MeloTTS API 服务已在运行 (端口 {port})", indent=1)
                # 即使服务已在运行，也尝试预加载模型（如果模型未加载或语言不匹配）
                log_info("MAIN", f"检查并预加载 MeloTTS 模型 (语言: {model_language})...", indent=1)
                try:
                    # 先检查模型是否已加载
                    speakers_response = requests.get(f"http://127.0.0.1:{port}/speakers", timeout=5)
                    if speakers_response.status_code == 200:
                        log_info("MAIN", "模型已加载，无需重新加载", indent=2)
                    else:
                        # 模型未加载，尝试加载
                        log_info("MAIN", "模型未加载，开始加载...", indent=2)
                        init_response = requests.post(
                            f"http://127.0.0.1:{port}/init",
                            json={
                                "language": model_language,
                                "device": "auto",
                                "use_hf": True
                            },
                            timeout=60
                        )
                        if init_response.status_code == 200:
                            init_result = init_response.json()
                            if init_result.get("success", False):
                                log_info("MAIN", f"模型加载成功: {init_result.get('message', '')}", indent=2)
                            else:
                                log_warn("MAIN", f"模型加载失败: {init_result.get('message', '未知错误')}", indent=2)
                except Exception as e:
                    log_warn("MAIN", f"检查/加载模型时出错: {e}，模型将在首次使用时加载", indent=2)
                return None
            else:
                log_warn("MAIN", f"端口 {port} 被占用，但不是 MeloTTS API 服务，跳过启动", indent=1)
                log_warn("MAIN", "请手动检查端口占用情况或更改配置中的端口号", indent=1)
                return None
        else:
            log_info("MAIN", f"端口 {port} 未被占用，准备启动 MeloTTS API 服务", indent=0)
        
        log_info("MAIN", f"正在启动 MeloTTS API 服务 (端口 {port})...", indent=0)
        
        # 获取 MeloTTS 目录路径
        melotts_dir = project_root / "MeloTTS"
        if not melotts_dir.exists():
            log_error("MAIN", f"MeloTTS 目录不存在: {melotts_dir}", indent=1)
            return None
        
        api_py_path = melotts_dir / "api.py"
        if not api_py_path.exists():
            log_error("MAIN", f"MeloTTS API 文件不存在: {api_py_path}", indent=1)
            return None
        
        # 设置环境变量（从 start-api-backend.bat 中提取）
        env = os.environ.copy()
        env["HF_ENDPOINT"] = "https://hf-mirror.com"
        env["HF_HOME"] = str(melotts_dir / "hf_download")
        
        # 启动 API 服务
        # 使用 uvicorn 直接运行，而不是通过 bat 文件
        if sys.platform == "win32":
            # Windows: 在新控制台窗口中运行，方便查看日志
            melo_tts_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", str(port)],
                cwd=str(melotts_dir),
                env=env,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:
            # Linux/Mac: 在后台运行，捕获输出用于错误诊断
            melo_tts_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", str(port)],
                cwd=str(melotts_dir),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        
        # 等待服务启动
        log_info("MAIN", "等待 MeloTTS API 服务启动...", indent=1)
        max_wait_time = 30  # 最多等待30秒
        check_interval = 0.5  # 每0.5秒检查一次
        waited_time = 0
        
        while waited_time < max_wait_time:
            try:
                response = requests.get(f"http://127.0.0.1:{port}/docs", timeout=2)
                if response.status_code == 200:
                    log_info("MAIN", f"MeloTTS API 服务启动成功 (端口 {port})", indent=1)
                    
                    # 服务启动后，预加载配置中指定的模型
                    model_language = melo_tts_config.get("model_language", "ZH")
                    log_info("MAIN", f"预加载 MeloTTS 模型 (语言: {model_language})...", indent=1)
                    try:
                        init_response = requests.post(
                            f"http://127.0.0.1:{port}/init",
                            json={
                                "language": model_language,
                                "device": "auto",
                                "use_hf": True
                            },
                            timeout=60  # 模型加载可能需要较长时间
                        )
                        if init_response.status_code == 200:
                            init_result = init_response.json()
                            if init_result.get("success", False):
                                log_info("MAIN", f"模型预加载成功: {init_result.get('message', '')}", indent=2)
                            else:
                                log_warn("MAIN", f"模型预加载失败: {init_result.get('message', '未知错误')}", indent=2)
                        else:
                            log_warn("MAIN", f"模型预加载请求失败: HTTP {init_response.status_code}", indent=2)
                    except requests.exceptions.Timeout:
                        log_warn("MAIN", "模型预加载超时（60秒），但服务已启动，模型将在首次使用时加载", indent=2)
                    except Exception as e:
                        log_warn("MAIN", f"模型预加载出错: {e}，但服务已启动，模型将在首次使用时加载", indent=2)
                    
                    return melo_tts_process
            except:
                pass
            
            # 检查进程是否还在运行
            if melo_tts_process.poll() is not None:
                # 进程已退出，读取错误信息
                stdout, stderr = melo_tts_process.communicate()
                error_msg = stderr.decode('utf-8', errors='ignore') if stderr else "未知错误"
                log_error("MAIN", f"MeloTTS API 服务启动失败: {error_msg}", indent=1)
                melo_tts_process = None
                return None
            
            time.sleep(check_interval)
            waited_time += check_interval
        
        log_error("MAIN", f"MeloTTS API 服务启动超时（等待 {max_wait_time} 秒）", indent=1)
        if melo_tts_process:
            melo_tts_process.terminate()
            melo_tts_process = None
        return None
        
    except Exception as e:
        log_error("MAIN", f"启动 MeloTTS API 服务时出错: {e}", indent=1)
        import traceback
        log_error("MAIN", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
        if melo_tts_process:
            try:
                melo_tts_process.terminate()
            except:
                pass
            melo_tts_process = None
        return None


def main():
    """主函数"""
    global dialogue_manager, melo_tts_process
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description="语音对话系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                           # 使用默认配置
  python main.py -c config.json            # 使用指定配置文件
  python main.py --log-level DEBUG         # 设置日志级别为DEBUG
        """
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="配置文件路径（JSON格式），如果未指定则使用默认配置"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        default="INFO",
        help="日志级别（默认: INFO）"
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="logs",
        help="日志文件目录（默认: logs）"
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="禁用日志文件输出（仅输出到控制台）"
    )
    
    args = parser.parse_args()
    
    # 初始化日志系统
    log_file_enabled = not args.no_log_file
    # 先加载配置（用于初始化日志系统）
    config = Config(config_file=args.config)
    
    # 初始化日志系统（传递 config 以读取过滤和颜色配置）
    init_logger(
        log_level=args.log_level or config.get("logging.level", "INFO"),
        log_file_enabled=log_file_enabled,
        log_dir=args.log_dir or config.get("logging.log_dir", "logs"),
        config=config  # 传递 config 以读取 filtered_categories 和 category_colors
    )
    
    log_info("MAIN", "=" * 60, indent=0)
    log_info("MAIN", "语音对话系统启动", indent=0)
    log_info("MAIN", "=" * 60, indent=0)
    
    try:
        if args.config:
            log_info("MAIN", f"配置文件: {args.config}", indent=1)
        else:
            log_info("MAIN", "使用默认配置", indent=1)
        
        # 更新 Logger 的过滤配置（从配置读取）
        update_logger_filter(config)
        # 更新 Logger 的类别颜色配置（从配置读取）
        update_logger_category_colors(config)
        
        # 如果使用 MeloTTS，先启动 API 服务
        melo_tts_process = start_melo_tts_api(config)
        if melo_tts_process is None and config["tts"].get("default_engine", "").lower() == "melotts":
            log_warn("MAIN", "MeloTTS API 服务启动失败，但继续运行主程序（如果服务已在运行则忽略此警告）", indent=0)
        
        # 创建DialogueManager实例
        log_info("MAIN", "初始化DialogueManager...", indent=0)
        global dialogue_manager
        dialogue_manager = DialogueManager(config=config)
        
        # 初始化所有模块
        log_info("MAIN", "初始化模块...", indent=0)
        dialogue_manager.initialize_modules()
        
        # 注册信号处理（优雅关闭）
        signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, signal_handler)  # 终止信号（Linux/Mac）
        if sys.platform == "win32" and hasattr(signal, 'SIGBREAK'):
            # Windows下可以注册SIGBREAK（Ctrl+Break）
            signal.signal(signal.SIGBREAK, signal_handler)
        
        # 启动系统
        log_info("MAIN", "启动系统...", indent=0)
        dialogue_manager.start()
        
        log_info("MAIN", "系统已启动，等待用户输入...", indent=0)
        log_info("MAIN", "按 Ctrl+C 退出", indent=0)
        
        # 保持主线程运行
        # 注意：KeyboardInterrupt会被SIGINT信号处理器捕获
        # 主循环只需要简单地sleep即可
        import time
        while dialogue_manager.running:
            time.sleep(0.1)
        
    except KeyboardInterrupt:
        log_info("MAIN", "收到键盘中断信号", indent=0)
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        log_error("MAIN", f"系统运行出错: {e}", indent=0)
        import traceback
        log_error("MAIN", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
        
        # 确保清理资源
        if dialogue_manager:
            try:
                dialogue_manager.stop()
            except Exception as cleanup_error:
                log_error("MAIN", f"清理资源时出错: {cleanup_error}", indent=1)
        
        sys.exit(1)
    finally:
        # 确保清理 MeloTTS API 服务进程
        # 注意：melo_tts_process 已在 main() 函数开头声明为 global
        if melo_tts_process:
            try:
                log_info("MAIN", "正在关闭 MeloTTS API 服务...", indent=0)
                melo_tts_process.terminate()
                melo_tts_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log_warn("MAIN", "MeloTTS API 服务未响应，强制终止...", indent=1)
                melo_tts_process.kill()
            except Exception as e:
                log_error("MAIN", f"关闭 MeloTTS API 服务时出错: {e}", indent=1)
        log_info("MAIN", "程序退出", indent=0)


if __name__ == "__main__":
    main()

