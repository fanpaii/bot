import asyncio
import json
import os
import tomllib
import traceback
from typing import Dict, List, Optional, Union, Any
import uuid
import time
import threading

import aiohttp
from fastapi import FastAPI, Request, Response, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from loguru import logger

from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase


class OpenAIAPI(PluginBase):
    description = "OpenAI API兼容插件"
    author = "XYBot团队"
    version = "1.0.0"
    is_ai_platform = True  # 标记为 AI 平台插件

    def __init__(self):
        super().__init__()

        try:
            # 读取主配置
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            
            # 读取插件配置
            config_path = os.path.join(os.path.dirname(__file__), "config.toml")
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                
            # 获取OpenAIAPI配置
            plugin_config = config.get("OpenAIAPI", {})
            self.enable = plugin_config.get("enable", False)
            self.api_key = plugin_config.get("api-key", "")
            self.base_url = plugin_config.get("base-url", "https://api.openai.com/v1")
            
            # 获取模型配置
            self.default_model = plugin_config.get("default-model", "gpt-3.5-turbo")
            self.available_models = plugin_config.get("available-models", ["gpt-3.5-turbo"])
            
            # 获取服务器配置
            self.port = plugin_config.get("port", 8100)
            self.host = plugin_config.get("host", "0.0.0.0")
            
            # 获取命令配置
            self.command_tip = plugin_config.get("command-tip", "")
            
            # 获取功能配置
            self.http_proxy = plugin_config.get("http-proxy", "")
            
            # 获取积分配置
            self.price = plugin_config.get("price", 0)
            self.admin_ignore = plugin_config.get("admin_ignore", True)
            self.whitelist_ignore = plugin_config.get("whitelist_ignore", True)
            
            # 获取高级设置
            self.max_tokens = plugin_config.get("max_tokens", 4096)
            self.temperature = plugin_config.get("temperature", 0.7)
            self.top_p = plugin_config.get("top_p", 1.0)
            self.frequency_penalty = plugin_config.get("frequency_penalty", 0.0)
            self.presence_penalty = plugin_config.get("presence_penalty", 0.0)
            
            # 初始化数据库
            self.db = XYBotDB()
            
            # 从主配置加载管理员
            self.admins = main_config.get("XYBot", {}).get("admins", [])
            
            # 初始化FastAPI应用
            self.app = FastAPI(title="OpenAI API兼容服务", description="提供OpenAI API兼容的接口")
            
            # 添加CORS中间件
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            
            # 初始化服务器
            self.server = None
            self.server_thread = None
            
            # 设置API路由
            self._setup_routes()
            
            logger.success("OpenAIAPI插件初始化成功")
            
        except Exception as e:
            logger.error(f"OpenAIAPI插件初始化失败: {str(e)}")
            logger.error(traceback.format_exc())
            self.enable = False
    
    def _setup_routes(self):
        """设置API路由"""
        
        @self.app.get("/v1/models")
        async def list_models():
            """列出可用的模型"""
            models = []
            for model_id in self.available_models:
                models.append({
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "organization-owner"
                })
            
            return {
                "object": "list",
                "data": models
            }
        
        @self.app.post("/v1/chat/completions")
        async def create_chat_completion(request: Request):
            """创建聊天完成"""
            try:
                # 获取请求体
                body = await request.json()
                
                # 获取请求头中的API密钥
                api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
                
                # 构建转发请求
                headers = {
                    "Content-Type": "application/json"
                }
                
                # 如果配置了API密钥，使用配置的API密钥
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                # 否则使用请求中的API密钥
                elif api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                # 设置代理
                proxy = self.http_proxy if self.http_proxy else None
                
                # 应用默认参数（如果请求中没有指定）
                if "model" not in body:
                    body["model"] = self.default_model
                
                if "max_tokens" not in body and self.max_tokens > 0:
                    body["max_tokens"] = self.max_tokens
                
                if "temperature" not in body:
                    body["temperature"] = self.temperature
                
                if "top_p" not in body:
                    body["top_p"] = self.top_p
                
                if "frequency_penalty" not in body:
                    body["frequency_penalty"] = self.frequency_penalty
                
                if "presence_penalty" not in body:
                    body["presence_penalty"] = self.presence_penalty
                
                # 转发请求到后端API
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=body,
                        proxy=proxy
                    ) as response:
                        # 获取响应
                        response_json = await response.json()
                        
                        # 返回响应
                        return Response(
                            content=json.dumps(response_json),
                            media_type="application/json",
                            status_code=response.status
                        )
            
            except Exception as e:
                logger.error(f"处理聊天完成请求失败: {str(e)}")
                logger.error(traceback.format_exc())
                
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": {
                            "message": f"处理请求失败: {str(e)}",
                            "type": "server_error",
                            "code": "internal_server_error"
                        }
                    }
                )
        
        @self.app.get("/")
        async def root():
            """API根路径"""
            return {
                "message": "OpenAI API兼容服务已启动",
                "version": self.version,
                "models": self.available_models,
                "documentation": "/docs"
            }
        
        @self.app.get("/docs")
        async def get_docs():
            """API文档"""
            return {
                "message": "访问 /docs 查看API文档",
                "swagger_ui": "/docs",
                "redoc": "/redoc"
            }
    
    async def _start_server(self):
        """启动API服务器"""
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info"
        )
        self.server = uvicorn.Server(config)
        await self.server.serve()
    
    def _run_server(self):
        """在线程中运行服务器"""
        asyncio.run(self._start_server())
    
    async def on_enable(self, bot=None):
        """插件启用时调用"""
        await super().on_enable(bot)
        
        if not self.enable:
            logger.warning("OpenAIAPI插件已禁用，不启动API服务器")
            return
        
        # 启动API服务器
        try:
            # 在新线程中启动服务器
            self.server_thread = threading.Thread(target=self._run_server, daemon=True)
            self.server_thread.start()
            
            logger.success(f"OpenAIAPI服务器已启动，监听地址: {self.host}:{self.port}")
            
            # 发送提示消息
            if bot and self.command_tip:
                # 向管理员发送提示
                for admin in self.admins:
                    try:
                        await bot.send_text_message(admin, self.command_tip)
                    except Exception as e:
                        logger.error(f"向管理员 {admin} 发送提示消息失败: {str(e)}")
        
        except Exception as e:
            logger.error(f"启动OpenAIAPI服务器失败: {str(e)}")
            logger.error(traceback.format_exc())
    
    async def on_disable(self):
        """插件禁用时调用"""
        # 停止API服务器
        if self.server:
            self.server.should_exit = True
            logger.info("OpenAIAPI服务器正在关闭...")
        
        await super().on_disable()
