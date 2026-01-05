#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web 服务器模块

提供 FastAPI 服务器，支持 WebSocket 连接和 HTTPS 访问
"""

import os
import ssl
import socket
import argparse
import logging
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from starlette.staticfiles import StaticFiles

from voice_chatbot.utils import log_info, log_warn, log_error


class NoCacheStaticFiles(StaticFiles):
    """
    无缓存的静态文件服务
    
    禁用客户端缓存，用于开发环境
    """
    async def get_response(self, path: str, scope: dict):
        response = await super().get_response(path, scope)
        if response:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            if "etag" in response.headers:
                del response.headers["etag"]
            if "last-modified" in response.headers:
                del response.headers["last-modified"]
        return response


class WebServer:
    """
    Web 服务器类
    
    负责：
    - FastAPI 应用初始化
    - WebSocket 端点管理
    - 静态文件服务
    - HTTPS 证书生成
    """
    
    def __init__(self, dialogue_manager, config):
        """
        初始化 Web 服务器
        
        Args:
            dialogue_manager: DialogueManager 实例
            config: 配置对象
        """
        self.dialogue_manager = dialogue_manager
        self.config = config
        self.app = FastAPI()
        self._setup_app()
        
    def _setup_app(self):
        """设置 FastAPI 应用"""
        # 添加 CORS 中间件
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # 设置路由
        self._setup_routes()
        
    def _setup_routes(self):
        """设置路由"""
        # 获取静态文件目录
        static_dir = Path(__file__).parent / "static"
        
        # 挂载静态文件（无缓存）
        if static_dir.exists():
            self.app.mount("/static", NoCacheStaticFiles(directory=str(static_dir)), name="static")
        else:
            log_warn("WEBSERVER", f"静态文件目录不存在: {static_dir}")
        
        # 主页路由
        @self.app.get("/")
        async def get_index() -> HTMLResponse:
            index_path = static_dir / "index.html"
            if index_path.exists():
                with open(index_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
                return HTMLResponse(content=html_content)
            else:
                return HTMLResponse(content="<h1>Web 界面未找到，请确保 static/index.html 存在</h1>", status_code=404)
        
        # WebSocket 端点
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self._handle_websocket(websocket)
    
    async def _handle_websocket(self, websocket: WebSocket):
        """
        处理 WebSocket 连接
        
        Args:
            websocket: WebSocket 连接对象
        """
        from .adapter import WebAdapter
        
        adapter = None
        try:
            await websocket.accept()
            log_info("WEBSERVER", "WebSocket 连接已建立", indent=0)
            
            # 创建适配器
            adapter = WebAdapter(self.dialogue_manager, websocket)
            
            # 处理连接
            await adapter.handle_websocket()
            
        except WebSocketDisconnect as e:
            log_warn("WEBSERVER", f"WebSocket 连接断开: code={e.code}, reason={e.reason}", indent=0)
        except Exception as e:
            log_error("WEBSERVER", f"WebSocket 处理错误: {e}", indent=0)
            import traceback
            log_error("WEBSERVER", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
        finally:
            # ⭐ 关键修复：关闭连接时取消事件订阅，避免重复处理
            if adapter is not None:
                adapter._unsubscribe_from_events()
                log_info("WEBSERVER", "WebSocket 连接已关闭，事件订阅已取消", indent=0)
            else:
                log_info("WEBSERVER", "WebSocket 连接已关闭", indent=0)
    
    def generate_self_signed_cert(self, cert_dir: str = "certs"):
        """
        生成自签名 SSL 证书
        
        Args:
            cert_dir: 证书保存目录
            
        Returns:
            tuple: (cert_path, key_path)
        """
        cert_dir = Path(cert_dir)
        cert_dir.mkdir(exist_ok=True)
        
        cert_path = cert_dir / "localhost.pem"
        key_path = cert_dir / "localhost-key.pem"
        
        # 检查证书是否已存在
        if cert_path.exists() and key_path.exists():
            log_info("WEBSERVER", f"使用现有证书: {cert_path}", indent=0)
            return str(cert_path), str(key_path)
        
        # 生成新证书
        try:
            log_info("WEBSERVER", f"生成自签名证书到: {cert_dir}", indent=0)
            
            # 使用 ssl 模块生成自签名证书
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            import datetime
            
            # 生成私钥
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            
            # 生成证书
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COUNTRY_NAME, u"CN"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Beijing"),
                x509.NameAttribute(NameOID.LOCALITY_NAME, u"Beijing"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"Lincoco"),
                x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
            ])
            
            cert = x509.CertificateBuilder().subject_name(
                subject
            ).issuer_name(
                issuer
            ).public_key(
                private_key.public_key()
            ).serial_number(
                x509.random_serial_number()
            ).not_valid_before(
                datetime.datetime.utcnow()
            ).not_valid_after(
                datetime.datetime.utcnow() + datetime.timedelta(days=365)
            ).add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName(u"localhost"),
                    x509.DNSName(u"127.0.0.1"),
                ]),
                critical=False,
            ).sign(private_key, hashes.SHA256(), default_backend())
            
            # 保存证书和私钥
            with open(cert_path, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
            
            with open(key_path, "wb") as f:
                f.write(private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            
            log_info("WEBSERVER", f"证书生成成功: {cert_path}", indent=1)
            log_info("WEBSERVER", f"私钥生成成功: {key_path}", indent=1)
            
            return str(cert_path), str(key_path)
            
        except ImportError:
            log_warn("WEBSERVER", "cryptography 模块未安装，使用 openssl 命令生成证书", indent=0)
            # 回退到使用 openssl 命令
            import subprocess
            result = subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-nodes", "-keyout", str(key_path), "-out", str(cert_path),
                "-days", "365", "-subj", "/C=CN/ST=Beijing/L=Beijing/O=Lincoco/CN=localhost"
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                log_info("WEBSERVER", f"证书生成成功: {cert_path}", indent=1)
                return str(cert_path), str(key_path)
            else:
                log_error("WEBSERVER", f"证书生成失败: {result.stderr}", indent=0)
                raise RuntimeError("证书生成失败")
        except Exception as e:
            log_error("WEBSERVER", f"证书生成失败: {e}", indent=0)
            raise
    
    def start(self, host: str = "0.0.0.0", port: int = 8443, use_https: bool = True):
        """
        启动 Web 服务器
        
        Args:
            host: 监听地址
            port: 监听端口
            use_https: 是否使用 HTTPS
        """
        log_info("WEBSERVER", f"启动 Web 服务器: {host}:{port}", indent=0)
        
        # 检查端口是否被占用
        if self._is_port_in_use(port, host):
            log_error("WEBSERVER", f"端口 {port} 已被占用", indent=0)
            raise RuntimeError(f"端口 {port} 已被占用")
        
        # HTTPS 配置
        ssl_context = None
        if use_https:
            log_info("WEBSERVER", "启用 HTTPS", indent=1)
            cert_path, key_path = self.generate_self_signed_cert()
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_context.load_cert_chain(cert_path, key_path)
        else:
            log_warn("WEBSERVER", "使用 HTTP（不安全，仅用于测试）", indent=1)
        
        # 启动服务器
        try:
            if use_https:
                url = f"https://{host}:{port}"
            else:
                url = f"http://{host}:{port}"
            
            log_info("WEBSERVER", f"访问地址: {url}", indent=0)
            log_info("WEBSERVER", f"按 Ctrl+C 停止服务器", indent=0)
            
            uvicorn.run(
                self.app,
                host=host,
                port=port,
                ssl_certfile=cert_path if use_https else None,
                ssl_keyfile=key_path if use_https else None,
                log_level="info"
            )
            
        except KeyboardInterrupt:
            log_info("WEBSERVER", "服务器已停止", indent=0)
        except Exception as e:
            log_error("WEBSERVER", f"服务器启动失败: {e}", indent=0)
            import traceback
            log_error("WEBSERVER", f"异常堆栈:\n{traceback.format_exc()}", indent=1)
            raise
    
    def _is_port_in_use(self, port: int, host: str = "127.0.0.1") -> bool:
        """
        检查端口是否被占用
        
        Args:
            port: 端口号
            host: 主机地址
            
        Returns:
            bool: 如果端口被占用返回 True
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex((host, port))
                return result == 0
        except Exception:
            return False

