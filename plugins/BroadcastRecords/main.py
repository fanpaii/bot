import os
import re
import tomllib
import datetime
import asyncio
from typing import List, Dict, Any, Tuple, Optional, Union, Set
from os import PathLike

from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, on_image_message, on_voice_message, on_file_message, on_at_message, schedule
from utils.admin_manager import AdminManager
from WechatAPI.Client.base import WechatAPIClientBase
from WechatAPI.Client import WechatAPIClient

from loguru import logger

from .db_models import BroadcastDB


class 开播记录插件(PluginBase):
    """开播记录插件，用于记录主播的开播/下播时间"""
    
    description = "开播记录插件"
    author = "XYBotV2"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        # 读取配置文件
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        try:
            with open(config_path, "rb") as f:
                self.config = tomllib.load(f)
            
            # 基本配置
            self.enable = self.config.get("basic", {}).get("enable", True)
            
            # 命令配置
            self.commands = self.config.get("commands", {})
            self.cmd_enable = self.commands.get("enable_broadcast", "启用开播记录")
            self.cmd_disable = self.commands.get("disable_broadcast", "禁用开播记录")
            self.cmd_register = self.commands.get("register_time", "开播时间")
            self.cmd_temp_time = self.commands.get("set_temp_time", "临时开播时间")
            self.cmd_start = self.commands.get("start_broadcast", "开播")
            self.cmd_end = self.commands.get("end_broadcast", "下播")
            self.cmd_query = self.commands.get("query_records", "查开播记录")
            self.cmd_help = self.commands.get("help_command", "开播记录帮助")
            self.cmd_set_reminder = self.commands.get("set_reminder_time", "设置提醒时间")
            self.cmd_reminder_list = self.commands.get("reminder_list", "开播提醒列表")
            self.cmd_query_broadcaster = self.commands.get("query_broadcaster", "查询开播")
            
            # 初始化数据库
            self.db = BroadcastDB()
            
            # 存储提醒任务，格式：{日期_群组ID_主播ID: task}
            self.reminder_tasks = {}
            
            # 定时提醒设置（开播前几分钟提醒）
            self.remind_before_minutes = self.config.get("basic", {}).get("reminder_minutes", 10)
            
            # 保存bot引用，用于发送消息
            self.bot = None
            
            # 日志前缀
            self.log_prefix = "[开播记录] "
            
            logger.success(f"{self.log_prefix}开播记录插件初始化成功")
        except Exception as e:
            logger.error(f"{self.log_prefix}加载开播记录插件配置文件失败: {str(e)}")
            self.enable = False
            
    async def async_init(self, bot=None):
        """异步初始化"""
        # 初始化管理员管理器
        self.admin_manager = AdminManager()
        await self.admin_manager.initialize()
        logger.success(f"{self.log_prefix}管理员管理器初始化成功")
        
        # 插件启动时，为所有群的所有主播设置提醒任务
        if self.enable:
            try:
                # 延迟几秒再设置，确保bot已经初始化完成
                await asyncio.sleep(5)
                await self.setup_all_broadcaster_reminders()
                logger.info(f"{self.log_prefix}已设置所有主播的提醒任务")
            except Exception as e:
                logger.error(f"{self.log_prefix}设置开播提醒任务失败: {str(e)}")
        
    async def is_admin(self, chatroom_id: str, sender_wxid: str) -> bool:
        """检查用户是否是管理员"""
        try:
            # 先检查是否是超级管理员
            from plugins.AdminManager.main import AdminManagerPlugin
            admin_plugin = AdminManagerPlugin()
            if sender_wxid in admin_plugin.super_admins:
                logger.info(f"{self.log_prefix}用户 {sender_wxid} 是超级管理员")
                return True
        except Exception as e:
            logger.error(f"{self.log_prefix}检查超级管理员失败: {e}")
        
        # 检查是否是群管理员
        return await self.admin_manager.is_admin(chatroom_id, sender_wxid) 

    @on_text_message(priority=50)
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本消息指令"""
        if not self.enable:
            return True
        
        # 保存bot引用，用于发送提醒消息
        self.bot = bot
        
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        is_group = message.get("IsGroup", False)
        
        # 只处理群聊消息
        if not is_group:
            return True
        
        # 获取群昵称
        try:
            # 使用项目内置的获取群昵称方法
            nickname = await bot.get_chatroom_nickname(from_wxid, sender_wxid)
        except Exception as e:
            logger.error(f"获取群昵称失败: {e}")
            nickname = sender_wxid
        
        # 处理帮助命令
        if content == self.cmd_help:
            await self.show_help(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理查询开播指令
        if content == self.cmd_query_broadcaster:
            await self.query_broadcaster_info(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理查开播记录命令
        query_pattern = re.compile(f"^{re.escape(self.cmd_query)}(?:\\s+(\\d+)-(\\d+))?$")
        query_match = query_pattern.match(content)
        if query_match:
            month = query_match.group(1)
            day = query_match.group(2)
            if month and day:
                # 查询指定日期
                await self.query_broadcast_records(bot, from_wxid, sender_wxid, nickname, int(month), int(day))
            else:
                # 查询当日记录
                await self.query_broadcast_records(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理开播提醒列表命令
        if content == self.cmd_reminder_list:
            await self.show_reminder_list(bot, from_wxid, sender_wxid, nickname)
            return False
            
        # 启用开播记录命令
        if content == self.cmd_enable:
            await self.enable_broadcast(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 禁用开播记录命令
        if content == self.cmd_disable:
            await self.disable_broadcast(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理开播命令
        if content == self.cmd_start:
            await self.start_broadcast(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理下播命令
        if content == self.cmd_end:
            await self.end_broadcast(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理设置提醒时间命令
        reminder_pattern = re.compile(f"^{re.escape(self.cmd_set_reminder)}\\s+(\\d+)$")
        reminder_match = reminder_pattern.match(content)
        if reminder_match:
            minutes = int(reminder_match.group(1))
            await self.set_reminder_minutes(bot, from_wxid, sender_wxid, nickname, minutes)
            return False
        
        # 处理开播时间命令 - 支持多个时间段，例如"开播时间 9-12 14-18 晚班"
        time_pattern = re.compile(f"^{re.escape(self.cmd_register)}\\s+((?:\\d+(?:\\.\\d+)?-\\d+(?:\\.\\d+)?\\s*)+)(?:\\s+([^\\d\\s].+))?$")
        time_match = time_pattern.match(content)
        if time_match:
            time_slots_str = time_match.group(1).strip()
            slot_name = time_match.group(2)  # 可选的时间段名称
            
            # 解析多个时间段
            time_slots = re.findall(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", time_slots_str)
            
            if not time_slots:
                await bot.send_text_message(from_wxid, "⚠️ 时间格式错误: 请输入有效的时间段，例如 '9-12 14-18'")
                return False
                
            # 检查输入的多个时间段之间是否存在冲突
            parsed_slots = []
            for time_start, time_end in time_slots:
                try:
                    broadcast_time_start = self._parse_time_string(time_start)
                    broadcast_time_end = self._parse_time_string(time_end)
                    parsed_slots.append((broadcast_time_start, broadcast_time_end))
                except ValueError as e:
                    await bot.send_text_message(from_wxid, f"⚠️ 时间格式错误: {str(e)}")
                    return False
            
            # 检查输入的多个时间段之间是否存在冲突
            conflicts = []
            for i, (start1, end1) in enumerate(parsed_slots):
                for j, (start2, end2) in enumerate(parsed_slots):
                    if i != j:  # 不和自己比较
                        if self._check_time_conflict(start1, end1, start2, end2):
                            conflicts.append(f"{start1.strftime('%H:%M')}-{end1.strftime('%H:%M')} 和 {start2.strftime('%H:%M')}-{end2.strftime('%H:%M')}")
            
            if conflicts:
                await bot.send_text_message(from_wxid, f"⚠️ 输入的时间段之间存在冲突:\n{', '.join(conflicts)}")
                return False
            
            # 逐个注册时间段
            updated_slots = []
            added_slots = []
            for broadcast_time_start, broadcast_time_end in parsed_slots:
                success, message = await self.register_broadcast_time(bot, from_wxid, sender_wxid, nickname, 
                                                              broadcast_time_start, broadcast_time_end, 
                                                              slot_name, send_message=False)
                if success:
                    if message.get("action") == "update":
                        updated_slots.append(f"{broadcast_time_start.strftime('%H:%M')}-{broadcast_time_end.strftime('%H:%M')}")
                    else:
                        added_slots.append(f"{broadcast_time_start.strftime('%H:%M')}-{broadcast_time_end.strftime('%H:%M')}")
            
            # 构建回复消息
            reply_msg = ""
            if added_slots:
                slot_info = f"[{slot_name}] " if slot_name else ""
                reply_msg += f"✅ 已为 {nickname} 添加开播时间段{slot_info}: {', '.join(added_slots)}\n"
            
            if updated_slots:
                slot_info = f"[{slot_name}] " if slot_name else ""
                reply_msg += f"🔄 已为 {nickname} 更新开播时间段{slot_info}: {', '.join(updated_slots)}"
            
            if reply_msg:
                await bot.send_text_message(from_wxid, reply_msg.strip())
            
            # 设置提醒任务
            broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, sender_wxid)
            if broadcaster_info:
                await self.setup_broadcaster_reminder(chatroom_id, broadcaster_info)
            
            return False
        
        # 处理临时开播时间命令
        temp_pattern = re.compile(f"^{re.escape(self.cmd_temp_time)}\\s+(\\d+(?:\\.\\d+)?)-(\\d+(?:\\.\\d+)?)$")
        temp_match = temp_pattern.match(content)
        if temp_match:
            time_start = temp_match.group(1)
            time_end = temp_match.group(2)
            
            # 处理时间格式
            try:
                broadcast_time_start = self._parse_time_string(time_start)
                broadcast_time_end = self._parse_time_string(time_end)
                await self.set_temp_time(bot, from_wxid, sender_wxid, nickname, broadcast_time_start, broadcast_time_end)
            except ValueError as e:
                await bot.send_text_message(from_wxid, f"⚠️ 时间格式错误: {str(e)}")
            return False
            
        # 继续处理其他消息
        return True
        
    def _parse_time_string(self, time_str: str) -> datetime.time:
        """
        解析时间字符串为datetime.time对象
        支持格式：
        - 整数小时: "18"
        - 小时.分钟: "18.30"
        """
        if "." in time_str:
            # 处理小时.分钟格式
            parts = time_str.split(".")
            if len(parts) != 2:
                raise ValueError("时间格式不正确，应为'小时.分钟'格式，例如'18.30'")
            
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                raise ValueError("小时和分钟必须为数字")
            
            # 检查时间范围
            if not (0 <= hour < 24):
                raise ValueError("小时必须在0-23之间")
            
            # 处理小数分钟，例如.5表示30分钟
            if len(parts[1]) == 1:
                minute = minute * 10
            elif len(parts[1]) > 2:
                # 截取前两位
                minute = int(parts[1][:2])
            
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
        
        # 确保都是整型，避免SQLite类型错误
        hour = int(hour)
        minute = int(minute)
        return datetime.time(hour, minute)
    
    def _time_minus_minutes(self, time_obj: datetime.time, minutes: int) -> datetime.time:
        """从时间对象中减去指定分钟数"""
        full_datetime = datetime.datetime.combine(datetime.date.today(), time_obj)
        result_datetime = full_datetime - datetime.timedelta(minutes=minutes)
        return result_datetime.time()
    
    async def register_broadcast_time(self, bot: WechatAPIClient, chatroom_id: str, 
                                   broadcaster_wxid: str, nickname: str, 
                                   time_start: datetime.time, time_end: datetime.time,
                                   slot_name: str = None, send_message: bool = True):
        """注册主播开播时间"""
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            if send_message:
                await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return False, {"error": "本群未启用开播记录功能"}
        
        # 确保time_start和time_end为datetime.time类型
        if not isinstance(time_start, datetime.time):
            raise TypeError("time_start必须是datetime.time类型")
        
        if not isinstance(time_end, datetime.time):
            raise TypeError("time_end必须是datetime.time类型")
        
        # 注册开播时间
        success, message = self.db.register_broadcaster(
            chatroom_id, broadcaster_wxid, nickname, time_start, time_end, slot_name, update_on_conflict=True
        )
        
        if success:
            time_start_str = time_start.strftime("%H:%M")
            time_end_str = time_end.strftime("%H:%M")
            
            if send_message:
                slot_info = f"[{slot_name}]" if slot_name else ""
                action = message.get("action", "")
                if action == "update":
                    reply_msg = f"🔄 已为 {nickname} 更新开播时间段{slot_info}: {time_start_str}-{time_end_str}"
                else:
                    reply_msg = f"✅ 已为 {nickname} 添加开播时间段{slot_info}: {time_start_str}-{time_end_str}"
                await bot.send_text_message(chatroom_id, reply_msg)
            
            # 设置提醒任务
            broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
            if broadcaster_info:
                await self.setup_broadcaster_reminder(chatroom_id, broadcaster_info)
        elif send_message:
            await bot.send_text_message(chatroom_id, f"❌ {message.get('error', '添加时间段失败')}")
        
        return success, message
    
    async def set_temp_time(self, bot: WechatAPIClient, chatroom_id: str, 
                         broadcaster_wxid: str, nickname: str, 
                         time_start: datetime.time, time_end: datetime.time):
        """设置临时开播时间"""
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
        
        # 检查是否已注册开播时间
        broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
        if not broadcaster_info:
            await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您尚未注册开播时间，请先使用 '{self.cmd_register} 开始时间-结束时间' 命令注册")
            return
        
        # 确保time_start和time_end为datetime.time类型
        if not isinstance(time_start, datetime.time):
            raise TypeError("time_start必须是datetime.time类型")
        
        if not isinstance(time_end, datetime.time):
            raise TypeError("time_end必须是datetime.time类型")
        
        # 设置临时开播时间
        success, message = self.db.set_temp_time(
            chatroom_id, broadcaster_wxid, nickname, time_start, time_end
        )
        
        if success:
            time_start_str = time_start.strftime("%H:%M")
            time_end_str = time_end.strftime("%H:%M")
            
            reg_time_start = broadcaster_info["broadcast_time_start"].strftime("%H:%M")
            reg_time_end = broadcaster_info["broadcast_time_end"].strftime("%H:%M")
            
            reply_msg = f"✅ 已为 {nickname} 设置今日临时开播时间段:\n"
            reply_msg += f"📅 临时: {time_start_str}-{time_end_str}\n"
            reply_msg += f"📅 固定: {reg_time_start}-{reg_time_end}"
            
            await bot.send_text_message(chatroom_id, reply_msg)
            
            # 取消原有提醒任务并设置新的提醒任务
            await self.cancel_broadcaster_reminder(chatroom_id, broadcaster_wxid)
            
            # 获取更新后的信息并设置新任务
            updated_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
            if updated_info:
                await self.setup_broadcaster_reminder(chatroom_id, updated_info)
        else:
            await bot.send_text_message(chatroom_id, f"❌ {message}")
    
    async def enable_broadcast(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """启用开播记录功能"""
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
        
        # 启用开播记录
        success = self.db.enable_group_broadcast(chatroom_id)
        
        if success:
            await bot.send_text_message(chatroom_id, f"✅ 已启用开播记录功能 (by {nickname})")
            
            # 为该群所有主播设置提醒任务
            await self.setup_all_broadcaster_reminders(chatroom_id)
        else:
            await bot.send_text_message(chatroom_id, f"❌ 启用开播记录功能失败")
    
    async def disable_broadcast(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """禁用开播记录功能"""
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
        
        # 禁用开播记录
        success = self.db.disable_group_broadcast(chatroom_id)
        
        if success:
            # 取消该群的所有提醒任务
            today = datetime.date.today()
            pattern = f"{today}_{chatroom_id}_"
            
            # 查找并取消匹配的任务
            task_keys = [k for k in self.reminder_tasks.keys() if k.startswith(pattern)]
            for key in task_keys:
                await self.cancel_reminder_task(key)
            
            await bot.send_text_message(chatroom_id, f"✅ 已禁用开播记录功能 (by {nickname})")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 禁用开播记录功能失败")
    
    async def start_broadcast(self, bot: WechatAPIClient, chatroom_id: str, broadcaster_wxid: str, nickname: str):
        """处理开播命令"""
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
        
        # 获取主播信息
        broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
        if not broadcaster_info:
            await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您尚未注册开播时间，请先使用 '{self.cmd_register} 开始时间-结束时间 [时间段名称]' 命令注册")
            return
        
        # 检查是否有时间段
        if not broadcaster_info.get("time_slots") or len(broadcaster_info["time_slots"]) == 0:
            await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您尚未设置任何开播时间段，请先注册开播时间")
            return
        
        # 获取主播时间段状态
        slots_status = self.db.check_time_slots_broadcast_status(chatroom_id, broadcaster_wxid)
        
        # 检查所有已过的时间段是否有未开播的
        current_time = datetime.datetime.now().time()
        today = datetime.date.today()
        current_dt = datetime.datetime.combine(today, current_time)
        
        # 查找当前或下一个时间段
        current_slot_index = -1
        for i, slot in enumerate(slots_status):
            slot_start = slot["broadcast_time_start"]
            slot_end = slot["broadcast_time_end"]
            
            start_dt = datetime.datetime.combine(today, slot_start)
            end_dt = datetime.datetime.combine(today, slot_end)
            
            # 处理跨天情况
            if end_dt < start_dt:
                end_dt += datetime.timedelta(days=1)
            
            # 当前时间处于该时间段内或接近该时间段开始（30分钟内）
            if (start_dt <= current_dt <= end_dt) or (start_dt > current_dt and (start_dt - current_dt).total_seconds() <= 1800):
                current_slot_index = i
                break
        
        # 检查之前时间段的开播状态
        missed_slots = []
        if current_slot_index > 0:
            for i in range(current_slot_index):
                slot = slots_status[i]
                if not slot["is_broadcasted"]:
                    # 构建未开播时间段信息
                    slot_name = f"[{slot['slot_name']}] " if slot['slot_name'] else ""
                    slot_time = f"{slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')}"
                    missed_slots.append(f"{slot_name}{slot_time}")
        
        # 取消提醒任务
        await self.cancel_broadcaster_reminder(chatroom_id, broadcaster_wxid)
        
        # 记录开播
        success, data = self.db.start_broadcast(nickname, broadcaster_wxid, nickname, chatroom_id)
        
        if not success:
            if data.get("already_started"):
                # 已经开播了
                broadcast_name = data.get("broadcast_name")
                start_time = data.get("start_time").strftime("%H:%M:%S")
                await bot.send_text_message(
                    chatroom_id, 
                    f"⚠️ {nickname} 您今天已经开播了\n开播时间: {start_time}"
                )
            else:
                # 其他错误
                await bot.send_text_message(chatroom_id, f"❌ 开播失败: {data.get('error', '未知错误')}")
            return
        
        # 开播成功，构建回复消息
        start_time = data.get("start_time")
        start_time_str = start_time.strftime("%H:%M:%S")
        
        # 获取要求时长（现在由主播的所有时间段决定）
        required_duration = data.get("required_duration", 0)
        
        # 计算预计结束时间（开始时间 + 要求时长）
        start_datetime = datetime.datetime.combine(datetime.date.today(), start_time)
        end_datetime = start_datetime + datetime.timedelta(minutes=required_duration)
        end_time = end_datetime.time()
        end_time_str = end_time.strftime("%H:%M:%S")
        
        # 构建回复消息
        reply_msg = f"🎉 {nickname} 开播成功！\n"
        reply_msg += f"⏰ 开播时间: {start_time_str}\n"
        
        # 显示当前使用的开播时间段
        reg_time_start = data.get("broadcast_time_start").strftime("%H:%M")
        reg_time_end = data.get("broadcast_time_end").strftime("%H:%M")
        
        # 获取时间段名称（如果有）
        time_slot_id = data.get("time_slot_id")
        slot_name = ""
        if time_slot_id and broadcaster_info["time_slots"]:
            for slot in broadcaster_info["time_slots"]:
                if slot["id"] == time_slot_id:
                    if slot["slot_name"]:
                        slot_name = f"[{slot['slot_name']}] "
                    break
        
        if data.get("is_temp_time"):
            reply_msg += f"📅 今日临时开播时段: {reg_time_start}-{reg_time_end}\n"
        else:
            reply_msg += f"📅 当前开播时段: {slot_name}{reg_time_start}-{reg_time_end}\n"
        
        # 显示已错过的时间段
        if missed_slots:
            reply_msg += f"⚠️ 已错过的时间段: {', '.join(missed_slots)}\n"
        
        # 显示预计结束时间
        reply_msg += f"🕙 预计结束时间: {end_time_str} (累计要求时长)\n"
        
        # 如果迟到，添加迟到信息
        if data.get("is_late"):
            late_minutes = data.get("late_minutes", 0)
            if late_minutes < 60:
                late_str = f"{late_minutes}分钟"
            else:
                late_hours = late_minutes // 60
                late_min = late_minutes % 60
                late_str = f"{late_hours}小时{late_min}分钟" if late_min > 0 else f"{late_hours}小时"
            
            reply_msg += f"⚠️ 您已迟到 {late_str}\n"
        
        # 格式化显示要求时长
        hours = required_duration // 60
        minutes = required_duration % 60
        time_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
        
        reply_msg += f"📊 累计要求开播时长: {time_str}"
        
        await bot.send_text_message(chatroom_id, reply_msg)
    
    async def end_broadcast(self, bot: WechatAPIClient, chatroom_id: str, broadcaster_wxid: str, nickname: str):
        """处理下播命令"""
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
        
        # 获取主播时间段状态
        slots_status = self.db.check_time_slots_broadcast_status(chatroom_id, broadcaster_wxid)
        
        # 检查当前激活的时间段
        current_slot = None
        current_slot_index = -1
        
        for i, slot in enumerate(slots_status):
            if slot["is_active"]:
                current_slot = slot
                current_slot_index = i
                break
        
        # 记录下播
        success, data = self.db.end_broadcast(broadcaster_wxid, chatroom_id)
        
        if not success:
            if data.get("not_started"):
                # 尚未开播
                await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您今天尚未开播，请先开播")
            else:
                # 其他错误
                await bot.send_text_message(chatroom_id, f"❌ 下播失败: {data.get('error', '未知错误')}")
            return
        
        # 下播成功，构建回复消息
        start_time = data.get("start_time").strftime("%H:%M:%S")
        end_time = data.get("end_time").strftime("%H:%M:%S")
        duration = data.get("duration", 0)
        
        # 格式化时长
        if duration < 60:
            duration_str = f"{duration}分钟"
        else:
            hours = duration // 60
            minutes = duration % 60
            duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
        
        # 获取今日总开播时长
        today_duration = data.get("today_duration", 0)
        if today_duration < 60:
            today_duration_str = f"{today_duration}分钟"
        else:
            hours = today_duration // 60
            minutes = today_duration % 60
            today_duration_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
        
        # 获取主播信息以查找时间段名称
        broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
        
        # 获取时间段信息
        time_slot_id = data.get("time_slot_id")
        slot_name = ""
        if time_slot_id and broadcaster_info and broadcaster_info.get("time_slots"):
            for slot in broadcaster_info["time_slots"]:
                if slot["id"] == time_slot_id:
                    if slot.get("slot_name"):
                        slot_name = f"[{slot['slot_name']}] "
                    break
        
        # 检查未开播的时间段
        missed_slots = []
        if current_slot_index > 0:
            for i in range(current_slot_index):
                slot = slots_status[i]
                if not slot["is_broadcasted"]:
                    # 构建未开播时间段信息
                    missed_slot_name = f"[{slot['slot_name']}] " if slot['slot_name'] else ""
                    slot_time = f"{slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')}"
                    missed_slots.append(f"{missed_slot_name}{slot_time}")
        
        # 构建回复消息
        reply_msg = f"✅ {nickname} 下播成功！\n"
        
        # 如果有时间段名称，显示
        if slot_name:
            reply_msg += f"📅 时间段: {slot_name}\n"
        
        # 显示已错过的时间段
        if missed_slots:
            reply_msg += f"⚠️ 已错过的时间段: {', '.join(missed_slots)}\n"
        
        reply_msg += f"⏰ 开播时间: {start_time}\n"
        reply_msg += f"⏰ 下播时间: {end_time}\n"
        reply_msg += f"⌛ 本次开播时长: {duration_str}\n"
        reply_msg += f"📊 今日总开播时长: {today_duration_str}\n"
        
        # 如果提前下播，添加提前下播信息
        if data.get("is_early"):
            early_minutes = data.get("early_minutes", 0)
            if early_minutes < 60:
                early_str = f"{early_minutes}分钟"
            else:
                early_hours = early_minutes // 60
                early_min = early_minutes % 60
                early_str = f"{early_hours}小时{early_min}分钟" if early_min > 0 else f"{early_hours}小时"
            
            reply_msg += f"⚠️ 您提前下播了 {early_str}\n"
        
        # 是否满足要求时长 - 现在基于主播所有时间段总时长
        required_duration = data.get("required_duration", 0)
        is_meet_required = data.get("is_meet_required", False)
        
        # 格式化要求时长
        if required_duration < 60:
            required_str = f"{required_duration}分钟"
        else:
            req_hours = required_duration // 60
            req_minutes = required_duration % 60
            required_str = f"{req_hours}小时{req_minutes}分钟" if req_minutes > 0 else f"{req_hours}小时"
        
        # 显示时间段要求
        reply_msg += f"📊 累计要求开播时长: {required_str}\n"
        
        if is_meet_required:
            reply_msg += f"✅ 已满足要求开播时长"
        else:
            remain_minutes = data.get("remain_minutes", 0)
            if remain_minutes < 60:
                remain_str = f"{remain_minutes}分钟"
            else:
                remain_hours = remain_minutes // 60
                remain_min = remain_minutes % 60
                remain_str = f"{remain_hours}小时{remain_min}分钟" if remain_min > 0 else f"{remain_hours}小时"
            
            reply_msg += f"⚠️ 未满足开播时长要求，还差 {remain_str}"
        
        await bot.send_text_message(chatroom_id, reply_msg)
        
        # 重新设置提醒任务，因为现在已经结束开播
        if broadcaster_info:
            await self.setup_broadcaster_reminder(chatroom_id, broadcaster_info)
    
    async def show_help(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """显示开播记录插件的帮助信息"""
        help_msg = f"📋 开播记录插件指令说明\n\n"
        
        help_msg += f"🔹 基础功能:\n"
        help_msg += f"  {self.cmd_register} 18-22 - 设置开播时间段\n"
        help_msg += f"  {self.cmd_register} 18-22 晚班 - 设置带名称的时间段\n"
        help_msg += f"  {self.cmd_temp_time} 19-23 - 设置今日临时开播时间\n"
        help_msg += f"  {self.cmd_start} - 开始开播\n"
        help_msg += f"  {self.cmd_end} - 结束开播\n"
        help_msg += f"  {self.cmd_query} - 查询今日开播记录\n"
        help_msg += f"  {self.cmd_query} 3-15 - 查询指定日期开播记录\n"
        help_msg += f"  {self.cmd_query_broadcaster} - 查询个人开播设置信息\n"
        
        help_msg += f"\n🔹 管理功能:\n"
        help_msg += f"  {self.cmd_enable} - 启用开播记录功能\n"
        help_msg += f"  {self.cmd_disable} - 禁用开播记录功能\n"
        help_msg += f"  {self.cmd_set_reminder} 15 - 设置开播前15分钟提醒\n"
        
        help_msg += f"\n🔹 补充说明:\n"
        help_msg += f"  开播时间设置支持两种格式:\n"
        help_msg += f"    - 整点: 18-22 (表示晚上6点到10点)\n"
        help_msg += f"    - 精确到分钟: 18.30-22.00 (表示晚上6点30分到10点整)\n"
        help_msg += f"  支持一次性设置多个时间段，例如: {self.cmd_register} 9-12 14-18 晚班\n"
        help_msg += f"  同一个主播可以设置多个不重叠的时间段\n"
        help_msg += f"  如果设置的时间段与已有时间段冲突，会自动更新为新设置的时间段\n"
        help_msg += f"  可以为时间段添加名称，例如：{self.cmd_register} 9-12 早班\n"
        help_msg += f"  开播/下播时会自动匹配当前或最近的时间段\n"
        help_msg += f"  要求开播时长为所有时间段的累计时长\n"
        help_msg += f"  系统会在开播时间前{self.remind_before_minutes}分钟自动@提醒主播\n"
        help_msg += f"  如设置了临时时间，则按临时时间发送提醒\n"
        
        await bot.send_text_message(chatroom_id, help_msg)
    
    async def query_broadcaster_info(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """查询主播的开播信息（固定时间段和临时时间段）
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊的wxid
            sender_wxid: 用户的wxid
            nickname: 用户昵称
        """
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
        
        # 获取主播信息
        broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, sender_wxid)
        if not broadcaster_info:
            await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 您尚未注册开播时间，请先使用 '{self.cmd_register} 开始时间-结束时间 [时间段名称]' 命令注册")
            return
        
        # 获取当前时间和日期
        now = datetime.datetime.now()
        today = datetime.date.today()
        now_time = now.time()
        
        # 构建回复消息
        reply_msg = f"📋 {nickname} 的开播信息：\n\n"
        
        # 检查当前是否已开播
        is_broadcasting = broadcaster_info.get("is_active", False)
        
        if is_broadcasting:
            # 获取最近的开播记录
            today_records = self.db.get_broadcast_records(chatroom_id, broadcaster_wxid=sender_wxid, date=today)
            active_record = next((r for r in today_records if r.get("is_active", False)), None)
            
            if active_record:
                # 开播信息
                start_time = active_record.get("start_time").strftime("%H:%M:%S")
                start_datetime = datetime.datetime.combine(today, active_record.get("start_time"))
                
                # 计算已开播时长
                if now < start_datetime:  # 如果开始时间大于当前时间，可能是昨天开始的
                    start_datetime = datetime.datetime.combine(today - datetime.timedelta(days=1), active_record.get("start_time"))
                
                duration_min = int((now - start_datetime).total_seconds() / 60)
                duration_str = f"{duration_min // 60}小时{duration_min % 60}分钟" if duration_min >= 60 else f"{duration_min}分钟"
                
                # 添加开播状态
                reply_msg += f"🔴 当前状态：正在开播\n"
                reply_msg += f"⏰ 开播时间：{start_time}\n"
                reply_msg += f"⌛ 已开播时长：{duration_str}\n"
                
                # 是否迟到
                if active_record.get("is_late", False):
                    late_min = active_record.get("late_minutes", 0)
                    if late_min < 60:
                        late_str = f"{late_min}分钟"
                    else:
                        late_str = f"{late_min // 60}小时{late_min % 60}分钟"
                    reply_msg += f"⚠️ 迟到时长：{late_str}\n"
                
                # 获取时间段信息
                time_slot_id = active_record.get("time_slot_id")
                if time_slot_id:
                    for slot in broadcaster_info.get("time_slots", []):
                        if slot["id"] == time_slot_id:
                            slot_name = f"[{slot['slot_name']}] " if slot.get('slot_name') else ""
                            slot_start = slot["broadcast_time_start"].strftime("%H:%M")
                            slot_end = slot["broadcast_time_end"].strftime("%H:%M")
                            if active_record.get("is_temp_time", False):
                                reply_msg += f"📅 当前临时时间段：{slot_name}{slot_start}-{slot_end}\n"
                            else:
                                reply_msg += f"📅 当前固定时间段：{slot_name}{slot_start}-{slot_end}\n"
                            break
                
                # 计算预计结束时间
                required_duration = active_record.get("required_duration", 0)
                end_datetime = start_datetime + datetime.timedelta(minutes=required_duration)
                end_time = end_datetime.time()
                end_time_str = end_time.strftime("%H:%M:%S")
                reply_msg += f"🕙 预计结束时间：{end_time_str}（按累计要求时长）\n\n"
        else:
            reply_msg += f"⚪ 当前状态：未开播\n\n"
            
        # 获取临时时间信息
        has_temp_time = broadcaster_info.get("has_temp_time", False)
        if has_temp_time:
            temp_start = broadcaster_info.get("temp_time_start").strftime("%H:%M")
            temp_end = broadcaster_info.get("temp_time_end").strftime("%H:%M")
            
            # 计算提醒时间
            remind_time = self._time_minus_minutes(broadcaster_info.get("temp_time_start"), self.remind_before_minutes)
            remind_time_str = remind_time.strftime("%H:%M")
            
            reply_msg += f"⏱️ 今日临时开播时间：{temp_start}-{temp_end}\n"
            reply_msg += f"🔔 提醒时间：{remind_time_str}\n\n"
            
        # 获取固定时间段信息
        time_slots = broadcaster_info.get("time_slots", [])
        if time_slots:
            reply_msg += f"📆 固定开播时间段（{len(time_slots)}个）：\n"
            
            # 按时间排序
            time_slots.sort(key=lambda x: x["broadcast_time_start"])
            
            # 查找当前适用的时间段
            current_slot = None
            upcoming_slot = None
            active_slot = None
            passed_slots = []
            
            for slot in time_slots:
                slot_start = slot["broadcast_time_start"]
                slot_end = slot["broadcast_time_end"]
                
                # 处理跨天情况
                today = datetime.date.today()
                start_dt = datetime.datetime.combine(today, slot_start)
                end_dt = datetime.datetime.combine(today, slot_end)
                if end_dt < start_dt:
                    end_dt += datetime.timedelta(days=1)
                
                now_dt = datetime.datetime.combine(today, now_time)
                
                # 判断时间段状态
                if slot.get("is_active", False):
                    active_slot = slot
                elif start_dt <= now_dt <= end_dt:
                    # 当前时间在时间段内
                    current_slot = slot
                elif start_dt > now_dt:
                    # 未来的时间段
                    if upcoming_slot is None or start_dt < datetime.datetime.combine(today, upcoming_slot["broadcast_time_start"]):
                        upcoming_slot = slot
                elif now_dt > end_dt:
                    # 已过的时间段
                    passed_slots.append(slot)
            
            # 显示活跃时间段
            if active_slot:
                slot_name = f"[{active_slot['slot_name']}] " if active_slot.get('slot_name') else ""
                slot_start = active_slot["broadcast_time_start"].strftime("%H:%M")
                slot_end = active_slot["broadcast_time_end"].strftime("%H:%M")
                reply_msg += f"  🔴 当前激活：{slot_name}{slot_start}-{slot_end}\n"
            
            # 显示当前时间段
            elif current_slot:
                slot_name = f"[{current_slot['slot_name']}] " if current_slot.get('slot_name') else ""
                slot_start = current_slot["broadcast_time_start"].strftime("%H:%M")
                slot_end = current_slot["broadcast_time_end"].strftime("%H:%M")
                reply_msg += f"  ⏰ 当前时段：{slot_name}{slot_start}-{slot_end}\n"
            
            # 显示未来时间段
            if upcoming_slot and upcoming_slot != active_slot and upcoming_slot != current_slot:
                slot_name = f"[{upcoming_slot['slot_name']}] " if upcoming_slot.get('slot_name') else ""
                slot_start = upcoming_slot["broadcast_time_start"].strftime("%H:%M")
                slot_end = upcoming_slot["broadcast_time_end"].strftime("%H:%M")
                
                # 计算提醒时间
                remind_time = self._time_minus_minutes(upcoming_slot["broadcast_time_start"], self.remind_before_minutes)
                remind_time_str = remind_time.strftime("%H:%M")
                
                reply_msg += f"  ⏳ 即将开播：{slot_name}{slot_start}-{slot_end}\n"
                reply_msg += f"  🔔 提醒时间：{remind_time_str}\n"
            
            # 显示其他时间段
            other_slots = [s for s in time_slots if s != active_slot and s != current_slot and s != upcoming_slot and s not in passed_slots]
            if other_slots:
                for slot in other_slots:
                    slot_name = f"[{slot['slot_name']}] " if slot.get('slot_name') else ""
                    slot_start = slot["broadcast_time_start"].strftime("%H:%M")
                    slot_end = slot["broadcast_time_end"].strftime("%H:%M")
                    reply_msg += f"  ⏰ 其他时段：{slot_name}{slot_start}-{slot_end}\n"
            
            # 显示已错过时间段
            if passed_slots and not is_broadcasting:
                reply_msg += f"\n  ⚠️ 今日已错过的时间段：\n"
                for slot in passed_slots:
                    slot_name = f"[{slot['slot_name']}] " if slot.get('slot_name') else ""
                    slot_start = slot["broadcast_time_start"].strftime("%H:%M")
                    slot_end = slot["broadcast_time_end"].strftime("%H:%M")
                    reply_msg += f"  - {slot_name}{slot_start}-{slot_end}\n"
            
            # 计算累计要求时长
            total_required_minutes = 0
            for slot in time_slots:
                start_time = slot["broadcast_time_start"]
                end_time = slot["broadcast_time_end"]
                
                # 计算时间差（处理跨天情况）
                if end_time < start_time:  # 跨天
                    end_datetime = datetime.datetime.combine(today + datetime.timedelta(days=1), end_time)
                else:
                    end_datetime = datetime.datetime.combine(today, end_time)
                
                start_datetime = datetime.datetime.combine(today, start_time)
                delta = end_datetime - start_datetime
                total_required_minutes += delta.seconds // 60
            
            # 格式化显示要求时长
            if total_required_minutes > 0:
                hours = total_required_minutes // 60
                minutes = total_required_minutes % 60
                time_str = f"{hours}小时{minutes}分钟" if minutes > 0 else f"{hours}小时"
                reply_msg += f"\n📊 累计要求开播时长：{time_str}"
        else:
            reply_msg += f"⚠️ 您尚未设置固定开播时间段"
        
        # 添加常用指令提示
        reply_msg += f"\n\n💡 常用指令：\n"
        reply_msg += f"  {self.cmd_start} - 开始开播\n"
        reply_msg += f"  {self.cmd_end} - 结束开播\n"
        reply_msg += f"  {self.cmd_temp_time} 开始时间-结束时间 - 设置临时开播时间\n"
        reply_msg += f"  {self.cmd_help} - 查看完整帮助"
        
        await bot.send_text_message(chatroom_id, reply_msg)
    
    async def setup_all_broadcaster_reminders(self, chatroom_id: str = None):
        """为所有主播设置提醒任务
        
        Args:
            chatroom_id: 可选，指定群ID。如果不指定，则为所有群设置
        """
        try:
            # 获取需要处理的群组
            if chatroom_id:
                # 只获取指定群聊
                groups = [{"chatroom_id": chatroom_id}]
            else:
                # 获取所有启用了开播记录的群组
                groups = self.db.get_enabled_groups()
            
            # 处理各群组
            for group in groups:
                group_id = group["chatroom_id"]
                
                # 获取该群所有主播信息
                all_broadcasters = self.db.get_all_broadcasters(group_id)
                
                # 为每个主播设置提醒任务
                for broadcaster in all_broadcasters:
                    await self.setup_broadcaster_reminder(group_id, broadcaster)
                
                logger.info(f"{self.log_prefix}群[{group_id}] 已设置 {len(all_broadcasters)} 个主播的提醒任务")
        except Exception as e:
            logger.error(f"{self.log_prefix}设置提醒任务失败: {str(e)}")
    
    async def update_all_broadcaster_reminders(self):
        """更新所有主播的提醒任务，用于设置修改后更新"""
        try:
            # 取消所有现有任务
            for task_key in list(self.reminder_tasks.keys()):
                await self.cancel_reminder_task(task_key)
            
            # 重新设置所有任务
            await self.setup_all_broadcaster_reminders()
        except Exception as e:
            logger.error(f"{self.log_prefix}更新所有提醒任务失败: {str(e)}")
    
    async def setup_broadcaster_reminder(self, chatroom_id: str, broadcaster_info: dict):
        """为单个主播设置提醒任务
        
        Args:
            chatroom_id: 群聊ID
            broadcaster_info: 主播信息字典
        """
        if not broadcaster_info:
            logger.info(f"{self.log_prefix}主播信息为空，无法设置提醒任务")
            return
            
        # 检查主播是否已经开播
        if broadcaster_info.get("is_active", False):
            logger.info(f"{self.log_prefix}主播[{broadcaster_info.get('broadcaster_nickname')}] 已开播，不需要设置提醒")
            return
        
        try:
            broadcaster_wxid = broadcaster_info.get("broadcaster_wxid", "")
            broadcaster_nickname = broadcaster_info.get("broadcaster_nickname", "未知用户")
            
            # 检查必要的键是否存在
            if not broadcaster_wxid:
                logger.error(f"{self.log_prefix}主播[{broadcaster_nickname}] ID不存在，无法设置提醒")
                return
                
            # 确定使用常规时间还是临时时间
            if broadcaster_info.get("has_temp_time", False):
                # 使用临时时间
                if "temp_time_start" not in broadcaster_info:
                    logger.error(f"{self.log_prefix}主播[{broadcaster_nickname}] 临时开始时间不存在，无法设置提醒")
                    return
                broadcast_time_start = broadcaster_info["temp_time_start"]
                time_type = "临时"
                slot_name = None
            else:
                # 使用最近的常规时间段
                if "time_slots" not in broadcaster_info or not broadcaster_info["time_slots"]:
                    logger.error(f"{self.log_prefix}主播[{broadcaster_nickname}] 没有设置时间段，无法设置提醒")
                    return
                
                # 获取当前时间最近的时间段
                now = datetime.datetime.now().time()
                today = datetime.date.today()
                now_dt = datetime.datetime.combine(today, now)
                
                # 查找最近的时间段
                min_diff = float('inf')
                nearest_slot = None
                slot_name = None
                
                for slot in broadcaster_info["time_slots"]:
                    start_time = slot["broadcast_time_start"]
                    start_dt = datetime.datetime.combine(today, start_time)
                    
                    # 如果开始时间已过，说明是今天的时间段
                    if start_dt < now_dt:
                        diff = (now_dt - start_dt).total_seconds()
                    else:
                        diff = (start_dt - now_dt).total_seconds()
                    
                    if diff < min_diff:
                        min_diff = diff
                        nearest_slot = slot
                        if slot.get("slot_name"):
                            slot_name = slot["slot_name"]
                
                if not nearest_slot:
                    logger.error(f"{self.log_prefix}主播[{broadcaster_nickname}] 无法找到最近的时间段，无法设置提醒")
                    return
                    
                broadcast_time_start = nearest_slot["broadcast_time_start"]
                time_type = "固定"
            
            # 生成任务key
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{broadcaster_wxid}"
            
            # 计算提醒时间
            now = datetime.datetime.now()
            reminder_time = self._time_minus_minutes(broadcast_time_start, self.remind_before_minutes)
            reminder_datetime = datetime.datetime.combine(today, reminder_time)
            
            # 检查是否已过提醒时间
            if reminder_datetime <= now:
                # 已过提醒时间，检查是否已过开播时间
                broadcast_datetime = datetime.datetime.combine(today, broadcast_time_start)
                if now > broadcast_datetime:
                    logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 开播时间 {broadcast_time_start} 已过，不设置提醒")
                else:
                    # 如果现在时间在提醒时间和开播时间之间，马上发送提醒
                    logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 提醒时间已过但开播时间未到，立即发送提醒")
                    await self._send_broadcast_time_reminder(
                        chatroom_id, broadcaster_nickname, broadcaster_wxid, 
                        broadcast_time_start, time_type, slot_name
                    )
                return
            
            # 计算延迟时间（秒）
            delay_seconds = (reminder_datetime - now).total_seconds()
            
            # 创建定时任务
            task = asyncio.create_task(
                self._reminder_task(
                    chatroom_id, broadcaster_nickname, broadcaster_wxid,
                    broadcast_time_start, time_type, slot_name, delay_seconds
                )
            )
            
            # 存储任务
            self.reminder_tasks[task_key] = task
            
            logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 提醒任务已设置，将在 {reminder_datetime.strftime('%H:%M:%S')} 提醒（延迟{int(delay_seconds)}秒）")
        except Exception as e:
            logger.error(f"{self.log_prefix}主播[{broadcaster_info.get('broadcaster_nickname', '未知用户')}] 设置提醒任务失败: {str(e)}", exc_info=True)
    
    async def _reminder_task(self, chatroom_id: str, broadcaster_nickname: str, broadcaster_wxid: str, 
                          broadcast_time: datetime.time, time_type: str, slot_name: str, delay_seconds: float):
        """提醒任务执行方法
        
        Args:
            chatroom_id: 群聊ID
            broadcaster_nickname: 主播昵称
            broadcaster_wxid: 主播wxid
            broadcast_time: 开播时间
            time_type: 时间类型（"固定"或"临时"）
            slot_name: 时间段名称
            delay_seconds: 延迟执行的秒数
        """
        try:
            # 等待指定时间
            await asyncio.sleep(delay_seconds)
            
            # 发送提醒消息
            await self._send_broadcast_time_reminder(
                chatroom_id, broadcaster_nickname, broadcaster_wxid, 
                broadcast_time, time_type, slot_name
            )
        except asyncio.CancelledError:
            # 任务被取消
            logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 提醒任务被取消")
        except Exception as e:
            logger.error(f"{self.log_prefix}执行提醒任务失败: {str(e)}")
        finally:
            # 清理任务引用
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{broadcaster_wxid}"
            if task_key in self.reminder_tasks:
                del self.reminder_tasks[task_key]
    
    async def _send_broadcast_time_reminder(self, chatroom_id: str, broadcaster_nickname: str, 
                                        broadcaster_wxid: str, broadcast_time: datetime.time, 
                                        time_type: str, slot_name: str = None):
        """发送开播时间提醒
        
        Args:
            chatroom_id: 群聊ID
            broadcaster_nickname: 主播昵称
            broadcaster_wxid: 主播wxid
            broadcast_time: 开播时间
            time_type: 时间类型（"固定"或"临时"）
            slot_name: 时间段名称（可选）
        """
        if not self.bot:
            logger.error(f"{self.log_prefix}无法发送提醒：bot引用不存在")
            return
            
        try:
            # 检查主播的当前状态
            broadcaster_info = self.db.get_broadcaster_by_wxid(chatroom_id, broadcaster_wxid)
            if not broadcaster_info:
                logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 不存在，取消提醒")
                return
                
            # 检查主播是否已开播
            if broadcaster_info.get("is_active", False):
                logger.info(f"{self.log_prefix}主播[{broadcaster_nickname}] 已开播，取消提醒")
                return
                
            # 构建提醒消息
            broadcast_time_str = broadcast_time.strftime("%H:%M")
            slot_info = f"[{slot_name}] " if slot_name else ""
            
            message = f"⏰ 开播提醒 @{broadcaster_nickname}\n"
            message += f"🔔 主播 {broadcaster_nickname} 即将开播\n"
            message += f"🕙 {time_type}开播时间: {slot_info}{broadcast_time_str}\n"
            message += f"⚠️ 距离开播时间还有{self.remind_before_minutes}分钟，请做好准备！\n"
            message += f"💡 开播请发送「{self.cmd_start}」，如需调整时间请使用「{self.cmd_temp_time}」命令"
            
            # 发送@消息
            await self.bot.send_text_message(
                wxid=chatroom_id,
                content=message,
                at=broadcaster_wxid
            )
            
            logger.info(f"{self.log_prefix}已发送开播提醒: 群[{chatroom_id}] 主播[{broadcaster_nickname}] 时间[{broadcast_time_str}]")
        except Exception as e:
            logger.error(f"{self.log_prefix}发送开播提醒失败: {e}", exc_info=True)
    
    async def cancel_broadcaster_reminder(self, chatroom_id: str, broadcaster_wxid: str):
        """取消主播的提醒任务
        
        Args:
            chatroom_id: 群聊ID
            broadcaster_wxid: 主播wxid
        """
        try:
            today = datetime.date.today()
            task_key = f"{today}_{chatroom_id}_{broadcaster_wxid}"
            await self.cancel_reminder_task(task_key)
        except Exception as e:
            logger.error(f"{self.log_prefix}取消主播[{broadcaster_wxid}] 提醒任务失败: {str(e)}")
    
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
                logger.info(f"{self.log_prefix}已取消提醒任务: {task_key}")
        except Exception as e:
            logger.error(f"{self.log_prefix}取消提醒任务失败: {str(e)}")
    
    async def set_reminder_minutes(self, bot: WechatAPIClient, chatroom_id: str, 
                               sender_wxid: str, nickname: str, minutes: int):
        """设置开播前提醒时间
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊的wxid
            sender_wxid: 用户的wxid
            nickname: 用户昵称
            minutes: 提前提醒时间(分钟)
        """
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
        
        # 检查分钟数是否有效
        if minutes <= 0 or minutes > 120:  # 限制在1-120分钟之间
            await bot.send_text_message(chatroom_id, f"⚠️ 提醒时间必须在1-120分钟之间")
            return
        
        # 更新提醒时间
        try:
            # 保存到内存中
            self.remind_before_minutes = minutes
            
            # 保存到配置文件中
            config_path = os.path.join(os.path.dirname(__file__), "config.toml")
            config = {}
            
            # 读取原有配置
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            
            # 更新配置
            if "basic" not in config:
                config["basic"] = {}
            config["basic"]["reminder_minutes"] = minutes
            
            # 保存配置
            with open(config_path, "w", encoding="utf-8") as f:
                f.write("[basic]\n")
                f.write(f"# 是否启用插件\n")
                f.write(f"enable = {str(config['basic'].get('enable', True)).lower()}\n\n")
                f.write(f"# 定时提醒配置（开播前几分钟提醒）\n")
                f.write(f"reminder_minutes = {minutes}\n\n")
                
                # 写入其他配置
                if "commands" in config:
                    f.write("[commands]\n")
                    f.write("# 插件指令配置\n")
                    for k, v in config["commands"].items():
                        f.write(f"{k} = \"{v}\"\n")
            
            # 发送成功消息
            await bot.send_text_message(
                chatroom_id, 
                f"✅ 已设置开播前{minutes}分钟提醒 (by {nickname})"
            )
            logger.info(f"{self.log_prefix}已设置开播前{minutes}分钟提醒 (群[{chatroom_id}] 用户[{nickname}])")
            
            # 更新所有主播的提醒任务
            await self.update_all_broadcaster_reminders()
        except Exception as e:
            logger.error(f"{self.log_prefix}设置提醒时间失败: {str(e)}")
            await bot.send_text_message(chatroom_id, f"❌ 设置提醒时间失败: {str(e)}")
    
    async def query_broadcast_records(self, bot: WechatAPIClient, chatroom_id: str, 
                                   sender_wxid: str, nickname: str, 
                                   month: int = None, day: int = None):
        """查询开播记录
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊的wxid
            sender_wxid: 用户的wxid
            nickname: 用户的昵称
            month: 月份，如果指定则查询特定日期，否则查询今天
            day: 日期，如果指定则查询特定日期，否则查询今天
        """
        # 检查是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
            
        # 确定查询日期
        query_date = None
        if month is not None and day is not None:
            try:
                # 确定查询的年份
                today = datetime.date.today()
                query_year = today.year
                
                # 如果查询的月份小于当前月份，可能是查询下一年的记录
                if month < today.month:
                    query_year += 1
                
                query_date = datetime.date(query_year, month, day)
                
                # 如果查询日期超过今天，可能是去年的日期
                if query_date > today:
                    query_date = datetime.date(query_year - 1, month, day)
            except ValueError as e:
                await bot.send_text_message(chatroom_id, f"⚠️ 日期格式错误: {e}")
                return
        
        # 查询开播记录
        records = self.db.get_broadcast_records(chatroom_id, date=query_date)
        
        # 构建回复消息
        date_str = query_date.strftime("%Y年%m月%d日") if query_date else "今日"
        reply_msg = f"📊 {date_str}开播记录统计\n\n"
        
        if not records:
            reply_msg += "暂无开播记录"
            await bot.send_text_message(chatroom_id, reply_msg)
            return
        
        # 按主播分组统计
        broadcaster_records = {}
        
        for record in records:
            wxid = record.get("broadcaster_wxid", "未知ID")
            if wxid not in broadcaster_records:
                broadcaster_records[wxid] = []
            broadcaster_records[wxid].append(record)
        
        # 遍历每个主播的记录
        active_broadcasters = []  # 当前正在开播的主播
        finished_broadcasters = []  # 已经结束开播的主播
        
        for wxid, b_records in broadcaster_records.items():
            nickname = b_records[0].get("broadcaster_nickname", "未知用户")
            
            # 检查是否有活跃记录
            has_active = any(r.get("is_active", False) for r in b_records)
            
            # 查询该主播所有时间段状态
            slots_status = self.db.check_time_slots_broadcast_status(chatroom_id, wxid, date=query_date)
            
            if has_active:
                # 找到活跃记录
                active_record = next((r for r in b_records if r.get("is_active", False)), None)
                
                if active_record:
                    start_time = active_record.get("start_time").strftime("%H:%M:%S")
                    
                    # 计算已开播时长
                    now = datetime.datetime.now()
                    today = datetime.date.today()
                    start_datetime = datetime.datetime.combine(today, active_record.get("start_time"))
                    if now < start_datetime:  # 如果开始时间大于当前时间，可能是昨天开始的
                        start_datetime = datetime.datetime.combine(today - datetime.timedelta(days=1), active_record.get("start_time"))
                    
                    duration_min = int((now - start_datetime).total_seconds() / 60)
                    duration_str = f"{duration_min // 60}小时{duration_min % 60}分钟" if duration_min >= 60 else f"{duration_min}分钟"
                    
                    # 是否迟到
                    late_str = ""
                    if active_record.get("is_late", False):
                        late_min = active_record.get("late_minutes", 0)
                        if late_min < 60:
                            late_str = f"(迟到{late_min}分钟)"
                        else:
                            late_str = f"(迟到{late_min // 60}小时{late_min % 60}分钟)"
                    
                    # 构建主播信息
                    broadcaster_info = f"  👤 {nickname}\n" + \
                                       f"  ⏰ 开播时间: {start_time} {late_str}\n" + \
                                       f"  ⌛ 已开播: {duration_str}"
                    
                    # 如果有多个时间段，添加时间段状态
                    if slots_status and len(slots_status) > 1:
                        # 找到当前激活的时间段
                        current_slot = None
                        for slot in slots_status:
                            if slot["is_active"]:
                                current_slot = slot
                                break
                        
                        if current_slot:
                            slot_name = f"[{current_slot['slot_name']}] " if current_slot['slot_name'] else ""
                            slot_time = f"{current_slot['broadcast_time_start'].strftime('%H:%M')}-{current_slot['broadcast_time_end'].strftime('%H:%M')}"
                            broadcaster_info += f"\n  📅 当前时间段: {slot_name}{slot_time}"
                        
                        # 添加已错过的时间段
                        missed_slots = [slot for slot in slots_status if not slot["is_broadcasted"] and not slot["is_active"]]
                        if missed_slots:
                            missed_info = []
                            for slot in missed_slots:
                                slot_name = f"[{slot['slot_name']}] " if slot['slot_name'] else ""
                                slot_time = f"{slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')}"
                                missed_info.append(f"{slot_name}{slot_time}")
                            
                            if missed_info:
                                broadcaster_info += f"\n  ⚠️ 已错过的时间段: {', '.join(missed_info)}"
                    
                    active_broadcasters.append(broadcaster_info)
                
            else:
                # 已结束开播的主播
                # 构建主播详细记录
                broadcaster_detail = []
                
                for record in b_records:
                    start_time = record.get("start_time").strftime("%H:%M:%S")
                    end_time = record.get("end_time").strftime("%H:%M:%S") if record.get("end_time") else "未记录"
                    
                    duration_min = record.get("duration", 0)
                    duration_str = f"{duration_min // 60}小时{duration_min % 60}分钟" if duration_min >= 60 else f"{duration_min}分钟"
                    
                    # 是否迟到
                    late_str = ""
                    if record.get("is_late", False):
                        late_min = record.get("late_minutes", 0)
                        if late_min < 60:
                            late_str = f"(迟到{late_min}分钟)"
                        else:
                            late_str = f"(迟到{late_min // 60}小时{late_min % 60}分钟)"
                            
                    # 构建记录信息
                    record_info = f"  ⏰ {start_time} - {end_time} {late_str}\n" + \
                                  f"  ⌛ 开播时长: {duration_str}"
                    
                    # 如果是多个时间段，添加时间段信息
                    for slot in slots_status:
                        if slot["is_broadcasted"] and slot["start_time"] == record.get("start_time"):
                            slot_name = f"[{slot['slot_name']}] " if slot['slot_name'] else ""
                            slot_time = f"{slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')}"
                            record_info += f"\n  📅 时间段: {slot_name}{slot_time}"
                            break
                    
                    broadcaster_detail.append(record_info)
                
                # 计算总开播时长
                total_duration = sum(r.get("duration", 0) for r in b_records)
                total_duration_str = f"{total_duration // 60}小时{total_duration % 60}分钟" if total_duration >= 60 else f"{total_duration}分钟"
                
                # 构建主播信息
                broadcaster_info = f"  👤 {nickname}\n"
                
                # 如果有多个时间段记录，显示
                if len(broadcaster_detail) > 1:
                    broadcaster_info += f"  📊 总开播场次: {len(broadcaster_detail)}\n"
                    broadcaster_info += f"  📊 总开播时长: {total_duration_str}\n"
                    broadcaster_info += "\n  🔹 详细记录:\n"
                    for i, detail in enumerate(broadcaster_detail):
                        broadcaster_info += f"  场次 {i+1}:\n{detail}\n"
                else:
                    broadcaster_info += broadcaster_detail[0] if broadcaster_detail else "  ⚠️ 无详细记录"
                
                # 添加未开播的时间段信息
                missed_slots = [slot for slot in slots_status if not slot["is_broadcasted"]]
                if missed_slots:
                    missed_info = []
                    for slot in missed_slots:
                        slot_name = f"[{slot['slot_name']}] " if slot['slot_name'] else ""
                        slot_time = f"{slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')}"
                        missed_info.append(f"{slot_name}{slot_time}")
                    
                    if missed_info:
                        broadcaster_info += f"\n  ⚠️ 未开播的时间段: {', '.join(missed_info)}"
                
                finished_broadcasters.append(broadcaster_info)
        
        # 添加正在开播的主播信息
        if active_broadcasters:
            reply_msg += f"🔴 正在开播 ({len(active_broadcasters)}人):\n"
            reply_msg += "\n\n".join(active_broadcasters)
            reply_msg += "\n\n"
        
        # 添加已结束开播的主播信息
        if finished_broadcasters:
            reply_msg += f"⚪ 已结束开播 ({len(finished_broadcasters)}人):\n"
            reply_msg += "\n\n".join(finished_broadcasters)
        
        await bot.send_text_message(chatroom_id, reply_msg)
    
    async def show_reminder_list(self, bot: WechatAPIClient, chatroom_id: str, 
                                 sender_wxid: str, nickname: str):
        """显示当前群聊所有设置了提醒的主播列表
        
        Args:
            bot: 微信API客户端
            chatroom_id: 群聊的wxid
            sender_wxid: 用户的wxid
            nickname: 用户昵称
        """
        # 检查是否为管理员
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 您没有权限执行此命令，需要管理员权限")
            return
            
        # 检查群聊是否启用开播记录
        if not self.db.is_group_broadcast_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "⚠️ 本群未启用开播记录功能，请联系管理员启用")
            return
            
        try:
            # 获取所有主播信息
            broadcasters = self.db.get_all_broadcasters(chatroom_id)
            
            if not broadcasters:
                await bot.send_text_message(chatroom_id, f"📝 当前群聊暂无主播记录")
                return
                
            # 获取今日日期
            today = datetime.date.today()
            today_str = today.strftime("%Y年%m月%d日")
            
            # 构建回复消息
            reply_msg = f"📋 {today_str}开播提醒列表\n\n"
            
            # 获取临时开播时间记录
            temp_records = self.db.get_temp_time_records(chatroom_id, date=today)
            temp_dict = {f"{r['chatroom_id']}_{r['broadcaster_wxid']}": r for r in temp_records}
            
            # 判断是否有主播正在开播
            active_broadcasters = self._get_active_broadcasters(chatroom_id)
            active_wxids = [b["broadcaster_wxid"] for b in active_broadcasters]
            
            # 临时时间主播列表
            temp_broadcasters = []
            # 固定时间主播列表
            regular_broadcasters = []
            
            for broadcaster in broadcasters:
                wxid = broadcaster["broadcaster_wxid"]
                name = broadcaster["broadcaster_nickname"]
                
                # 检查是否已经开播
                is_active = wxid in active_wxids
                status = "🔴 开播中" if is_active else "⚪ 未开播"
                
                # 判断是否使用临时时间
                temp_key = f"{chatroom_id}_{wxid}"
                if temp_key in temp_dict:
                    temp_record = temp_dict[temp_key]
                    start_time = temp_record["broadcast_time_start"].strftime("%H:%M")
                    end_time = temp_record["broadcast_time_end"].strftime("%H:%M")
                    
                    # 计算提醒时间
                    remind_time = self._time_minus_minutes(temp_record["broadcast_time_start"], self.remind_before_minutes)
                    remind_time_str = remind_time.strftime("%H:%M")
                    
                    # 添加到临时时间主播列表
                    temp_broadcasters.append({
                        "name": name,
                        "wxid": wxid,
                        "start_time": start_time,
                        "end_time": end_time,
                        "remind_time": remind_time_str,
                        "is_active": is_active,
                        "status": status
                    })
                else:
                    # 使用固定时间
                    start_time = broadcaster["broadcast_time_start"].strftime("%H:%M")
                    end_time = broadcaster["broadcast_time_end"].strftime("%H:%M")
                    
                    # 计算提醒时间
                    remind_time = self._time_minus_minutes(broadcaster["broadcast_time_start"], self.remind_before_minutes)
                    remind_time_str = remind_time.strftime("%H:%M")
                    
                    # 添加到固定时间主播列表
                    regular_broadcasters.append({
                        "name": name,
                        "wxid": wxid,
                        "start_time": start_time,
                        "end_time": end_time,
                        "remind_time": remind_time_str,
                        "is_active": is_active,
                        "status": status
                    })
            
            # 添加临时时间主播信息到回复
            if temp_broadcasters:
                reply_msg += f"⏱️ 临时开播时间 ({len(temp_broadcasters)}人):\n"
                for b in temp_broadcasters:
                    reply_msg += f"  👤 {b['name']} {b['status']}\n"
                    reply_msg += f"  ⏰ 开播时间: {b['start_time']}-{b['end_time']}\n"
                    reply_msg += f"  🔔 提醒时间: {b['remind_time']}\n\n"
            
            # 添加固定时间主播信息到回复
            if regular_broadcasters:
                if temp_broadcasters:
                    reply_msg += f"📆 固定开播时间 ({len(regular_broadcasters)}人):\n"
                else:
                    reply_msg += f"📆 开播时间 ({len(regular_broadcasters)}人):\n"
                    
                for b in regular_broadcasters:
                    reply_msg += f"  👤 {b['name']} {b['status']}\n"
                    reply_msg += f"  ⏰ 开播时间: {b['start_time']}-{b['end_time']}\n"
                    reply_msg += f"  🔔 提醒时间: {b['remind_time']}\n\n"
            
            # 提醒设置信息
            reply_msg += f"ℹ️ 当前设置为开播前{self.remind_before_minutes}分钟提醒\n"
            reply_msg += f"📝 可使用 {self.cmd_set_reminder} 分钟数 修改"
            
            await bot.send_text_message(chatroom_id, reply_msg)
            
        except Exception as e:
            logger.error(f"{self.log_prefix}显示提醒列表失败: {str(e)}", exc_info=True)
            await bot.send_text_message(chatroom_id, f"❌ 显示提醒列表失败: {str(e)}")
    
    def _get_active_broadcasters(self, chatroom_id: str) -> List[Dict]:
        """获取当前正在开播的主播列表"""
        today = datetime.date.today()
        records = self.db.get_broadcast_records(chatroom_id, date=today)
        return [r for r in records if r.get("is_active", False)]

    def _check_time_conflict(self, start1: datetime.time, end1: datetime.time, 
                         start2: datetime.time, end2: datetime.time) -> bool:
        """检查两个时间段是否冲突"""
        today = datetime.date.today()
        
        # 转换为datetime对象方便比较
        start1_dt = datetime.datetime.combine(today, start1)
        end1_dt = datetime.datetime.combine(today, end1)
        start2_dt = datetime.datetime.combine(today, start2)
        end2_dt = datetime.datetime.combine(today, end2)
        
        # 如果结束时间小于开始时间，说明跨天，加一天
        if end1_dt < start1_dt:
            end1_dt += datetime.timedelta(days=1)
        if end2_dt < start2_dt:
            end2_dt += datetime.timedelta(days=1)
        
        # 检查时间段是否重叠
        return (start1_dt < end2_dt and end1_dt > start2_dt) 