import re
import os
import tomllib
import functools
from typing import List, Optional, Dict, Any, Callable, Union

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase
from utils.admin_manager import admin_manager


class CommandHandler:
    """命令处理工具类"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        # 存储函数映射
        self.handlers = {}
    
    def register(self, cmd_name: str, handler_func: Callable):
        """注册命令处理函数"""
        self.handlers[cmd_name] = handler_func
        # 如果命令有别名，也注册别名
        for command in self.plugin.commands:
            if command.get("name") == cmd_name:
                for alias in command.get("aliases", []):
                    self.handlers[alias] = handler_func
    
    def get_handler(self, cmd_name: str) -> Optional[Callable]:
        """获取命令处理函数"""
        return self.handlers.get(cmd_name)
    
    async def execute(self, cmd_name: str, bot: WechatAPIClient, group_id: str, user_id: str, 
                     params: str = "", at_users: List[str] = None) -> bool:
        """执行命令"""
        handler = self.get_handler(cmd_name)
        if not handler:
            return False
            
        cmd = self.plugin._get_command_by_name_or_alias(cmd_name)
        if not cmd:
            return False
            
        # 前置钩子
        if not await self.plugin._pre_command_hook(cmd, bot, group_id, user_id):
            return False
            
        try:
            # 确保at_users是有效的列表
            if at_users is None:
                at_users = []
                
            logger.debug(f"命令 {cmd_name} 的@用户列表: {at_users}")
            
            # 处理文本参数中可能包含的@符号，这些@不应被视为用户名一部分
            cleaned_params = params
            # 如果文本参数中以@开头，那么可能是微信客户端自动添加的@文本，应该去除
            if cleaned_params and cleaned_params.startswith('@'):
                # 尝试提取有效参数部分（去除@标记）
                parts = cleaned_params.split(None, 1)
                if len(parts) > 1:
                    # 如果有空格分隔，取第二部分作为有效参数
                    cleaned_params = parts[1].strip()
                else:
                    # 如果只有@部分，则清空参数，但保留at_users
                    cleaned_params = ""
                    
            logger.debug(f"参数清理: 原始='{params}' -> 清理后='{cleaned_params}', @用户={at_users}")
            
            # 解析参数
            parsed_params = {
                "params": cleaned_params,
                "at_users": at_users
            }
            
            # 执行命令
            result = await handler(bot, group_id, user_id, parsed_params)
            
            # 后置钩子
            await self.plugin._post_command_hook(cmd, bot, group_id, user_id, result)
            return True
        except Exception as e:
            # 错误处理
            await self.plugin._handle_command_error(cmd, bot, group_id, user_id, e)
            return False


def require_admin(func):
    """管理员权限检查装饰器"""
    @functools.wraps(func)
    async def wrapper(self, bot, group_id, user_id, *args, **kwargs):
        if not await self.check_admin_permission(bot, user_id, group_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，需要管理员权限",
                at=user_id
            )
            return False
        return await func(self, bot, group_id, user_id, *args, **kwargs)
    return wrapper


class AdminManagerPlugin(PluginBase):
    description = "管理员管理插件"
    author = "XYBotV2"
    version = "1.1.0"

    def __init__(self):
        super().__init__()
        self.enable = True
        self.super_admins = []
        
        # 从配置文件加载设置
        try:
            with open("plugins/AdminManager/config.toml", "rb") as f:
                config = tomllib.load(f)
                self.enable = config["AdminManager"].get("enable", True)
                self.super_admins = config["AdminManager"].get("super_admins", [])
                logger.info(f"超级管理员列表加载成功: {self.super_admins}")
                
            # 加载命令映射
            self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
            self._load_command_map()
            
            # 初始化命令处理器
            self.cmd_handler = CommandHandler(self)
            self._register_command_handlers()
            
            logger.success("管理员管理插件初始化成功")
        except Exception as e:
            logger.error(f"管理员管理插件初始化失败: {e}")
            self.enable = False
    
    def _load_command_map(self):
        """加载命令映射配置"""
        try:
            if os.path.exists(self.command_map_path):
                with open(self.command_map_path, "rb") as f:
                    config = tomllib.load(f)
                
                self.commands = config.get("commands", [])
                logger.info(f"已加载 {len(self.commands)} 条命令映射")
            else:
                # 如果命令映射文件不存在，使用默认配置
                logger.warning(f"命令映射文件不存在: {self.command_map_path}，使用默认配置")
                self.commands = [
                    {
                        "name": "添加管理",
                        "aliases": ["加管理"],
                        "admin_only": True
                    },
                    {
                        "name": "删除管理",
                        "aliases": ["移除管理", "撤销管理"],
                        "admin_only": True
                    },
                    {
                        "name": "管理列表",
                        "aliases": ["列出管理", "查看管理员"]
                    }
                ]
        except Exception as e:
            logger.error(f"加载命令映射失败: {e}")
            self.commands = []
    
    def _register_command_handlers(self):
        """注册命令处理函数"""
        self.cmd_handler.register("添加管理", self.handle_add_admin)
        self.cmd_handler.register("删除管理", self.handle_remove_admin)
        self.cmd_handler.register("管理列表", self.handle_list_admins)
    
    def _get_command_by_name_or_alias(self, cmd_name: str) -> dict:
        """通过名称或别名获取命令配置"""
        for command in self.commands:
            if command.get("name") == cmd_name or cmd_name in command.get("aliases", []):
                return command
        return {}
    
    async def _pre_command_hook(self, cmd: dict, bot, group_id: str, user_id: str) -> bool:
        """命令执行前的钩子"""
        # 检查命令是否需要管理员权限
        if cmd.get("admin_only", False):
            is_admin = await self.check_admin_permission(bot, user_id, group_id)
            if not is_admin:
                await bot.send_text_message(
                    wxid=group_id,
                    content="⚠️ 权限不足，此命令需要管理员权限",
                    at=user_id
                )
                logger.warning(f"用户 {user_id} 尝试执行管理员命令 {cmd.get('name', '')} 但没有权限")
                return False
        
        return True
    
    async def _post_command_hook(self, cmd: dict, bot, group_id: str, user_id: str, result: Any) -> None:
        """命令执行后的钩子"""
        # 可以在这里实现命令执行后的通用逻辑
        pass
    
    async def _handle_command_error(self, cmd: dict, bot, group_id: str, user_id: str, error: Exception) -> None:
        """统一错误处理"""
        cmd_name = cmd.get("name", "未知命令")
        logger.error(f"执行命令 {cmd_name} 失败: {str(error)}")
        
        # 发送错误消息
        await bot.send_text_message(
            wxid=group_id,
            content=f"❌ 执行命令时出错: {str(error)}",
            at=user_id
        )

    async def async_init(self, bot=None):
        # 确保管理员管理器已初始化
        await admin_manager.initialize()
        logger.info("管理员管理插件异步初始化完成")

    @on_text_message(2)  # 设置高优先级
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        """处理文本消息"""
        if not self.enable:
            return True
        
        try:
            # 获取消息内容
            content = message.get("Content", "").strip()
            wxid = message.get("SenderWxid", "")
            from_wxid = message.get("FromWxid", "")
            is_group = message.get("IsGroup")
            msg_id = message.get("MsgId", "")
            
            # 调试输出完整消息内容
            logger.debug(f"收到消息详情: ID:{msg_id} 内容:{content} 发送者:{wxid} 群组:{from_wxid} 群消息:{is_group} 原始消息:{message}")
            
            # 检查是否是群消息
            if not is_group:
                return True
                
            # 获取@用户列表
            at_users = message.get("AtWxids", []) 
            # 也兼容使用Ats字段
            if not at_users and "Ats" in message:
                at_users = message.get("Ats", [])
            
            logger.debug(f"收到管理命令: {content}")
            if at_users:
                logger.debug(f"包含@用户: {at_users}")
            
            # 处理命令
            for command in self.commands:
                cmd_name = command.get("name", "")
                aliases = command.get("aliases", [])
                
                # 检查完全匹配（如"管理列表"等）
                if content == cmd_name or content in aliases:
                    logger.info(f"匹配到完全命令: {cmd_name}")
                    await self.cmd_handler.execute(cmd_name, bot, from_wxid, wxid, "", at_users)
                    return False
                    
                # 检查前缀匹配（如"添加管理xxx"等）
                if content.startswith(cmd_name + " ") or any(content.startswith(alias + " ") for alias in aliases):
                    # 提取命令后的参数
                    if content.startswith(cmd_name + " "):
                        params = content[len(cmd_name) + 1:]
                        logger.info(f"匹配到命令: {cmd_name}, 原始参数: {params}, @用户: {at_users}")
                    else:
                        # 找出匹配的别名
                        matched_alias = next((alias for alias in aliases if content.startswith(alias + " ")), "")
                        params = content[len(matched_alias) + 1:]
                        logger.info(f"匹配到命令别名: {matched_alias}, 原始参数: {params}, @用户: {at_users}")
                    
                    await self.cmd_handler.execute(cmd_name, bot, from_wxid, wxid, params, at_users)
                    return False
            
            return True
        except Exception as e:
            logger.error(f"处理命令失败: {e}")
            return True
    
    async def is_member_in_group(self, bot: WechatAPIClient, group_wxid: str, wxid: str) -> bool:
        """检查用户是否在群中"""
        try:
            members = await bot.get_chatroom_member_list(group_wxid)
            for member in members:
                if member.get("UserName") == wxid:
                    return True
            return False
        except Exception as e:
            logger.error(f"检查用户是否在群中失败: {e}")
            return False
    
    async def check_admin_permission(self, bot: WechatAPIClient, wxid: str, group_wxid: str) -> bool:
        """检查用户是否有管理员操作权限"""
        # 检查是否是超级管理员
        is_super_admin = wxid in self.super_admins
        logger.info(f"超级管理员检查: 用户={wxid}, 是超级管理员={is_super_admin}")
        if is_super_admin:
            return True
            
        # 检查是否是已有管理员
        is_admin = await admin_manager.is_admin(group_wxid, wxid)
        logger.info(f"管理员检查: 用户={wxid}, 是管理员={is_admin}")
        
        return is_admin
    
    @require_admin
    async def handle_add_admin(self, bot: WechatAPIClient, group_wxid: str, sender_wxid: str, params_dict: Dict):
        """处理添加管理员命令"""
        try:
            # 获取需要添加的用户
            target_wxid = None
            target_name = None
            
            # 从参数中获取数据
            text_params = params_dict.get("params", "")
            at_users = params_dict.get("at_users", [])
            
            logger.info(f"添加管理参数解析 - 文本参数: '{text_params}', @用户列表: {at_users}")
            
            # 优先从@用户列表中获取
            if at_users and len(at_users) > 0:
                target_wxid = at_users[0]
                logger.info(f"通过@指定用户wxid: {target_wxid}")
                
                # 获取用户昵称
                try:
                    target_name = await bot.get_chatroom_nickname(group_wxid, target_wxid)
                    logger.info(f"获取到用户昵称: {target_name}")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    # 获取失败时使用wxid
                    target_name = target_wxid
            # 如果没有@用户，但有文本参数，从文本参数中解析用户名
            elif text_params:
                # 从命令文本中解析用户名
                target_name = text_params.strip()
                logger.info(f"尝试查找用户: '{target_name}'")
                
                # 查找用户wxid
                target_wxid = await bot.find_user_by_name(group_wxid, target_name)
                logger.info(f"找到用户: {target_name} -> {target_wxid}")
                    
            # 检查是否找到目标用户
            if not target_wxid:
                logger.warning(f"未找到要添加的管理员")
                await bot.send_text_message(
                    group_wxid, 
                    "⚠️ 未指定要添加的管理员，请@要添加的用户或在命令后输入用户名",
                    at=sender_wxid
                )
                return False
                
            # 如果目标是自己，提示错误
            if target_wxid == sender_wxid:
                logger.warning(f"用户尝试添加自己为管理员")
                await bot.send_text_message(
                    group_wxid, 
                    "⚠️ 不能添加自己为管理员",
                    at=sender_wxid
                )
                return False
                
            # 添加管理员
            logger.info(f"尝试添加管理员: {target_name} ({target_wxid})")
            success = await admin_manager.add_admin(group_wxid, target_wxid)
            
            if success:
                # 添加成功，发送通知
                logger.success(f"添加管理员成功: {target_name} ({target_wxid})")
                await bot.send_text_message(
                    group_wxid, 
                    f"✅ 已成功添加 {target_name} 为管理员"
                )
                return True
            else:
                # 添加失败，可能已经是管理员
                logger.warning(f"添加管理员失败，可能已是管理员: {target_name} ({target_wxid})")
                await bot.send_text_message(
                    group_wxid, 
                    f"⚠️ {target_name} 已经是管理员"
                )
                return False
                
        except Exception as e:
            logger.error(f"添加管理员失败: {e}")
            raise
    
    @require_admin
    async def handle_remove_admin(self, bot: WechatAPIClient, group_wxid: str, sender_wxid: str, params_dict: Dict):
        """处理删除管理员命令"""
        try:
            # 获取需要移除的用户
            target_wxid = None
            target_name = None
            
            # 从参数中获取数据
            text_params = params_dict.get("params", "")
            at_users = params_dict.get("at_users", [])
            
            logger.info(f"移除管理参数解析 - 文本参数: '{text_params}', @用户列表: {at_users}")
            
            # 优先从@用户列表中获取
            if at_users and len(at_users) > 0:
                target_wxid = at_users[0]
                logger.info(f"通过@指定用户wxid: {target_wxid}")
                
                # 获取用户昵称
                try:
                    target_name = await bot.get_chatroom_nickname(group_wxid, target_wxid)
                    logger.info(f"获取到用户昵称: {target_name}")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    # 获取失败时使用wxid
                    target_name = target_wxid
            # 如果没有@用户，但有文本参数，从文本参数中解析用户名
            elif text_params:
                # 从命令文本中解析用户名
                target_name = text_params.strip()
                logger.info(f"尝试查找用户: '{target_name}'")
                
                # 查找用户wxid
                target_wxid = await bot.find_user_by_name(group_wxid, target_name)
                logger.info(f"找到用户: {target_name} -> {target_wxid}")
                    
            # 检查是否找到目标用户
            if not target_wxid:
                logger.warning(f"未找到要移除的管理员")
                await bot.send_text_message(
                    group_wxid, 
                    "⚠️ 未指定要移除的管理员，请@要移除的用户或在命令后输入用户名",
                    at=sender_wxid
                )
                return False
                
            # 移除管理员
            logger.info(f"尝试移除管理员: {target_name} ({target_wxid})")
            success = await admin_manager.remove_admin(group_wxid, target_wxid)
            
            if success:
                # 移除成功，发送通知
                logger.success(f"移除管理员成功: {target_name} ({target_wxid})")
                await bot.send_text_message(
                    group_wxid, 
                    f"✅ 已成功移除 {target_name} 的管理员权限"
                )
                return True
            else:
                # 移除失败，可能不是管理员
                logger.warning(f"移除管理员失败，可能不是管理员: {target_name} ({target_wxid})")
                await bot.send_text_message(
                    group_wxid, 
                    f"⚠️ {target_name} 不是管理员",
                    at=sender_wxid
                )
                return False
                
        except Exception as e:
            logger.error(f"移除管理员失败: {e}")
            raise
    
    async def handle_list_admins(self, bot: WechatAPIClient, group_wxid: str, sender_wxid: str, params_dict: Dict = None):
        """处理列出管理员命令"""
        try:
            # 获取所有管理员
            admins = await admin_manager.get_admins(group_wxid)
            
            # 构建回复消息
            reply = f"📋 管理员列表\n"
            
            # 添加超级管理员信息
            super_admins_in_group = []
            for admin_wxid in self.super_admins:
                try:
                    # 检查超级管理员是否在群内
                    if await self.is_member_in_group(bot, group_wxid, admin_wxid):
                        admin_name = await bot.get_chatroom_nickname(group_wxid, admin_wxid)
                        super_admins_in_group.append((admin_wxid, admin_name))
                except Exception as e:
                    logger.error(f"获取超级管理员信息失败: {e}")
            
            if super_admins_in_group:
                reply += "🌟 超级管理员:\n"
                for i, (admin_wxid, admin_name) in enumerate(super_admins_in_group, 1):
                    reply += f"{i}. {admin_name}\n"
                reply += "\n"
            
            # 添加管理员信息
            if admins:
                reply += "👮‍♂️ 管理员:\n"
                regular_admins = []
                for admin_wxid in admins:
                    # 过滤掉已经显示为超级管理员的用户
                    if admin_wxid not in [sa[0] for sa in super_admins_in_group]:
                        try:
                            admin_name = await bot.get_chatroom_nickname(group_wxid, admin_wxid)
                            regular_admins.append((admin_wxid, admin_name))
                        except:
                            regular_admins.append((admin_wxid, admin_wxid))
                
                for i, (admin_wxid, admin_name) in enumerate(regular_admins, 1):
                    reply += f"{i}. {admin_name}\n"
            else:
                if not super_admins_in_group:
                    reply += "当前没有设置其他管理员"
                
            # 发送回复
            logger.info(f"发送管理员列表: {reply}")
            await bot.send_text_message(
                group_wxid, 
                reply
            )
            return True
                
        except Exception as e:
            logger.error(f"列出管理员失败: {e}")
            raise 