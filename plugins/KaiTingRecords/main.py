import os
import re
import tomllib
import datetime
import asyncio
import functools
import time
from typing import Optional, Tuple, Dict, List, Any, Callable, Union
import inspect

from loguru import logger
from WechatAPI import WechatAPIClient
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, schedule
from utils.admin_manager import admin_manager

from .db_models import KaiTingDB, HallRecord, HallTempTime


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
                     nickname: str, params: str = "") -> bool:
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
            # 参数处理
            parsed_params = self.plugin._parse_parameters(cmd, params)
            if isinstance(parsed_params, dict) and parsed_params.get("error"):
                await bot.send_text_message(
                    wxid=group_id, 
                    content=f"❌ {parsed_params['error']}",
                    at=user_id
                )
                return False
                
            # 根据函数名称来决定参数传递方式
            handler_name = handler.__name__
            
            # 不需要params参数的函数列表，开厅和关厅从列表中移除，因为现在它们需要处理参数
            no_params_funcs = [
                "list_halls", 
                "list_reminders", 
                "enable_kaiting", 
                "disable_kaiting"
            ]
            
            # 特殊处理show_help和list_all_halls函数(它们接受可选的params参数)
            if handler_name in ["show_help", "list_all_halls"]:
                result = await handler(bot, group_id, user_id, nickname, parsed_params)
            # 处理不需要params参数的函数
            elif handler_name in no_params_funcs:
                result = await handler(bot, group_id, user_id, nickname)
            # 其他需要params参数的函数
            else:
                result = await handler(bot, group_id, user_id, nickname, parsed_params)
            
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
    async def wrapper(self, bot, group_id, user_id, nickname, *args, **kwargs):
        if not await self.is_admin(group_id, user_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，需要管理员权限",
                at=user_id
            )
            return
        return await func(self, bot, group_id, user_id, nickname, *args, **kwargs)
    return wrapper


class KaiTingRecords(PluginBase):
    """开厅记录插件"""
    description = "群聊开厅记录管理插件"
    author = "XYBot开发团队"
    version = "1.1.0"
    
    def __init__(self):
        super().__init__()
        # 读取配置文件
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
        
        try:
            with open(config_path, "rb") as f:
                self.config = tomllib.load(f)
            
            # 基本配置
            self.enable = self.config.get("basic", {}).get("enable", True)
            
            # 命令配置 - 保留命令名称变量供函数使用
            self.commands_config = self.config.get("commands", {})
            self.cmd_enable = self.commands_config.get("enable_kaiting", "启用开厅记录")
            self.cmd_disable = self.commands_config.get("disable_kaiting", "禁用开厅记录")
            self.cmd_register = self.commands_config.get("register_hall", "厅名注册")
            self.cmd_set_time = self.commands_config.get("set_hall_time", "开厅时间")
            self.cmd_temp_time = self.commands_config.get("set_temp_time", "临时时间")
            self.cmd_open = self.commands_config.get("hall_open", "开厅")
            self.cmd_close = self.commands_config.get("hall_close", "关厅")
            self.cmd_set_duration = self.commands_config.get("set_duration", "设置开厅时长")
            self.cmd_list_halls = self.commands_config.get("list_halls", "列出厅名")
            self.cmd_help = self.commands_config.get("help_command", "开厅记录帮助")
            self.cmd_query = self.commands_config.get("query_records", "查开厅记录")
            self.cmd_set_reminder = self.commands_config.get("set_reminder_time", "设置提醒时间")
            self.cmd_list_reminders = self.commands_config.get("list_reminders", "开厅提醒列表")
            self.cmd_remove_reminder = self.commands_config.get("remove_reminder", "取消提醒")
            self.cmd_delete_hall = self.commands_config.get("delete_hall", "删除厅名")
            self.cmd_list_all_halls = self.commands_config.get("list_all_halls", "列出所有厅名")
            self.cmd_query_user_hall = self.commands_config.get("query_user_hall", "查询厅")
            
            # 初始化数据库
            self.db = KaiTingDB()
            
            # 存储提醒任务，格式：{日期_群组ID_厅名: task}
            self.reminder_tasks = {}
            
            # 定时提醒设置（开厅前几分钟提醒）
            self.remind_before_minutes = self.config.get("basic", {}).get("reminder_minutes", 10)
            
            # 保存bot引用，用于发送消息
            self.bot = None
            
            # 命令列表 - 新命令映射方式
            self.commands = []
            self._load_command_map()
            
            # 初始化命令处理器
            self.cmd_handler = CommandHandler(self)
            self._register_command_handlers()
            
            # 命令统计
            self.command_stats = {}
            
            self.log_success("开厅记录插件初始化成功")
        except Exception as e:
            self.log_error(f"加载开厅记录插件配置文件失败: {str(e)}")
            self.enable = False
    
    def _register_command_handlers(self):
        """注册命令处理函数"""
        # 基础命令
        self.cmd_handler.register("开厅记录帮助", self.show_help)
        self.cmd_handler.register("启用开厅记录", self.enable_kaiting)
        self.cmd_handler.register("禁用开厅记录", self.disable_kaiting)
        
        # 厅管理命令
        self.cmd_handler.register("厅名注册", self._handle_register_command)
        self.cmd_handler.register("开厅时间", self._handle_set_time_command)
        self.cmd_handler.register("临时时间", self._handle_temp_time_command)
        self.cmd_handler.register("开厅", self.open_hall)  # 修改为接受参数的处理函数
        self.cmd_handler.register("关厅", self.close_hall)  # 修改为接受参数的处理函数
        self.cmd_handler.register("设置开厅时长", self._handle_set_duration_command)
        self.cmd_handler.register("列出厅名", self.list_halls)
        self.cmd_handler.register("查开厅记录", self._handle_query_command)
        self.cmd_handler.register("删除厅名", self._handle_delete_hall_command)
        self.cmd_handler.register("查询厅", self.query_user_hall)
        
        # 提醒管理
        self.cmd_handler.register("设置提醒时间", self._handle_set_reminder_command)
        self.cmd_handler.register("开厅提醒列表", self.list_reminders)
        self.cmd_handler.register("取消提醒", self._handle_remove_reminder_command)
        
        # 管理命令
        self.cmd_handler.register("列出所有厅名", self.list_all_halls)
        self.cmd_handler.register("查看营业中厅", self.list_active_halls)
        self.cmd_handler.register("查看未营业厅", self.list_inactive_halls)
        self.cmd_handler.register("关闭指定厅", self.admin_close_hall)
        self.cmd_handler.register("检查补全厅ID", self.check_and_fill_hall_ids)
        
        # 隐藏命令
        self.cmd_handler.register("删", self._handle_delete_hall_command)
    
    def _load_command_map(self):
        """加载命令映射配置"""
        try:
            if os.path.exists(self.command_map_path):
                with open(self.command_map_path, "rb") as f:
                    config = tomllib.load(f)
                
                self.commands = config.get("commands", [])
                self.log_info(f"已加载 {len(self.commands)} 条命令映射")
                
                # 初始化核心命令列表 - 这些命令需要精确匹配
                self.core_commands = [
                    self.cmd_open,        # 开厅
                    self.cmd_close,       # 关厅
                    "开",  # 可能的别名
                    "关",  # 可能的别名
                    "营业", # 可能的别名
                    "停业"  # 可能的别名
                ]
                
                # 加载核心命令的别名
                for command in self.commands:
                    cmd_name = command.get("name", "")
                    if cmd_name in [self.cmd_open, self.cmd_close]:  # 核心命令
                        # 将别名添加到核心命令列表
                        aliases = command.get("aliases", [])
                        if aliases:
                            self.core_commands.extend(aliases)
                            self.log_debug(f"为核心命令 {cmd_name} 添加别名: {aliases}")
            else:
                # 如果命令映射文件不存在，创建默认配置
                self.log_warning(f"命令映射文件不存在: {self.command_map_path}，使用默认配置")
                self.commands = []
                
                # 初始化默认核心命令列表
                self.core_commands = [
                    self.cmd_open,        # 开厅
                    self.cmd_close,       # 关厅
                ]
        except Exception as e:
            self.log_error(f"加载命令映射失败: {str(e)}")
            self.commands = []
            
            # 初始化默认核心命令列表
            self.core_commands = [
                self.cmd_open,        # 开厅
                self.cmd_close,       # 关厅
            ]
    
    def _get_command_config(self, command_name: str) -> dict:
        """获取命令配置"""
        for command in self.commands:
            if command.get("name", "") == command_name:
                return command
        return {}
    
    def _get_command_by_name_or_alias(self, cmd_name: str) -> dict:
        """通过名称或别名获取命令配置"""
        for command in self.commands:
            if command.get("name") == cmd_name or cmd_name in command.get("aliases", []):
                return command
        return {}
    
    def _is_command_admin_only(self, command_name: str) -> bool:
        """检查命令是否仅管理员可用"""
        command = self._get_command_by_name_or_alias(command_name)
        return command.get("admin_only", False)
    
    def _parse_parameters(self, command: dict, params_str: str) -> Union[dict, List, str]:
        """解析命令参数
        
        返回:
            成功时返回解析后的参数字典
            失败时返回带有error键的字典
        """
        parameters = command.get("parameters", [])
        self.log_debug(f"解析命令参数: 命令={command.get('name')}, 参数字符串='{params_str}'")
        
        if not parameters:
            # 如果命令没有定义参数，直接返回原始字符串
            self.log_debug(f"命令 {command.get('name')} 没有定义参数，返回原始字符串")
            return params_str
            
        result = {}
        # 简单参数解析，按空格分割
        parts = params_str.split()
        self.log_debug(f"参数分割后: {parts}, 需要参数数量: {len(parameters)}")
        
        # 检查必填参数
        required_count = sum(1 for param in parameters if param.get("required", False))
        if len(parts) < required_count:
            missing = [p["name"] for p in parameters[:required_count] if p.get("required", False)]
            self.log_warning(f"命令 {command.get('name')} 缺少必填参数: {', '.join(missing)}")
            return {"error": f"缺少必填参数: {', '.join(missing)}"}
        
        # 解析参数
        for i, param in enumerate(parameters):
            if i < len(parts):
                param_name = param.get("name", f"param{i}")
                param_value = parts[i]
                
                # 检查参数模式
                pattern = param.get("pattern")
                if pattern and not re.match(pattern, param_value):
                    self.log_warning(f"命令 {command.get('name')} 参数 {param_name}='{param_value}' 不匹配模式 {pattern}")
                    return {"error": f"参数 {param_name} 格式不正确"}
                
                self.log_debug(f"解析参数 {param_name}='{param_value}' 成功")
                result[param_name] = param_value
        
        self.log_info(f"命令 {command.get('name')} 参数解析成功: {result}")
        return result
    
    async def _pre_command_hook(self, cmd: dict, bot, group_id: str, user_id: str) -> bool:
        """命令执行前的钩子"""
        cmd_name = cmd.get("name", "")
        
        # 记录命令使用频率
        if cmd_name not in self.command_stats:
            self.command_stats[cmd_name] = 0
        self.command_stats[cmd_name] += 1
        
        # 检查命令是否需要管理员权限
        if cmd.get("admin_only", False):
            is_admin = await self.is_admin(group_id, user_id)
            if not is_admin:
                await bot.send_text_message(
                    wxid=group_id,
                    content="⚠️ 权限不足，此命令需要管理员权限",
                    at=user_id
                )
                self.log_warning(f"用户 {user_id} 尝试执行管理员命令 {cmd_name} 但没有权限")
                return False
        
        return True
    
    async def _post_command_hook(self, cmd: dict, bot, group_id: str, user_id: str, result: Any) -> None:
        """命令执行后的钩子"""
        # 可以在这里实现命令执行后的通用逻辑
        pass
    
    async def _handle_command_error(self, cmd: dict, bot, group_id: str, user_id: str, error: Exception) -> None:
        """统一错误处理"""
        cmd_name = cmd.get("name", "未知命令")
        self.log_error(f"执行命令 {cmd_name} 失败: {str(error)}")
        
        # 获取用户友好的错误信息
        friendly_error = self._get_user_friendly_error(error)
        
        # 发送错误消息
        await bot.send_text_message(
            wxid=group_id,
            content=f"❌ 执行命令时出错: {friendly_error}",
            at=user_id
        )
    
    def _get_user_friendly_error(self, error: Exception) -> str:
        """获取用户友好的错误信息"""
        error_str = str(error)
        
        # 转换常见错误为用户友好的提示
        if "InvalidParameter" in error_str:
            return "参数格式不正确，请检查输入"
        elif "NotFound" in error_str:
            return "未找到相关记录"
        elif "PermissionDenied" in error_str:
            return "权限不足"
        elif "DatabaseError" in error_str:
            return "数据库操作失败，请稍后重试"
        
        # 防止暴露内部错误细节
        return "处理请求时发生错误，请联系管理员"
    
    # 日志前缀
    LOG_PREFIX = "【开厅记录】"
    
    def log_debug(self, message: str):
        """记录调试日志"""
        logger.debug(f"{self.LOG_PREFIX} {message}")
        
    def log_info(self, message: str):
        """记录信息日志"""
        logger.info(f"{self.LOG_PREFIX} {message}")
        
    def log_success(self, message: str):
        """记录成功日志"""
        logger.success(f"{self.LOG_PREFIX} {message}")
        
    def log_warning(self, message: str):
        """记录警告日志"""
        logger.warning(f"{self.LOG_PREFIX} {message}")
        
    def log_error(self, message: str):
        """记录错误日志"""
        logger.error(f"{self.LOG_PREFIX} {message}")
    
    async def async_init(self, bot=None):
        """异步初始化"""
        if self.enable:
            try:
                # 保存bot引用，用于定时任务发送消息
                self.bot = bot
                self.log_info("已保存bot引用，用于后续定时任务")
                
                # 延迟几秒再设置，确保bot已经初始化完成
                await asyncio.sleep(5)
                await self.setup_all_hall_reminders()
                self.log_info("开厅记录插件已设置所有厅的提醒任务")
            except Exception as e:
                self.log_error(f"设置开厅提醒任务失败: {str(e)}")
    
    @on_text_message(priority=50)
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本消息中的命令"""
        # 如果插件被禁用，不处理任何命令
        if not self.enable:
            return
        
        self.log_debug(f"收到原始消息: {message}")
        
        # 解析消息，支持多种可能的字段名称格式
        # 消息内容支持 "content" 或 "Content"
        content = message.get("content", message.get("Content", ""))
        if not content:
            return
            
        # 群聊ID支持 "room_wxid", "FromWxid" 或 "from_wxid"
        chatroom_id = message.get("room_wxid", message.get("FromWxid", message.get("from_wxid", "")))
        if not chatroom_id:
            return  # 暂不处理私聊消息
            
        # 发送者ID支持 "sender", "SenderWxid" 或 "sender_wxid"
        sender_wxid = message.get("sender", message.get("SenderWxid", message.get("sender_wxid", "")))
        if not sender_wxid:
            return
        
        # 获取用户昵称，支持多种格式
        sender_nickname = message.get("sender_nickname", message.get("NickName", "用户"))
        
        self.log_debug(f"解析消息: 内容={content}, 群聊={chatroom_id}, 发送者={sender_wxid}, 昵称={sender_nickname}")
        
        # 获取用户在群里的昵称
        try:
            # 直接使用API提供的方法获取群昵称
            nickname = await bot.get_chatroom_nickname(chatroom_id, sender_wxid) 
            
        except Exception as e:
            self.log_error(f"获取用户昵称失败: {e}")
            nickname = sender_nickname
            
        self.log_debug(f"获取到群昵称: {nickname} (用户: {sender_wxid})")
        
        # 检查是否是命令
        # 根据消息内容匹配命令
        matched_cmd = None
        cmd_params = ""
        
        # 打印核心命令列表，帮助调试
        self.log_debug(f"核心命令列表: {self.core_commands}")
        
        # 直接匹配核心命令，如"开厅"、"关厅"
        for cmd in self.core_commands:
            # 精确匹配完整的命令（例如消息就是"开厅"或"关厅"）
            if content == cmd:
                self.log_debug(f"精确匹配到核心命令: {cmd}")
                matched_cmd = cmd
                cmd_params = ""
                break
            # 匹配带参数的命令（例如"开厅 xxxx"，"关厅 xxxx"）
            elif content.startswith(cmd + " "):
                self.log_debug(f"匹配到带参数的核心命令: {cmd}")
                matched_cmd = cmd
                cmd_params = content[len(cmd):].strip()
                break
        
        # 如果没有匹配到核心命令，尝试匹配其他命令
        if not matched_cmd:
            self.log_debug("未匹配到核心命令，尝试匹配其他命令")
            for command in self.commands:
                cmd_name = command.get("name", "")
                # 检查命令别名
                aliases = command.get("aliases", [])
                all_names = [cmd_name] + aliases
                
                for name in all_names:
                    # 精确匹配完整的命令
                    if content == name:
                        self.log_debug(f"精确匹配到命令: {name} -> {cmd_name}")
                        matched_cmd = cmd_name
                        cmd_params = ""
                        break
                    # 匹配带参数的命令
                    elif content.startswith(name + " "):
                        self.log_debug(f"匹配到带参数的命令: {name} -> {cmd_name}")
                        matched_cmd = cmd_name
                        cmd_params = content[len(name):].strip()
                        break
                
                if matched_cmd:
                    break
        
        # 如果匹配到命令，执行命令
        if matched_cmd:
            self.log_info(f"匹配到命令: {matched_cmd}, 参数: {cmd_params}")
            
            # 特殊处理：如果匹配到的是核心命令的别名（如"开"、"关"），转换为主命令名
            if matched_cmd in ["开", "营业"]:
                matched_cmd = self.cmd_open
                self.log_debug(f"将别名 {matched_cmd} 转换为主命令 {self.cmd_open}")
            elif matched_cmd in ["关", "停业"]:
                matched_cmd = self.cmd_close
                self.log_debug(f"将别名 {matched_cmd} 转换为主命令 {self.cmd_close}")
            
            # 检查权限
            admin_only = self._is_command_admin_only(matched_cmd)
            if admin_only:
                is_admin = await self.is_admin(chatroom_id, sender_wxid)
                if not is_admin:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content="⚠️ 权限不足，此命令需要管理员权限",
                        at=sender_wxid
                    )
                    return
            
            # 保存bot引用
            self.bot = bot
            
            # 执行命令
            try:
                await self.cmd_handler.execute(matched_cmd, bot, chatroom_id, sender_wxid, nickname, cmd_params)
            except Exception as e:
                self.log_error(f"执行命令 {matched_cmd} 时出错: {str(e)}")
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"❌ 执行命令时出错: {str(e)}",
                    at=sender_wxid
                )
            return
    
    async def is_admin(self, chatroom_id: str, sender_wxid: str) -> bool:
        """检查用户是否是机器人管理员"""
        try:
            # 读取主配置
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            
            # 优先检查超级管理员
            super_admins = main_config.get("XYBot", {}).get("admins", [])
            if sender_wxid in super_admins:
                self.log_debug(f"用户 {sender_wxid} 是超级管理员")
                return True
        except Exception as e:
            self.log_error(f"检查超级管理员权限失败: {str(e)}")
            return False
    
    @require_admin
    async def enable_kaiting(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """启用开厅记录功能"""
        try:
            # 设置群组启用状态
            self.db.enable_group_kaiting(chatroom_id)
            
            # 启用后，为所有厅设置提醒任务
            await self.setup_all_hall_reminders()  # 修复：不传入chatroom_id参数
            
            await bot.send_text_message(
                wxid=chatroom_id,
                content="✅ 已启用开厅记录功能"
            )
            self.log_info(f"群组 {chatroom_id} 已启用开厅记录功能，操作者: {nickname}")
            return True
        except Exception as e:
            self.log_error(f"启用开厅记录功能失败: {str(e)}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content="❌ 启用开厅记录功能失败",
                at=sender_wxid
            )
            return False
    
    @require_admin
    async def disable_kaiting(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """禁用开厅记录功能"""
        try:
            # 设置群组启用状态
            self.db.disable_group_kaiting(chatroom_id)
            await bot.send_text_message(
                wxid=chatroom_id,
                content="⛔ 已禁用开厅记录功能"
            )
            self.log_info(f"群组 {chatroom_id} 已禁用开厅记录功能，操作者: {nickname}")
            return True
        except Exception as e:
            self.log_error(f"禁用开厅记录功能失败: {str(e)}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content="❌ 禁用开厅记录功能失败",
                at=sender_wxid
            )
            return False
    
    async def register_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, 
                          nickname: str, hall_name: str):
        """处理厅名注册命令"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(
                wxid=chatroom_id,
                content="⚠️ 本群未启用开厅记录功能，请联系管理员启用",
                at=sender_wxid
            )
            return
        
        # 注册厅名
        success, result = self.db.register_hall(
            hall_name=hall_name,
            owner_wxid=sender_wxid,
            owner_nickname=nickname,
            chatroom_id=chatroom_id
        )
        
        if success:
            # 注册成功，result是厅唯一ID
            hall_id = result
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"✅ {nickname} 厅名 '{hall_name}' 注册成功！\n🆔 厅唯一ID: {hall_id}\n⚠️ 请妥善保管您的厅唯一ID，可用于后续管理操作",
                at=sender_wxid
            )
            self.log_success(f"用户 {nickname} 在群 {chatroom_id} 成功注册厅名: {hall_name}, 厅ID: {hall_id}")
        else:
            # 注册失败，result是错误信息
            error_msg = result
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 厅名注册失败: {error_msg}",
                at=sender_wxid
            )
            self.log_warning(f"用户 {nickname} 在群 {chatroom_id} 注册厅名失败: {error_msg}")
    
    async def set_hall_time(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, 
                          nickname: str, hall_name: str, open_time_start: datetime.time, 
                          open_time_end: datetime.time):
        """设置厅开厅时间"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        # 获取厅信息
        hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
        if not hall_info:
            await bot.send_text_message(chatroom_id, f"❌ 未找到厅名 '{hall_name}'")
            return
        
        # 检查是否为厅主或管理员
        if hall_info["owner_wxid"] != sender_wxid and not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限设置此厅的开厅时间")
            return
        
        # 设置开厅时间
        success, message = self.db.set_hall_time(hall_name, chatroom_id, open_time_start, open_time_end)
        
        if success:
            time_start_str = open_time_start.strftime("%H:%M")
            time_end_str = open_time_end.strftime("%H:%M")
            await bot.send_text_message(
                chatroom_id, 
                f"✅ 厅名 '{hall_name}' 的开厅时间已设置为 {time_start_str}-{time_end_str}"
            )
            
            # 更新提醒任务
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if hall_info:
                # 先取消原有任务
                await self.cancel_hall_reminder(chatroom_id, hall_name)
                # 设置新任务
                await self.setup_hall_reminder(chatroom_id, hall_info)
        else:
            await bot.send_text_message(chatroom_id, f"❌ {message}")
    
    async def set_temp_time(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, 
                          nickname: str, hall_name: str, open_time_start: datetime.time, 
                          open_time_end: datetime.time):
        """设置厅临时开厅时间"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        # 获取厅信息
        hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
        if not hall_info:
            await bot.send_text_message(chatroom_id, f"❌ 未找到厅名 '{hall_name}'")
            return
        
        # 检查是否为厅主或管理员
        if hall_info["owner_wxid"] != sender_wxid and not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限设置此厅的临时开厅时间")
            return
        
        # 设置临时开厅时间
        today = datetime.date.today()
        success, message = self.db.set_temp_time(hall_name, chatroom_id, open_time_start, open_time_end, today)
        
        if success:
            time_start_str = open_time_start.strftime("%H:%M")
            time_end_str = open_time_end.strftime("%H:%M")
            today_str = today.strftime("%Y-%m-%d")
            await bot.send_text_message(
                chatroom_id, 
                f"✅ 厅名 '{hall_name}' 的临时开厅时间已设置为 {time_start_str}-{time_end_str} (仅今日 {today_str} 有效)"
            )
            
            # 更新提醒任务
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if hall_info:
                # 先取消原有任务
                await self.cancel_hall_reminder(chatroom_id, hall_name)
                # 设置新任务
                await self.setup_hall_reminder(chatroom_id, hall_info)
        else:
            await bot.send_text_message(chatroom_id, f"❌ {message}")
    
    async def set_required_duration(self, bot: WechatAPIClient, chatroom_id: str, 
                                  sender_wxid: str, nickname: str, duration: int):
        """设置群聊要求开厅时长"""
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
        
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请先启用")
            return
        
        # 设置要求时长
        if self.db.set_required_duration(chatroom_id, duration):
            hours = duration // 60
            minutes = duration % 60
            time_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
            await bot.send_text_message(
                chatroom_id, 
                f"✅ 本群要求开厅时长已设置为 {time_str} (by {nickname})"
            )
        else:
            await bot.send_text_message(chatroom_id, "❌ 设置要求开厅时长失败，请联系管理员")
    
    async def open_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """处理开厅命令"""
        self.log_debug(f"执行开厅命令: nickname={nickname}, params={params}")
        
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        # 获取用户厅信息
        halls_info = self.db.get_halls_by_owner(chatroom_id, sender_wxid)
        self.log_debug(f"用户 {nickname} 的厅信息: {halls_info}")
        
        if not halls_info:
            await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您尚未注册厅名，请先使用 '{self.cmd_register} 厅名' 命令注册")
            return
        
        # 检查是否提供了厅名参数
        hall_name = params.get("厅名", "") if isinstance(params, dict) else params
        self.log_debug(f"解析到的厅名参数: {hall_name}")
        
        # 如果提供了厅名，直接开指定的厅
        if hall_name:
            self.log_debug(f"用户 {nickname} 尝试开启指定厅: {hall_name}")
            # 查找这个厅名是否存在且属于该用户
            hall_exists = False
            for hall in halls_info:
                if hall["hall_name"] == hall_name:
                    hall_exists = True
                    # 检查该厅是否已经开启
                    if hall.get("is_active", False):
                        await bot.send_text_message(
                            chatroom_id,
                            f"⚠️ {nickname} 您的厅「{hall_name}」已经处于开厅状态"
                        )
                        return
                    break
            
            if not hall_exists:
                await bot.send_text_message(
                    chatroom_id,
                    f"⚠️ {nickname} 您名下没有「{hall_name}」这个厅"
                )
                return
            
            # 执行开厅操作
            await self._do_open_hall(bot, chatroom_id, sender_wxid, nickname, hall_name)
            return
            
        # 如果用户只有一个厅，直接开厅
        if len(halls_info) == 1:
            hall_info = halls_info[0]
            self.log_debug(f"用户 {nickname} 只有一个厅，直接开厅: {hall_info['hall_name']}")
            await self._do_open_hall(bot, chatroom_id, sender_wxid, nickname, hall_info["hall_name"])
            return
        
        # 如果用户有多个厅但没有指定厅名，展示所有厅名
        inactive_halls = [hall for hall in halls_info if not hall.get("is_active")]
        self.log_debug(f"用户 {nickname} 的未开厅列表: {inactive_halls}")
        
        # 如果所有厅都已开，提示用户
        if not inactive_halls:
            all_halls_names = ", ".join([f"「{hall['hall_name']}」" for hall in halls_info])
            await bot.send_text_message(
                chatroom_id,
                f"⚠️ {nickname} 您的所有厅 {all_halls_names} 都已开厅"
            )
            return
        
        # 列出用户的所有未开厅
        msg = f"📋 {nickname} 您有以下可开的厅，请使用「开厅 厅名」命令开厅：\n\n"
        for i, hall in enumerate(inactive_halls):
            hall_name = hall['hall_name']
            
            # 获取开厅时间信息
            if hall.get("has_temp_time", False):
                temp_time_start = hall.get("temp_time_start")
                temp_time_end = hall.get("temp_time_end")
                time_start = temp_time_start.strftime("%H:%M") if temp_time_start else "未设置"
                time_end = temp_time_end.strftime("%H:%M") if temp_time_end else "未设置"
                time_type = "临时"
            else:
                regular_time_start = hall.get("regular_time_start")
                regular_time_end = hall.get("regular_time_end")
                time_start = regular_time_start.strftime("%H:%M") if regular_time_start else "未设置"
                time_end = regular_time_end.strftime("%H:%M") if regular_time_end else "未设置"
                time_type = "固定"
                
            msg += f"{i+1}. {hall_name} ({time_type}时段: {time_start}-{time_end})\n"
        
        await bot.send_text_message(chatroom_id, msg)
    
    async def _do_open_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, hall_name: str):
        """执行开厅操作"""
        try:
            self.log_debug(f"开始执行开厅操作: 厅名={hall_name}, 用户={nickname}")
            
            # 检查群是否启用开厅记录
            if not self.db.is_group_kaiting_enabled(chatroom_id):
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="⚠️ 该群未启用开厅记录功能，请管理员先启用",
                    at=sender_wxid
                )
                return False
                
            # 获取厅信息，验证厅名和所有者
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if not hall_info:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"❌ 未找到厅 '{hall_name}'，请先注册厅名",
                    at=sender_wxid
                )
                return False
                
            # 检查是否为厅主
            owner_wxid = hall_info.get("owner_wxid")
            if owner_wxid != sender_wxid:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"⚠️ 只有厅主才能操作自己的厅",
                    at=sender_wxid
                )
                return False
                
            # 检查厅是否已经开启
            if hall_info.get("is_active"):
                # 计算已开厅时间
                open_date = hall_info.get("open_date")
                open_time = hall_info.get("open_time")
                
                if open_date and open_time:
                    open_datetime = datetime.datetime.combine(open_date, open_time)
                    now = datetime.datetime.now()
                    
                    # 计算已经开厅的时间（分钟）
                    minutes_open = int((now - open_datetime).total_seconds() / 60)
                    
                    # 格式化为小时和分钟
                    hours = minutes_open // 60
                    mins = minutes_open % 60
                    time_str = f"{hours}小时{mins}分钟" if hours > 0 else f"{mins}分钟"
                    
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 您的厅 '{hall_name}' 已经在{open_time.strftime('%H:%M')}开始营业，已持续{time_str}",
                        at=sender_wxid
                    )
                else:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 您的厅 '{hall_name}' 已经在营业中",
                        at=sender_wxid
                    )
                return False
            
            # 执行开厅操作
            success, result = self.db.open_hall(
                hall_name=hall_name,
                owner_wxid=sender_wxid,
                owner_nickname=nickname,
                chatroom_id=chatroom_id
            )
            
            if not success:
                # 处理特定错误
                if result.get("already_opened", False):
                    prev_hall = result.get("hall_name", "未知厅")
                    prev_time = result.get("open_time", "未知时间")
                    
                    if isinstance(prev_time, datetime.time):
                        prev_time = prev_time.strftime("%H:%M")
                        
                    # 判断是否是同一个厅
                    if prev_hall == hall_name:
                        msg = f"⚠️ 您的厅 '{hall_name}' 已在{prev_time}开始营业"
                    else:
                        msg = f"⚠️ 您已有厅 '{prev_hall}' 在{prev_time}开始营业，请先关闭该厅"
                        
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=msg,
                        at=sender_wxid
                    )
                else:
                    error_msg = result.get("error", "未知错误")
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"❌ 开厅失败: {error_msg}",
                        at=sender_wxid
                    )
                return False
            
            # 开厅成功
            hall_name = result.get("hall_name", hall_name)
            open_time = result.get("open_time", datetime.datetime.now().time())
            is_late = result.get("is_late", False)
            late_minutes = result.get("late_minutes", 0)
            
            # 获取开厅时间信息
            open_time_str = open_time.strftime("%H:%M")
            close_time_expected = result.get("open_time_end", None)
            close_time_str = close_time_expected.strftime("%H:%M") if close_time_expected else "未知"
            
            # 确定是临时时间还是固定时间
            # 修复：使用has_temp_time而不是is_temp_time，或者添加兼容代码
            is_temp_time = hall_info.get("has_temp_time", False)
            time_type = "临时" if is_temp_time else "默认"
            
            # 记录开厅ID，用于提醒和关厅
            record_id = result.get("record_id", 0)
            
            # 组织通知消息
            message = f"✅ 厅 '{hall_name}' 已开始营业\n"
            message += f"⏰ 开始时间: {open_time_str}\n"
            message += f"⌛ 预计结束: {close_time_str} ({time_type}时间表)\n"
            
            # 如果迟到，显示迟到信息
            if is_late and late_minutes > 0:
                message += f"⚠️ 已迟到: {late_minutes} 分钟\n"
            
            # 设置提醒（提醒关厅）
            if close_time_expected:
                # 计算距离关厅还有多少时间
                now = datetime.datetime.now()
                today = datetime.date.today()
                close_datetime = datetime.datetime.combine(today, close_time_expected)
                
                # 如果关厅时间已经过了，可能是跨天的情况
                if close_datetime < now:
                    close_datetime = datetime.datetime.combine(today + datetime.timedelta(days=1), close_time_expected)
                
                # 计算距离关厅的分钟数
                minutes_until_close = int((close_datetime - now).total_seconds() / 60)
                
                if minutes_until_close > 0:
                    hours = minutes_until_close // 60
                    mins = minutes_until_close % 60
                    time_left = f"{hours}小时{mins}分钟" if hours > 0 else f"{mins}分钟"
                    message += f"🔔 距离关厅时间还有: {time_left}\n"
                    
                    # 建立提醒
                    if hours >= 1:
                        # 如果超过1小时，设置提前30分钟提醒
                        await self.setup_hall_reminder(chatroom_id, {
                            "hall_name": hall_name,
                            "owner_wxid": sender_wxid,
                            "owner_nickname": nickname,
                            "open_time_end": close_time_expected,
                            "record_id": record_id
                        })
            
            # 发送开厅通知
            await bot.send_text_message(
                wxid=chatroom_id,
                content=message,
                at=sender_wxid
            )
            
            return True
        except Exception as e:
            self.log_error(f"开厅操作执行失败: {str(e)}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 开厅操作执行失败: {str(e)}",
                at=sender_wxid
            )
            return False
    
    async def close_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """处理关厅命令"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        try:
            # 获取用户当前开着的所有厅
            session = self.db.DBSession()
            active_records = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.owner_wxid == sender_wxid,
                HallRecord.is_active == True
            ).all()
            
            # 如果没有开着的厅，提示用户
            if not active_records:
                await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您当前没有进行中的开厅记录")
                return
                
            # 检查是否提供了厅名参数
            hall_name = params.get("厅名", "") if isinstance(params, dict) else params
            
            # 如果提供了厅名，直接关闭指定的厅
            if hall_name:
                # 查找这个厅是否存在且处于开启状态
                hall_record = None
                for record in active_records:
                    if record.hall_name == hall_name:
                        hall_record = record
                        break
                
                if not hall_record:
                    await bot.send_text_message(
                        chatroom_id,
                        f"⚠️ {nickname} 您名下没有处于开厅状态的「{hall_name}」厅"
                    )
                    return
                
                # 执行关厅操作
                # 更新记录状态
                record.is_active = False
                record.close_time = datetime.datetime.now().time()
                
                # 计算开厅时长
                open_datetime = datetime.datetime.combine(record.open_date, record.open_time)
                close_datetime = datetime.datetime.combine(datetime.date.today(), record.close_time)
                
                # 计算实际差值
                duration_minutes = int((close_datetime - open_datetime).total_seconds() / 60)
                record.duration = duration_minutes
                
                # 添加跨天标记
                record.is_cross_day = close_datetime.date() > open_datetime.date()
                
                # 保存记录
                session.commit()
                
                # 构建回复消息
                open_time_str = record.open_time.strftime("%H:%M:%S")
                close_time_str = record.close_time.strftime("%H:%M:%S")
                
                # 格式化时长
                if duration_minutes < 60:
                    duration_str = f"{duration_minutes}分钟"
                else:
                    hours = duration_minutes // 60
                    minutes = duration_minutes % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                # 构建回复消息
                reply_msg = f"✅ {nickname} 关厅成功！\n"
                reply_msg += f"📝 厅名: {hall_name}\n"
                reply_msg += f"⏰ 开厅时间: {open_time_str}\n"
                reply_msg += f"⏰ 关厅时间: {close_time_str}"
                
                # 如果是跨天，添加跨天提示
                if record.is_cross_day:
                    reply_msg += " (次日)"
                
                reply_msg += f"\n⌛ 开厅时长: {duration_str}"
                
                await bot.send_text_message(chatroom_id, reply_msg)
                return
            
            # 如果只有一个开着的厅，直接关闭
            if len(active_records) == 1:
                # 关闭厅 - 使用数据库方法
                success, data = self.db.close_hall(chatroom_id, sender_wxid)
                
                if not success:
                    if data.get("not_opened"):
                        # 未开厅
                        await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您当前没有进行中的开厅记录")
                    else:
                        # 其他错误
                        await bot.send_text_message(chatroom_id, f"❌ 关厅失败: {data.get('error', '未知错误')}")
                    return
                
                # 关厅成功，构建回复消息
                hall_name = data.get("hall_name")
                open_time = data.get("open_time")
                close_time = data.get("close_time")
                is_cross_day = data.get("is_cross_day", False)
                
                open_time_str = open_time.strftime("%H:%M:%S")
                close_time_str = close_time.strftime("%H:%M:%S")
                
                # 格式化时长
                if data.get("duration") < 60:
                    duration_str = f"{data.get('duration')}分钟"
                else:
                    hours = data.get('duration') // 60
                    minutes = data.get('duration') % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                # 构建回复消息
                reply_msg = f"✅ {nickname} 关厅成功！\n"
                reply_msg += f"📝 厅名: {hall_name}\n"
                reply_msg += f"⏰ 开厅时间: {open_time_str}\n"
                reply_msg += f"⏰ 关厅时间: {close_time_str}"
                
                # 如果是跨天，添加跨天提示
                if is_cross_day:
                    reply_msg += " (次日)"
                
                reply_msg += f"\n⌛ 开厅时长: {duration_str}"
                
                await bot.send_text_message(chatroom_id, reply_msg)
                return
            
            # 如果有多个开着的厅，列出所有正在开的厅
            msg = f"📋 {nickname} 您有以下正在开着的厅，请使用「关厅 厅名」命令关厅：\n\n"
            
            for i, record in enumerate(active_records):
                hall_name = record.hall_name
                open_time = record.open_time.strftime("%H:%M")
                
                # 计算已开厅时长
                open_datetime = datetime.datetime.combine(record.open_date, record.open_time)
                now = datetime.datetime.now()
                current_duration = int((now - open_datetime).total_seconds() / 60)
                
                # 格式化时长
                if current_duration < 60:
                    duration_str = f"{current_duration}分钟"
                else:
                    hours = current_duration // 60
                    minutes = current_duration % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                msg += f"{i+1}. {hall_name} (开厅时间: {open_time}, 已开厅: {duration_str})\n"
            
            await bot.send_text_message(chatroom_id, msg)
            
        except Exception as e:
            self.log_error(f"查询用户开厅记录失败: {e}")
            await bot.send_text_message(chatroom_id, f"❌ 查询开厅记录失败: {str(e)}")
        finally:
            session.close()
    
    async def list_halls(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """处理列出厅名命令"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        # 获取所有厅信息
        halls = self.db.get_all_halls(chatroom_id)
        
        if not halls:
            await bot.send_text_message(chatroom_id, f"📋 本群尚未注册任何厅名")
            return
        
        # 构建回复消息
        reply_msg = f"📋 本群已注册厅名列表 ({len(halls)}个):\n"
        reply_msg += f"━━━━━━━━━━━━━━━━\n"
        
        for i, hall in enumerate(halls):
            # 获取时间信息
            register_time = hall.get("register_time").strftime("%Y-%m-%d")
            reg_time_start = hall.get("regular_time_start").strftime("%H:%M")
            reg_time_end = hall.get("regular_time_end").strftime("%H:%M")
            
            status = "⚪ 未开厅"
            if hall.get("is_active"):
                status = "🟢 已开厅"
                open_time = hall.get("open_time").strftime("%H:%M:%S")
                status += f" ({open_time})"
            
            # 显示各厅信息
            reply_msg += f"{i+1}. {hall.get('hall_name')} - {status}\n"
            reply_msg += f"   👤 厅主: {hall.get('owner_nickname')}\n"
            
            # 显示厅ID，如果有的话
            hall_id = hall.get("hall_id")
            if hall_id:
                reply_msg += f"   🆔 厅ID: {hall_id}\n"
            
            # 显示时间段
            if hall.get("has_temp_time"):
                temp_start = hall.get("temp_time_start").strftime("%H:%M")
                temp_end = hall.get("temp_time_end").strftime("%H:%M")
                reply_msg += f"   ⏰ 今日时间: {temp_start}-{temp_end}\n"
            else:
                reply_msg += f"   ⏰ 固定时间: {reg_time_start}-{reg_time_end}\n"
        
        await bot.send_text_message(chatroom_id, reply_msg)
    
    async def show_help(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """显示帮助信息"""
        try:
            # 检查用户是否是管理员
            is_admin = await self.is_admin(chatroom_id, sender_wxid)
            
            # 构建帮助消息
            help_text = "📋 开厅记录插件使用说明\n\n"
            
            # 收集可见命令
            visible_commands = []
            for command in self.commands:
                # 跳过隐藏命令
                if command.get("hidden", False):
                    continue
                
                # 根据权限过滤命令
                if command.get("admin_only", False) and not is_admin:
                    continue
                    
                visible_commands.append(command)
            
            # 按分组整理命令
            command_groups = {}
            for command in visible_commands:
                group = command.get("group", "其他")
                if group not in command_groups:
                    command_groups[group] = []
                command_groups[group].append(command)
            
            # 分组顺序
            group_order = ["基础命令", "厅管理命令", "提醒管理", "管理命令", "其他"]
            
            # 按分组显示
            for group in group_order:
                if group in command_groups and command_groups[group]:
                    help_text += f"🔹 {group}:\n"
                    for cmd in command_groups[group]:
                        cmd_name = cmd.get("name", "")
                        cmd_desc = cmd.get("description", "")
                        cmd_usage = cmd.get("usage", "")
                        
                        # 显示别名
                        aliases = cmd.get("aliases", [])
                        alias_text = f"(别名: {', '.join(aliases)})" if aliases else ""
                        
                        help_text += f"  • {cmd_usage}: {cmd_desc} {alias_text}\n"
                    help_text += "\n"
            
            # 添加提示
            help_text += "💡 提示: 命令可以使用别名，例如\"开厅记录帮助\"可以简写为\"开厅帮助\"。"
            
            # 发送帮助消息
            await bot.send_text_message(
                wxid=chatroom_id,
                content=help_text,
                at=sender_wxid
            )
            self.log_info(f"向用户 {nickname}({sender_wxid}) 发送开厅记录插件帮助信息")
            return True
        except Exception as e:
            self.log_error(f"显示帮助信息失败: {str(e)}")
            # 发送简化版帮助信息
            await bot.send_text_message(
                wxid=chatroom_id,
                content="显示帮助信息时出错，请联系管理员。",
                at=sender_wxid
            )
            return False
    
    async def query_hall_records(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, 
                           nickname: str, month: int = None, day: int = None):
        """查询指定日期的开厅记录"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        # 处理日期
        if month and day:
            # 使用指定日期
            try:
                today = datetime.date.today()
                query_date = datetime.date(today.year, month, day)
                date_str = query_date.strftime("%Y年%m月%d日")
            except ValueError:
                await bot.send_text_message(chatroom_id, f"⚠️ 日期格式错误：{month}-{day}，请使用正确的月份(1-12)和日期(1-31)")
                return
        else:
            # 使用当前日期
            query_date = datetime.date.today()
            date_str = "今日"
        
        # 获取厅记录
        hall_records = self.db.get_hall_records(chatroom_id, date=query_date)
        
        # 获取临时时间设置
        temp_time_records = self.db.get_temp_time_records(chatroom_id, date=query_date)
        
        # 分类记录
        active_records = []
        completed_records = []
        for record in hall_records:
            if record["is_active"]:
                active_records.append(record)
            else:
                completed_records.append(record)
        
        # 构建回复消息
        reply_msg = f"📊 {date_str}开厅记录统计\n"
        reply_msg += f"━━━━━━━━━━━━━━━━\n"
        
        # 1. 显示正在进行中的开厅
        if active_records:
            reply_msg += f"🟢 进行中的开厅 ({len(active_records)}个):\n"
            for i, record in enumerate(active_records):
                hall_name = record["hall_name"]
                nickname = record["owner_nickname"]
                open_time = record["open_time"].strftime("%H:%M:%S")
                
                # 计算已开厅时长
                open_datetime = datetime.datetime.combine(query_date, record["open_time"])
                now = datetime.datetime.now()
                current_duration = int((now - open_datetime).total_seconds() / 60)
                
                # 格式化时长
                if current_duration < 60:
                    duration_str = f"{current_duration}分钟"
                else:
                    hours = current_duration // 60
                    minutes = current_duration % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                # 迟到信息
                late_str = ""
                if record["is_late"]:
                    late_minutes = record["late_minutes"]
                    if late_minutes < 60:
                        late_str = f"(迟到{late_minutes}分钟)"
                    else:
                        late_hours = late_minutes // 60
                        late_min = late_minutes % 60
                        late_str = f"(迟到{late_hours}小时{late_min}分钟)" if late_min > 0 else f"(迟到{late_hours}小时)"
                
                reply_msg += f"{i+1}. {hall_name} - {nickname}\n"
                reply_msg += f"   ⏰ 开厅: {open_time} {late_str}\n"
                reply_msg += f"   ⌛ 已开厅时长: {duration_str}\n"
                
                if i < len(active_records) - 1:
                    reply_msg += f"   -----------------\n"
            
            reply_msg += "\n"
        
        # 2. 显示已完成的开厅
        if completed_records:
            reply_msg += f"✅ 已完成的开厅 ({len(completed_records)}个):\n"
            
            # 按关厅时间排序，最近关厅的排在前面
            completed_records.sort(key=lambda x: x["close_time"] if x["close_time"] else datetime.time(0, 0), reverse=True)
            
            for i, record in enumerate(completed_records):
                hall_name = record["hall_name"]
                nickname = record["owner_nickname"]
                open_time = record["open_time"].strftime("%H:%M:%S")
                close_time = record["close_time"].strftime("%H:%M:%S") if record["close_time"] else "未关厅"
                
                # 跨天标记
                is_cross_day = record.get("is_cross_day", False)
                cross_day_mark = " (次日)" if is_cross_day else ""
                
                # 时长信息
                duration = record["duration"] or 0
                if duration < 60:
                    duration_str = f"{duration}分钟"
                else:
                    hours = duration // 60
                    minutes = duration % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                # 迟到信息
                late_str = ""
                if record["is_late"]:
                    late_minutes = record["late_minutes"] or 0
                    if late_minutes < 60:
                        late_str = f"(迟到{late_minutes}分钟)"
                    else:
                        late_hours = late_minutes // 60
                        late_min = late_minutes % 60
                        late_str = f"(迟到{late_hours}小时{late_min}分钟)" if late_min > 0 else f"(迟到{late_hours}小时)"
                
                reply_msg += f"{i+1}. {hall_name} - {nickname}\n"
                reply_msg += f"   ⏰ 开厅: {open_time} {late_str}\n"
                reply_msg += f"   ⏰ 关厅: {close_time}{cross_day_mark}\n"
                reply_msg += f"   ⌛ 时长: {duration_str}\n"
                
                if i < len(completed_records) - 1:
                    reply_msg += f"   -----------------\n"
            
            reply_msg += "\n"
        
        # 如果没有记录，显示提示信息
        if not active_records and not completed_records:
            reply_msg += f"📭 暂无开厅记录\n\n"
        
        # 3. 显示临时时间设置
        if temp_time_records:
            reply_msg += f"⚡ 临时时间报备 ({len(temp_time_records)}个):\n"
            
            for i, temp in enumerate(temp_time_records):
                hall_name = temp["hall_name"]
                nickname = temp["owner_nickname"]
                
                # 格式化时间
                temp_start = temp["temp_time_start"].strftime("%H:%M")
                temp_end = temp["temp_time_end"].strftime("%H:%M")
                regular_start = temp["regular_time_start"].strftime("%H:%M")
                regular_end = temp["regular_time_end"].strftime("%H:%M")
                
                reply_msg += f"{i+1}. {hall_name} - {nickname}\n"
                reply_msg += f"   📅 固定时间段: {regular_start}-{regular_end}\n"
                reply_msg += f"   🔄 临时时间段: {temp_start}-{temp_end}\n"
                
                if i < len(temp_time_records) - 1:
                    reply_msg += f"   -----------------\n"
            
            reply_msg += "\n"
        
        # 4. 统计信息
        reply_msg += f"📋 统计信息:\n"
        reply_msg += f"   总开厅数: {len(hall_records)}个\n"
        reply_msg += f"   进行中: {len(active_records)}个\n"
        reply_msg += f"   已完成: {len(completed_records)}个\n"
        reply_msg += f"   临时时间报备: {len(temp_time_records)}个\n"
        
        # 添加查询日期
        if month and day:
            reply_msg += f"\n💡 查询日期: {date_str}"
        
        await bot.send_text_message(chatroom_id, reply_msg)
    
    def _parse_time_string(self, time_str: str) -> datetime.time:
        """
        解析时间字符串为datetime.time对象
        支持格式：
        - 整数小时: "18"
        - 小时.分钟: "18.30", "14.51"
        - 小时:分钟: "18:30", "14:51"
        - 带两位小数: "17.00", "19.00"
        """
        self.log_debug(f"解析时间字符串: '{time_str}'")
        
        # 检查是否包含冒号（标准时间格式）
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) != 2:
                raise ValueError("时间格式不正确，应为'小时:分钟'格式，例如'18:30'")
            
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                raise ValueError("小时和分钟必须为数字")
            
            # 检查时间范围
            if not (0 <= hour < 24):
                raise ValueError("小时必须在0-23之间")
            if not (0 <= minute < 60):
                raise ValueError("分钟必须在0-59之间")
                
        elif "." in time_str:
            # 处理小时.分钟格式
            parts = time_str.split(".")
            if len(parts) != 2:
                raise ValueError("时间格式不正确，应为'小时.分钟'格式，例如'18.30'")
            
            try:
                hour = int(parts[0])
                minute_str = parts[1]
            except ValueError:
                raise ValueError("小时和分钟必须为数字")
            
            # 特殊处理17.00这种格式，将.00识别为0分钟
            if minute_str == "00" or minute_str == "0":
                minute = 0
            else:
                # 确保正确处理分钟部分
                try:
                    # 如果分钟部分是1位或2位，直接解析
                    if len(minute_str) <= 2:
                        minute = int(minute_str)
                        # 如果是1位数，认为是十位数（.5表示50分钟）
                        if len(minute_str) == 1:
                            minute = minute * 10
                    # 如果超过2位，只取前两位
                    else:
                        minute = int(minute_str[:2])
                except ValueError:
                    raise ValueError("分钟必须为数字")
            
            # 检查时间范围
            if not (0 <= hour < 24):
                raise ValueError("小时必须在0-23之间")
            if not (0 <= minute < 60):
                raise ValueError("分钟必须在0-59之间")
                
        else:
            # 处理整数小时格式
            try:
                hour = int(time_str)
                minute = 0
            except ValueError:
                raise ValueError("小时必须为数字")
            
            # 检查小时范围
            if not (0 <= hour < 24):
                raise ValueError("小时必须在0-23之间")
        
        self.log_debug(f"时间解析结果: {hour}:{minute}")
        return datetime.time(hour, minute)
    
    async def setup_all_hall_reminders(self):
        """设置所有的厅提醒任务"""
        try:
            # 获取所有启用了开厅记录的群
            groups = self.db._get_enabled_groups()
            
            for group in groups:
                chatroom_id = group["chatroom_id"]
                self.log_info(f"正在为群 {chatroom_id} 设置厅提醒任务...")
                
                try:
                    # 获取该群的所有厅
                    halls = self.db.get_all_halls(chatroom_id)  # 修正：使用公共方法get_all_halls
                    hall_names = [h["hall_name"] for h in halls]
                    self.log_info(f"已为群 {chatroom_id} 的 {len(hall_names)} 个厅设置提醒任务")
                    
                    # 为每个厅设置今天的提醒任务
                    for hall in halls:
                        hall_name = hall["hall_name"]
                        try:
                            # 直接传递厅信息对象
                            await self.setup_hall_reminder(chatroom_id, hall)
                        except Exception as e:
                            # 捕获单个厅设置提醒失败的异常，继续处理其他厅
                            self.log_error(f"为厅 {hall_name} 设置提醒任务失败: {str(e)}")
                            continue
                except Exception as e:
                    # 捕获获取单个群厅列表失败的异常
                    if "is_cross_day" in str(e):
                        self.log_warning(f"群 {chatroom_id} 的数据库结构存在问题，可能是is_cross_day字段缺失")
                        # 尝试使用兼容模式获取厅列表
                        try:
                            halls = self._get_halls_compatible_mode(chatroom_id)
                            self.log_info(f"兼容模式: 已为群 {chatroom_id} 找到 {len(halls)} 个厅")
                            
                            # 为每个厅设置今天的提醒任务
                            for hall in halls:
                                hall_name = hall["hall_name"]
                                try:
                                    # 直接传递厅信息对象，而不是仅传递厅名
                                    await self.setup_hall_reminder_compatible(chatroom_id, hall)
                                except Exception as e2:
                                    self.log_error(f"兼容模式: 为厅 {hall_name} 设置提醒任务失败: {str(e2)}")
                                    continue
                        except Exception as e3:
                            self.log_error(f"兼容模式获取厅列表失败: {str(e3)}")
                    else:
                        self.log_error(f"获取群 {chatroom_id} 的厅列表失败: {str(e)}")
                    continue
        except Exception as e:
            self.log_error(f"设置提醒任务失败: {str(e)}")
    
    def _get_halls_compatible_mode(self, chatroom_id):
        """兼容模式获取厅列表，不依赖is_cross_day字段"""
        # 直接使用SQLite查询获取厅列表
        session = self.db.DBSession()
        try:
            # 使用兼容的SQL查询，不包含is_cross_day字段
            from sqlalchemy import text
            sql = text("""
            SELECT id, hall_name, owner_wxid, owner_nickname, chatroom_id, 
                   open_date, open_time, close_time, duration, is_late, 
                   late_minutes, is_active
            FROM hall_record
            WHERE chatroom_id = :chatroom_id AND open_date = :today AND is_active = 1
            """)
            
            import datetime
            today = datetime.date.today()
            result = session.execute(sql, {"chatroom_id": chatroom_id, "today": today})
            
            halls = []
            for row in result:
                # 构建不包含is_cross_day的hall对象
                hall = {
                    "id": row[0],
                    "hall_name": row[1],
                    "owner_wxid": row[2],
                    "owner_nickname": row[3],
                    "chatroom_id": row[4],
                    "open_date": row[5],
                    "open_time": row[6],
                    "close_time": row[7],
                    "duration": row[8],
                    "is_late": row[9],
                    "late_minutes": row[10],
                    "is_active": row[11]
                }
                halls.append(hall)
            
            return halls
        finally:
            session.close()
    
    async def setup_hall_reminder_compatible(self, chatroom_id, hall_info):
        """兼容模式设置厅提醒任务，不使用is_cross_day字段"""
        # 此处实现兼容模式的厅提醒设置逻辑
        # 简化实现，直接复用原方法但捕获is_cross_day相关异常
        try:
            await self.setup_hall_reminder(chatroom_id, hall_info)
        except Exception as e:
            if "is_cross_day" in str(e):
                # 从hall_info中获取必要信息
                hall_name = hall_info.get("hall_name", "未知厅")
                owner_wxid = hall_info.get("owner_wxid", "")
                owner_nickname = hall_info.get("owner_nickname", "未知用户")
                
                # 尝试获取开厅时间
                open_time = None
                if "open_time" in hall_info:
                    open_time = hall_info["open_time"]
                
                # 如果没有open_time，尝试获取厅注册信息
                if not open_time:
                    hall_reg = self.db.get_hall_by_name(chatroom_id, hall_name)
                    if hall_reg:
                        open_time = hall_reg.get("open_time_start")
                
                if not open_time:
                    self.log_warning(f"兼容模式: 找不到厅 {hall_name} 的开厅时间")
                    return
                
                # 计算提醒时间，不考虑is_cross_day
                import datetime
                today = datetime.date.today()
                
                # 计算提醒时间
                reminder_datetime = datetime.datetime.combine(today, open_time) - \
                                   datetime.timedelta(minutes=self.remind_before_minutes)
                
                # 设置定时任务
                await self._send_hall_time_reminder(
                    chatroom_id, hall_name, owner_wxid, owner_nickname, open_time, "常规"
                )
            else:
                raise
    
    async def update_all_hall_reminders(self):
        """更新所有厅的提醒任务，用于设置修改后更新"""
        try:
            # 取消所有现有任务
            for task_key in list(self.reminder_tasks.keys()):
                await self.cancel_reminder_task(task_key)
            
            # 重新设置所有任务
            await self.setup_all_hall_reminders()
        except Exception as e:
            self.log_error(f"更新所有提醒任务失败: {str(e)}")
    
    async def setup_hall_reminder(self, chatroom_id: str, hall_info: dict):
        """为单个厅设置提醒任务
        
        Args:
            chatroom_id: 群聊ID
            hall_info: 厅信息字典
        """
        if not hall_info:
            self.log_info("厅信息为空，无法设置提醒任务")
            return
            
        # 检查厅是否已经开厅
        if hall_info.get("is_active", False):
            self.log_info(f"厅 {hall_info.get('hall_name')} 已开厅，不需要设置提醒")
            return
        
        try:
            hall_name = hall_info.get("hall_name", "未知厅")
            owner_wxid = hall_info.get("owner_wxid", "")
            owner_nickname = hall_info.get("owner_nickname", "未知用户")
            
            # 检查必要的键是否存在
            if not owner_wxid:
                self.log_error(f"厅 {hall_name} 的所有者ID不存在，无法设置提醒")
                return
                
            # 确定使用常规时间还是临时时间
            open_time_start = None
            time_type = ""
            
            if hall_info.get("is_temp_time", False) and "temp_time_start" in hall_info:
                # 使用临时时间
                open_time_start = hall_info["temp_time_start"]
                time_type = "临时"
                self.log_debug(f"厅 {hall_name} 使用临时时间: {open_time_start}")
            elif "open_time_start" in hall_info:
                # 使用常规时间
                open_time_start = hall_info["open_time_start"]
                time_type = "常规"
                self.log_debug(f"厅 {hall_name} 使用常规时间: {open_time_start}")
            else:
                # 两种时间都不存在
                self.log_error(f"厅 {hall_name} 的开始时间不存在，无法设置提醒")
                return
            
            # 生成任务key
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{hall_name}"
            
            # 检查是否已经有该任务
            if task_key in self.reminder_tasks:
                self.log_info(f"厅 {hall_name} 已存在提醒任务，将先取消再重新设置")
                await self.cancel_reminder_task(task_key)
            
            # 计算提醒时间
            now = datetime.datetime.now()
            reminder_time = self._time_minus_minutes(open_time_start, self.remind_before_minutes)
            reminder_datetime = datetime.datetime.combine(today, reminder_time)
            
            # 检查是否已过提醒时间
            if reminder_datetime <= now:
                # 已过提醒时间，检查是否已过开厅时间
                open_datetime = datetime.datetime.combine(today, open_time_start)
                if now > open_datetime:
                    self.log_info(f"厅 {hall_name} 的开厅时间 {open_time_start} 已过，不设置提醒")
                else:
                    # 如果现在时间在提醒时间和开厅时间之间，马上发送提醒
                    self.log_info(f"厅 {hall_name} 的提醒时间已过但开厅时间未到，立即发送提醒")
                    await self._send_hall_time_reminder(
                        chatroom_id, hall_name, owner_wxid, owner_nickname, open_time_start, time_type
                    )
                return
            
            # 计算延迟时间（秒）
            delay_seconds = (reminder_datetime - now).total_seconds()
            
            # 创建定时任务
            task = asyncio.create_task(
                self._reminder_task(
                    chatroom_id, hall_name, owner_wxid, owner_nickname,
                    open_time_start, time_type, delay_seconds
                )
            )
            
            # 存储任务
            self.reminder_tasks[task_key] = task
            
            self.log_info(f"已为厅 {hall_name} 设置提醒任务，将在 {reminder_datetime.strftime('%H:%M:%S')} 提醒（延迟{int(delay_seconds)}秒）")
        except Exception as e:
            self.log_error(f"为厅 {hall_info.get('hall_name', '未知厅')} 设置提醒任务失败: {str(e)}", exc_info=True)
    
    async def _reminder_task(self, chatroom_id: str, hall_name: str, owner_wxid: str, 
                           owner_nickname: str, open_time: datetime.time, time_type: str, 
                           delay_seconds: float):
        """提醒任务执行方法
        
        Args:
            chatroom_id: 群聊ID
            hall_name: 厅名
            owner_wxid: 厅主wxid
            owner_nickname: 厅主昵称
            open_time: 开厅时间
            time_type: 时间类型（"常规"或"临时"）
            delay_seconds: 延迟执行的秒数
        """
        try:
            # 等待指定时间
            await asyncio.sleep(delay_seconds)
            
            # 发送提醒消息
            await self._send_hall_time_reminder(
                chatroom_id, hall_name, owner_wxid, owner_nickname, open_time, time_type
            )
        except asyncio.CancelledError:
            # 任务被取消
            self.log_info(f"厅 {hall_name} 的提醒任务被取消")
        except Exception as e:
            self.log_error(f"执行提醒任务失败: {str(e)}")
        finally:
            # 清理任务引用
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{hall_name}"
            if task_key in self.reminder_tasks:
                del self.reminder_tasks[task_key]
    
    async def _send_hall_time_reminder(self, chatroom_id: str, hall_name: str, owner_wxid: str, 
                                     owner_nickname: str, open_time: datetime.time, time_type: str):
        """发送开厅时间提醒
        
        Args:
            chatroom_id: 群聊ID
            hall_name: 厅名
            owner_wxid: 厅主wxid
            owner_nickname: 厅主昵称
            open_time: 开厅时间
            time_type: 时间类型（"常规"或"临时"）
        """
        if not self.bot:
            self.log_error("无法发送提醒：bot引用不存在")
            return
            
        try:
            # 检查厅的当前状态
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if not hall_info:
                self.log_info(f"厅 {hall_name} 不存在，取消提醒")
                return
                
            # 检查厅是否已开厅
            if hall_info.get("is_active", False):
                self.log_info(f"厅 {hall_name} 已开厅，取消提醒")
                return
                
            # 构建提醒消息
            open_time_str = open_time.strftime("%H:%M")
            message = f"【开厅提醒】@{owner_nickname}\n"
            message += f"━━━━━━━━━━━━━━━\n"
            message += f"📝 厅名: {hall_name}\n"
            message += f"👤 厅主: {owner_nickname}\n"
            message += f"🕙 {time_type}开厅时间: {open_time_str}\n"
            message += f"⏰ 距离开厅时间还有 {self.remind_before_minutes} 分钟\n"
            message += f"请做好准备！开厅愉快~\n"
            message += f"━━━━━━━━━━━━━━━"
            
            # 发送@消息，确保在用户名旁显示@标记
            await self.bot.send_text_message(
                wxid=chatroom_id,
                content=message,
                at=owner_wxid
            )
            
            self.log_info(f"已发送开厅前提醒: 群:{chatroom_id}, 厅:{hall_name}, 开厅时间:{open_time_str}")
        except Exception as e:
            self.log_error(f"发送开厅提醒失败: {e}", exc_info=True)
    
    async def cancel_hall_reminder(self, chatroom_id: str, hall_name: str):
        """取消厅的提醒任务
        
        Args:
            chatroom_id: 群聊ID
            hall_name: 厅名
        """
        try:
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{hall_name}"
            await self.cancel_reminder_task(task_key)
        except Exception as e:
            self.log_error(f"取消厅 {hall_name} 的提醒任务失败: {str(e)}")
    
    async def cancel_reminder_task(self, task_key: str):
        """取消指定的提醒任务
        
        Args:
            task_key: 任务键名
        """
        try:
            if task_key in self.reminder_tasks:
                # 取消任务
                task = self.reminder_tasks[task_key]
                if isinstance(task, asyncio.Task) and not task.done():
                    task.cancel()
                
                # 移除任务引用
                del self.reminder_tasks[task_key]
                self.log_info(f"已取消提醒任务: {task_key}")
        except Exception as e:
            self.log_error(f"取消提醒任务失败: {str(e)}")
    
    def _time_minus_minutes(self, time_obj: datetime.time, minutes: int) -> datetime.time:
        """从时间对象中减去指定分钟数"""
        full_datetime = datetime.datetime.combine(datetime.date.today(), time_obj)
        result_datetime = full_datetime - datetime.timedelta(minutes=minutes)
        return result_datetime.time()
    
    async def _handle_query_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理查询开厅记录命令"""
        # 处理参数，支持字典或字符串
        param_value = ""
        if isinstance(params, dict):
            # 如果是字典，尝试获取其中的值或使用空字符串
            param_value = next(iter(params.values()), "") if params else ""
        else:
            # 如果是字符串，直接使用
            param_value = str(params)

        # 清理参数
        param_value = param_value.strip()
        
        # 解析月份和日期
        match = re.match(r"^(?:(\d+)-(\d+))?$", param_value)
        month, day = match.groups() if match and match.groups() else (None, None)
        
        if month and day:
            # 查询指定日期
            await self.query_hall_records(bot, chatroom_id, sender_wxid, nickname, int(month), int(day))
        else:
            # 查询当日记录
            await self.query_hall_records(bot, chatroom_id, sender_wxid, nickname)
    
    async def _handle_set_reminder_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理设置提醒时间命令"""
        try:
            # 处理参数，支持字典或字符串
            param_value = ""
            if isinstance(params, dict):
                # 如果是字典，尝试获取其中的值或使用空字符串
                param_value = next(iter(params.values()), "") if params else ""
            else:
                # 如果是字符串，直接使用
                param_value = str(params)
                
            # 清理参数
            param_value = param_value.strip()
            
            minutes = int(param_value)
            await self.set_reminder_minutes(bot, chatroom_id, sender_wxid, nickname, minutes)
        except ValueError:
            await bot.send_text_message(chatroom_id, "⚠️ 提醒时间必须是整数分钟数")
    
    async def _handle_register_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params):
        """处理厅名注册命令"""
        try:
            # 参数处理
            hall_name = ""
            if isinstance(params, dict) and "厅名" in params:
                hall_name = params["厅名"].strip()
            else:
                hall_name = str(params).strip()
            
            # 参数验证
            if not hall_name:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="❌ 请提供厅名\n正确格式：厅名注册 <厅名>",
                    at=sender_wxid
                )
                return False
            
            # 注册厅名
            await self.register_hall(bot, chatroom_id, sender_wxid, nickname, hall_name)
            return True
        except Exception as e:
            self.log_error(f"处理厅名注册命令失败: {str(e)}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content="❌ 处理厅名注册命令失败",
                at=sender_wxid
            )
            return False
    
    async def _handle_set_time_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params):
        """处理设置开厅时间命令"""
        try:
            # 参数处理
            if isinstance(params, dict):
                if "厅名" in params and "时间范围" in params:
                    hall_name = params["厅名"].strip()
                    time_range = params["时间范围"].strip()
                else:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content="❌ 参数格式错误\n正确格式：开厅时间 <厅名> <开始时间>-<结束时间>",
                        at=sender_wxid
                    )
                    return False
            else:
                # 从字符串解析参数
                parts = str(params).split(maxsplit=1)
                if len(parts) < 2:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content="❌ 参数不足\n正确格式：开厅时间 <厅名> <开始时间>-<结束时间>",
                        at=sender_wxid
                    )
                    return False
                
                hall_name = parts[0].strip()
                time_range = parts[1].strip()
            
            # 解析时间范围
            time_parts = time_range.split("-")
            if len(time_parts) != 2:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="❌ 时间格式错误\n正确格式：<开始时间>-<结束时间>，如 19:30-22:00",
                    at=sender_wxid
                )
                return False
            
            start_time_str = time_parts[0].strip()
            end_time_str = time_parts[1].strip()
            
            try:
                start_time = self._parse_time_string(start_time_str)
                end_time = self._parse_time_string(end_time_str)
            except ValueError as e:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"❌ 时间格式错误: {str(e)}\n"
                            f"支持的时间格式：\n- 18-22（小时）\n- 18:30-22:00（小时:分钟）\n- 18.30-22.30（小时.分钟）",
                    at=sender_wxid
                )
                return False
            
            # 设置开厅时间
            await self.set_hall_time(bot, chatroom_id, sender_wxid, nickname, hall_name, start_time, end_time)
            return True
        except Exception as e:
            self.log_error(f"处理设置开厅时间命令失败: {str(e)}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content="❌ 处理设置开厅时间命令失败",
                at=sender_wxid
            )
            return False
    
    async def _handle_temp_time_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理临时时间命令"""
        # 记录原始参数用于调试
        self.log_debug(f"处理临时时间命令，原始参数: {params}, 类型: {type(params)}")
        
        # 处理参数，支持字典或字符串
        param_value = ""
        hall_name = ""
        time_range = ""
        
        # 情况1: 参数是字典（通过_parse_parameters处理）
        if isinstance(params, dict):
            self.log_debug(f"参数是字典类型: {params}")
            # 从字典中提取厅名和时间范围
            hall_name = params.get("厅名", "")
            time_range = params.get("时间范围", "")
            
            if hall_name and time_range:
                self.log_debug(f"从字典提取参数: 厅名='{hall_name}', 时间范围='{time_range}'")
                # 尝试从时间范围中分离开始和结束时间
                if "-" in time_range:
                    time_parts = time_range.split("-")
                    if len(time_parts) == 2:
                        time_start, time_end = time_parts
                        time_start = time_start.strip()
                        time_end = time_end.strip()
                        
                        try:
                            open_time_start = self._parse_time_string(time_start)
                            open_time_end = self._parse_time_string(time_end)
                            self.log_info(f"解析时间成功: 开始={open_time_start}, 结束={open_time_end}")
                            await self.set_temp_time(bot, chatroom_id, sender_wxid, nickname, hall_name, open_time_start, open_time_end)
                            return
                        except ValueError as e:
                            self.log_warning(f"时间格式解析失败: {e}")
                            await bot.send_text_message(chatroom_id, f"⚠️ 时间格式错误: {str(e)}")
                            return
                
                self.log_warning(f"无法从时间范围解析时间: {time_range}")
        
        # 情况2: 参数是字符串
        else:
            self.log_debug(f"参数是字符串类型: '{params}'")
            # 如果是字符串，直接使用
            param_value = str(params).strip()
            
            # 增强的正则表达式匹配模式，支持更多格式
            # 支持的格式:
            # - 厅名 18-22 (简化格式)
            # - 厅名 18:00-22:00 (标准格式)
            # - 厅名 18.30-22.30 (点分隔格式)
            # - 厅名 17.00-19.00 (小数点格式)
            
            # 先尝试使用更宽松的正则表达式
            match = re.match(r"^(.+?)\s+(\d+(?:[:.]\d+)?)\s*-\s*(\d+(?:[:.]\d+)?)$", param_value)
            self.log_debug(f"正则匹配结果: {bool(match)}")
            
            # 如果第一种模式不匹配，尝试特殊格式
            if not match:
                # 尝试提取厅名和时间范围的模式（以最后一个空格为分隔点）
                parts = param_value.rsplit(' ', 1)
                self.log_debug(f"尝试按最后空格拆分: {parts}")
                
                if len(parts) == 2 and '-' in parts[1]:
                    hall_name = parts[0].strip()
                    time_range = parts[1].strip()
                    
                    # 尝试从时间范围中分离开始和结束时间
                    time_parts = time_range.split('-')
                    if len(time_parts) == 2:
                        time_start, time_end = time_parts
                        time_start = time_start.strip()
                        time_end = time_end.strip()
                        
                        self.log_debug(f"手动解析: 厅名='{hall_name}', 开始时间='{time_start}', 结束时间='{time_end}'")
                        
                        try:
                            open_time_start = self._parse_time_string(time_start)
                            open_time_end = self._parse_time_string(time_end)
                            self.log_info(f"手动解析时间成功: 开始={open_time_start}, 结束={open_time_end}")
                            await self.set_temp_time(bot, chatroom_id, sender_wxid, nickname, hall_name, open_time_start, open_time_end)
                            return
                        except ValueError as e:
                            self.log_warning(f"手动解析时间格式失败: {e}")
                            await bot.send_text_message(chatroom_id, f"⚠️ 时间格式错误: {str(e)}")
                            return
            else:
                # 正则表达式匹配成功，提取匹配组
                hall_name, time_start, time_end = match.groups()
                hall_name = hall_name.strip()
                self.log_info(f"正则匹配成功: 厅名='{hall_name}', 开始时间='{time_start}', 结束时间='{time_end}'")
                
                # 处理时间格式
                try:
                    open_time_start = self._parse_time_string(time_start)
                    open_time_end = self._parse_time_string(time_end)
                    await self.set_temp_time(bot, chatroom_id, sender_wxid, nickname, hall_name, open_time_start, open_time_end)
                    return
                except ValueError as e:
                    await bot.send_text_message(chatroom_id, f"⚠️ 时间格式错误: {str(e)}")
                    return
        
        # 如果以上所有解析尝试都失败，显示错误信息
        self.log_warning(f"无法解析临时时间命令参数: {params}")
        await bot.send_text_message(
            chatroom_id, 
            f"⚠️ 命令格式错误\n正确用法: {self.cmd_temp_time} 厅名 开始时间-结束时间\n"
            f"支持的时间格式：\n- 18-22（小时）\n- 18:30-22:00（小时:分钟）\n- 18.00-22.00（小时.分钟）"
        )
    
    async def _handle_set_duration_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理设置开厅时长命令"""
        try:
            # 处理参数，支持字典或字符串
            param_value = ""
            if isinstance(params, dict):
                # 如果是字典，尝试获取其中的值或使用空字符串
                param_value = next(iter(params.values()), "") if params else ""
            else:
                # 如果是字符串，直接使用
                param_value = str(params)
                
            # 清理参数
            param_value = param_value.strip()
            
            if '-' in param_value:
                hour, minute = map(int, param_value.split('-'))
                if 0 <= hour < 24 and 0 <= minute < 60:
                    total_minutes = hour * 60 + minute
                    await self.set_required_duration(bot, chatroom_id, sender_wxid, nickname, total_minutes)
                else:
                    await bot.send_text_message(chatroom_id, "⚠️ 时间格式错误: 小时必须在0-23之间，分钟必须在0-59之间")
            else:
                total_minutes = int(param_value)
                await self.set_required_duration(bot, chatroom_id, sender_wxid, nickname, total_minutes)
        except ValueError:
            await bot.send_text_message(chatroom_id, f"⚠️ 时间格式错误\n正确用法: {self.cmd_set_duration} 小时-分钟 或 总分钟数")
    
    async def list_reminders(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """列出当前群聊中设置的所有提醒任务"""
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
            
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        today = datetime.date.today()
        
        # 过滤当前群聊的提醒任务
        group_reminder_tasks = {}
        group_prefix = f"{today}_{chatroom_id}_"
        
        self.log_info(f"正在为群 {chatroom_id} 查找提醒任务，当前提醒任务总数: {len(self.reminder_tasks)}")
        
        for task_key in self.reminder_tasks:
            if task_key.startswith(group_prefix):
                hall_name = task_key[len(group_prefix):]
                group_reminder_tasks[hall_name] = task_key
                self.log_info(f"找到群 {chatroom_id} 的提醒任务: {hall_name}")
        
        self.log_info(f"群 {chatroom_id} 的待执行提醒任务数量: {len(group_reminder_tasks)}")
        
        if not group_reminder_tasks:
            await bot.send_text_message(chatroom_id, "📋 本群今日没有待执行的开厅提醒任务")
            return
        
        # 获取所有厅的详细信息
        halls_info = {}
        for hall_name in group_reminder_tasks:
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if hall_info:
                halls_info[hall_name] = hall_info
                self.log_info(f"获取到厅信息: {hall_name}, owner: {hall_info.get('owner_nickname', '未知')}")
            else:
                self.log_warning(f"未找到厅信息: {hall_name}")
        
        # 构建提醒任务列表消息
        message = "📋 开厅提醒任务列表：\n\n"
        task_count = 0
        
        # 如果没有找到有效的厅信息
        if not halls_info:
            message += "没有找到有效的提醒任务\n\n"
            self.log_warning(f"群 {chatroom_id} 没有找到有效的厅信息")
        else:
            for idx, (hall_name, hall_info) in enumerate(halls_info.items(), 1):
                if not hall_info:
                    self.log_warning(f"厅 {hall_name} 信息为空")
                    continue
                
                # 确定使用常规时间还是临时时间
                time_type = ""
                open_time = None
                
                if hall_info.get("is_temp_time", False):
                    time_type = "临时"
                    open_time = hall_info.get("open_time_start")
                    self.log_info(f"厅 {hall_name} 使用临时时间: {open_time}")
                else:
                    time_type = "常规"
                    open_time = hall_info.get("open_time_start")
                    self.log_info(f"厅 {hall_name} 使用常规时间: {open_time}")
                
                if not open_time:
                    self.log_warning(f"厅 {hall_name} 的开厅时间为空")
                    # 尝试直接访问字典键
                    self.log_info(f"尝试直接获取时间键，可用键: {list(hall_info.keys())}")
                    if "open_time_start" in hall_info:
                        open_time = hall_info["open_time_start"]
                        time_type = "常规"
                        self.log_info(f"直接获取到开厅时间: {open_time}")
                    else:
                        continue
                
                try:
                    # 记录时间类型
                    self.log_info(f"厅 {hall_name} 最终使用 {time_type} 时间: {open_time}, 类型: {type(open_time)}")
                    
                    # 计算提醒时间
                    reminder_time = self._time_minus_minutes(open_time, self.remind_before_minutes)
                    
                    # 格式化时间
                    open_time_str = open_time.strftime("%H:%M")
                    reminder_time_str = reminder_time.strftime("%H:%M")
                    
                    # 添加厅信息
                    message += f"{idx}. 厅名: {hall_name}\n"
                    message += f"   厅主: {hall_info.get('owner_nickname', '未知')}\n"
                    message += f"   开厅时间: {open_time_str} ({time_type}时间)\n"
                    message += f"   提醒时间: {reminder_time_str}\n\n"
                    
                    task_count += 1
                    self.log_info(f"成功添加厅 {hall_name} 的提醒信息到列表")
                except Exception as e:
                    self.log_error(f"处理厅 {hall_name} 的提醒信息时出错: {e}", exc_info=True)
        
        # 如果没有找到有效任务，添加提示
        if task_count == 0:
            message += "未找到有效的待执行提醒任务，请检查厅设置\n\n"
        
        message += f"💡 提示: 使用「{self.cmd_remove_reminder} 序号」可取消对应的提醒任务"
        
        self.log_info(f"已构建提醒任务列表消息，任务数: {task_count}")
        await bot.send_text_message(chatroom_id, message)
        self.log_info(f"已发送提醒任务列表: 群:{chatroom_id}, 共{task_count}个任务")
    
    async def remove_reminder_by_index(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, index: int):
        """通过序号移除提醒任务
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊ID
            sender_wxid: 发送者wxid
            nickname: 发送者昵称
            index: 提醒任务序号
        """
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
            
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开厅记录功能，请联系管理员启用")
            return
        
        today = datetime.date.today()
        
        # 过滤当前群聊的提醒任务并按序号排序
        group_reminder_tasks = []
        group_prefix = f"{today}_{chatroom_id}_"
        
        for task_key in self.reminder_tasks:
            if task_key.startswith(group_prefix):
                hall_name = task_key[len(group_prefix):]
                hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
                if hall_info:
                    group_reminder_tasks.append((hall_name, task_key, hall_info))
        
        if not group_reminder_tasks:
            await bot.send_text_message(chatroom_id, "⚠️ 本群没有待执行的提醒任务")
            return
        
        # 检查序号是否有效
        if index < 1 or index > len(group_reminder_tasks):
            await bot.send_text_message(chatroom_id, f"⚠️ 无效的序号: {index}，序号范围: 1-{len(group_reminder_tasks)}")
            return
        
        # 获取要取消的提醒任务
        hall_name, task_key, hall_info = group_reminder_tasks[index - 1]
        
        # 取消提醒任务
        await self.cancel_reminder_task(task_key)
        
        # 发送成功消息
        await bot.send_text_message(
            chatroom_id, 
            f"✅ 已取消厅 {hall_name} 的提醒任务 (by {nickname})"
        )
        
        self.log_info(f"已取消提醒任务: 群:{chatroom_id}, 厅:{hall_name}, 用户:{nickname}")
    
    async def _handle_remove_reminder_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理取消提醒命令"""
        try:
            index = int(params.strip())
            await self.remove_reminder_by_index(bot, chatroom_id, sender_wxid, nickname, index)
        except ValueError:
            await bot.send_text_message(chatroom_id, f"⚠️ 命令格式错误\n正确用法: {self.cmd_remove_reminder} 序号")
    
    async def _handle_delete_hall_command(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params: str = ""):
        """处理删除厅名命令
        
        支持三种方式:
        1. 普通用户/管理员: "删除厅名 厅名" - 删除当前群聊的厅名
        2. 超级管理员: "删除厅名 厅名" - 列出所有群中包含此厅名的群
        3. 超级管理员: "删 序号" - 确认删除列表中指定序号的厅名
        """
        # 处理参数，支持字典和字符串两种格式
        self.log_debug(f"收到删除厅名相关命令，参数类型: {type(params)}, 值: {params}")
        
        if isinstance(params, dict):
            # 如果是字典格式，检查是"删序号"还是"删除厅名"命令
            if "序号" in params:
                # "删 序号"命令逻辑
                index_str = params.get("序号", "")
                try:
                    # 检查是否为超级管理员
                    from plugins.AdminManager.main import AdminManagerPlugin
                    admin_plugin = AdminManagerPlugin()
                    is_super_admin = sender_wxid in admin_plugin.super_admins
                    if not is_super_admin:
                        await bot.send_text_message(chatroom_id, "⚠️ 权限不足，只有超级管理员可以使用此命令")
                        return
                    
                    # 获取要删除的序号
                    try:
                        index = int(index_str)
                        await self._handle_delete_confirmation(bot, chatroom_id, sender_wxid, nickname, index)
                    except ValueError:
                        await bot.send_text_message(chatroom_id, "⚠️ 序号格式错误，应为数字")
                except Exception as e:
                    self.log_error(f"处理删除厅名确认命令失败: {e}")
                    await bot.send_text_message(chatroom_id, f"❌ 处理删除命令时出错: {str(e)}")
            else:
                # "删除厅名 厅名"命令逻辑
                target_hall_name = params.get("厅名", "")
                if not target_hall_name:
                    await bot.send_text_message(chatroom_id, f"⚠️ 命令格式错误\n正确用法: {self.cmd_delete_hall} 厅名")
                    return
                
                await self._handle_delete_hall_by_name(bot, chatroom_id, sender_wxid, nickname, target_hall_name)
        else:
            # 如果是字符串格式
            # 首先处理"删 序号"格式的指令
            delete_pattern = re.match(r"^删\s+(\d+)$", str(params)) if params else None
            if delete_pattern:
                try:
                    # 检查是否为超级管理员
                    from plugins.AdminManager.main import AdminManagerPlugin
                    admin_plugin = AdminManagerPlugin()
                    is_super_admin = sender_wxid in admin_plugin.super_admins
                    if not is_super_admin:
                        await bot.send_text_message(chatroom_id, "⚠️ 权限不足，只有超级管理员可以使用此命令")
                        return
                    
                    # 获取要删除的序号
                    try:
                        index = int(delete_pattern.group(1))
                        await self._handle_delete_confirmation(bot, chatroom_id, sender_wxid, nickname, index)
                    except ValueError:
                        await bot.send_text_message(chatroom_id, "⚠️ 序号格式错误，应为数字")
                except Exception as e:
                    self.log_error(f"处理删除厅名确认命令失败: {e}")
                    await bot.send_text_message(chatroom_id, f"❌ 处理删除命令时出错: {str(e)}")
                return
            
            # 处理"删除厅名 厅名"格式的指令
            if not params:
                await bot.send_text_message(chatroom_id, f"⚠️ 命令格式错误\n正确用法: {self.cmd_delete_hall} 厅名")
                return
            
            # 解析目标厅名
            target_hall_name = str(params).strip()
            await self._handle_delete_hall_by_name(bot, chatroom_id, sender_wxid, nickname, target_hall_name)
            
    async def _handle_delete_confirmation(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, index: int):
        """处理删除厅名确认命令（删 序号）"""
        # 从临时存储中获取待删除列表
        task_key = f"delete_list_{sender_wxid}"
        if task_key not in self.reminder_tasks:
            await bot.send_text_message(chatroom_id, "⚠️ 没有找到待删除的厅名列表，请先使用「删除厅名 厅名」命令")
            return
        
        delete_list = self.reminder_tasks[task_key]
        if not isinstance(delete_list, list) or index < 1 or index > len(delete_list):
            await bot.send_text_message(chatroom_id, f"⚠️ 无效的序号: {index}，序号范围: 1-{len(delete_list) if isinstance(delete_list, list) else 0}")
            return
        
        # 获取要删除的厅信息
        target_info = delete_list[index-1]
        target_group_id = target_info["group_id"]
        target_hall_name = target_info["hall_name"]
        target_group_name = target_info["group_name"]
        
        # 执行删除
        success, message = self.db.delete_hall(target_group_id, target_hall_name)
        
        # 取消提醒任务
        await self.cancel_hall_reminder(target_group_id, target_hall_name)
        
        if success:
            result_msg = f"✅ 成功删除群「{target_group_name}」中的厅名「{target_hall_name}」"
            await bot.send_text_message(chatroom_id, result_msg)
            self.log_success(f"超级管理员 {nickname} 删除了群 {target_group_id} 的厅名 {target_hall_name}")
            
            # 通知目标群
            try:
                notify_msg = f"⚠️ 超级管理员 {nickname} 已删除本群厅名 {target_hall_name}"
                await bot.send_text_message(target_group_id, notify_msg)
            except Exception as e:
                self.log_error(f"通知群 {target_group_id} 删除厅名操作失败: {e}")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 删除失败: {message}")
            self.log_error(f"删除厅名失败: {message}")
            
    async def _handle_delete_hall_by_name(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, target_hall_name: str):
        """处理根据厅名删除的命令（删除厅名 厅名）"""
        # 检查是否为超级管理员
        is_super_admin = False
        try:
            from plugins.AdminManager.main import AdminManagerPlugin
            admin_plugin = AdminManagerPlugin()
            is_super_admin = sender_wxid in admin_plugin.super_admins
        except Exception as e:
            self.log_error(f"检查超级管理员权限失败: {e}")
        
        if is_super_admin:
            # 超级管理员模式：可以删除所有群中的指定厅名
            self.log_info(f"超级管理员 {nickname} 尝试删除所有群中的厅名 {target_hall_name}")
            
            # 获取所有群聊的所有厅信息
            all_halls = self.db.get_all_halls_all_groups()
            
            # 查找所有包含目标厅名的群
            target_groups = []
            for group_id, halls in all_halls.items():
                for hall in halls:
                    if hall.get("hall_name") == target_hall_name:
                        # 获取群名称
                        try:
                            group_info = await bot.get_chatroom_info(group_id)
                            group_name = group_info.get("nickname", "未知群聊") if group_info else "未知群聊"
                        except:
                            group_name = f"群ID: {group_id}"
                        
                        target_groups.append({
                            "group_id": group_id,
                            "hall_name": hall.get("hall_name"),
                            "owner_nickname": hall.get("owner_nickname", "未知"),
                            "group_name": group_name
                        })
                        break
            
            if not target_groups:
                await bot.send_text_message(chatroom_id, f"❌ 未找到厅名 {target_hall_name} 在任何群中")
                return
                
            # 显示所有匹配的厅名列表
            # 存储待删除列表到临时存储中
            task_key = f"delete_list_{sender_wxid}"
            self.reminder_tasks[task_key] = target_groups
            
            confirm_msg = f"📋 找到 {len(target_groups)} 个包含厅名「{target_hall_name}」的群:\n\n"
            
            for idx, group in enumerate(target_groups, 1):
                confirm_msg += f"{idx}. 群名: {group['group_name']}\n"
                confirm_msg += f"   厅名: {group['hall_name']}\n"
                confirm_msg += f"   厅主: {group['owner_nickname']}\n\n"
            
            confirm_msg += f"要删除特定厅名，请回复「删 序号」，例如：删 1"
            await bot.send_text_message(chatroom_id, confirm_msg)
        else:
            # 普通模式：只在当前群聊中删除厅名
            await self.delete_hall(bot, chatroom_id, sender_wxid, nickname, target_hall_name)
    
    async def delete_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, hall_name: str, is_admin_override: bool = False):
        """删除厅名
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊ID
            sender_wxid: 发送者wxid
            nickname: 发送者昵称
            hall_name: 要删除的厅名
            is_admin_override: 是否超级管理员覆盖权限检查
        """
        try:
            self.log_info(f"执行删除厅名，厅名: {hall_name}, 群聊: {chatroom_id}, 操作人: {nickname}")
            
            # 检查群聊是否启用开厅记录（跨群删除时可能仍需要检查）
            if not self.db.is_group_kaiting_enabled(chatroom_id):
                message = f"⚠️ 群 {chatroom_id} 未启用开厅记录功能"
                await bot.send_text_message(chatroom_id if not is_admin_override else sender_wxid, message)
                return
            
            # 获取厅名信息
            hall_info = self.db.get_hall_by_name(chatroom_id, hall_name)
            if not hall_info:
                message = f"❌ 厅名 {hall_name} 在群 {chatroom_id} 中不存在"
                await bot.send_text_message(chatroom_id if not is_admin_override else sender_wxid, message)
                return
            
            # 权限检查（超级管理员可以跳过）
            if not is_admin_override:
                is_admin = await self.is_admin(chatroom_id, sender_wxid)
                is_owner = sender_wxid == hall_info["owner_wxid"]
                
                self.log_debug(f"权限检查 - 是管理员: {is_admin}, 是厅主: {is_owner}")
                
                if not is_admin and not is_owner:
                    await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员或厅主权限")
                    return
            
            # 取消所有相关提醒任务
            await self.cancel_hall_reminder(chatroom_id, hall_name)
            
            # 删除厅名
            success, message = self.db.delete_hall(chatroom_id, hall_name)
            
            # 确定消息发送目标：如果是跨群删除，则发送给操作者
            target_wxid = chatroom_id if not is_admin_override else sender_wxid
            
            if success:
                success_msg = f"✅ 厅名 {hall_name} 已成功从群 {chatroom_id} 删除 (by {nickname})"
                await bot.send_text_message(target_wxid, success_msg)
                self.log_success(f"厅名 {hall_name} 已从群 {chatroom_id} 被 {nickname} 删除")
                
                # 如果是跨群删除，同时通知目标群
                if is_admin_override and chatroom_id != sender_wxid:
                    notify_msg = f"⚠️ 超级管理员 {nickname} 已删除本群厅名 {hall_name}"
                    try:
                        await bot.send_text_message(chatroom_id, notify_msg)
                    except Exception as e:
                        self.log_error(f"通知目标群删除厅名操作失败: {e}")
            else:
                await bot.send_text_message(target_wxid, f"❌ 删除厅名失败: {message}")
                self.log_error(f"删除厅名 {hall_name} 失败: {message}")
        except Exception as e:
            self.log_error(f"删除厅名异常: {e}")
            # 确定错误消息发送目标
            target_wxid = chatroom_id if not is_admin_override else sender_wxid
            await bot.send_text_message(target_wxid, f"❌ 删除厅名时出错: {str(e)}")
            import traceback
            self.log_error(traceback.format_exc())
    
    async def list_all_halls(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """列出所有群聊的所有厅名（仅超级管理员可用）"""
        # 检查是否为超级管理员
        try:
            from plugins.AdminManager.main import AdminManagerPlugin
            admin_plugin = AdminManagerPlugin()
            if sender_wxid not in admin_plugin.super_admins:
                await bot.send_text_message(chatroom_id, "⚠️ 权限不足，只有超级管理员可以使用此命令")
                self.log_warning(f"用户 {nickname} ({sender_wxid}) 尝试使用超级管理员命令但权限不足")
                return
        except Exception as e:
            self.log_error(f"检查超级管理员权限失败: {e}")
            await bot.send_text_message(chatroom_id, "⚠️ 检查权限失败，无法执行命令")
            return
            
        # 获取所有群聊的所有厅信息
        try:
            self.log_info(f"超级管理员 {nickname} 请求获取所有群聊的所有厅名")
            all_halls = self.db.get_all_halls_all_groups()
            
            if not all_halls:
                await bot.send_text_message(chatroom_id, "📋 系统中没有任何已注册的厅名")
                return
            
            # 构建回复消息
            reply_msg = f"📋 系统中所有群聊的厅名列表 ({len(all_halls)}个群):\n"
            reply_msg += f"━━━━━━━━━━━━━━━━\n"
            
            # 计算总厅数
            total_halls = sum(len(halls) for halls in all_halls.values())
            
            for group_id, halls in all_halls.items():
                # 获取群信息
                try:
                    group_info = await bot.get_chatroom_info(group_id)
                    group_name = group_info.get("nickname", "未知群聊") if group_info else "未知群聊"
                except Exception:
                    group_name = "未知群聊"
                
                reply_msg += f"\n▶ 群聊: {group_name} - {len(halls)}个厅\n"
                
                # 列出每个厅的信息
                for i, hall in enumerate(halls):
                    # 获取基本信息
                    hall_name = hall.get("hall_name", "未知厅")
                    owner_nickname = hall.get("owner_nickname", "未知")
                    
                    # 状态信息
                    status = "🟢 已开厅" if hall.get("is_active") else "⚪ 未开厅"
                    
                    # 时间信息
                    if "open_time_start" in hall and hall["open_time_start"]:
                        open_time_start = hall["open_time_start"].strftime("%H:%M")
                        open_time_end = hall["open_time_end"].strftime("%H:%M") if hall.get("open_time_end") else "未知"
                        time_info = f"{open_time_start}-{open_time_end}"
                    else:
                        time_info = "未设置"
                    
                    reply_msg += f"  {i+1}. {hall_name} - {status}\n"
                    reply_msg += f"     👤 厅主: {owner_nickname}\n"
                    reply_msg += f"     ⏰ 时段: {time_info}\n"
            
            # 添加汇总信息
            reply_msg += f"\n━━━━━━━━━━━━━━━━\n"
            reply_msg += f"📊 统计: 共{len(all_halls)}个群, {total_halls}个厅名"
            
            # 发送消息
            await bot.send_text_message(chatroom_id, reply_msg)
            self.log_info(f"超级管理员 {nickname} 请求查看所有厅名列表")
        except Exception as e:
            self.log_error(f"获取所有厅名列表失败: {e}")
            await bot.send_text_message(chatroom_id, f"❌ 获取所有厅名列表失败: {str(e)}")
            import traceback
            self.log_error(traceback.format_exc())
    
    @require_admin
    async def list_active_halls(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """列出所有当前正在营业中的厅(仅管理员可用)"""
        try:
            self.log_info(f"管理员 {nickname} 请求获取所有营业中的厅")
            
            # 获取所有厅信息
            all_halls = self.db.get_all_halls_all_groups()
            
            if not all_halls:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="📋 系统中没有任何已注册的厅名",
                    at=sender_wxid
                )
                return
            
            # 筛选营业中的厅
            active_halls_by_group = {}
            active_hall_count = 0
            
            for group_id, halls in all_halls.items():
                active_halls = [hall for hall in halls if hall.get("is_active")]
                if active_halls:
                    active_halls_by_group[group_id] = active_halls
                    active_hall_count += len(active_halls)
            
            if not active_halls_by_group:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="📋 当前没有正在营业中的厅",
                    at=sender_wxid
                )
                return
            
            # 构建回复消息
            reply_msg = f"📋 当前营业中的厅列表 ({active_hall_count}个):\n"
            reply_msg += f"━━━━━━━━━━━━━━━━\n"
            
            for group_id, halls in active_halls_by_group.items():
                # 获取群信息
                try:
                    group_info = await bot.get_chatroom_info(group_id)
                    group_name = group_info.get("nickname", "未知群聊") if group_info else "未知群聊"
                except Exception:
                    group_name = "未知群聊"
                
                reply_msg += f"\n▶ 群聊: {group_name} - {len(halls)}个营业中的厅\n"
                
                # 列出每个厅的信息
                for i, hall in enumerate(halls):
                    # 获取基本信息
                    hall_name = hall.get("hall_name", "未知厅")
                    hall_id = hall.get("hall_id")
                    owner_nickname = hall.get("owner_nickname", "未知")
                    
                    # 开厅时间信息
                    open_time = hall.get("open_time", datetime.time(0, 0)).strftime("%H:%M")
                    
                    # 计算已开厅时长
                    open_date = hall.get("open_date", datetime.date.today())
                    open_datetime = datetime.datetime.combine(open_date, hall.get("open_time", datetime.time(0, 0)))
                    now = datetime.datetime.now()
                    duration_minutes = int((now - open_datetime).total_seconds() / 60)
                    
                    # 格式化时长
                    if duration_minutes < 60:
                        duration_str = f"{duration_minutes}分钟"
                    else:
                        hours = duration_minutes // 60
                        minutes = duration_minutes % 60
                        duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                    
                    reply_msg += f"  {i+1}. {hall_name}\n"
                    # 显示厅ID，如果有的话
                    if hall_id:
                        reply_msg += f"     🆔 厅ID: {hall_id}\n"
                    else:
                        reply_msg += f"     📝 群ID: {group_id}\n"
                    reply_msg += f"     👤 厅主: {owner_nickname}\n"
                    reply_msg += f"     🕒 开厅时间: {open_time}\n"
                    reply_msg += f"     ⏱️ 已开厅: {duration_str}\n"
            
            # 添加使用说明
            reply_msg += f"\n💡 提示: 使用「关闭指定厅 厅ID」可强制关闭营业中的厅"
            
            # 发送消息
            await bot.send_text_message(wxid=chatroom_id, content=reply_msg)
            self.log_info(f"管理员 {nickname} 查看了营业中的厅列表")
        except Exception as e:
            self.log_error(f"获取营业中的厅列表失败: {e}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 获取营业中的厅列表失败: {str(e)}",
                at=sender_wxid
            )
    
    @require_admin
    async def list_inactive_halls(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """列出所有当前未营业的厅(仅管理员可用)"""
        try:
            self.log_info(f"管理员 {nickname} 请求获取所有未营业的厅")
            
            # 获取所有厅信息
            all_halls = self.db.get_all_halls_all_groups()
            
            if not all_halls:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="📋 系统中没有任何已注册的厅名",
                    at=sender_wxid
                )
                return
            
            # 筛选未营业的厅
            inactive_halls_by_group = {}
            inactive_hall_count = 0
            
            for group_id, halls in all_halls.items():
                inactive_halls = [hall for hall in halls if not hall.get("is_active")]
                if inactive_halls:
                    inactive_halls_by_group[group_id] = inactive_halls
                    inactive_hall_count += len(inactive_halls)
            
            if not inactive_halls_by_group:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="📋 当前所有厅都在营业中",
                    at=sender_wxid
                )
                return
            
            # 构建回复消息
            reply_msg = f"📋 当前未营业的厅列表 ({inactive_hall_count}个):\n"
            reply_msg += f"━━━━━━━━━━━━━━━━\n"
            
            for group_id, halls in inactive_halls_by_group.items():
                # 获取群信息
                try:
                    group_info = await bot.get_chatroom_info(group_id)
                    group_name = group_info.get("nickname", "未知群聊") if group_info else "未知群聊"
                except Exception:
                    group_name = "未知群聊"
                
                reply_msg += f"\n▶ 群聊: {group_name} - {len(halls)}个未营业的厅\n"
                
                # 列出每个厅的信息
                for i, hall in enumerate(halls):
                    # 获取基本信息
                    hall_name = hall.get("hall_name", "未知厅")
                    hall_id = hall.get("hall_id")
                    owner_nickname = hall.get("owner_nickname", "未知")
                    
                    # 时间信息
                    if "open_time_start" in hall and hall["open_time_start"]:
                        open_time_start = hall["open_time_start"].strftime("%H:%M")
                        open_time_end = hall["open_time_end"].strftime("%H:%M") if hall.get("open_time_end") else "未知"
                        time_info = f"{open_time_start}-{open_time_end}"
                    else:
                        time_info = "未设置"
                    
                    reply_msg += f"  {i+1}. {hall_name}\n"
                    # 显示厅ID，如果有的话
                    if hall_id:
                        reply_msg += f"     🆔 厅ID: {hall_id}\n"
                    else:
                        reply_msg += f"     📝 群ID: {group_id}\n"
                    reply_msg += f"     👤 厅主: {owner_nickname}\n"
                    reply_msg += f"     ⏰ 预定时段: {time_info}\n"
            
            # 发送消息
            await bot.send_text_message(wxid=chatroom_id, content=reply_msg)
            self.log_info(f"管理员 {nickname} 查看了未营业的厅列表")
        except Exception as e:
            self.log_error(f"获取未营业的厅列表失败: {e}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 获取未营业的厅列表失败: {str(e)}",
                at=sender_wxid
            )
    
    @require_admin
    async def admin_close_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """管理员结束指定厅的营业(仅管理员可用)"""
        try:
            # 获取参数
            if not params or not isinstance(params, dict):
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="⚠️ 命令格式错误，请使用「关闭指定厅 厅ID」或「关闭指定厅 群ID 厅名」",
                    at=sender_wxid
                )
                return
            
            # 判断参数类型
            hall_info = None
            target_hall_id = None
            
            # 方式1: 使用厅ID
            if "厅ID" in params:
                target_hall_id = params.get("厅ID")
                self.log_info(f"管理员 {nickname} 请求关闭厅ID为 {target_hall_id} 的厅")
                
                # 根据厅ID获取厅信息
                hall_info = self.db.get_hall_by_id(target_hall_id)
                
                if not hall_info:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 未找到ID为 {target_hall_id} 的厅",
                        at=sender_wxid
                    )
                    return
            
            # 方式2: 使用群ID和厅名
            elif "群ID" in params and "厅名" in params:
                target_group_id = params.get("群ID")
                target_hall_name = params.get("厅名")
                
                self.log_info(f"管理员 {nickname} 请求关闭群 {target_group_id} 的 {target_hall_name} 厅")
                
                # 根据群ID和厅名获取厅信息
                hall_info = self.db.get_hall_by_name(target_group_id, target_hall_name)
                
                if not hall_info:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 未找到群 {target_group_id} 中的厅 {target_hall_name}",
                        at=sender_wxid
                    )
                    return
                
                # 如果厅有ID，记录下来
                target_hall_id = hall_info.get("hall_id")
            
            else:
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content="⚠️ 命令格式错误，请使用「关闭指定厅 厅ID」或「关闭指定厅 群ID 厅名」",
                    at=sender_wxid
                )
                return
            
            # 通用处理
            if not hall_info.get("is_active"):
                hall_name = hall_info.get("hall_name")
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"⚠️ 厅「{hall_name}」当前未开厅",
                    at=sender_wxid
                )
                return
            
            # 获取需要的信息
            target_group_id = hall_info.get("chatroom_id")
            target_hall_name = hall_info.get("hall_name")
            owner_wxid = hall_info.get("owner_wxid")
            owner_nickname = hall_info.get("owner_nickname")
            
            # 关闭厅 - 使用数据库会话直接操作
            session = self.db.DBSession()
            try:
                # 获取开厅记录
                hall_record = session.query(HallRecord).filter(
                    HallRecord.chatroom_id == target_group_id,
                    HallRecord.hall_name == target_hall_name,
                    HallRecord.is_active == True
                ).first()
                
                if not hall_record:
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 获取厅记录失败，无法关闭厅",
                        at=sender_wxid
                    )
                    return
                
                # 更新记录状态
                hall_record.is_active = False
                hall_record.close_time = datetime.datetime.now().time()
                
                # 计算开厅时长
                open_datetime = datetime.datetime.combine(hall_record.open_date, hall_record.open_time)
                close_datetime = datetime.datetime.combine(datetime.date.today(), hall_record.close_time)
                
                # 计算实际差值
                duration_minutes = int((close_datetime - open_datetime).total_seconds() / 60)
                hall_record.duration = duration_minutes
                
                # 添加跨天标记
                hall_record.is_cross_day = close_datetime.date() > open_datetime.date()
                
                # 保存记录
                session.commit()
                
                # 构建管理员回复消息
                open_time_str = hall_record.open_time.strftime("%H:%M:%S")
                close_time_str = hall_record.close_time.strftime("%H:%M:%S")
                
                # 格式化时长
                if duration_minutes < 60:
                    duration_str = f"{duration_minutes}分钟"
                else:
                    hours = duration_minutes // 60
                    minutes = duration_minutes % 60
                    duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                
                # 构建回复消息
                admin_reply_msg = f"✅ 管理员 {nickname} 已关闭指定厅！\n"
                # 如果有厅ID则显示
                if target_hall_id:
                    admin_reply_msg += f"🆔 厅ID: {target_hall_id}\n"
                admin_reply_msg += f"📝 厅名: {target_hall_name}\n"
                admin_reply_msg += f"👤 厅主: {owner_nickname}\n"
                admin_reply_msg += f"🏠 所在群: {target_group_id}\n"
                admin_reply_msg += f"⏰ 开厅时间: {open_time_str}\n"
                admin_reply_msg += f"⏰ 关厅时间: {close_time_str}"
                
                # 如果是跨天，添加跨天提示
                if hall_record.is_cross_day:
                    admin_reply_msg += " (次日)"
                
                admin_reply_msg += f"\n⌛ 开厅时长: {duration_str}"
                
                # 向当前群（管理员所在群）发送结果
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=admin_reply_msg,
                    at=sender_wxid
                )
                
                # 向厅所在群发送关厅通知
                try:
                    owner_reply_msg = f"⚠️ 系统通知: 管理员已关闭您的厅\n"
                    # 如果有厅ID则显示
                    if target_hall_id:
                        owner_reply_msg += f"🆔 厅ID: {target_hall_id}\n"
                    owner_reply_msg += f"📝 厅名: {target_hall_name}\n"
                    owner_reply_msg += f"⏰ 开厅时间: {open_time_str}\n"
                    owner_reply_msg += f"⏰ 关厅时间: {close_time_str}"
                    
                    # 如果是跨天，添加跨天提示
                    if hall_record.is_cross_day:
                        owner_reply_msg += " (次日)"
                    
                    owner_reply_msg += f"\n⌛ 开厅时长: {duration_str}"
                    
                    await bot.send_text_message(
                        wxid=target_group_id,
                        content=owner_reply_msg,
                        at=owner_wxid
                    )
                except Exception as e:
                    self.log_error(f"发送厅主关厅通知失败: {e}")
                    await bot.send_text_message(
                        wxid=chatroom_id,
                        content=f"⚠️ 通知厅主失败，但厅已成功关闭",
                        at=sender_wxid
                    )
                
                # 记录日志
                if target_hall_id:
                    self.log_success(f"管理员 {nickname} 成功关闭厅ID {target_hall_id}，厅名: {target_hall_name}，群ID: {target_group_id}")
                else:
                    self.log_success(f"管理员 {nickname} 成功关闭群 {target_group_id} 的 {target_hall_name} 厅")
            
            except Exception as e:
                session.rollback()
                self.log_error(f"管理员关闭指定厅失败: {e}")
                await bot.send_text_message(
                    wxid=chatroom_id,
                    content=f"❌ 关闭指定厅失败: {str(e)}",
                    at=sender_wxid
                )
            finally:
                session.close()
                
        except Exception as e:
            self.log_error(f"管理员关闭指定厅失败: {e}")
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 关闭指定厅失败: {str(e)}",
                at=sender_wxid
            )
            import traceback
            self.log_error(traceback.format_exc())
    
    async def query_user_hall(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """查询用户在群内注册的厅信息"""
        # 检查群聊是否启用开厅记录
        if not self.db.is_group_kaiting_enabled(chatroom_id):
            await bot.send_text_message(
                wxid=chatroom_id,
                content="⚠️ 该群尚未启用开厅记录功能",
                at=sender_wxid
            )
            return
        
        # 从数据库获取用户的所有厅信息
        halls_info = self.db.get_halls_by_owner(chatroom_id, sender_wxid)
        
        if not halls_info:
            await bot.send_text_message(
                wxid=chatroom_id,
                content="❌ 您在本群尚未注册厅名",
                at=sender_wxid
            )
            return
        
        # 构建回复消息
        message = f"📋 {nickname} 的厅信息({len(halls_info)}个)：\n\n"
        
        for i, hall_info in enumerate(halls_info):
            # 格式化时间
            open_time_start = hall_info.get("open_time_start").strftime("%H:%M")
            open_time_end = hall_info.get("open_time_end").strftime("%H:%M")
            register_time = hall_info.get("register_time").strftime("%Y-%m-%d %H:%M")
            
            # 当前开厅状态
            current_status = "未开厅"
            if hall_info.get("is_active"):
                open_time = hall_info.get("open_time").strftime("%H:%M")
                current_status = f"已开厅 {open_time} 至今"
            
            # 厅信息
            message += f"{i+1}. 厅名: {hall_info.get('hall_name')}\n"
            message += f"   🕒 固定时间: {open_time_start}-{open_time_end}\n"
            
            if hall_info.get("is_temp_time"):
                temp_start = hall_info.get("temp_time_start").strftime("%H:%M")
                temp_end = hall_info.get("temp_time_end").strftime("%H:%M")
                message += f"   📅 今日临时时间: {temp_start}-{temp_end}\n"
            
            message += f"   📊 当前状态: {current_status}\n"
            message += f"   📆 注册时间: {register_time}\n"
            
            if i < len(halls_info) - 1:
                message += "   ------------------------\n"
        
        await bot.send_text_message(wxid=chatroom_id, content=message)
    
    @on_text_message(priority=40)
    async def handle_hall_selection(self, bot: WechatAPIClient, message: dict):
        """处理厅选择消息 - 已弃用，保留代码是为了兼容性，可在后续版本中移除"""
        # 不再处理序号选择逻辑，使用直接指定厅名的方式
        return
        
        # 以下是原始逻辑，已弃用
        """
        # 检查消息类型和内容
        if not message.get("Content"):
            return
            
        # 检查是否是群聊
        sender_id = message.get("FromUserName", "")
        chatroom_id = None
        sender_wxid = None
        
        if '@chatroom' in sender_id:
            # 是群聊消息
            chatroom_id = sender_id
            sender_info = message.get("Content", {}).get("at_user_list", [])
            if sender_info:
                sender_wxid = sender_info[0].get("UserName", "")
                if not sender_wxid:
                    # 兼容不同消息格式
                    sender_wxid = sender_info[0].get("wxid", "")
            if not sender_wxid:
                # 从Xml中解析
                content_xml = message.get("Content", {}).get("content", "")
                if content_xml:
                    match = re.search(r'wxid="([^"]+)"', content_xml)
                    if match:
                        sender_wxid = match.group(1)
        else:
            # 非群聊消息，直接取发送者ID
            sender_wxid = sender_id
            chatroom_id = sender_id
        
        # 如果没有找到发送者wxid，直接返回
        if not sender_wxid:
            return
            
        # 检查该用户是否有厅选择缓存
        if not hasattr(self, "hall_selection_cache") or not self.hall_selection_cache.get(sender_wxid):
            return
            
        # 获取缓存
        cache = self.hall_selection_cache.get(sender_wxid)
        
        # 检查是否超时（默认5分钟过期）
        current_time = time.time()
        if current_time - cache.get("timestamp", 0) > 300:  # 5分钟
            # 缓存过期，删除
            del self.hall_selection_cache[sender_wxid]
            return
            
        # 检查群组ID是否匹配
        if cache.get("chatroom_id") != chatroom_id:
            return
            
        # 获取消息内容
        content = message.get("Content", {}).get("content", "")
        if not content:
            return
            
        # 检查是否是数字
        if not content.isdigit():
            return
            
        # 转换为数字
        selection = int(content)
        
        # 获取选择类型
        selection_type = cache.get("type")
        
        # 处理选择
        if selection_type == "open_hall":
            # 选择开厅
            inactive_halls = cache.get("inactive_halls", [])
            
            # 检查选择是否有效
            if selection < 1 or selection > len(inactive_halls):
                await bot.send_text_message(
                    chatroom_id,
                    f"⚠️ 选择无效，请输入1-{len(inactive_halls)}之间的数字"
                )
                return
                
            # 获取选择的厅
            selected_hall = inactive_halls[selection - 1]
            hall_name = selected_hall["hall_name"]
            
            # 获取用户昵称
            try:
                # 获取群成员信息
                chatroom_members = await bot.get_chatroom_members(chatroom_id)
                nickname = ""
                
                for member in chatroom_members:
                    if member.get("UserName") == sender_wxid:
                        nickname = member.get("NickName", "")
                        # 检查群昵称
                        display_name = member.get("DisplayName", "")
                        if display_name:
                            nickname = display_name
                        break
                        
                if not nickname:
                    # 尝试获取用户个人信息
                    user_info = await bot.get_contact_detail(wxid=sender_wxid)
                    nickname = user_info.get("nickname", "用户")
            except Exception as e:
                self.log_error(f"获取用户昵称失败: {e}")
                nickname = "用户"
                
            # 执行开厅操作
            await self._do_open_hall(bot, chatroom_id, sender_wxid, nickname, hall_name)
            
            # 清除缓存
            del self.hall_selection_cache[sender_wxid]
            
        elif selection_type == "close_hall":
            # 选择关厅
            active_halls = cache.get("active_halls", [])
            
            # 检查选择是否有效
            if selection < 1 or selection > len(active_halls):
                await bot.send_text_message(
                    chatroom_id,
                    f"⚠️ 选择无效，请输入1-{len(active_halls)}之间的数字"
                )
                return
                
            # 获取选择的厅
            selected_hall = active_halls[selection - 1]
            record_id = selected_hall["record_id"]
            
            # 获取用户昵称
            try:
                # 获取群成员信息
                chatroom_members = await bot.get_chatroom_members(chatroom_id)
                nickname = ""
                
                for member in chatroom_members:
                    if member.get("UserName") == sender_wxid:
                        nickname = member.get("NickName", "")
                        # 检查群昵称
                        display_name = member.get("DisplayName", "")
                        if display_name:
                            nickname = display_name
                        break
                        
                if not nickname:
                    # 尝试获取用户个人信息
                    user_info = await bot.get_contact_detail(wxid=sender_wxid)
                    nickname = user_info.get("nickname", "用户")
            except Exception as e:
                self.log_error(f"获取用户昵称失败: {e}")
                nickname = "用户"
                
            # 执行关厅操作
            await self._do_close_hall(bot, chatroom_id, sender_wxid, nickname)
            
            # 清除缓存
            del self.hall_selection_cache[sender_wxid]
        elif selection_type == "delete_hall":
            # 选择删除厅名
            await self._handle_delete_confirmation(bot, chatroom_id, sender_wxid, nickname, selection)
            
            # 清除缓存
            del self.hall_selection_cache[sender_wxid]
        """
    
    @require_admin
    async def check_and_fill_hall_ids(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, params=None):
        """检查并为缺少唯一ID的厅生成ID
        
        Args:
            bot: 机器人API客户端
            chatroom_id: 群聊ID
            sender_wxid: 发送者微信ID
            nickname: 发送者昵称
            params: 参数字符串
        """
        self.log_info(f"管理员 {nickname}({sender_wxid}) 请求检查并补全厅唯一ID")
        
        # 显示处理中提示
        await bot.send_text_message(
            wxid=chatroom_id,
            content=f"⏳ 正在检查所有厅记录并补全缺失的唯一ID，请稍候...",
            at=sender_wxid
        )
        
        # 调用数据库方法检查并补全厅ID
        success, result = self.db.check_and_fill_all_hall_ids()
        
        if not success:
            await bot.send_text_message(
                wxid=chatroom_id,
                content=f"❌ 检查并补全厅唯一ID失败: {result.get('message', '未知错误')}",
                at=sender_wxid
            )
            self.log_error(f"检查并补全厅唯一ID失败: {result.get('error', '未知错误')}")
            return
        
        # 构建回复消息
        total = result.get("total", 0)
        missing = result.get("missing", 0)
        updated = result.get("updated", 0)
        
        reply = f"✅ 厅ID检查与补全已完成\n\n"
        reply += f"总厅数量: {total}\n"
        
        if missing == 0:
            reply += "所有厅均已有唯一ID，无需更新"
        else:
            updated_halls = result.get("updated_halls", [])
            reply += f"缺失ID数量: {missing}\n"
            reply += f"已更新数量: {updated}\n\n"
            
            # 显示所有更新的厅
            if updated > 0:
                reply += "已更新厅列表:\n"
                for i, hall in enumerate(updated_halls, 1):
                    hall_name = hall.get("hall_name", "未知")
                    hall_id = hall.get("hall_id", "未知")
                    owner = hall.get("owner_nickname", "未知")
                    reply += f"{i}. {hall_name} (ID: {hall_id}) - {owner}\n"
        
        # 发送回复
        await bot.send_text_message(
            wxid=chatroom_id,
            content=reply,
            at=sender_wxid
        )
        
        self.log_success(f"成功检查并补全厅唯一ID: 总数={total}, 缺失={missing}, 已更新={updated}")