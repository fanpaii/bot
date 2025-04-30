from loguru import logger
import aiohttp
import json
import os
import re
import urllib.parse
import time
import subprocess

# 尝试导入TOML相关库
try:
    import tomllib  # Python 3.11+
    TOMLLIB_AVAILABLE = True
except ImportError:
    try:
        import tomli as tomllib  # Python 3.10及以下
        TOMLLIB_AVAILABLE = True
    except ImportError:
        TOMLLIB_AVAILABLE = False
        logger.error("未找到TOML库，请安装tomllib或tomli")
        raise ImportError("缺少TOML库，请使用pip安装tomli")

# 尝试导入tomli_w
try:
    import tomli_w
except ImportError:
    logger.error("未找到tomli_w库，请安装tomli_w")
    raise ImportError("缺少tomli_w库，请使用pip安装tomli_w")

from typing import Dict, Any, List
import asyncio
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import random
import datetime

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class APIInterface(PluginBase):
    description = "API接口插件，支持通过命令调用各种API接口"
    author = "Claude"
    version = "1.0.1"

    def __init__(self):
        super().__init__()
        
        # 配置文件路径
        self.config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        self.api_config_path = os.path.join(os.path.dirname(__file__), "api_config.toml")
        self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
        
        # 默认启用
        self.enable = True
        
        # API接口配置
        self.api_configs = {}
        
        # 命令映射
        self.commands = []
        
        # 星座列表
        self.constellations = ["白羊", "金牛", "双子", "巨蟹", "狮子", "处女", "天秤", "天蝎", "射手", "摩羯", "水瓶", "双鱼"]
        
        # 加载配置
        self._load_config()
        self._load_api_config()
        self._load_command_map()
        
        # 加载白名单配置
        self.whitelist = []
        self.ignore_mode = ""
        self._load_whitelist()
        
    def _load_config(self):
        """加载插件配置"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "rb") as f:
                    config = tomllib.load(f)
                    
                # 读取基本配置
                basic_config = config.get("basic", {})
                self.enable = basic_config.get("enable", True)
            else:
                # 创建默认配置
                with open(self.config_path, "wb") as f:
                    tomli_w.dump({"basic": {"enable": True}}, f)
        except Exception as e:
            logger.error(f"加载APIInterface配置文件失败: {str(e)}")
    
    def _load_api_config(self):
        """加载API接口配置"""
        try:
            # 加载TOML格式配置
            if os.path.exists(self.api_config_path):
                with open(self.api_config_path, "rb") as f:
                    config = tomllib.load(f)
                    self.api_configs = config.get("api", {})
            else:
                # 创建默认API配置
                self._create_default_config()
        except Exception as e:
            logger.error(f"加载API配置文件失败: {str(e)}")
            # 使用默认配置
            self._create_default_config()
    
    def _load_command_map(self):
        """加载命令映射"""
        try:
            if os.path.exists(self.command_map_path):
                with open(self.command_map_path, "rb") as f:
                    config = tomllib.load(f)
                    self.commands = config.get("commands", [])
                logger.info(f"已加载 {len(self.commands)} 条命令映射")
            else:
                # 创建默认命令映射
                self._create_default_command_map()
        except Exception as e:
            logger.error(f"加载命令映射失败: {str(e)}")
            # 使用空列表
            self.commands = []
    
    def _create_default_command_map(self):
        """创建默认命令映射"""
        default_commands = [
            {
                "name": "18+",
                "description": "获取R18图片",
                "usage": "18+",
                "hidden": False,
                "prefix_required": False
            },
            {
                "name": "测试图片",
                "description": "发送一张测试图片，用于验证图片发送功能",
                "usage": "测试图片",
                "hidden": False,
                "prefix_required": False
            },
            {
                "name": "添加API",
                "description": "添加新的API接口",
                "usage": "添加API 命令 URL 请求方法 返回类型 描述",
                "hidden": False,
                "admin_only": True,
                "prefix_required": False
            },
            {
                "name": "删除API",
                "description": "删除API接口",
                "usage": "删除API 命令",
                "hidden": False,
                "admin_only": True,
                "prefix_required": False
            },
            {
                "name": "运势占卜",
                "description": "随机获取运势占卜图片",
                "usage": "运势占卜",
                "hidden": False,
                "prefix_required": False
            },
            {
                "name": "运势",
                "description": "随机获取运势占卜图片",
                "usage": "运势",
                "hidden": False,
                "prefix_required": False
            }
        ]
        
        # 为每个星座添加命令
        for constellation in self.constellations:
            default_commands.append({
                "name": constellation,
                "description": f"获取{constellation}座运势",
                "usage": constellation,
                "hidden": False,
                "prefix_required": False
            })
        
        # 添加短剧和小说相关命令
        default_commands.extend([
            {
                "name": "短剧",
                "description": "搜索短剧",
                "usage": "短剧<关键词>",
                "hidden": False,
                "prefix_required": False
            },
            {
                "name": "显示剩余",
                "description": "显示剩余短剧搜索结果",
                "usage": "显示剩余",
                "hidden": True,
                "prefix_required": False
            },
            {
                "name": "小说",
                "description": "搜索小说",
                "usage": "小说<关键词>",
                "hidden": False,
                "prefix_required": False
            }
        ])
        
        self.commands = default_commands
        
        try:
            with open(self.command_map_path, "wb") as f:
                tomli_w.dump({"commands": default_commands}, f)
            logger.success("已创建默认命令映射")
        except Exception as e:
            logger.error(f"创建默认命令映射失败: {str(e)}")
    
    def _create_default_config(self):
        """创建默认API配置"""
        self.api_configs = {
            "18+": {
                "url": "https://laterouapi.tonghang.fun/api/R18_2",
                "method": "get",
                "return_type": "img",
                "description": "获取R18图片"
            },
            "小说": {
                "url": "https://www.hhlqilongzhu.cn/api/novel_1.php",
                "method": "get",
                "return_type": "json",
                "description": "搜索小说信息，可根据关键词或书名查询"
            },
            "短剧": {
                "url": "https://v2.xxapi.cn/api/duanjusearch",
                "method": "get",
                "return_type": "json",
                "headers": {"User-Agent": "xiaoxiaoapi/1.0.0 (https://xxapi.cn)"},
                "description": "搜索短剧信息，可根据关键词查询"
            },
            "运势占卜": {
                "url": "https://www.hhlqilongzhu.cn/api/tu_yunshi.php",
                "method": "get",
                "return_type": "img",
                "description": "随机获取运势占卜图片"
            }
        }
        self._save_api_config()
    
    def _save_api_config(self):
        """保存API接口配置"""
        try:
            os.makedirs(os.path.dirname(self.api_config_path), exist_ok=True)
            
            # 构建配置
            config = {"api": self.api_configs}
            
            # 保存为TOML格式
            with open(self.api_config_path, "wb") as f:
                tomli_w.dump(config, f)
            logger.info("API配置已保存到TOML文件")
        except Exception as e:
            logger.error(f"保存API配置文件失败: {str(e)}")
    
    def _save_command_map(self):
        """保存命令映射"""
        try:
            os.makedirs(os.path.dirname(self.command_map_path), exist_ok=True)
            
            # 构建配置
            config = {"commands": self.commands}
            
            # 保存为TOML格式
            with open(self.command_map_path, "wb") as f:
                tomli_w.dump(config, f)
            logger.info("命令映射已保存到TOML文件")
        except Exception as e:
            logger.error(f"保存命令映射失败: {str(e)}")
    
    async def async_init(self):
        """异步初始化"""
        pass
    
    def _get_command_config(self, command_name: str) -> dict:
        """获取命令配置
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令配置字典，如果命令不存在则返回空字典
        """
        for command in self.commands:
            if command.get("name") == command_name:
                return command
        return {}
    
    def _is_command_admin_only(self, command_name: str) -> bool:
        """检查命令是否仅限管理员使用
        
        Args:
            command_name: 命令名称
            
        Returns:
            是否仅限管理员使用
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("admin_only", False)
    
    def _is_command_hidden(self, command_name: str) -> bool:
        """检查命令是否隐藏
        
        Args:
            command_name: 命令名称
            
        Returns:
            是否隐藏
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("hidden", False)
    
    def _get_command_usage(self, command_name: str) -> str:
        """获取命令使用说明
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令使用说明
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("usage", command_name)
    
    def _get_command_description(self, command_name: str) -> str:
        """获取命令描述
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令描述
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("description", "")
    
    def _load_whitelist(self):
        """从main_config.toml加载白名单配置"""
        try:
            # 读取主配置文件
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            
            # 获取白名单
            xybot_config = main_config.get("XYBot", {})
            self.whitelist = xybot_config.get("whitelist", [])
            self.ignore_mode = xybot_config.get("ignore-mode", "")
            
            logger.info(f"已加载白名单({len(self.whitelist)}个)，当前模式: {self.ignore_mode}")
        except Exception as e:
            logger.error(f"加载白名单配置失败: {str(e)}")
            self.whitelist = []
            
    def _is_in_whitelist(self, wxid: str) -> bool:
        """检查wxid是否在白名单中
        
        Args:
            wxid: 用户或群聊wxid
            
        Returns:
            是否在白名单中
        """
        # 如果ignore-mode不是Whitelist，视为白名单不生效
        if self.ignore_mode != "Whitelist":
            return True
            
        # 检查是否在白名单中
        for item in self.whitelist:
            if item == wxid:
                return True
                
        return False

    @on_text_message(priority=50)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        if not self.enable:
            return True  # 修改：即使未启用也允许其他插件处理
        
        # 获取消息内容和发送者
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        is_group = message.get("IsGroup", False)
        
        # 白名单检查
        if not self._is_in_whitelist(from_wxid):
            # 对于非白名单群聊/用户，直接忽略，允许其他插件处理
            logger.info(f"忽略非白名单的消息: {from_wxid}")
            return True
        
        # 处理格式为"wxid_xxx: 命令"的情况，提取真正的命令内容
        if ":" in content:
            parts = content.split(":", 1)
            if len(parts) == 2 and parts[0].strip().startswith("wxid_"):
                content = parts[1].strip()
                logger.info(f"提取到实际命令内容: {content}")
        
        # 测试图片发送功能
        if content == "测试图片":
            await self._send_test_image(bot, from_wxid)
            return True  # 修改：允许其他插件处理
        
        # 直接处理星座运势请求，不需要前缀
        constellation_match = None
        for constellation in self.constellations:
            if content == constellation:
                constellation_match = constellation
                break
                
        if constellation_match:
            await self._handle_constellation(bot, message, constellation_match)
            return True  # 修改：允许其他插件处理
        
        # 处理运势占卜命令
        if content == "运势占卜" or content == "运势":
            logger.info(f"收到运势占卜请求")
            api_config = self.api_configs.get("运势占卜")
            if api_config:
                await self._call_api(bot, from_wxid, "运势占卜", api_config)
            else:
                logger.error("运势占卜接口未配置")
                await bot.send_text_message(from_wxid, "运势占卜功能暂不可用，请联系管理员")
            return True
            
        # 直接处理短剧搜索请求，不需要前缀
        if content.startswith("短剧"):
            if content == "短剧":
                await bot.send_text_message(from_wxid, "请指定搜索关键词，例如：短剧总裁")
                return True  # 修改：允许其他插件处理
                
            params = content[2:].strip()
            if params:
                await self._handle_drama(bot, message, params)
                return True  # 修改：允许其他插件处理
                
        # 显示剩余短剧结果
        if content == "显示剩余" or content == "短剧显示剩余":
            if hasattr(self, '_drama_cache') and self._drama_cache:
                await self._handle_drama(bot, message, "显示剩余")
            else:
                await bot.send_text_message(from_wxid, "没有可显示的剩余结果，请先进行搜索")
            return True  # 修改：允许其他插件处理
            
        # 新增：处理小说搜索请求
        if content.startswith("小说"):
            if content == "小说":
                await bot.send_text_message(from_wxid, "请指定搜索关键词，例如：小说总裁")
                return True
                
            params = content[2:].strip()
            if params:
                await self._handle_novel(bot, message, params)
                return True
                
        # 新增：处理小说序号选择
        if content.isdigit() and hasattr(self, '_novel_cache') and self._novel_cache:
            await self._handle_novel_selection(bot, message, int(content))
            return True
        
        # 新增：处理短剧序号选择
        if content.isdigit() and hasattr(self, '_drama_cache') and self._drama_cache:
            await self._handle_drama_selection(bot, message, int(content))
            return True
        
        # 处理管理命令，无需@机器人
        if content.startswith("添加API "):
            await self._add_api(bot, message)
            return True  # 修改：允许其他插件处理
        elif content.startswith("删除API "):
            await self._remove_api(bot, message)
            return True  # 修改：允许其他插件处理
        elif content.startswith("API列表"):
            await self._list_api(bot, message)
            return True  # 修改：允许其他插件处理
                
        # 检查是否是API调用指令
        for cmd, api_config in self.api_configs.items():
            if content == cmd:
                logger.info(f"收到API调用指令: {cmd}")
                await self._call_api(bot, from_wxid, cmd, api_config)
                return True  # 修改：允许其他插件处理
                
        return True  # 修改：无论是否匹配，都允许其他插件处理

    async def _get_user_info(self, message: dict) -> tuple:
        """获取用户信息"""
        user_id = message.get("SenderId") or message.get("FromWxid", "")
        # 尝试获取用户昵称
        user_name = message.get("SenderNickname") or message.get("FromName", "未知用户")
        
        return user_id, user_name
        
    def is_admin(self, user_id: str) -> bool:
        """检查用户是否是管理员"""
        # 这里可以根据项目的管理员判断逻辑进行调整
        # 简单起见，先使用一个简单的判断
        try:
            from utils.admin import is_admin
            return is_admin(user_id)
        except ImportError:
            # 如果没有专门的管理员判断函数，使用简单的判断方式
            # 这里是假设的管理员ID列表，实际项目中应该从配置或数据库获取
            admin_ids = ["wxid_abcdefg", "wxid_12345678"]
            return user_id in admin_ids
            
    async def _send_test_image(self, bot: WechatAPIClient, to_wxid: str, message: str = None):
        """发送测试图片，验证图片发送功能是否正常"""
        try:
            # 创建临时文件目录
            temp_dir = os.path.join(os.path.dirname(__file__), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            
            # 生成测试图片
            img_size = (400, 200)
            bg_color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            
            img = Image.new('RGB', img_size, color=bg_color)
            draw = ImageDraw.Draw(img)
            
            # 添加文本
            text = message or f"测试图片 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            text_color = (255 - bg_color[0], 255 - bg_color[1], 255 - bg_color[2])
            
            # 尝试加载字体，如果失败则使用默认字体
            try:
                font = ImageFont.truetype("arial.ttf", 20)
                draw.text((20, 80), text, fill=text_color, font=font)
            except:
                draw.text((20, 80), text, fill=text_color)
            
            # 保存图片
            test_img_path = os.path.join(temp_dir, "test_image.jpg")
            img.save(test_img_path)
            
            # 发送图片
            try:
                logger.info(f"发送测试图片: {test_img_path}")
                result = await bot.send_image_message(to_wxid, test_img_path)
                client_img_id, create_time, new_msg_id = result
                logger.info(f"测试图片发送结果: ClientImgId: {client_img_id}, MsgId: {new_msg_id}")
                
                # 等待一段时间，检查图片是否真正发送成功
                await asyncio.sleep(2)  # 等待2秒
                
                if not message:  # 只有在直接测试时才发送成功消息
                    await bot.send_text_message(to_wxid, f"✅ 测试图片发送成功\nClientImgId: {client_img_id}\nMsgId: {new_msg_id}")
            except Exception as e:
                logger.error(f"测试图片发送失败: {e}")
                
                if not message:  # 只有在直接测试时才发送失败消息
                    await bot.send_text_message(to_wxid, f"❌ 测试图片发送失败: {str(e)}")
                
                # 尝试使用字节方式发送
                try:
                    with open(test_img_path, "rb") as f:
                        img_bytes = f.read()
                    retry_result = await bot.send_image_message(to_wxid, img_bytes)
                    logger.info(f"使用字节数据重试发送测试图片: {retry_result}")
                    
                    # 等待一段时间，检查重试是否成功
                    await asyncio.sleep(2)  # 等待2秒
                    
                    if not message:  # 只有在直接测试时才发送成功消息
                        await bot.send_text_message(to_wxid, f"✅ 测试图片(字节方式)发送成功: {retry_result}")
                except Exception as retry_e:
                    logger.error(f"重试发送测试图片失败: {retry_e}")
                    
                    if not message:  # 只有在直接测试时才发送失败消息
                        await bot.send_text_message(to_wxid, f"❌ 重试测试图片发送也失败: {str(retry_e)}")
        except Exception as e:
            logger.error(f"生成测试图片失败: {e}")
            if not message:  # 只有在直接测试时才发送失败消息
                await bot.send_text_message(to_wxid, f"❌ 生成测试图片失败: {str(e)}")

    async def _call_api(self, bot: WechatAPIClient, to_wxid: str, cmd: str, api_config: Dict[str, Any]):
        """调用API接口"""
        url = api_config.get("url")
        method = api_config.get("method", "GET")
        params = api_config.get("params", {})
        headers = api_config.get("headers", {})
        return_type = api_config.get("return_type", "text")
        send_type = api_config.get("send_type", "bytes")  # 发送方式，可选base64或bytes
        
        if not url:
            await bot.send_text_message(to_wxid, f"⚠️ API接口配置错误: 缺少URL")
            return

        try:
            # 创建临时目录，用于存储视频文件
            temp_dir = "plugins/APIInterface/temp"
            os.makedirs(temp_dir, exist_ok=True)
            
            # 设置超时时间
            timeout = aiohttp.ClientTimeout(total=30)  # 总超时30秒
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                logger.info(f"调用API: {url}, 方法: {method}, 参数: {params}")
                
                if method.upper() == "GET":
                    async with session.get(url, params=params, headers=headers) as response:
                        if response.status != 200:
                            logger.error(f"API返回错误，状态码: {response.status}")
                            await bot.send_text_message(to_wxid, f"⚠️ API返回错误，状态码: {response.status}")
                            return
                        
                        # 根据返回类型处理响应
                        if return_type == "image":
                            # 读取图片数据
                            img_data = await response.read()
                            
                            # 验证图片数据有效性
                            if len(img_data) < 100:  # 一个有效图片通常至少有几百字节
                                logger.warning(f"API返回的图片数据可能无效，大小仅为 {len(img_data)} 字节")
                                logger.info(f"API返回数据前100字节: {img_data[:100]}")
                                await bot.send_text_message(to_wxid, f"⚠️ API返回的图片数据无效")
                                return
                            
                            # 根据配置的发送方式处理图片数据
                            try:
                                if send_type == "base64":
                                    img_base64 = base64.b64encode(img_data).decode('utf-8')
                                    result = await bot.send_image_message(to_wxid, img_base64)
                                else:  # 默认使用字节方式
                                    result = await bot.send_image_message(to_wxid, img_data)
                                    
                                client_img_id, create_time, new_msg_id = result
                                logger.info(f"已发送图片，ClientImgId: {client_img_id}, MsgId: {new_msg_id}")
                            except Exception as img_e:
                                logger.error(f"发送图片时发生错误: {img_e}")
                                await bot.send_text_message(to_wxid, f"⚠️ 发送图片失败: {str(img_e)}")
                        elif return_type == "video":
                            # 读取视频数据
                            video_data = await response.read()
                            
                            # 验证视频数据有效性
                            if len(video_data) < 100:  # 一个有效视频通常至少有几百字节
                                logger.warning(f"API返回的视频数据可能无效，大小仅为 {len(video_data)} 字节")
                                logger.info(f"API返回数据前100字节: {video_data[:100]}")
                                await bot.send_text_message(to_wxid, f"⚠️ API返回的视频数据无效")
                                return
                            
                            # 保存视频到临时文件
                            video_path = os.path.join(temp_dir, f"video_{int(time.time())}_{random.randint(1000, 9999)}.mp4")
                            try:
                                with open(video_path, "wb") as f:
                                    f.write(video_data)
                                logger.info(f"视频已保存到临时文件: {video_path}")
                                
                                # 编码视频数据为base64
                                with open(video_path, "rb") as f:
                                    video_base64 = base64.b64encode(f.read()).decode('utf-8')
                                
                                # 提取视频首帧作为封面
                                cover_base64 = None
                                try:
                                    # 使用ffmpeg提取首帧
                                    thumbnail_path = os.path.join(temp_dir, f"thumb_{int(time.time())}_{random.randint(1000, 9999)}.jpg")
                                    
                                    # 异步执行ffmpeg命令
                                    ffmpeg_cmd = f'ffmpeg -i "{video_path}" -ss 00:00:01 -vframes 1 -f image2 "{thumbnail_path}" -y'
                                    process = await asyncio.create_subprocess_shell(
                                        ffmpeg_cmd,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE
                                    )
                                    stdout, stderr = await process.communicate()
                                    
                                    if process.returncode == 0 and os.path.exists(thumbnail_path):
                                        with open(thumbnail_path, "rb") as f:
                                            cover_data = f.read()
                                            cover_base64 = base64.b64encode(cover_data).decode('utf-8')
                                        logger.info(f"成功提取视频封面")
                                        
                                        # 删除封面临时文件
                                        try:
                                            os.remove(thumbnail_path)
                                        except Exception as e:
                                            logger.warning(f"删除封面临时文件失败: {e}")
                                    else:
                                        logger.warning(f"提取视频封面失败: {stderr.decode()}")
                                except Exception as e:
                                    logger.warning(f"提取视频封面时出错: {e}")
                                
                                # 发送视频
                                try:
                                    # 使用VideoDemand的参数格式
                                    client_msg_id, new_msg_id = await bot.send_video_message(
                                        to_wxid,
                                        video=video_base64,
                                        image=cover_base64 or "None"  # 使用字符串"None"作为默认值
                                    )
                                    logger.info(f"视频发送成功: client_msg_id={client_msg_id}, new_msg_id={new_msg_id}")
                                    
                                    # 发送成功后等待60秒再删除文件，确保视频已经完全发送
                                    if client_msg_id:
                                        logger.info(f"视频已成功发送，将在60秒后清理文件")
                                        await asyncio.sleep(60)
                                        try:
                                            if os.path.exists(video_path):
                                                os.remove(video_path)
                                                logger.debug(f"清理视频文件成功: {video_path}")
                                        except Exception as e:
                                            logger.error(f"清理已发送的视频文件失败: {video_path}", exc_info=e)
                                    else:
                                        # 只有当client_msg_id为空时才认为发送失败
                                        logger.error(f"发送视频失败 - client_msg_id为空")
                                        await bot.send_text_message(to_wxid, "发送视频失败，请稍后重试")
                                except Exception as video_e:
                                    logger.error(f"发送视频时发生错误: {video_e}")
                                    await bot.send_text_message(to_wxid, f"⚠️ 发送视频失败: {str(video_e)}")
                                    # 清理文件
                                    if os.path.exists(video_path):
                                        try:
                                            os.remove(video_path)
                                        except Exception as cleanup_error:
                                            logger.error(f"清理视频文件失败: {cleanup_error}")
                            except Exception as file_e:
                                logger.error(f"处理视频文件时发生错误: {file_e}")
                                await bot.send_text_message(to_wxid, f"⚠️ 处理视频失败: {str(file_e)}")
                                # 清理可能存在的临时文件
                                if os.path.exists(video_path):
                                    try:
                                        os.remove(video_path)
                                    except:
                                        pass
                        elif return_type == "json":
                            # 处理JSON返回
                            try:
                                # 先尝试直接解析JSON
                                json_data = await response.json()
                            except Exception as json_e:
                                # 如果直接解析失败，尝试从HTML中提取JSON
                                text = await response.text()
                                logger.info(f"API返回原始数据: {text[:200]}...")  # 只记录前200个字符
                                try:
                                    # 尝试从文本中提取JSON
                                    import re
                                    json_match = re.search(r'\{.*\}', text, re.DOTALL)
                                    if json_match:
                                        json_str = json_match.group(0)
                                        import json
                                        json_data = json.loads(json_str)
                                    else:
                                        raise ValueError("无法从响应中提取JSON数据")
                                except Exception as extract_e:
                                    logger.error(f"解析JSON失败: {extract_e}")
                                    await bot.send_text_message(to_wxid, f"⚠️ API返回数据格式错误: {str(extract_e)}")
                                    return
                            
                            logger.info(f"API返回JSON数据: {json_data}")
                            
                            # 处理星座运势数据
                            if cmd == "星座" and isinstance(json_data, dict):
                                if json_data.get("code") == 200 and "data" in json_data:
                                    data = json_data["data"]
                                    reply = f"✨ {data.get('title', '星座运势')} ✨\n"
                                    reply += f"日期：{data.get('time', '未知')}\n"
                                    reply += f"综合运势：{data.get('shortcomment', '未知')}\n"
                                    reply += f"幸运数字：{data.get('luckynumber', '未知')}\n"
                                    reply += f"幸运颜色：{data.get('luckycolor', '未知')}\n"
                                    reply += f"幸运星座：{data.get('luckyconstellation', '未知')}\n"
                                    reply += f"健康指数：{data.get('health', '未知')}\n"
                                    reply += f"讨论指数：{data.get('discuss', '未知')}\n\n"
                                    reply += "详细运势：\n"
                                    reply += f"💫 整体运势：{data.get('alltext', '未知')}\n"
                                    reply += f"💕 爱情运势：{data.get('lovetext', '未知')}\n"
                                    reply += f"💼 事业运势：{data.get('worktext', '未知')}\n"
                                    reply += f"💰 财运运势：{data.get('moneytext', '未知')}\n"
                                    reply += f"🏃 健康运势：{data.get('healthtxt', '未知')}\n"
                                    await bot.send_text_message(to_wxid, reply)
                                    return
                            
                            # 处理短剧搜索数据
                            if cmd == "短剧" and isinstance(json_data, dict):
                                if json_data.get("code") == 200 and "data" in json_data:
                                    return json_data  # 返回完整的JSON数据，让_handle_drama处理
                            
                            # 检查JSON数据中是否包含视频URL
                            if isinstance(json_data, dict):
                                # 尝试从不同路径获取视频URL
                                video_url = None
                                if "data" in json_data and isinstance(json_data["data"], dict):
                                    if "videourl" in json_data["data"]:
                                        video_url = json_data["data"]["videourl"]
                                    elif "url" in json_data["data"]:
                                        video_url = json_data["data"]["url"]
                                elif "videourl" in json_data:
                                    video_url = json_data["videourl"]
                                elif "url" in json_data:
                                    video_url = json_data["url"]
                                
                                if video_url:
                                    logger.info(f"从JSON中获取到视频URL: {video_url}")
                                    
                                    # 下载视频
                                    async with session.get(video_url) as media_response:
                                        if media_response.status == 200:
                                            media_data = await media_response.read()
                                            
                                            # 保存视频到临时文件
                                            video_path = os.path.join(temp_dir, f"json_video_{int(time.time())}_{random.randint(1000, 9999)}.mp4")
                                            try:
                                                with open(video_path, "wb") as f:
                                                    f.write(media_data)
                                                logger.info(f"从JSON获取的视频已保存到临时文件: {video_path}")
                                                
                                                # 编码视频数据为base64
                                                with open(video_path, "rb") as f:
                                                    video_base64 = base64.b64encode(f.read()).decode('utf-8')
                                                
                                                # 提取视频首帧作为封面
                                                cover_base64 = None
                                                try:
                                                    # 使用ffmpeg提取首帧
                                                    thumbnail_path = os.path.join(temp_dir, f"json_thumb_{int(time.time())}_{random.randint(1000, 9999)}.jpg")
                                                    
                                                    # 异步执行ffmpeg命令
                                                    ffmpeg_cmd = f'ffmpeg -i "{video_path}" -ss 00:00:01 -vframes 1 -f image2 "{thumbnail_path}" -y'
                                                    process = await asyncio.create_subprocess_shell(
                                                        ffmpeg_cmd,
                                                        stdout=subprocess.PIPE,
                                                        stderr=subprocess.PIPE
                                                    )
                                                    stdout, stderr = await process.communicate()
                                                    
                                                    if process.returncode == 0 and os.path.exists(thumbnail_path):
                                                        with open(thumbnail_path, "rb") as f:
                                                            cover_data = f.read()
                                                            cover_base64 = base64.b64encode(cover_data).decode('utf-8')
                                                        logger.info(f"成功提取视频封面")
                                                        
                                                        # 删除封面临时文件
                                                        try:
                                                            os.remove(thumbnail_path)
                                                        except Exception as e:
                                                            logger.warning(f"删除封面临时文件失败: {e}")
                                                    else:
                                                        logger.warning(f"提取视频封面失败: {stderr.decode()}")
                                                except Exception as e:
                                                    logger.warning(f"提取视频封面时出错: {e}")
                                                
                                                # 发送视频
                                                try:
                                                    # 使用VideoDemand的参数格式
                                                    client_msg_id, new_msg_id = await bot.send_video_message(
                                                        to_wxid,
                                                        video=video_base64,
                                                        image=cover_base64 or "None"  # 使用字符串"None"作为默认值
                                                    )
                                                    logger.info(f"从JSON获取的视频发送成功: client_msg_id={client_msg_id}, new_msg_id={new_msg_id}")
                                                    
                                                    # 发送成功后等待60秒再删除文件，确保视频已经完全发送
                                                    if client_msg_id:
                                                        logger.info(f"视频已成功发送，将在60秒后清理文件")
                                                        await asyncio.sleep(60)
                                                        try:
                                                            if os.path.exists(video_path):
                                                                os.remove(video_path)
                                                                logger.debug(f"清理视频文件成功: {video_path}")
                                                        except Exception as e:
                                                            logger.error(f"清理已发送的视频文件失败: {video_path}", exc_info=e)
                                                    else:
                                                        # 只有当client_msg_id为空时才认为发送失败
                                                        logger.error(f"发送视频失败 - client_msg_id为空")
                                                        await bot.send_text_message(to_wxid, "发送视频失败，请稍后重试")
                                                except Exception as video_e:
                                                    logger.error(f"发送视频时发生错误: {video_e}")
                                                    await bot.send_text_message(to_wxid, f"⚠️ 发送视频失败: {str(video_e)}")
                                                    # 清理文件
                                                    if os.path.exists(video_path):
                                                        try:
                                                            os.remove(video_path)
                                                        except Exception as cleanup_error:
                                                            logger.error(f"清理视频文件失败: {cleanup_error}")
                                            except Exception as file_e:
                                                logger.error(f"处理视频文件时发生错误: {file_e}")
                                                await bot.send_text_message(to_wxid, f"⚠️ 处理视频失败: {str(file_e)}")
                                                # 清理可能存在的临时文件
                                                if os.path.exists(video_path):
                                                    try:
                                                        os.remove(video_path)
                                                    except:
                                                        pass
                                        else:
                                            logger.error(f"下载视频失败，状态码: {media_response.status}")
                                            await bot.send_text_message(to_wxid, f"⚠️ 下载视频失败: {media_response.status}")
                                else:
                                    # 如果不是视频URL，直接返回JSON数据
                                    return json_data
                            else:
                                # 如果不是字典类型，直接返回数据
                                return json_data
                        else:
                            # 处理文本返回
                            text = await response.text()
                            await bot.send_text_message(to_wxid, text)
                            logger.info(f"已发送文本: {text[:100]}...")
                else:
                    logger.error(f"不支持的请求方法: {method}")
                    await bot.send_text_message(to_wxid, f"⚠️ 不支持的请求方法: {method}")
                    return
        except aiohttp.ClientError as http_err:
            logger.error(f"HTTP请求错误: {http_err}")
            await bot.send_text_message(to_wxid, f"⚠️ API请求失败: {str(http_err)}")
        except asyncio.TimeoutError:
            logger.error("API请求超时")
            await bot.send_text_message(to_wxid, f"⚠️ API请求超时，请稍后重试")
        except Exception as e:
            logger.error(f"调用API失败: {str(e)}")
            await bot.send_text_message(to_wxid, f"⚠️ 调用API失败: {str(e)}")

    async def _handle_constellation(self, bot, message, params):
        """处理星座运势请求"""
        if not params:
            await bot.send_text_message(message["FromWxid"], "请直接发送星座名称，例如：白羊")
            return

        # 星座名称映射（已不需要检查，因为在handle_text已经检查过）
        constellation_map = {
            "白羊": "白羊", "金牛": "金牛", "双子": "双子", "巨蟹": "巨蟹",
            "狮子": "狮子", "处女": "处女", "天秤": "天秤", "天蝎": "天蝎",
            "射手": "射手", "摩羯": "摩羯", "水瓶": "水瓶", "双鱼": "双鱼"
        }

        # 获取API配置
        api_config = self.api_configs.get("星座")
        if not api_config:
            await bot.send_text_message(message["FromWxid"], "星座运势接口配置错误")
            return

        # 调用API
        try:
            # 创建一个新的配置副本
            api_config_copy = api_config.copy()
            # 添加参数
            api_config_copy["params"] = {"xz": params}
            
            result = await self._call_api(bot, message["FromWxid"], "星座", api_config_copy)
            if result and isinstance(result, dict):
                if result.get("code") == 200 and "data" in result:
                    data = result["data"]
                    reply = f"✨ {data.get('title', '星座运势')} ✨\n"
                    reply += f"日期：{data.get('time', '未知')}\n"
                    reply += f"综合运势：{data.get('shortcomment', '未知')}\n"
                    reply += f"幸运数字：{data.get('luckynumber', '未知')}\n"
                    reply += f"幸运颜色：{data.get('luckycolor', '未知')}\n"
                    reply += f"幸运星座：{data.get('luckyconstellation', '未知')}\n"
                    reply += f"健康指数：{data.get('health', '未知')}\n"
                    reply += f"讨论指数：{data.get('discuss', '未知')}\n\n"
                    reply += "详细运势：\n"
                    reply += f"💫 整体运势：{data.get('alltext', '未知')}\n"
                    reply += f"💕 爱情运势：{data.get('lovetext', '未知')}\n"
                    reply += f"💼 事业运势：{data.get('worktext', '未知')}\n"
                    reply += f"💰 财运运势：{data.get('moneytext', '未知')}\n"
                    reply += f"🏃 健康运势：{data.get('healthtxt', '未知')}\n"
                    await bot.send_text_message(message["FromWxid"], reply)
                else:
                    await bot.send_text_message(message["FromWxid"], "获取星座运势失败，请稍后重试")
            else:
                await bot.send_text_message(message["FromWxid"], "获取星座运势失败，请稍后重试")
        except Exception as e:
            logger.error(f"获取星座运势失败: {str(e)}")
            await bot.send_text_message(message["FromWxid"], "获取星座运势失败，请稍后重试")

    async def _handle_drama(self, bot, message, params):
        """处理短剧搜索请求"""
        if not params:
            await bot.send_text_message(message["FromWxid"], "请指定搜索关键词，例如：短剧总裁")
            return

        # 检查是否是显示剩余结果的命令
        if params.startswith("显示剩余"):
            # 从缓存中获取上次搜索结果
            if not hasattr(self, '_drama_cache') or not self._drama_cache:
                await bot.send_text_message(message["FromWxid"], "没有可显示的剩余结果，请先进行搜索")
                return
            
            dramas = self._drama_cache
            if len(dramas) <= 5:
                await bot.send_text_message(message["FromWxid"], "没有更多结果了")
                return

            # 构建剩余结果的回复消息
            reply = f"📺 搜索关键词：{self._last_search_keyword}\n"
            reply += f"显示剩余 {len(dramas) - 5} 部短剧：\n\n"
            
            for i, drama in enumerate(dramas[5:], 6):  # 从第6部开始显示
                title = drama.get('title', '未知')
                book_id = drama.get('book_id', '')
                if book_id:
                    reply += f"{i}. {title} (ID: {book_id})\n"
                else:
                    reply += f"{i}. {title}\n"
                
                # 添加短剧描述信息
                desc = drama.get('desc', '')
                if desc:
                    # 限制描述长度，避免消息过长
                    if len(desc) > 100:
                        desc = desc[:100] + "..."
                    reply += f"   简介：{desc}\n"
                
                # 添加播放量信息
                play_count = drama.get('play_count', '')
                if play_count:
                    reply += f"   播放量：{play_count}\n"
                
                # 分割线
                reply += "   ---------------\n\n"

            reply += "🔍 回复数字序号查看短剧详情和播放链接"
            await bot.send_text_message(message["FromWxid"], reply)
            return

        # 获取API配置
        api_config = self.api_configs.get("短剧")
        if not api_config:
            await bot.send_text_message(message["FromWxid"], "短剧搜索接口配置错误")
            return

        # 设置搜索参数
        api_config_copy = api_config.copy()  # 创建副本以避免修改原始配置
        api_config_copy["params"] = {"name": params, "page": "1", "showRawParams": "false"}

        # 调用API
        try:
            result = await self._call_api(bot, message["FromWxid"], "短剧", api_config_copy)
            if result and isinstance(result, dict):
                if result.get("code") == 200 and "data" in result:
                    dramas = result["data"]
                    if not dramas:
                        await bot.send_text_message(message["FromWxid"], f'未找到与"{params}"相关的短剧')
                        return

                    # 保存搜索结果到缓存
                    self._drama_cache = dramas
                    self._last_search_keyword = params

                    # 构建回复消息
                    reply = f"📺 搜索关键词：{params}\n"
                    reply += f"找到 {len(dramas)} 部相关短剧：\n\n"
                    
                    for i, drama in enumerate(dramas[:5], 1):  # 只显示前5部
                        title = drama.get('title', '未知')
                        book_id = drama.get('book_id', '')
                        if book_id:
                            reply += f"{i}. {title} (ID: {book_id})\n"
                        else:
                            reply += f"{i}. {title}\n"
                        
                        # 添加短剧描述信息
                        desc = drama.get('desc', '')
                        if desc:
                            # 限制描述长度，避免消息过长
                            if len(desc) > 100:
                                desc = desc[:100] + "..."
                            reply += f"   简介：{desc}\n"
                        
                        # 添加播放量信息
                        play_count = drama.get('play_count', '')
                        if play_count:
                            reply += f"   播放量：{play_count}\n"
                        
                        # 分割线
                        reply += "   ---------------\n\n"

                    if len(dramas) > 5:
                        reply += f"... 还有 {len(dramas) - 5} 部更多结果\n"
                        reply += "发送\"显示剩余\"可查看剩余结果\n\n"
                    
                    reply += "🔍 回复数字序号查看短剧详情和播放链接"
                    await bot.send_text_message(message["FromWxid"], reply)
                else:
                    error_msg = result.get("title", "搜索短剧失败，请稍后重试")
                    await bot.send_text_message(message["FromWxid"], f"搜索短剧失败: {error_msg}")
            else:
                await bot.send_text_message(message["FromWxid"], "搜索短剧失败，请稍后重试")
        except Exception as e:
            logger.error(f"搜索短剧失败: {str(e)}")
            await bot.send_text_message(message["FromWxid"], "搜索短剧失败，请稍后重试")
    
    # 新增处理短剧序号选择的方法
    async def _handle_drama_selection(self, bot: WechatAPIClient, message: dict, index: int):
        """处理短剧序号选择，获取详情和播放链接"""
        from_wxid = message.get("FromWxid", "")
        
        # 验证缓存和索引
        if not hasattr(self, '_drama_cache') or not self._drama_cache:
            await bot.send_text_message(from_wxid, "请先搜索短剧，然后再选择序号")
            return
            
        if index <= 0 or index > len(self._drama_cache):
            await bot.send_text_message(from_wxid, f"序号 {index} 无效，请输入1-{len(self._drama_cache)}之间的数字")
            return
            
        # 获取选定的短剧信息
        drama = self._drama_cache[index-1]
        title = drama.get('title', '未知短剧')
        book_id = drama.get('book_id', '')
        
        if not book_id:
            await bot.send_text_message(from_wxid, f"无法获取 {title} 的剧集信息，缺少ID")
            return
        
        logger.info(f"用户选择了第{index}部短剧: {title}, ID: {book_id}")
        
        # 获取API配置
        api_config = self.api_configs.get("短剧")
        if not api_config:
            await bot.send_text_message(from_wxid, "短剧接口配置错误")
            return
            
        # 设置详情参数
        api_config_copy = api_config.copy()
        api_config_copy["params"] = {
            "book_id": book_id,
            "showRawParams": "false"
        }
        
        try:
            # 发送加载提示
            await bot.send_text_message(from_wxid, f"正在获取《{title}》的详细信息，请稍候...")
            
            # 调用API获取详情
            result = await self._call_api(bot, from_wxid, "短剧", api_config_copy)
            
            if result and isinstance(result, dict):
                if result.get("code") == 200 and "data" in result:
                    videos = result["data"]
                    if not videos:
                        await bot.send_text_message(from_wxid, f"《{title}》暂无剧集信息")
                        return
                    
                    # 发送短剧基本信息
                    info_reply = f"📺 《{title}》剧集列表\n"
                    info_reply += f"━━━━━━━━━━━━━━━━\n"
                    info_reply += f"✅ 共找到 {len(videos)} 集\n"
                    info_reply += f"⏳ 正在生成播放链接...\n"
                    await bot.send_text_message(from_wxid, info_reply)
                    
                    # 将剧集分批发送，每批最多15集，避免单条消息过长
                    batch_size = 15
                    total_batches = (len(videos) + batch_size - 1) // batch_size  # 向上取整
                    
                    for batch_index in range(total_batches):
                        start_idx = batch_index * batch_size
                        end_idx = min(start_idx + batch_size, len(videos))
                        batch_videos = videos[start_idx:end_idx]
                        
                        # 构建批次回复
                        batch_reply = f"第 {start_idx+1}-{end_idx} 集：\n\n"
                        
                        for i, video in enumerate(batch_videos, start_idx+1):
                            video_title = video.get('title', f'第{i}集')
                            video_id = video.get('video_id', '')
                            
                            batch_reply += f"第{i}集: {video_title}\n"
                            
                            if video_id:
                                # 生成视频直链
                                video_link = f"https://api.cenguigui.cn/api/duanju/api.php?video_id={video_id}&type=mp4"
                                batch_reply += f"🔗 播放链接: {video_link}\n\n"
                            else:
                                batch_reply += "⚠️ 无法获取播放链接\n\n"
                        
                        # 发送当前批次
                        await bot.send_text_message(from_wxid, batch_reply)
                        
                        # 如果批次数超过1，添加短暂延迟避免消息发送过快
                        if total_batches > 1:
                            await asyncio.sleep(0.5)
                    
                    # 发送汇总信息
                    await bot.send_text_message(from_wxid, f"《{title}》的全部 {len(videos)} 集链接已发送完毕")
                else:
                    error_msg = result.get("title", "获取剧集信息失败")
                    await bot.send_text_message(from_wxid, f"获取《{title}》的剧集信息失败: {error_msg}")
            else:
                await bot.send_text_message(from_wxid, f"获取《{title}》的剧集信息失败，请稍后重试")
        except Exception as e:
            logger.error(f"获取短剧详情失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"获取《{title}》的剧集信息失败: {str(e)}")

    @on_at_message(priority=100)
    async def handle_at(self, bot: WechatAPIClient, message: dict):
        """处理@消息，用于添加/删除API"""
        if not self.enable:
            return True  # 修改：即使未启用也允许其他插件处理
        
        content = message.get("Content", "")
        from_wxid = message.get("FromWxid", "")
        
        # 白名单检查
        if not self._is_in_whitelist(from_wxid):
            # 对于非白名单群聊/用户，直接忽略，允许其他插件处理
            logger.info(f"忽略非白名单的@消息: {from_wxid}")
            return True
        
        if content.startswith("添加API "):
            await self._add_api(bot, message)
        elif content.startswith("删除API "):
            await self._remove_api(bot, message)
        elif content.startswith("API列表"):
            await self._list_api(bot, message)
            
        return True  # 修改：无论是否匹配，都允许其他插件处理

    async def _add_api(self, bot: WechatAPIClient, message: dict):
        """添加API接口"""
        content = message.get("Content", "")
        from_wxid = message.get("FromWxid", "")
        user_id, user_name = await self._get_user_info(message)
        
        # 检查权限
        if not self.is_admin(user_id):
            await bot.send_text_message(from_wxid, "⚠️ 权限不足，只有管理员可以添加API")
            return
        
        # 解析API信息
        try:
            # 格式: 添加API 命令 URL 请求方法 返回类型 描述
            parts = content.split(" ")
            if len(parts) < 5:
                await bot.send_text_message(from_wxid, "⚠️ 格式错误，正确格式: 添加API 命令 URL 请求方法 返回类型 描述")
                return
            
            cmd = parts[2]
            url = parts[3]
            method = parts[4]
            return_type = parts[5] if len(parts) > 5 else "text"
            description = " ".join(parts[6:]) if len(parts) > 6 else "无描述"
            
            # 添加到配置
            self.api_configs[cmd] = {
                "url": url,
                "method": method,
                "return_type": return_type,
                "description": description
            }
            
            # 保存配置
            self._save_api_config()
            
            await bot.send_text_message(from_wxid, f"✅ 成功添加API: {cmd}\nURL: {url}\n方法: {method}\n返回类型: {return_type}\n描述: {description}")
        except Exception as e:
            logger.error(f"添加API失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"⚠️ 添加API失败: {str(e)}")
    
    async def _remove_api(self, bot: WechatAPIClient, message: dict):
        """删除API接口"""
        content = message.get("Content", "")
        from_wxid = message.get("FromWxid", "")
        user_id, user_name = await self._get_user_info(message)
        
        # 检查权限
        if not self.is_admin(user_id):
            await bot.send_text_message(from_wxid, "⚠️ 权限不足，只有管理员可以删除API")
            return
        
        # 解析API信息
        try:
            # 格式: 删除API 命令
            parts = content.split(" ")
            if len(parts) < 3:
                await bot.send_text_message(from_wxid, "⚠️ 格式错误，正确格式: 删除API 命令")
                return
            
            cmd = parts[2]
            
            # 检查API是否存在
            if cmd not in self.api_configs:
                await bot.send_text_message(from_wxid, f"⚠️ API不存在: {cmd}")
                return
            
            # 删除API
            del self.api_configs[cmd]
            
            # 保存配置
            self._save_api_config()
            
            await bot.send_text_message(from_wxid, f"✅ 成功删除API: {cmd}")
        except Exception as e:
            logger.error(f"删除API失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"⚠️ 删除API失败: {str(e)}")
    
    async def _list_api(self, bot: WechatAPIClient, message: dict):
        """列出所有API接口和命令"""
        from_wxid = message.get("FromWxid", "")
        content = message.get("Content", "").strip()
        
        # 检查是否指定了命令
        parts = content.split(" ")
        if len(parts) > 1 and parts[1]:
            command = parts[1]
            # 获取命令配置
            command_config = self._get_command_config(command)
            if command_config:
                reply = f"📋 命令详情: {command}\n"
                reply += f"📝 描述: {command_config.get('description', '无描述')}\n"
                reply += f"📖 用法: {command_config.get('usage', command)}\n"
                reply += f"🔒 管理员限定: {'是' if command_config.get('admin_only', False) else '否'}\n"
                reply += f"🔍 隐藏命令: {'是' if command_config.get('hidden', False) else '否'}\n"
                await bot.send_text_message(from_wxid, reply)
                return
            
            # 检查是否是API命令
            if command in self.api_configs:
                api_config = self.api_configs[command]
                reply = f"📡 API接口详情: {command}\n"
                reply += f"📝 描述: {api_config.get('description', '无描述')}\n"
                reply += f"🔗 URL: {api_config.get('url', '未设置')}\n"
                reply += f"📊 方法: {api_config.get('method', 'get')}\n"
                reply += f"📦 返回类型: {api_config.get('return_type', 'text')}\n"
                
                # 检查是否有参数
                if "params" in api_config and api_config["params"]:
                    reply += "📋 参数:\n"
                    for key, value in api_config["params"].items():
                        reply += f"  - {key}: {value}\n"
                
                await bot.send_text_message(from_wxid, reply)
                return
            
            await bot.send_text_message(from_wxid, f"⚠️ 未找到命令或API: {command}")
            return
        
        # 否则，列出所有非隐藏命令
        visible_commands = [cmd for cmd in self.commands if not cmd.get("hidden", False)]
        
        # 检查是否有可用命令
        if not visible_commands and not self.api_configs:
            await bot.send_text_message(from_wxid, "⚠️ 当前没有可用命令和API接口")
            return
        
        command_list = "📋 可用命令列表：\n"
        for cmd in visible_commands:
            command_list += f"• {cmd.get('name')}: {cmd.get('description', '无描述')}\n"
        
        # 添加API列表
        if self.api_configs:
            command_list += "\n📡 API接口列表：\n"
            for cmd, config in self.api_configs.items():
                # 检查是否已在命令列表中
                if not any(c.get('name') == cmd for c in visible_commands):
                    command_list += f"• {cmd}: {config.get('description', '无描述')}\n"
        
        command_list += "\n💡 提示: 发送\"API列表 <命令名>\"可查看命令详情"
        await bot.send_text_message(from_wxid, command_list)

    # 新增处理小说搜索的方法
    async def _handle_novel(self, bot: WechatAPIClient, message: dict, params: str):
        """处理小说搜索请求"""
        from_wxid = message.get("FromWxid", "")
        
        # 获取API配置
        api_config = self.api_configs.get("小说")
        if not api_config:
            await bot.send_text_message(from_wxid, "小说搜索接口配置错误")
            return
            
        # 设置搜索参数
        api_config_copy = api_config.copy()  # 创建副本以避免修改原始配置
        api_config_copy["params"] = {"name": params, "type": "json"}
            
        # 调用API
        try:
            logger.info(f"搜索小说关键词: {params}")
            
            # 使用通用API调用方法
            result = await self._call_api(bot, from_wxid, "小说", api_config_copy)
            
            if result and isinstance(result, list):
                # 记录返回结构以便调试
                logger.info(f"小说搜索返回示例数据结构: {result[0] if result else None}")
                
                # 保存搜索结果到缓存
                self._novel_cache = result
                self._novel_search_keyword = params
                
                # 构建回复消息
                reply = f"📚 搜索关键词：{params}\n"
                reply += f"找到 {len(result)} 部相关小说：\n\n"
                
                # 每次最多显示15部小说
                max_display = min(15, len(result))
                
                for i in range(max_display):
                    novel = result[i]
                    # 检查不同可能的标题字段名
                    novel_title = self._extract_novel_field(novel, ["title", "name", "bookname", "book_name", "novel_name", "novel_title"])
                    # 使用列表索引作为选择序号
                    reply += f"{i+1}. {novel_title}\n"
                    
                    # 处理作者字段
                    novel_author = self._extract_novel_field(novel, ["author", "writer", "auth", "aut", "creator", "作者"])
                    if novel_author:
                        reply += f"   作者：{novel_author}\n"
                    
                    # 处理类型字段
                    novel_type = self._extract_novel_field(novel, ["type", "category", "class", "genre", "tag", "tags", "分类", "类型"])
                    if novel_type:
                        # 处理可能的数组或字符串
                        if isinstance(novel_type, list):
                            novel_type = "、".join(novel_type)
                        reply += f"   类型：{novel_type}\n"
                    reply += "\n"
                
                if len(result) > max_display:
                    reply += f"... 共找到 {len(result)} 部相关小说\n"
                
                reply += "请回复数字序号查看小说详情"
                
                await bot.send_text_message(from_wxid, reply)
            else:
                logger.warning(f"小说搜索返回数据异常: {result}")
                
                if not result:
                    await bot.send_text_message(from_wxid, f"未找到与\"{params}\"相关的小说")
                else:
                    await bot.send_text_message(from_wxid, "搜索小说失败，返回数据格式错误")
        except Exception as e:
            logger.error(f"搜索小说失败: {str(e)}")
            await bot.send_text_message(from_wxid, "搜索小说失败，请稍后重试")
    
    def _extract_novel_field(self, data: dict, possible_fields: list, default="未知"):
        """从小说数据中提取指定字段，支持多种可能的字段名
        
        Args:
            data: 小说数据字典
            possible_fields: 可能的字段名列表
            default: 默认值，当所有字段都不存在时返回
            
        Returns:
            提取到的字段值或默认值
        """
        # 先尝试直接获取字段
        for field in possible_fields:
            value = data.get(field)
            if value:
                return value
                
        # 尝试检查是否有嵌套的info或data字段
        for container in ["info", "data", "detail", "details"]:
            if container in data and isinstance(data[container], dict):
                for field in possible_fields:
                    value = data[container].get(field)
                    if value:
                        return value
        
        # 尝试处理可能分开存储的特殊情况，例如first_name + last_name
        if "first_name" in data and "last_name" in data:
            if data["first_name"] and data["last_name"]:
                return f"{data['first_name']} {data['last_name']}"
                
        # 尝试从原始返回数据中搜索包含特定关键词的字段
        for key, value in data.items():
            for field in possible_fields:
                if field.lower() in key.lower() and value:
                    return value
        
        return default
    
    # 新增处理小说序号选择的方法
    async def _handle_novel_selection(self, bot: WechatAPIClient, message: dict, index: int):
        """处理小说序号选择"""
        from_wxid = message.get("FromWxid", "")
        
        # 验证缓存和索引
        if not hasattr(self, '_novel_cache') or not self._novel_cache:
            await bot.send_text_message(from_wxid, "请先搜索小说，然后再选择序号")
            return
            
        if index <= 0 or index > len(self._novel_cache):
            await bot.send_text_message(from_wxid, f"序号 {index} 无效，请输入1-{len(self._novel_cache)}之间的数字")
            return
            
        # 获取选定的小说信息
        novel = self._novel_cache[index-1]
        novel_title = self._extract_novel_field(novel, ["title", "name", "bookname", "book_name", "novel_name", "novel_title"])
        logger.info(f"用户选择了第{index}部小说: {novel_title}")
        
        # 获取API配置
        api_config = self.api_configs.get("小说")
        if not api_config:
            await bot.send_text_message(from_wxid, "小说搜索接口配置错误")
            return
            
        # 设置详情参数
        api_config_copy = api_config.copy()
        api_config_copy["params"] = {
            "name": self._novel_search_keyword,
            "n": str(index), 
            "type": "json"
        }
        
        try:
            # 调用API获取详情
            result = await self._call_api(bot, from_wxid, "小说", api_config_copy)
            
            # 记录返回结构以便调试
            logger.info(f"小说详情返回数据结构: {result}")
            
            if result and isinstance(result, dict):
                # 尝试从不同可能的字段获取信息
                novel_title = self._extract_novel_field(result, ["title", "name", "bookname", "book_name", "novel_name", "novel_title"])
                novel_author = self._extract_novel_field(result, ["author", "writer", "auth", "aut", "creator", "作者"])
                novel_type = self._extract_novel_field(result, ["type", "category", "class", "genre", "tag", "tags", "分类", "类型"])
                novel_img = self._extract_novel_field(result, ["img", "cover", "image", "pic", "picture", "thumb", "封面"], "")
                novel_download = self._extract_novel_field(result, ["download", "link", "url", "download_url", "book_url", "下载链接"], "")
                novel_summary = self._extract_novel_field(result, ["js", "summary", "desc", "description", "intro", "introduction", "content", "简介"], "")
                
                # 处理类型字段，可能是数组
                if isinstance(novel_type, list):
                    novel_type = "、".join(novel_type)
                
                # 构建详情回复
                reply = f"📕 小说详情\n"
                reply += f"━━━━━━━━━━━━━━━━\n"
                reply += f"📗 书名: {novel_title}\n"
                
                if novel_author and novel_author != "未知":
                    reply += f"✍️ 作者: {novel_author}\n"
                    
                if novel_type and novel_type != "未知":
                    reply += f"📋 分类: {novel_type}\n"
                    
                if novel_img:
                    reply += f"🖼️ 封面: 见下方图片\n"
                    
                if novel_download:
                    reply += f"📥 下载地址: {novel_download}\n"
                
                if novel_summary and novel_summary != "未知":
                    # 格式化概括内容，处理可能的HTML标签
                    summary = novel_summary.replace("<br>", "\n").replace("&nbsp;", " ")
                    reply += f"\n📝 内容简介:\n{summary}\n"
                
                await bot.send_text_message(from_wxid, reply)
                
                # 如果有封面图片，尝试发送
                if novel_img and novel_img.startswith("http"):
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(novel_img) as response:
                                if response.status == 200:
                                    img_data = await response.read()
                                    await bot.send_image_message(from_wxid, img_data)
                    except Exception as img_e:
                        logger.error(f"发送小说封面图片失败: {str(img_e)}")
            else:
                logger.warning(f"获取小说详情返回数据异常: {result}")
                await bot.send_text_message(from_wxid, "获取小说详情失败，返回数据格式错误")
        except Exception as e:
            logger.error(f"获取小说详情失败: {str(e)}")
            await bot.send_text_message(from_wxid, "获取小说详情失败，请稍后重试")