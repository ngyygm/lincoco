#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
语音对话系统主程序

启动和运行voice_chatbot语音对话系统
"""

import sys
import signal
import argparse
from pathlib import Path


from voice_chatbot.utils import Config, init_logger, log_info, log_error, log_warn, update_logger_filter, update_logger_category_colors
from voice_chatbot.dialogue import DialogueManager


# 全局变量，用于信号处理
dialogue_manager = None


def signal_handler(signum, frame):
    """
    信号处理函数（用于优雅关闭）
    
    Args:
        signum: 信号编号
        frame: 当前堆栈帧
    """
    log_info("MAIN", f"收到信号 {signum}，正在关闭系统...", indent=0)
    if dialogue_manager:
        try:
            dialogue_manager.stop()
        except Exception as e:
            log_error("MAIN", f"关闭系统时出错: {e}", indent=1)
    sys.exit(0)


def main():
    """主函数"""
    global dialogue_manager
    
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
    init_logger(
        log_file_enabled=log_file_enabled,
        log_dir=args.log_dir
    )
    
    log_info("MAIN", "=" * 60, indent=0)
    log_info("MAIN", "语音对话系统启动", indent=0)
    log_info("MAIN", "=" * 60, indent=0)
    
    try:
        # 加载配置
        log_info("MAIN", "加载配置...", indent=0)
        config = Config(config_file=args.config)
        if args.config:
            log_info("MAIN", f"配置文件: {args.config}", indent=1)
        else:
            log_info("MAIN", "使用默认配置", indent=1)
        
        # 更新 Logger 的过滤配置（从配置读取）
        update_logger_filter(config)
        # 更新 Logger 的类别颜色配置（从配置读取）
        update_logger_category_colors(config)
        
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
        log_info("MAIN", "程序退出", indent=0)


if __name__ == "__main__":
    main()

