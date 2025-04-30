import asyncio
import re
import tomllib
from typing import List, Optional

from loguru import logger
from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import on_text_message, schedule
from utils.plugin_base import PluginBase
import os
import sqlite3
from datetime import datetime, timedelta
from dateutil import parser
import time
from utils.event_manager import EventManager


class Reminder(PluginBase):
    description = "备忘录插件"
    author = "老夏的金库"
    version = "1.2.1"  # 更新版本号

    def __init__(self):
        super().__init__()
        with open("main_config.toml", "rb") as f:
            config = tomllib.load(f)
        self.admins = config["XYBot"]["admins"]

        with open("plugins/Reminder/config.toml", "rb") as f:
            config = tomllib.load(f)
        self.plugin_config = config["Reminder"]

        self.enable = self.plugin_config["enable"]
        self.commands = self.plugin_config["commands"]
        self.other_plugin_cmd = self.plugin_config["other-plugin_cmd"]
        self.command_tip = self.plugin_config["command-tip"]
        self.price = self.plugin_config["price"]
        self.admin_ignore = self.plugin_config["admin_ignore"]
        self.whitelist_ignore = self.plugin_config["whitelist_ignore"]
        self.http_proxy = self.plugin_config["http-proxy"]

        self.db = XYBotDB()
        self.processed_message_ids = set()
        self.data_dir = "reminder_data"
        os.makedirs(self.data_dir, exist_ok=True)

        self.store_command = "记录"
        self.query_command = ["我的记录"]
        self.delete_command = "删除"
        self.help_command = "记录帮助"

        # 添加其他插件的触发命令列表
        self.other_plugin_cmd = [
            "早报",
            "天气",
            "新闻",
            # ... 添加其他插件的触发命令
        ]

        self.format_match = ["提醒", "重复提醒", "定时提醒", "每天提醒", "每周提醒", "每月提醒", "每年提醒", "每小时提醒"]

    def get_db_path(self, wxid: str) -> str:
        db_name = f"user_{wxid}.db"
        return os.path.join(self.data_dir, db_name)

    def create_table(self, db_path: str):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wxid TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    chat_id TEXT NOT NULL,  -- 新增字段，存储创建时的聊天ID
                    is_done INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
        except sqlite3.Error as e:
            logger.exception(f"创建数据库表失败: {e}")
        finally:
            conn.close()

    async def store_reminder(self, wxid: str, content: str, reminder_type: str, reminder_time: str, chat_id: str) -> Optional[int]:
        db_path = self.get_db_path(wxid)
        self.create_table(db_path)
        
        # 如果是相对时间类型，计算绝对时间并转换为 one_time
        if reminder_type in ["minutes_later", "hours_later", "days_later"]:
            now = datetime.now()
            if reminder_type == "minutes_later":
                minutes = int(reminder_time.replace("分钟后", ""))
                absolute_time = now + timedelta(minutes=minutes)
            elif reminder_type == "hours_later":
                hours = int(reminder_time.replace("小时后", ""))
                absolute_time = now + timedelta(hours=hours)
            elif reminder_type == "days_later":
                days = int(reminder_time.replace("天后", ""))
                absolute_time = now + timedelta(days=days)
            reminder_time = absolute_time.strftime('%Y-%m-%d %H:%M:%S')
            reminder_type = "one_time"

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO reminders (wxid, content, reminder_type, reminder_time, chat_id) VALUES (?, ?, ?, ?, ?)",
                           (wxid, content, reminder_type, reminder_time, chat_id))
            new_id = cursor.lastrowid
            conn.commit()
            logger.info(f"用户 {wxid} 存储备忘录成功: {content}, {reminder_type}, {reminder_time}, chat_id={chat_id}")
            return new_id
        except sqlite3.Error as e:
            logger.exception(f"存储备忘录失败: {e}")
            return None
        finally:
            conn.close()

    async def query_reminders(self, wxid: str) -> List[tuple]:
        db_path = self.get_db_path(wxid)
        if not os.path.exists(db_path):
            return []
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT id, content, reminder_type, reminder_time, chat_id FROM reminders WHERE wxid = ? AND is_done = 0", (wxid,))
            results = cursor.fetchall()
            conn.close()
            return results
        except sqlite3.Error as e:
            logger.exception(f"查询用户 {wxid} 的备忘录失败: {e}")
            return []
        finally:
            conn.close()

    async def delete_reminder(self, wxid: str, reminder_id: int) -> bool:
        db_path = self.get_db_path(wxid)
        if not os.path.exists(db_path):
            logger.warning(f"用户 {wxid} 的数据库不存在")
            return False
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reminders WHERE id = ? AND wxid = ?", (reminder_id, wxid))
            conn.commit()
            logger.info(f"删除备忘录 {reminder_id} 成功")
            return True
        except sqlite3.Error as e:
            logger.exception(f"删除备忘录失败: {e}")
            return False
        finally:
            conn.close()

    async def delete_all_reminders(self, wxid: str) -> bool:
        db_path = self.get_db_path(wxid)
        if not os.path.exists(db_path):
            logger.warning(f"用户 {wxid} 的数据库不存在")
            return False
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reminders WHERE wxid = ?", (wxid,))
            conn.commit()
            logger.info(f"删除用户 {wxid} 的所有备忘录成功")
            return True
        except sqlite3.Error as e:
            logger.exception(f"删除所有备忘录失败: {e}")
            return False
        finally:
            conn.close()

    @on_text_message(priority=90)
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        wxid = message["SenderWxid"]
        content = message["Content"].strip()
        chat_id = message["FromWxid"]
        message_id = message["MsgId"]
        is_group_chat = chat_id.endswith("chatroom")

        if not self.enable:
            return True

        if content == self.store_command or (content.startswith(self.store_command) and len(content.strip()) == len(self.store_command)):
            help_message = (
                "📝-----vm-----📝\n"
                "⏰备忘录使用说明\n\n"
                "🕒支持的时间格式:\n"
                " - 每天 HH:MM（如：每天 08:00）\n"
                " - 每周一/二/三/四/五/六/日 HH:MM\n"
                " - 每月DD HH:MM\n"
                " - XX分钟后\n - XX小时后\n - XX天后\n\n"
                "📝示例:\n"
                " - 记录 每天 08:00 早报\n"
                " - 记录 每天 12:00 天气 北京\n"
                " - 记录 每周一 09:00 新闻\n"
                " - 记录 30分钟后 提醒我喝水\n\n"
                "📋支持的提示词:\n"
                f"{', '.join(self.other_plugin_cmd)}\n\n"
                "📋管理记录:\n"
                " - 我的记录 (查看所有记录)\n"
                " - 删除 序号 (取消单个记录)\n"
                " - 删除 全部 (取消所有记录)"
            )
            
            try:
                if is_group_chat:
                    await bot.send_at_message(chat_id, help_message, [wxid])
                else:
                    await bot.send_text_message(chat_id, help_message)
                logger.info(f"向用户 {wxid} 发送帮助信息")
            except Exception as e:
                logger.error(f"发送帮助信息失败: {e}")
            return False

        elif content.startswith(self.store_command):
            try:
                info = content[len(self.store_command):].strip()
                parts = info.split(maxsplit=2)
                if len(parts) < 2:
                    error_msg = "\n参数错误！请使用：记录 [时间/周期] [内容]"
                    if is_group_chat:
                        await bot.send_at_message(chat_id, error_msg, [wxid])
                    else:
                        await bot.send_text_message(chat_id, error_msg)
                    return False

                time_period_str = parts[0]
                reminder_content = parts[1]

                reminder_type = None
                reminder_time = None
                next_time = None

                if "分钟后" in time_period_str:
                    reminder_type = "minutes_later"
                    reminder_time = time_period_str
                    now = datetime.now()
                    minutes = int(reminder_time.replace("分钟后", ""))
                    next_time = now + timedelta(minutes=minutes)
                elif "小时后" in time_period_str:
                    reminder_type = "hours_later"
                    reminder_time = time_period_str
                    now = datetime.now()
                    hours = int(reminder_time.replace("小时后", ""))
                    next_time = now + timedelta(hours=hours)
                elif "天后" in time_period_str:
                    reminder_type = "days_later"
                    reminder_time = time_period_str
                    now = datetime.now()
                    days = int(reminder_time.replace("天后", ""))
                    next_time = now + timedelta(days=days)
                elif re.match(r"^\d{2}:\d{2}$", time_period_str):
                    reminder_type = "daily"
                    reminder_time = time_period_str
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                elif "每年" in time_period_str:
                    reminder_type = "yearly"
                    reminder_time = time_period_str.replace("每年", "")
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                elif "每月" in time_period_str:
                    reminder_type = "monthly"
                    reminder_time = time_period_str.replace("每月", "")
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                elif "每周" in time_period_str:
                    reminder_type = "weekly"
                    day_mapping = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "日": "7"}
                    match = re.match(r"每周([一二三四五六日])\s*(\d{1,2}:\d{2})", time_period_str)
                    if match:
                        weekday = day_mapping[match.group(1)]
                        time_str = match.group(2)
                        reminder_time = f"{weekday} {time_str}"
                        next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                    else:
                        error_msg = "\n格式错误，请使用：每周一 9:00"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, error_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, error_msg)
                        return False
                elif time_period_str.startswith("每天"):
                    reminder_type = "every_day"
                    # 提取时间部分
                    time_match = re.search(r'每天\s*(\d{1,2}:\d{2})', time_period_str)
                    if time_match:
                        reminder_time = time_match.group(1)
                        next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                    else:
                        error_msg = "\n时间格式错误！请使用：每天 HH:MM 格式"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, error_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, error_msg)
                        return False
                elif time_period_str == "每小时":
                    reminder_type = "every_hour"
                    reminder_time = ""
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                elif time_period_str == "每周":
                    reminder_type = "every_week"
                    reminder_time = ""
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                else:
                    try:
                        reminder_time_obj = parser.parse(time_period_str)
                        reminder_type = "one_time"
                        reminder_time = str(reminder_time_obj)
                        next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                    except ValueError:
                        error_msg = "\n不支持的时间/周期格式"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, error_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, error_msg)
                        return False

                if await self._check_point(bot, message):
                    new_id = await self.store_reminder(wxid, reminder_content, reminder_type, reminder_time, chat_id)
                    if new_id is not None:
                        output = "🎉成功存储备忘录\n"
                        output += f"🆔任务ID：{new_id}\n"
                        output += f"🗒️内 容：{reminder_content}\n"
                        if next_time:
                            output += f"⏱️提醒时间：{next_time.strftime('%Y-%m-%d %H:%M')}\n"
                        else:
                            output += f"⏱️提醒时间：未知\n"
                        output += "——————————————————\n"
                        existing_reminders = await self.query_reminders(wxid)
                        if existing_reminders:
                            output += "📝您当前的记录如下：\n"
                            for id, content, reminder_type, reminder_time, _ in existing_reminders:
                                existing_next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                                if existing_next_time:
                                    output += f"👉 {id}. {content} (提醒时间：{existing_next_time.strftime('%Y-%m-%d %H:%M')})\n"
                                else:
                                    output += f"👉 {id}. {content} (提醒时间：未知)\n"
                        else:
                            output += "目前您还没有其他记录哦😉"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, output, [wxid])
                        else:
                            await bot.send_text_message(chat_id, output)
                    else:
                        error_msg = "\n存储备忘录失败，请稍后再试"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, error_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, error_msg)
                    return False
                else:
                    logger.warning(f"用户 {wxid} 触发风控保护机制")
                    return False

            except Exception as e:
                logger.exception(f"处理存储备忘录指令时出错: {e}")
                error_msg = "\n参数错误或服务器错误，请稍后再试"
                if is_group_chat:
                    await bot.send_at_message(chat_id, error_msg, [wxid])
                else:
                    await bot.send_text_message(chat_id, error_msg)
                return False

        elif content in self.query_command:
            print("收到了查询记录的命令")
            reminders = await self.query_reminders(wxid)
            print(f"查询到的记录: {reminders}")
            if reminders:
                output = "📝-----vm-----📝\n您的记录：\n"
                for id, content, reminder_type, reminder_time, _ in reminders:
                    next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                    if next_time:
                        output += f"👉 {id}. {content} (提醒时间：{next_time.strftime('%Y-%m-%d %H:%M')})\n"
                    else:
                        output += f"👉 {id}. {content} (提醒时间：未知)\n"
                if is_group_chat:
                    await bot.send_at_message(chat_id, output, [wxid])
                else:
                    await bot.send_text_message(chat_id, output)
            else:
                empty_msg = "您还没有任何记录😔"
                if is_group_chat:
                    await bot.send_at_message(chat_id, empty_msg, [wxid])
                else:
                    await bot.send_text_message(chat_id, empty_msg)
            return False

        elif content.startswith(self.delete_command):
            try:
                delete_id = content[len(self.delete_command):].strip()
                
                if delete_id == "全部":
                    if await self.delete_all_reminders(wxid):
                        success_msg = "🗑️已清空所有记录"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, success_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, success_msg)
                    else:
                        fail_msg = "❌清空记录失败，请稍后再试"
                        if is_group_chat:
                            await bot.send_at_message(chat_id, fail_msg, [wxid])
                        else:
                            await bot.send_text_message(chat_id, fail_msg)
                    return False
                
                # 原有的删除单个提醒的逻辑
                reminder_id = int(delete_id)
                if await self.delete_reminder(wxid, reminder_id):
                    success_msg = f"🗑️成功删除记录 {reminder_id}"
                    if is_group_chat:
                        await bot.send_at_message(chat_id, success_msg, [wxid])
                    else:
                        await bot.send_text_message(chat_id, success_msg)
                else:
                    fail_msg = f"❌删除记录 {reminder_id} 失败，请稍后再试"
                    if is_group_chat:
                        await bot.send_at_message(chat_id, fail_msg, [wxid])
                    else:
                        await bot.send_text_message(chat_id, fail_msg)
                return False
                
            except ValueError:
                error_msg = "\n参数错误！请使用：\n删除 <记录ID> 或\n删除 全部"
                if is_group_chat:
                    await bot.send_at_message(chat_id, error_msg, [wxid])
                else:
                    await bot.send_text_message(chat_id, error_msg)
                return False
            except Exception as e:
                logger.exception(f"处理删除记录指令时出错: {e}")
                error_msg = "\n处理删除指令时出现错误，请稍后再试"
                if is_group_chat:
                    await bot.send_at_message(chat_id, error_msg, [wxid])
                else:
                    await bot.send_text_message(chat_id, error_msg)
                return False

        elif content == self.help_command:
            help_message = "⏰设置提醒:\n 记录 [时间/周期] [内容]\n\n"
            help_message += "🕒支持的时间格式:\n - XX分钟后\n - XX小时后\n - XX天后\n - HH:MM (具体时间)\n\n"
            help_message += "📅支持的周期格式:\n - 每年 MM月DD日 (如: 每年 3月15日)\n - 每月 DD号 HH:MM (如: 每月 8号 8:00)\n"
            help_message += " - 每周一/每周二/.../每周日\n - 每周1/每周2/.../每周7\n - 每周 (每7天)\n - 每天\n - 每小时\n\n"
            help_message += "📝提醒指令示例:\n - 记录 10分钟后 提醒我喝水\n - 记录 每天 8:00 提醒我吃早饭\n"
            help_message += " - 记录 每周一 9:00 开周会\n - 记录 每月 8号 8:00 开会\n - 记录 每年 3月15日 生日快乐\n"
            help_message += " - 记录 17:30 下班提醒\n\n"
            help_message += "📋管理提醒:\n - 我的记录 (查看所有提醒)\n - 删除 序号 (取消单个提醒)\n"
            help_message += " - 删除 全部 (取消所有提醒)\n - 记录帮助 (查看帮助信息)"
            if is_group_chat:
                await bot.send_at_message(chat_id, help_message, [wxid])
            else:
                await bot.send_text_message(chat_id, help_message)
            return False

        return True

    @schedule('interval', seconds=30)
    async def check_reminders(self, bot: WechatAPIClient):
        now = datetime.now()
        buffer_time = timedelta(seconds=30)
        check_start = now - buffer_time
        check_end = now + buffer_time
        
        wxids = set()

        for filename in os.listdir(self.data_dir):
            if filename.startswith("user_") and filename.endswith(".db"):
                wxid = filename[5:-3]
                wxids.add(wxid)

        for wxid in wxids:
            try:
                reminders = await self.query_reminders(wxid)
                if reminders:
                    for id, content, reminder_type, reminder_time, chat_id in reminders:
                        try:
                            if reminder_type == "every_day":
                                next_time = await self.calculate_remind_time("every_day", reminder_time)
                            else:
                                next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                            
                            if next_time and check_start <= next_time <= check_end:
                                await self.send_reminder(bot, wxid, content, id, chat_id)
                                
                                if reminder_type in ["daily", "weekly", "monthly", "yearly", "every_hour", "every_day", "every_week"]:
                                    new_next_time = await self.calculate_remind_time(reminder_type, reminder_time)
                                    if new_next_time:
                                        db_path = self.get_db_path(wxid)
                                        conn = sqlite3.connect(db_path)
                                        cursor = conn.cursor()
                                        try:
                                            cursor.execute(
                                                "UPDATE reminders SET reminder_time = ? WHERE id = ?",
                                                (reminder_time, id)
                                            )
                                            conn.commit()
                                            logger.info(f"已更新提醒 {id} 的下次提醒时间为 {new_next_time}")
                                        except sqlite3.Error as e:
                                            logger.error(f"更新提醒时间失败: {e}")
                                        finally:
                                            conn.close()
                                else:
                                    await self.delete_reminder(wxid, id)
                                
                        except ValueError as e:
                            logger.warning(f"时间格式错误，无法执行提醒 {id}: {e}")
                            
            except Exception as e:
                logger.exception(f"处理用户 {wxid} 的提醒时出错: {e}")

    async def send_reminder(self, bot: WechatAPIClient, wxid: str, content: str, reminder_id: int, chat_id: str):
        try:
            # 获取消息的第一个词
            first_word = content.split()[0] if content else ""
            
            # 检查是否是其他插件的命令
            if first_word in self.other_plugin_cmd:
                logger.info(f"检测到插件联动命令: {first_word}")
                try:
                    # 构造一个消息事件
                    simulated_message = {
                        "MsgId": str(int(time.time() * 1000)),
                        "ToWxid": bot.wxid,  # 机器人的 wxid
                        "MsgType": 1,  # 文本消息
                        "Content": content,
                        "Status": 3,
                        "ImgStatus": 1,
                        "ImgBuf": {"iLen": 0},
                        "CreateTime": int(time.time()),
                        "MsgSource": "",
                        "PushContent": "",
                        "NewMsgId": str(int(time.time() * 1000)),
                        "MsgSeq": int(time.time()),
                        "FromWxid": chat_id,
                        "IsGroup": chat_id.endswith("@chatroom"),
                        "SenderWxid": wxid,
                        "Ats": []
                    }
                    
                    # 触发文本消息事件
                    await EventManager.emit("text_message", bot, simulated_message)
                    logger.info(f"成功触发插件命令: {content}")
                except Exception as e:
                    logger.error(f"触发插件命令失败: {e}")
                    await self._send_normal_reminder(bot, wxid, content, reminder_id, chat_id)
            else:
                await self._send_normal_reminder(bot, wxid, content, reminder_id, chat_id)
                
        except Exception as e:
            logger.error(f"发送提醒消息失败: {e}")

    async def _send_normal_reminder(self, bot: WechatAPIClient, wxid: str, content: str, reminder_id: int, chat_id: str):
        """发送普通提醒消息"""
        try:
            nickname = await bot.get_nickname(wxid)
            if not nickname:
                nickname = "用户"
        except Exception as e:
            logger.error(f"获取用户 {wxid} 昵称失败: {e}")
            nickname = "用户"

        output = f"⏰-----XY-----⏰\n"
        output += "⏳达到时间啦⏳\n"
        output += f"🆔任务ID：{reminder_id}\n"
        output += f"🗒️内 容：{content}\n"
        output += f"⏰提醒时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        output += "——————————————————\n"
        
        if chat_id.endswith("@chatroom"):
            await bot.send_at_message(chat_id, output, [wxid])
        else:
            await bot.send_text_message(chat_id, output)

    async def _check_point(self, bot: WechatAPIClient, message: dict) -> bool:
        wxid = message["SenderWxid"]
        chat_id = message["FromWxid"]
        is_group_chat = chat_id.endswith("chatroom")
        try:
            nickname = await bot.get_nickname(wxid)
            if not nickname:
                nickname = "用户"
        except Exception as e:
            logger.error(f"获取用户 {wxid} 昵称失败: {e}")
            nickname = "用户"

        if wxid in self.admins and self.admin_ignore:
            return True
        elif self.db.get_whitelist(wxid) and self.whitelist_ignore:
            return True
        else:
            if self.db.get_points(wxid) < self.price:
                error_msg = f"\n😭-----XY-----\n你的积分不够啦！需要 {self.price} 积分"
                if is_group_chat:
                    await bot.send_at_message(chat_id, error_msg, [wxid])
                else:
                    await bot.send_text_message(chat_id, error_msg)
                return False
            self.db.add_points(wxid, -self.price)
            return True

    async def calculate_remind_time(self, reminder_type: str, reminder_time: str) -> Optional[datetime]:
        now = datetime.now()
        try:
            if reminder_type == "one_time":
                if isinstance(reminder_time, str):
                    try:
                        return datetime.strptime(reminder_time, '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        logger.warning(f"无法解析 one_time 时间格式: {reminder_time}")
                        return None
                return None
                
            elif reminder_type == "every_day":
                if not reminder_time:
                    return None
                hour, minute = map(int, reminder_time.split(":"))
                next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_time <= now:
                    next_time += timedelta(days=1)
                return next_time
            
            elif reminder_type == "daily":
                hour, minute = map(int, reminder_time.split(":"))
                next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_time <= now:
                    next_time += timedelta(days=1)
                return next_time
            
            elif reminder_type == "weekly":
                weekday, time_str = reminder_time.split()
                weekday = int(weekday)
                hour, minute = map(int, time_str.split(":"))
                days_ahead = weekday - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                next_time = now + timedelta(days=days_ahead)
                return next_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            elif reminder_type == "monthly":
                day, time_str = reminder_time.split()
                day = int(day)
                hour, minute = map(int, time_str.split(":"))
                next_time = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
                if next_time <= now:
                    month = next_time.month + 1
                    year = next_time.year
                    if month > 12:
                        month = 1
                        year += 1
                    next_time = next_time.replace(year=year, month=month)
                return next_time
            
            elif reminder_type == "yearly":
                month, day, time_str = reminder_time.split()
                month, day = int(month), int(day)
                hour, minute = map(int, time_str.split(":"))
                next_time = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
                if next_time <= now:
                    next_time = next_time.replace(year=now.year + 1)
                return next_time
            
            elif reminder_type == "every_hour":
                next_time = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
                return next_time
            
            elif reminder_type == "every_week":
                hour, minute = map(int, reminder_time.split(":"))
                next_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if next_time <= now:
                    next_time += timedelta(days=7)
                return next_time
            
            else:
                logger.warning(f"未知的提醒类型: {reminder_type}")
                return None
        except ValueError as e:
            logger.warning(f"时间格式错误: {reminder_time}, 错误信息: {e}")
            return None

    async def create_reminder_task(self, bot: WechatAPIClient, wxid: str, content: str, remind_time: datetime, message_id: int, new_id: int):
        now = datetime.now()
        if remind_time <= now:
            logger.warning(f"提醒时间 {remind_time} 已经过去，无法创建定时任务")
            return
        delay = (remind_time - now).total_seconds()

        async def reminder_callback():
            try:
                await self.send_reminder(bot, wxid, content, new_id, chat_id) # type: ignore
            except Exception as e:
                logger.exception(f"执行定时任务失败: {e}")

        asyncio.create_task(self.schedule_reminder(delay, reminder_callback))

    async def schedule_reminder(self, delay: float, callback):
        await asyncio.sleep(delay)
        await callback()