#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网页端启动脚本

启动 WebSocket 服务器，提供网页端实时语音交互界面
"""

import sys
import os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    # 导入并运行 web_server
    from web_server import app
    import uvicorn
    
    # 设置 Windows 事件循环策略
    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    print("=" * 60)
    print("Lincoco 网页端实时语音对话系统")
    print("=" * 60)
    print("访问 http://localhost:8000 使用网页端界面")
    print("按 Ctrl+C 退出")
    print("=" * 60)
    
    uvicorn.run(
        "web_server:app",
        host="0.0.0.0",
        port=8000,
        log_config=None
    )
