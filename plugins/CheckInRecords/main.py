import os
import re
import tomllib
import datetime
from typing import Optional

from loguru import logger
from WechatAPI import WechatAPIClient
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, schedule

from .db_models import CheckInDB


class CheckInRecords(PluginBase):
    """打卡记录插件"""
    description = "群聊打卡记录管理插件"
    author = "XYBot开发团队"
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
            self.cmd_enable = self.commands.get("enable_checkin", "启用打卡")
            self.cmd_disable = self.commands.get("disable_checkin", "禁用打卡")
            self.cmd_checkin = self.commands.get("checkin", "打卡")
            self.cmd_set_time = self.commands.get("set_checkin_time", "设置打卡时间")
            self.cmd_query = self.commands.get("query_checkin", "查打卡记录")
            
            # 初始化数据库
            self.db = CheckInDB()
            logger.success("打卡记录插件初始化成功")
        except Exception as e:
            logger.error(f"加载打卡记录插件配置文件失败: {str(e)}")
            self.enable = False
    
    async def async_init(self, bot=None):
        """异步初始化"""
        # 定时任务在on_enable中配置
        pass
    
    @on_text_message(priority=50)
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本消息指令"""
        if not self.enable:
            return
        
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        is_group = message.get("IsGroup", False)
        
        # 只处理群聊消息
        if not is_group:
            return
        
        # 获取群昵称
        try:
            # 使用项目内置的获取群昵称方法
            nickname = await bot.get_chatroom_nickname(from_wxid, sender_wxid)
        except Exception as e:
            logger.error(f"获取群昵称失败: {e}")
            nickname = ""
        
        # 启用打卡命令
        if content == self.cmd_enable:
            await self.enable_checkin(bot, from_wxid, sender_wxid)
            return
        
        # 禁用打卡命令
        if content == self.cmd_disable:
            await self.disable_checkin(bot, from_wxid, sender_wxid)
            return
        
        # 处理打卡命令
        if content == self.cmd_checkin:
            await self.do_checkin(bot, from_wxid, sender_wxid, nickname)
            return
        
        # 设置打卡时间
        time_pattern = re.compile(f"{self.cmd_set_time}\\s+(\\d+)-(\\d+)")
        time_match = time_pattern.match(content)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            if 0 <= hour < 24 and 0 <= minute < 60:
                await self.set_checkin_time(bot, from_wxid, sender_wxid, hour, minute)
            else:
                await bot.send_text_message(from_wxid, "时间格式错误，请使用 小时-分钟 格式，如：12-30")
            return
        
        # 查询打卡记录
        # 支持三种格式：无参数、月-日、年-月-日
        query_patterns = [
            re.compile(f"^{re.escape(self.cmd_query)}$"),  # 无参数
            re.compile(f"^{re.escape(self.cmd_query)}\\s+(\\d+)-(\\d+)$"),  # 月-日
            re.compile(f"^{re.escape(self.cmd_query)}\\s+(\\d+)-(\\d+)-(\\d+)$")  # 年-月-日
        ]
        
        # 检查是否查询打卡记录命令
        for i, pattern in enumerate(query_patterns):
            match = pattern.match(content)
            if match:
                try:
                    if i == 0:  # 无参数
                        await self.query_checkin_records(bot, from_wxid)
                    elif i == 1:  # 月-日
                        current_year = datetime.datetime.now().year
                        month = int(match.group(1))
                        day = int(match.group(2))
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            try:
                                query_date = datetime.date(current_year, month, day)
                                await self.query_checkin_records(bot, from_wxid, query_date)
                            except ValueError:
                                await bot.send_text_message(from_wxid, f"日期格式错误，{month}月{day}日不是有效日期")
                        else:
                            await bot.send_text_message(from_wxid, "日期格式错误，月份应在1-12之间，日应在1-31之间")
                    elif i == 2:  # 年-月-日
                        year = int(match.group(1))
                        month = int(match.group(2))
                        day = int(match.group(3))
                        try:
                            query_date = datetime.date(year, month, day)
                            await self.query_checkin_records(bot, from_wxid, query_date)
                        except ValueError:
                            await bot.send_text_message(from_wxid, f"日期格式错误，{year}年{month}月{day}日不是有效日期")
                except Exception as e:
                    logger.error(f"处理查打卡记录命令出错: {e}")
                    await bot.send_text_message(from_wxid, "查询打卡记录失败，请检查日期格式")
                return
    
    async def enable_checkin(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str):
        """启用群聊打卡功能"""
        # 获取用户昵称
        nickname = await bot.get_chatroom_nickname(chatroom_id, sender_wxid)
        
        if self.db.enable_group_checkin(chatroom_id):
            await bot.send_text_message(chatroom_id, f"✅ 本群打卡功能已启用 (by {nickname})")
        else:
            await bot.send_text_message(chatroom_id, "❌ 启用打卡功能失败，请联系管理员")
    
    async def disable_checkin(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str):
        """禁用群聊打卡功能"""
        # 获取用户昵称
        nickname = await bot.get_chatroom_nickname(chatroom_id, sender_wxid)
        
        if self.db.disable_group_checkin(chatroom_id):
            await bot.send_text_message(chatroom_id, f"✅ 本群打卡功能已禁用 (by {nickname})")
        else:
            await bot.send_text_message(chatroom_id, "❌ 禁用打卡功能失败，请联系管理员")
    
    async def set_checkin_time(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, hour: int, minute: int):
        """设置群聊打卡时间"""
        # 获取用户昵称
        nickname = await bot.get_chatroom_nickname(chatroom_id, sender_wxid)
        
        # 检查群聊是否启用打卡
        if not self.db.is_group_checkin_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "❌ 请先启用本群打卡功能")
            return
        
        checkin_time = datetime.time(hour, minute)
        if self.db.set_checkin_time(chatroom_id, checkin_time):
            time_str = checkin_time.strftime("%H:%M")
            await bot.send_text_message(chatroom_id, f"✅ 本群打卡时间已设置为 {time_str} (by {nickname})")
        else:
            await bot.send_text_message(chatroom_id, "❌ 设置打卡时间失败，请联系管理员")
    
    async def do_checkin(self, bot: WechatAPIClient, chatroom_id: str, member_wxid: str, nickname: str):
        """处理成员打卡"""
        # 检查群聊是否启用打卡
        if not self.db.is_group_checkin_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "❌ 本群未启用打卡功能")
            return
        
        # 记录打卡
        success, data = self.db.record_checkin(chatroom_id, member_wxid, nickname)
        
        if not success:
            if data.get("already_checked"):
                # 已经打过卡了
                checkin_time = data.get("checkin_time").strftime("%Y年%m月%d日 %H:%M:%S")
                await bot.send_text_message(chatroom_id, f"⚠️ {nickname} 今天已经打过卡了，打卡时间：{checkin_time}")
            else:
                # 其他错误
                await bot.send_text_message(chatroom_id, f"❌ 打卡失败：{data.get('error', '未知错误')}")
            return
        
        # 打卡成功，获取数据
        checkin_time = data.get("checkin_time")
        is_late = data.get("is_late", False)
        late_minutes = data.get("late_minutes", 0)
        month_count = data.get("month_count", 1)
        
        # 构建打卡信息
        current_date = datetime.datetime.now().strftime("%Y年%m月%d日")
        time_str = checkin_time.strftime("%H:%M:%S")
        
        message = f"✅ 打卡成功\n"
        message += f"📅 日期：{current_date}\n"
        message += f"👤 成员：{nickname}\n"
        message += f"⏰ 打卡时间：{time_str}\n"
        
        # 打卡状态
        if is_late:
            message += f"⚠️ 打卡状态：迟到\n"
            message += f"⏱️ 迟到时间：{late_minutes}分钟\n"
        else:
            message += f"✨ 打卡状态：正常\n"
        
        message += f"📊 本月打卡次数：{month_count}次"
        
        await bot.send_text_message(chatroom_id, message)
    
    async def query_checkin_records(self, bot: WechatAPIClient, chatroom_id: str, check_date: Optional[datetime.date] = None):
        """查询打卡记录"""
        # 检查群聊是否启用打卡
        if not self.db.is_group_checkin_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, "❌ 本群未启用打卡功能")
            return
        
        # 获取打卡记录
        records = self.db.get_today_records(chatroom_id, check_date)
        
        # 获取当前星期几
        now = check_date or datetime.datetime.now().date()
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[now.weekday()]
        
        if not records:
            date_str = check_date.strftime("%Y年%m月%d日") if check_date else datetime.datetime.now().strftime("%Y年%m月%d日")
            await bot.send_text_message(chatroom_id, f"📊 {date_str} {weekday} 暂无打卡记录")
            return
        
        # 构建打卡记录信息
        date_str = check_date.strftime("%Y年%m月%d日") if check_date else datetime.datetime.now().strftime("%Y年%m月%d日")
        message = f"📊 {date_str} {weekday}打卡记录\n"
        message += f"━━━━━━━━━━━━━━\n"
        
        # 按时间排序的记录
        normal_records = []
        late_records = []
        
        for record in records:
            nickname = record.get("nickname", "") or record.get("member_wxid", "")
            checkin_time = record.get("checkin_time").strftime("%H:%M:%S")
            is_late = record.get("is_late", False)
            late_minutes = record.get("late_minutes", 0)
            
            record_line = f"👤 {nickname} | ⏰ {checkin_time}"
            
            if is_late:
                record_line += f" | ⚠️ 迟到{late_minutes}分钟"
                late_records.append(record)
            else:
                normal_records.append(record)
            
            message += record_line + "\n"
        
        # 汇总信息
        message += f"━━━━━━━━━━━━━━\n"
        message += f"✅ 总人数：{len(records)}人\n"
        message += f"✨ 正常打卡：{len(normal_records)}人\n"
        
        # 有迟到记录时添加迟到信息
        if late_records:
            message += f"⚠️ 迟到人数：{len(late_records)}人\n"
            message += f"━━━━━━━━━━━━━━\n"
            message += f"⚠️ 迟到成员：\n"
            
            for record in late_records:
                nickname = record.get("nickname", "") or record.get("member_wxid", "")
                late_minutes = record.get("late_minutes", 0)
                message += f"👤 {nickname} | ⏱️ 迟到{late_minutes}分钟\n"
        
        await bot.send_text_message(chatroom_id, message)
    
    @schedule('cron', hour=0, minute=1)
    async def reset_daily_records(self, bot: WechatAPIClient):
        """每日零点后清理缓存，准备新一天的打卡"""
        if not self.enable:
            return
        logger.info("打卡记录日切换，准备新一天的打卡记录")
        # 日切换时无需实际操作，因为打卡记录是按日期存储的 