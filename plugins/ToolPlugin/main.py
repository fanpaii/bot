import os
import re
import json
import datetime
import tomllib
from typing import List, Dict, Optional, Union
from pathlib import Path

from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, on_quote_message, on_at_message
from utils.admin_manager import AdminManager
from WechatAPI.Client import WechatAPIClient

from loguru import logger


class ToolPlugin(PluginBase):
    """工具插件，提供一些实用工具功能"""
    
    description = "工具插件"
    author = "XYBotV2"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        # 读取配置文件
        self.config = {}
        self.config_path = Path(__file__).parent / "config.toml"
        try:
            with open(self.config_path, "rb") as f:
                self.config = tomllib.load(f)
            
            # 基本配置
            self.enable = self.config.get("basic", {}).get("enable", True)
            
            # 指令配置
            commands = self.config.get("commands", {})
            self.revoke_cmd = commands.get("revoke_command", "撤回")
            self.info_cmd = commands.get("info_command", "信息")
            self.help_cmd = commands.get("help_command", "工具帮助")
            self.everyone_cmd = commands.get("everyone_command", "所有人")
            self.enable_cmd = commands.get("enable_command", "启用工具插件")
            self.disable_cmd = commands.get("disable_command", "禁用工具插件")

            # 命令映射表
            self.command_map = {
                self.revoke_cmd: self.handle_revoke_command,
                self.info_cmd: self.handle_info_command,
                self.help_cmd: self.show_help,
                self.everyone_cmd: self.handle_everyone_command,
                self.enable_cmd: self.handle_enable_plugin,
                self.disable_cmd: self.handle_disable_plugin
            }
            
            self.log_info("工具插件初始化成功")
        except Exception as e:
            self.log_error(f"加载工具插件配置文件失败: {str(e)}")
            self.enable = False
    
    def log_info(self, message: str):
        """记录信息日志"""
        logger.info(f"[工具插件] {message}")

    def log_error(self, message: str):
        """记录错误日志"""
        logger.error(f"[工具插件] {message}")

    def log_success(self, message: str):
        """记录成功日志"""
        logger.success(f"[工具插件] {message}")

    def log_warning(self, message: str):
        """记录警告日志"""
        logger.warning(f"[工具插件] {message}")
    
    async def async_init(self, bot=None):
        """异步初始化"""
        # 初始化管理员管理器
        self.admin_manager = AdminManager()
        await self.admin_manager.initialize()
        
        self.log_success("管理员管理器初始化成功")
    
    async def is_admin(self, chatroom_id: str, sender_wxid: str) -> bool:
        """检查用户是否是管理员"""
        try:
            # 先检查是否是超级管理员
            from plugins.AdminManager.main import AdminManagerPlugin
            admin_plugin = AdminManagerPlugin()
            if sender_wxid in admin_plugin.super_admins:
                self.log_info(f"用户 {sender_wxid} 是超级管理员")
                return True
        except Exception as e:
            self.log_error(f"检查超级管理员失败: {e}")
        
        # 检查是否是群管理员
        return await self.admin_manager.is_admin(chatroom_id, sender_wxid)
    
    @on_text_message(priority=50)
    async def handle_text_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本消息指令"""
        if not self.enable:
            return True
        
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        
        # 提取命令名称（第一个单词）
        command_parts = content.split(maxsplit=1)
        command = command_parts[0] if command_parts else ""
        
        # 检查是否是支持的命令
        if command in [self.info_cmd, self.help_cmd, self.everyone_cmd, self.enable_cmd, self.disable_cmd]:
            # 检查权限
            if not await self.is_admin(from_wxid, sender_wxid):
                await bot.send_text_message(from_wxid, "⚠️ 您没有权限执行此操作，请联系管理员")
                return False
                
            # 根据命令调用相应的处理函数
            if command == self.info_cmd:
                await self.handle_info_command(bot, message)
            elif command == self.help_cmd:
                await self.show_help(bot, from_wxid, sender_wxid)
            elif command == self.everyone_cmd:
                await self.handle_everyone_command(bot, message)
            elif command == self.enable_cmd:
                await self.handle_enable_plugin(bot, message)
            elif command == self.disable_cmd:
                await self.handle_disable_plugin(bot, message)
                
            return False
        
        return True
    
    @on_quote_message(priority=60)
    async def handle_quote_commands(self, bot: WechatAPIClient, message: dict):
        """处理引用消息指令"""
        if not self.enable:
            return True
        
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        
        # 处理撤回命令
        if content == self.revoke_cmd:
            # 检查权限
            if not await self.is_admin(from_wxid, sender_wxid):
                await bot.send_text_message(from_wxid, "⚠️ 您没有权限执行此操作，请联系管理员")
                return False
            
            await self.handle_revoke_command(bot, message)
            return False
        
        return True
    
    async def handle_enable_plugin(self, bot: WechatAPIClient, message: dict):
        """处理启用工具插件命令"""
        from_wxid = message.get("FromWxid", "")
        
        if self.enable:
            await bot.send_text_message(from_wxid, "⚠️ 工具插件已处于启用状态")
            return
            
        try:
            # 更新内存中的状态
            self.enable = True
            
            # 更新配置文件
            if "basic" not in self.config:
                self.config["basic"] = {}
            self.config["basic"]["enable"] = True
            
            # 保存到配置文件
            await self._save_config()
            
            await bot.send_text_message(from_wxid, "✅ 工具插件已启用")
            self.log_success("工具插件已启用")
        except Exception as e:
            self.log_error(f"启用插件失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"❌ 启用工具插件失败: {str(e)}")
    
    async def handle_disable_plugin(self, bot: WechatAPIClient, message: dict):
        """处理禁用工具插件命令"""
        from_wxid = message.get("FromWxid", "")
        
        if not self.enable:
            await bot.send_text_message(from_wxid, "⚠️ 工具插件已处于禁用状态")
            return
            
        try:
            # 更新内存中的状态
            self.enable = False
            
            # 更新配置文件
            if "basic" not in self.config:
                self.config["basic"] = {}
            self.config["basic"]["enable"] = False
            
            # 保存到配置文件
            await self._save_config()
            
            await bot.send_text_message(from_wxid, "✅ 工具插件已禁用")
            self.log_success("工具插件已禁用")
        except Exception as e:
            self.log_error(f"禁用插件失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"❌ 禁用工具插件失败: {str(e)}")
    
    async def _save_config(self):
        """保存配置到文件"""
        # 由于tomllib只支持读取，不支持写入，我们需要使用其他方式
        # 这里使用简单的文本替换方式修改配置文件
        try:
            # 读取原配置文件内容
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
                
            # 修改enable值
            enable_value = "true" if self.enable else "false"
            content = re.sub(r'enable\s*=\s*(true|false)', f'enable = {enable_value}', content)
            
            # 写入配置文件
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(content)
                
            self.log_success(f"配置已保存，工具插件状态: {'启用' if self.enable else '禁用'}")
            return True
        except Exception as e:
            self.log_error(f"保存配置失败: {str(e)}")
            return False
    
    async def handle_revoke_command(self, bot: WechatAPIClient, message: dict):
        """处理撤回命令"""
        from_wxid = message.get("FromWxid", "")
        quote = message.get("Quote", {})
        
        # 确保消息中包含引用数据
        if not quote:
            # 不发送失败消息，只记录日志
            self.log_warning("撤回失败：未找到要撤回的消息，用户需要引用要撤回的消息")
            return
        
        try:
            # 获取被引用消息的信息
            new_msg_id = int(quote.get("NewMsgId", 0))
            create_time = int(quote.get("Createtime", 0))
            client_msg_id = int(quote.get("MsgId", new_msg_id))
            
            if new_msg_id and create_time:
                try:
                    # 撤回消息，使用正确的参数顺序
                    result = await bot.revoke_message(from_wxid, client_msg_id, create_time, new_msg_id)
                    if result:
                        self.log_success(f"成功撤回消息: {new_msg_id}")
                except IndexError:
                    # 这个错误很可能是API内部日志格式问题，但撤回可能已经成功
                    self.log_success(f"撤回消息已发送，无法确认结果: {new_msg_id}")
                except Exception as e:
                    # 其他错误记录但不通知用户
                    self.log_error(f"撤回消息时发生异常: {e}")
            else:
                # 不发送失败消息，只记录日志
                self.log_warning("撤回消息失败: 无法获取消息信息")
        except Exception as e:
            # 不发送失败消息，只记录详细日志
            self.log_error(f"撤回消息处理出错: {e}")
            self.log_error(f"撤回消息详细信息 - 群聊ID: {from_wxid}, 消息ID: {new_msg_id if 'new_msg_id' in locals() else '未知'}")
    
    async def handle_info_command(self, bot: WechatAPIClient, message: dict):
        """处理信息命令"""
        from_wxid = message.get("FromWxid", "")
        at_users = message.get("Ats", [])
        
        # 确保消息中包含@用户
        if not at_users:
            await bot.send_text_message(from_wxid, "⚠️ 请@要查询的用户")
            return
        
        # 获取第一个被@的用户
        target_wxid = at_users[0]
        
        try:
            # 检查是否是群聊
            is_group = message.get("IsGroup", False)
            
            # 获取用户信息
            if is_group:
                # 获取群聊成员信息
                nickname = await bot.get_chatroom_nickname(from_wxid, target_wxid)
                
                # 尝试获取更多信息
                try:
                    user_info = await bot.get_contact(target_wxid)
                    user_detail = await bot.get_contract_detail(target_wxid, from_wxid)
                except Exception as e:
                    self.log_error(f"获取用户详细信息失败: {e}")
                    user_info = {}
                    user_detail = []
            else:
                # 获取私聊用户信息
                user_info = await bot.get_contact(target_wxid)
                nickname = user_info.get("nickname", "未知")
                user_detail = await bot.get_contract_detail(target_wxid)
            
            # 构建用户信息回复
            reply = f"📋 用户信息\n"
            reply += f"━━━━━━━━━━━━━━━━\n"
            reply += f"🆔 微信ID: {target_wxid}\n"
            reply += f"👤 昵称: {nickname}\n"
            
            # 添加额外信息
            if isinstance(user_info, dict):
                if "alias" in user_info and user_info["alias"]:
                    reply += f"📝 微信号: {user_info.get('alias', '未设置')}\n"
                
                if "nickname" in user_info and user_info["nickname"] != nickname:
                    reply += f"📋 微信昵称: {user_info.get('nickname', '未知')}\n"
                
                if "sex" in user_info:
                    gender = "男" if user_info.get("sex") == 1 else "女" if user_info.get("sex") == 2 else "未知"
                    reply += f"⚧️ 性别: {gender}\n"
                
                if "country" in user_info and user_info["country"]:
                    country = user_info.get("country", "")
                    province = user_info.get("province", "")
                    city = user_info.get("city", "")
                    location = " ".join(filter(None, [country, province, city]))
                    if location:
                        reply += f"📍 地区: {location}\n"
                
                if "signature" in user_info and user_info["signature"]:
                    signature = user_info.get("signature", "").replace("\n", " ")
                    # 限制签名长度
                    if len(signature) > 50:
                        signature = signature[:50] + "..."
                    reply += f"✍️ 签名: {signature}\n"
            
            # 发送用户信息
            await bot.send_text_message(from_wxid, reply)
            
        except Exception as e:
            self.log_error(f"获取用户信息时发生错误: {e}")
            await bot.send_text_message(from_wxid, f"⚠️ 获取用户信息失败: {str(e)}")
    
    async def show_help(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str):
        """显示帮助信息"""
        help_msg = f"📖 工具插件使用帮助\n"
        help_msg += f"━━━━━━━━━━━━━━━━\n"
        help_msg += f"🔹 管理员命令:\n"
        help_msg += f"  {self.revoke_cmd}  - 撤回消息 (需引用要撤回的消息)\n"
        help_msg += f"  {self.info_cmd} @用户  - 查询用户信息\n"
        help_msg += f"  {self.everyone_cmd} [内容]  - 发送@所有人消息\n"
        help_msg += f"  {self.enable_cmd}  - 启用工具插件\n"
        help_msg += f"  {self.disable_cmd}  - 禁用工具插件\n"
        help_msg += f"  {self.help_cmd}  - 显示本帮助信息\n\n"
        
        help_msg += f"🔹 使用说明:\n"
        help_msg += f"  1. 撤回命令需要引用要撤回的消息\n"
        help_msg += f"  2. 所有人命令后可添加文本内容\n"
        help_msg += f"  3. 所有命令仅限管理员使用"
        
        await bot.send_text_message(chatroom_id, help_msg)
    
    async def handle_everyone_command(self, bot: WechatAPIClient, message: dict):
        """处理@所有人命令"""
        from_wxid = message.get("FromWxid", "")
        content = message.get("Content", "").strip()
        
        # 检查是否是群聊
        is_group = message.get("IsGroup", False)
        if not is_group:
            await bot.send_text_message(from_wxid, "⚠️ 该命令只能在群聊中使用")
            return
        
        # 获取指令后面的文本内容
        # 示例: "所有人 注意查看群公告" -> "注意查看群公告"
        text_content = ""
        if len(content) > len(self.everyone_cmd):
            text_content = content[len(self.everyone_cmd):].strip()
        
        try:
            self.log_info(f"发送@所有人消息到群聊 {from_wxid}")
            
            # 直接使用notify@all发送@所有人消息
            message_content = "@所有人 " + (text_content or "")
            
            # 使用send_text_message并指定at参数为notify@all
            await bot.send_text_message(
                wxid=from_wxid,
                content=message_content,
                at="notify@all"
            )
            
            self.log_success(f"成功发送@所有人消息到群 {from_wxid}")
        except Exception as e:
            self.log_error(f"发送@所有人消息失败: {str(e)}")
            await bot.send_text_message(from_wxid, f"⚠️ 发送@所有人消息失败，请联系管理员") 