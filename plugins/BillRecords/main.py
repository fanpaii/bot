import os
import re
import json
import datetime
import tomllib
from typing import List, Dict, Tuple, Optional, Union, Set
from pathlib import Path

from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, on_image_message, on_voice_message, on_file_message, on_at_message, schedule
from utils.admin_manager import AdminManager
from WechatAPI.Client import WechatAPIClient

from loguru import logger

from .db_models import BillDB


class BillRecordsPlugin(PluginBase):
    """账单记录插件，用于记录群组成员的奖励和惩罚情况"""
    
    description = "账单记录插件"
    author = "XYBotV2"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        # 读取配置文件
        self.config = {}
        config_path = Path(__file__).parent / "config.toml"
        try:
            with open(config_path, "rb") as f:
                self.config = tomllib.load(f)
            
            # 基本配置
            self.enable = self.config.get("basic", {}).get("enable", True)
            
            # 指令配置
            commands = self.config.get("commands", {})
            self.reward_cmd = commands.get("reward_command", "奖励")
            self.punish_cmd = commands.get("punish_command", "惩罚")
            self.query_cmd = commands.get("query_command", "查账单记录")
            self.help_cmd = commands.get("help_command", "账单帮助")
            
            # 初始化数据库
            self.db = BillDB()
            
            logger.success("账单记录插件初始化成功")
        except Exception as e:
            logger.error(f"加载账单记录插件配置文件失败: {str(e)}")
            self.enable = False
    
    async def async_init(self, bot=None):
        """异步初始化"""
        # 初始化数据库
        await self.db.init_db()
        
        # 初始化管理员管理器
        self.admin_manager = AdminManager()
        await self.admin_manager.initialize()
        
        logger.success("账单记录插件数据库和管理员管理器初始化成功")
    
    async def is_admin(self, chatroom_id: str, sender_wxid: str) -> bool:
        """检查用户是否是管理员"""
        try:
            # 先检查是否是超级管理员
            from plugins.AdminManager.main import AdminManagerPlugin
            admin_plugin = AdminManagerPlugin()
            if sender_wxid in admin_plugin.super_admins:
                logger.info(f"用户 {sender_wxid} 是超级管理员")
                return True
        except Exception as e:
            logger.error(f"检查超级管理员失败: {e}")
        
        # 检查是否是群管理员
        return await self.admin_manager.is_admin(chatroom_id, sender_wxid)
    
    @on_text_message(priority=50)
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本消息指令"""
        if not self.enable:
            return True
        
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        is_group = message.get("IsGroup", False)
        
        # 只处理群聊消息
        if not is_group:
            return True
        
        # 获取发送者的群昵称
        try:
            sender_nickname = await bot.get_chatroom_nickname(from_wxid, sender_wxid)
        except Exception as e:
            logger.error(f"获取群昵称失败: {e}")
            sender_nickname = sender_wxid
        
        # 处理帮助命令
        if content == self.help_cmd:
            await self.show_help(bot, from_wxid, sender_wxid, sender_nickname)
            return False
        
        # 处理奖励命令
        if content.startswith(self.reward_cmd + " "):
            await self.handle_reward(bot, message, content, is_reward=True)
            return False
        
        # 处理惩罚命令
        if content.startswith(self.punish_cmd + " "):
            await self.handle_reward(bot, message, content, is_reward=False)
            return False
        
        # 处理查询账单记录命令
        query_pattern = re.compile(f"^{re.escape(self.query_cmd)}(?:\\s+(\\d{1,2}))?$")
        query_match = query_pattern.match(content)
        if query_match:
            month = query_match.group(1)
            month = int(month) if month else None
            await self.query_bill_records(bot, from_wxid, sender_wxid, sender_nickname, month)
            return False
        
        return True
    
    async def handle_reward(self, bot: WechatAPIClient, message: dict, content: str, is_reward: bool = True):
        """处理奖励或惩罚命令
        
        Args:
            bot: 微信API客户端
            message: 消息数据
            content: 消息内容
            is_reward: 是否是奖励命令，True为奖励，False为惩罚
        """
        from_wxid = message.get("FromWxid", "")  # 群聊ID
        sender_wxid = message.get("SenderWxid", "")  # 操作者wxid
        
        # 获取操作者昵称
        try:
            operator_nickname = await bot.get_chatroom_nickname(from_wxid, sender_wxid)
        except Exception as e:
            logger.error(f"获取操作者群昵称失败: {e}")
            operator_nickname = sender_wxid
        
        # 检查用户是否有管理员权限
        if not await self.is_admin(from_wxid, sender_wxid):
            await bot.send_text_message(from_wxid, f"⚠️ {operator_nickname} 您没有权限执行此操作，请联系管理员")
            return
        
        # 解析命令
        command = self.reward_cmd if is_reward else self.punish_cmd
        pattern = f"^{re.escape(command)}\\s+(\\d+(\\.\\d+)?)\\s*([^@]*)(@.+)$"
        match = re.match(pattern, content)
        
        if not match:
            await bot.send_text_message(from_wxid, f"⚠️ 命令格式错误，正确格式为：{command} 金额 [备注] @用户")
            return
        
        # 提取金额、备注和@的用户
        amount_str, _, remark, at_part = match.groups()
        amount = float(amount_str)
        remark = remark.strip()
        
        # 如果是惩罚，金额为负数
        if not is_reward:
            amount = -amount
        
        # 获取被@的用户wxid
        at_users = message.get("Ats", [])
        if not at_users:
            await bot.send_text_message(from_wxid, f"⚠️ 请@要{command}的用户")
            return
        
        # 只处理第一个被@的用户
        target_wxid = at_users[0]
        
        # 获取被@用户的昵称
        try:
            target_nickname = await bot.get_chatroom_nickname(from_wxid, target_wxid)
        except Exception as e:
            logger.error(f"获取目标用户群昵称失败: {e}")
            target_nickname = target_wxid
        
        # 记录账单
        if self.db.add_bill_record(
            from_wxid, target_wxid, target_nickname, 
            sender_wxid, operator_nickname, amount, remark
        ):
            # 获取用户当月余额
            balance = self.db.get_user_monthly_balance(from_wxid, target_wxid)
            
            # 获取当前时间
            now = datetime.datetime.now()
            time_str = now.strftime("%Y-%m-%d %H:%M:%S")
            
            # 构建回复消息
            operation = "奖励" if is_reward else "惩罚"
            reply = f"💰 {operation}记录已添加\n"
            reply += f"━━━━━━━━━━━━━━━━\n"
            reply += f"👤 用户: {target_nickname}\n"
            reply += f"👨‍💼 执行人: {operator_nickname}\n"
            reply += f"⏰ 时间: {time_str}\n"
            reply += f"💵 金额: {abs(amount)}\n"
            
            if remark:
                reply += f"📝 备注: {remark}\n"
            else:
                reply += f"📝 备注: 未填写备注\n"
            
            # 显示用户当月余额
            sign = "+" if balance >= 0 else ""
            reply += f"💳 本月余额: {sign}{balance:.2f}"
            
            await bot.send_text_message(from_wxid, reply)
        else:
            await bot.send_text_message(from_wxid, f"❌ {operation}记录添加失败，请稍后重试")
    
    async def query_bill_records(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, 
                              sender_nickname: str, month: Optional[int] = None):
        """查询账单记录
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊ID
            sender_wxid: 发送者wxid
            sender_nickname: 发送者昵称
            month: 要查询的月份，如果为None则查询当月
        """
        # 确定查询的年月
        now = datetime.datetime.now()
        year = now.year
        query_month = month if month is not None else now.month
        
        # 构建月份字符串
        month_str = f"{year}年{query_month}月"
        
        # 获取群聊中有账单记录的所有用户
        users = self.db.get_chatroom_users(chatroom_id, year, query_month)
        
        if not users:
            await bot.send_text_message(chatroom_id, f"📊 {month_str}没有账单记录")
            return
        
        # 构建回复消息
        reply = f"📊 {month_str}账单记录\n"
        reply += f"━━━━━━━━━━━━━━━━\n"
        
        # 按余额排序（从高到低）
        users.sort(key=lambda x: x["balance"], reverse=True)
        
        # 用户汇总信息
        for i, user in enumerate(users):
            nickname = user["nickname"]
            balance = user["balance"]
            sign = "+" if balance >= 0 else ""
            
            reply += f"{i+1}. {nickname}: {sign}{balance:.2f}\n"
        
        # 分隔线
        reply += f"━━━━━━━━━━━━━━━━\n"
        
        # 详细账单记录
        reply += f"📝 详细记录:\n"
        
        # 获取本月所有账单记录
        bills = self.db.get_monthly_bills(chatroom_id, year, query_month)
        
        # 最多显示15条记录，避免消息过长
        max_records = 15
        display_bills = bills[:max_records]
        
        for bill in display_bills:
            user_nickname = bill["user_nickname"]
            operator_nickname = bill["operator_nickname"]
            amount = bill["amount"]
            remark = bill["remark"] or "未填写备注"
            
            # 格式化创建时间
            create_time = bill["create_time"]
            if isinstance(create_time, str):
                # 如果是字符串，尝试解析为日期时间
                try:
                    dt = datetime.datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S")
                    time_str = dt.strftime("%m-%d %H:%M")
                except ValueError:
                    time_str = create_time
            else:
                # 获取创建时间的月日和时分
                time_str = datetime.datetime.now().strftime("%m-%d %H:%M")
            
            # 操作类型
            operation = "奖励" if amount > 0 else "惩罚"
            
            reply += f"[{time_str}] {operator_nickname} {operation} {user_nickname} {abs(amount)}，备注: {remark}\n"
        
        # 如果记录太多，显示提示信息
        if len(bills) > max_records:
            reply += f"...共有 {len(bills)} 条记录，仅显示最近 {max_records} 条\n"
        
        await bot.send_text_message(chatroom_id, reply)
    
    async def show_help(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, sender_nickname: str):
        """显示帮助信息
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊ID
            sender_wxid: 发送者wxid
            sender_nickname: 发送者昵称
        """
        help_msg = f"📖 账单记录插件使用帮助\n"
        help_msg += f"━━━━━━━━━━━━━━━━\n"
        help_msg += f"🔹 管理员命令:\n"
        help_msg += f"  {self.reward_cmd} 金额 [备注] @用户  - 为用户添加奖励记录\n"
        help_msg += f"  {self.punish_cmd} 金额 [备注] @用户  - 为用户添加惩罚记录\n"
        help_msg += f"  {self.query_cmd}  - 查询本月账单记录\n"
        help_msg += f"  {self.query_cmd} 月份  - 查询指定月份的账单记录 (例如: {self.query_cmd} 1 查询1月记录)\n"
        help_msg += f"  {self.help_cmd}  - 显示本帮助信息\n\n"
        
        help_msg += f"🔹 使用说明:\n"
        help_msg += f"  1. 只有管理员可以添加奖励和惩罚记录\n"
        help_msg += f"  2. 奖励金额为正数，惩罚金额会自动转为负数\n"
        help_msg += f"  3. 备注为可选，不填写时默认为\"未填写备注\"\n"
        help_msg += f"  4. 查询账单记录可查看所有用户的奖惩记录和余额\n"
        help_msg += f"  5. 余额为用户当月奖惩金额的总和\n"
        
        await bot.send_text_message(chatroom_id, help_msg) 