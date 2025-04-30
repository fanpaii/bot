import os
import re
import tomllib
import datetime
import asyncio
from typing import List, Dict, Any, Tuple, Optional, Union, Set
from os import PathLike

from loguru import logger
from utils.plugin_base import PluginBase
from utils.decorators import on_text_message, on_image_message, on_voice_message, on_file_message, on_at_message, schedule
from utils.admin_manager import AdminManager
from WechatAPI.Client import WechatAPIClient

from .db_models import MaikeDB


class MaikeSequence(PluginBase):
    """麦序管理插件，用于管理群聊麦序表和相关功能"""
    
    description = "麦序管理插件"
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
            self.cmd_enable = self.commands.get("enable", "启用麦序")
            self.cmd_disable = self.commands.get("disable", "禁用麦序")
            self.cmd_query_settings = self.commands.get("query_settings", "查询设置数据")
            self.cmd_set_start_minute = self.commands.get("set_start_minute", "麦序开始分钟")
            self.cmd_set_end_minute = self.commands.get("set_end_minute", "麦序截止分钟")
            self.cmd_set_buffer_time = self.commands.get("set_buffer_time", "补位时间")
            self.cmd_set_max_slots = self.commands.get("set_max_slots", "扣排人数")
            self.cmd_set_host = self.commands.get("set_host", "设置主持")
            self.cmd_set_maike_doc = self.commands.get("set_maike_doc", "麦序文档")
            self.cmd_query_maike_doc = self.commands.get("query_maike_doc", "查询麦序文档")
            self.cmd_query_statistics = self.commands.get("query_maike_statistics", "查询麦序统计")
            self.cmd_query_black_maike = self.commands.get("query_black_maike", "查询黑麦统计")
            
            # 排档关键词
            self.register_keywords = self.commands.get("register_keywords", ["p", "排", "P"])
            self.force_set_maike = self.commands.get("force_set_maike", ["设麦序", "补"])
            
            # 初始化数据库
            self.db = MaikeDB()
            
            # 记录已经发送过麦序表的时间段，避免重复发送 {日期_群组ID_小时: True}
            self.sent_maike_tables = {}
            
            # 超级管理员列表
            self.super_admins = []
            
            # 从主配置获取超管列表
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            self.super_admins = main_config.get("XYBot", {}).get("admins", [])
            
            logger.success("麦序管理插件初始化成功")
        except Exception as e:
            logger.error(f"加载麦序管理插件配置文件失败: {str(e)}")
            self.enable = False
            
    async def async_init(self, bot=None):
        """异步初始化"""
        # 初始化管理员管理器
        self.admin_manager = AdminManager()
        await self.admin_manager.initialize()
        logger.success("麦序管理插件管理员管理器初始化成功")
        
    async def is_admin(self, chatroom_id: str, sender_wxid: str) -> bool:
        """检查用户是否为管理员"""
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
        try:
            is_admin = await self.admin_manager.is_admin(chatroom_id, sender_wxid)
            logger.info(f"用户 {sender_wxid} 在群 {chatroom_id} 是管理员: {is_admin}")
            return is_admin
        except Exception as e:
            logger.error(f"检查管理员失败: {e}")
            return False
    
    async def get_nickname(self, bot: WechatAPIClient, wxid: str, chatroom_id: str = None) -> str:
        """获取用户昵称"""
        try:
            if chatroom_id:
                # 获取群聊中的昵称
                profile = await bot.get_chatroom_nickname(chatroom_id, wxid)
                return profile if profile else wxid
            else:
                # 获取好友昵称
                profile = await bot.get_nickname(wxid)
                return profile if profile else wxid
        except Exception as e:
            logger.error(f"获取用户昵称失败: {e}")
            return wxid
    
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
        
        # 获取用户昵称
        nickname = await self.get_nickname(bot, sender_wxid, from_wxid)
        
        # 处理启用麦序命令
        if content == self.cmd_enable:
            await self.enable_maike(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理禁用麦序命令
        if content == self.cmd_disable:
            await self.disable_maike(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理查询设置数据命令
        if content == self.cmd_query_settings:
            await self.query_settings(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理麦序开始分钟命令
        start_minute_pattern = re.compile(f"^{re.escape(self.cmd_set_start_minute)}\\s+(\\d+)$")
        start_minute_match = start_minute_pattern.match(content)
        if start_minute_match:
            start_minute = int(start_minute_match.group(1))
            await self.set_start_minute(bot, from_wxid, sender_wxid, nickname, start_minute)
            return False
        
        # 处理麦序截止分钟命令
        end_minute_pattern = re.compile(f"^{re.escape(self.cmd_set_end_minute)}\\s+(\\d+)$")
        end_minute_match = end_minute_pattern.match(content)
        if end_minute_match:
            end_minute = int(end_minute_match.group(1))
            await self.set_end_minute(bot, from_wxid, sender_wxid, nickname, end_minute)
            return False
        
        # 处理补位时间命令
        buffer_time_pattern = re.compile(f"^{re.escape(self.cmd_set_buffer_time)}\\s+(\\d+)$")
        buffer_time_match = buffer_time_pattern.match(content)
        if buffer_time_match:
            buffer_time = int(buffer_time_match.group(1))
            await self.set_buffer_time(bot, from_wxid, sender_wxid, nickname, buffer_time)
            return False
        
        # 处理扣排人数命令
        max_slots_pattern = re.compile(f"^{re.escape(self.cmd_set_max_slots)}\\s+(\\d+)$")
        max_slots_match = max_slots_pattern.match(content)
        if max_slots_match:
            max_slots = int(max_slots_match.group(1))
            await self.set_max_slots(bot, from_wxid, sender_wxid, nickname, max_slots)
            return False
        
        # 处理设置主持命令
        if content.startswith(self.cmd_set_host):
            # 获取下一行的多行内容
            lines = content.split('\n')
            if len(lines) > 1:
                host_content = '\n'.join(lines[1:])
                await self.set_host(bot, from_wxid, sender_wxid, nickname, host_content)
                return False
        
        # 处理麦序文档命令
        if content.startswith(self.cmd_set_maike_doc):
            # 获取下一行的多行内容
            lines = content.split('\n')
            if len(lines) > 1:
                doc_content = '\n'.join(lines[1:])
                await self.set_maike_doc(bot, from_wxid, sender_wxid, nickname, doc_content)
                return False
        
        # 处理查询麦序文档命令
        if content == self.cmd_query_maike_doc:
            await self.query_maike_doc(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 处理查询麦序统计命令
        query_pattern = re.compile(f"^{re.escape(self.cmd_query_statistics)}(\\s+.+)?$")
        query_match = query_pattern.match(content)
        if query_match:
            param = query_match.group(1).strip() if query_match.group(1) else None
            await self.query_maike_statistics(bot, from_wxid, sender_wxid, nickname, param)
            return False
        
        # 处理查询黑麦统计命令
        black_pattern = re.compile(f"^{re.escape(self.cmd_query_black_maike)}(\\s+.+)?$")
        black_match = black_pattern.match(content)
        if black_match:
            param = black_match.group(1).strip() if black_match.group(1) else None
            await self.query_black_maike_statistics(bot, from_wxid, sender_wxid, nickname, param)
            return False
        
        # 处理强制设置麦序命令（如"@xx设麦序"或"@xx补"）
        for keyword in self.force_set_maike:
            if "Ats" in message and message["Ats"] and f"@{nickname} {keyword}" in content:
                at_user_wxid = message["Ats"][0]  # 假设只有一个@
                await self.force_set_maike_slot(bot, from_wxid, sender_wxid, nickname, at_user_wxid)
                return False
        
        # 处理排档关键词（如"p", "排", "P"）
        if any(keyword == content for keyword in self.register_keywords):
            await self.register_maike_slot(bot, from_wxid, sender_wxid, nickname)
            return False
        
        # 继续处理其他消息
        return True
    
    # 基础功能实现
    
    async def enable_maike(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """启用麦序功能"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能启用麦序功能")
            return
        
        # 启用麦序功能
        result = self.db.enable_group_maike(chatroom_id)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 麦序功能已启用")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 启用麦序功能失败，请稍后再试")
    
    async def disable_maike(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """禁用麦序功能"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能禁用麦序功能")
            return
        
        # 禁用麦序功能
        result = self.db.disable_group_maike(chatroom_id)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 麦序功能已禁用")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 禁用麦序功能失败，请稍后再试")
    
    async def query_settings(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """查询麦序设置数据"""
        # 获取群聊设置
        settings = self.db.get_group_settings(chatroom_id)
        
        # 获取主持人设置
        hosts = self.db.get_hosts(chatroom_id)
        
        # 构建回复消息
        enabled_text = "已启用" if settings["enabled"] else "未启用"
        host_text = ""
        
        for host in hosts:
            host_text += f"{host['time_range']} {host['host_name']}\n"
        
        if not host_text:
            host_text = "暂无主持人设置\n"
        
        message = f"""1.扣排人数 {settings['max_slots']}
2.手速优先人数 0
3.表序开始分钟 {settings['start_minute']}
4.表序截止分钟{settings['end_minute']}
5.特殊置顶人数 3
6.整点后补位时间 {settings['buffer_time']}
7.接近时间 33
8.手速可否取排 可以取排
9.任务可否取排 不可取排
10.不发排小时
25,2,4,6,8,10,12,14,16,18,20,22,0
11.报备功能
①是否启用报备功能 {enabled_text}
②报备时间10
③报备提示 请在10分钟内回应，回应再发一个'回'

12.打卡选项 矿场 普麦 音响模式 普通模式 计算模式 语音模式 图片模式 文本模式 引用模式 表情包模式

当前主持表:
{host_text}"""
        
        await bot.send_text_message(chatroom_id, message)
    
    async def set_start_minute(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, start_minute: int):
        """设置麦序开始分钟"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置麦序开始分钟")
            return
        
        # 检查参数
        if not 0 <= start_minute < 60:
            await bot.send_text_message(chatroom_id, f"⚠️ 麦序开始分钟必须在0-59之间")
            return
        
        # 更新设置
        result = self.db.update_group_settings(chatroom_id, start_minute=start_minute)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 设置麦序开始分钟为{start_minute}分钟")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置麦序开始分钟失败，请稍后再试")
    
    async def set_end_minute(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, end_minute: int):
        """设置麦序截止分钟"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置麦序截止分钟")
            return
        
        # 检查参数
        if not 0 <= end_minute < 60:
            await bot.send_text_message(chatroom_id, f"⚠️ 麦序截止分钟必须在0-59之间")
            return
        
        # 更新设置
        result = self.db.update_group_settings(chatroom_id, end_minute=end_minute)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 设置麦序截止分钟为{end_minute}分钟")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置麦序截止分钟失败，请稍后再试")
    
    async def set_buffer_time(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, buffer_time: int):
        """设置补位时间"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置补位时间")
            return
        
        # 检查参数
        if buffer_time < 0:
            await bot.send_text_message(chatroom_id, f"⚠️ 补位时间必须大于等于0")
            return
        
        # 更新设置
        result = self.db.update_group_settings(chatroom_id, buffer_time=buffer_time)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 设置补位时间为{buffer_time}分钟")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置补位时间失败，请稍后再试")
    
    async def set_max_slots(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, max_slots: int):
        """设置扣排人数"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置扣排人数")
            return
        
        # 检查参数
        if max_slots <= 0:
            await bot.send_text_message(chatroom_id, f"⚠️ 扣排人数必须大于0")
            return
        
        # 更新设置
        result = self.db.update_group_settings(chatroom_id, max_slots=max_slots)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 设置扣排人数为{max_slots}")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置扣排人数失败，请稍后再试")
    
    async def set_host(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, host_content: str):
        """设置主持人"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置主持人")
            return
        
        # 解析主持人设置内容
        lines = host_content.strip().split('\n')
        success_count = 0
        fail_count = 0
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 尝试匹配"0-2 主持人"格式
            match = re.match(r"(\d+)-(\d+)\s+(.+)", line)
            if match:
                start_hour = int(match.group(1))
                end_hour = int(match.group(2))
                host_name = match.group(3).strip()
                
                # 检查时间范围
                if 0 <= start_hour < 24 and 0 <= end_hour < 24:
                    time_range = f"{start_hour}-{end_hour}"
                    result = self.db.set_host(chatroom_id, time_range, host_name)
                    if result:
                        success_count += 1
                    else:
                        fail_count += 1
                else:
                    fail_count += 1
            else:
                fail_count += 1
        
        if success_count > 0:
            await bot.send_text_message(chatroom_id, f"✅ 成功设置{success_count}个主持人时间段" + 
                                      (f"，{fail_count}个设置失败" if fail_count > 0 else ""))
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置主持人失败，请检查格式是否正确（例如：0-2 主持人）")
    
    async def set_maike_doc(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, doc_content: str):
        """设置麦序文档"""
        # 检查权限
        if not await self.is_admin(chatroom_id, sender_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能设置麦序文档")
            return
        
        # 保存麦序文档
        result = self.db.set_maike_document(chatroom_id, doc_content)
        
        if result:
            await bot.send_text_message(chatroom_id, f"✅ 麦序文档设置成功")
        else:
            await bot.send_text_message(chatroom_id, f"❌ 设置麦序文档失败，请稍后再试")
    
    async def query_maike_doc(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str):
        """查询麦序文档"""
        # 获取麦序文档
        doc_content = self.db.get_maike_document(chatroom_id)
        
        if doc_content:
            await bot.send_text_message(chatroom_id, f"📄 麦序文档：\n{doc_content}")
        else:
            await bot.send_text_message(chatroom_id, f"❓ 未设置麦序文档")
    
    # 麦序排档相关功能
    
    async def is_maike_open(self, chatroom_id: str, current_hour: int = None, current_minute: int = None) -> bool:
        """检查当前时间是否开放麦序排档"""
        # 检查群聊是否启用麦序
        if not self.db.is_group_maike_enabled(chatroom_id):
            return False
        
        # 获取设置
        settings = self.db.get_group_settings(chatroom_id)
        
        # 获取当前时间
        now = datetime.datetime.now()
        current_hour = current_hour if current_hour is not None else now.hour
        current_minute = current_minute if current_minute is not None else now.minute
        
        # 检查当前小时是否有主持人
        host = self.db.get_host_by_hour(chatroom_id, current_hour)
        if not host:
            # 没有主持人的时间段不开放排档
            return False
        
        # 检查是否在排档时间段内（开始分钟到截止分钟）
        if settings["start_minute"] <= current_minute < settings["end_minute"]:
            return True
        
        # 检查是否在补位时间内
        if current_minute >= settings["end_minute"] and current_minute < (settings["end_minute"] + settings["buffer_time"]):
            # 可以补位的情况
            return True
        
        return False
    
    async def can_register_maike(self, chatroom_id: str, current_hour: int = None) -> Tuple[bool, str]:
        """检查是否可以排档，返回(可否排档, 原因)"""
        # 检查是否开放麦序
        now = datetime.datetime.now()
        current_hour = current_hour if current_hour is not None else now.hour
        current_minute = now.minute
        
        if not await self.is_maike_open(chatroom_id, current_hour, current_minute):
            return False, "当前时间不开放麦序排档"
        
        # 获取设置
        settings = self.db.get_group_settings(chatroom_id)
        
        # 获取当前时段的麦序记录
        records = self.db.get_maike_records(chatroom_id, current_hour)
        
        # 检查已排档人数是否达到上限
        if len(records) >= settings["max_slots"]:
            return False, f"当前时段麦序已满({len(records)}/{settings['max_slots']})"
        
        # 检查是否在补位时间内需要特殊处理
        if current_minute >= settings["end_minute"]:
            return True, "补位时间内"
        
        return True, "正常排档时间"
    
    async def register_maike_slot(self, bot: WechatAPIClient, chatroom_id: str, user_wxid: str, user_nickname: str):
        """用户排档"""
        # 检查是否可以排档
        now = datetime.datetime.now()
        current_hour = now.hour
        can_register, reason = await self.can_register_maike(chatroom_id, current_hour)
        
        if not can_register:
            await bot.send_text_message(chatroom_id, f"⚠️ @{user_nickname} 排档失败：{reason}")
            return
        
        # 获取主持人
        host_name = self.db.get_host_by_hour(chatroom_id, current_hour)
        
        # 获取下一个排档号
        slot_number = self.db.get_next_slot_number(chatroom_id, current_hour)
        
        # 排档类型，默认手速
        slot_type = "手速"
        
        # 添加麦序记录
        result = self.db.add_maike_record(
            chatroom_id=chatroom_id,
            user_wxid=user_wxid,
            user_nickname=user_nickname,
            slot_number=slot_number,
            slot_type=slot_type,
            time_hour=current_hour,
            host_name=host_name
        )
        
        if result:
            # 获取当前时段的所有麦序记录
            all_records = self.db.get_maike_records(chatroom_id, current_hour)
            
            # 构建回复消息
            settings = self.db.get_group_settings(chatroom_id)
            buffer_end_time = settings["end_minute"] + settings["buffer_time"]
            
            message = f"主持:{host_name}\n时间:{current_hour}-{current_hour+1}\n—————————\n"
            
            for record in all_records:
                message += f"{record['slot_number']}.@{record['user_nickname']} ({record['slot_type']})\n"
            
            # 添加空位信息
            remaining = settings["max_slots"] - len(all_records)
            message += f"   🈳️：{remaining}\n"
            
            # 添加补位时间信息
            if now.minute < settings["end_minute"]:
                message += f"～{current_hour}时{settings['end_minute']}分之前🉑️补"
            elif now.minute < buffer_end_time:
                message += f"补位中～{current_hour}时{buffer_end_time}分结束"
            
            await bot.send_text_message(chatroom_id, message)
        else:
            await bot.send_text_message(chatroom_id, f"❌ @{user_nickname} 排档失败，请稍后再试")
    
    async def force_set_maike_slot(self, bot: WechatAPIClient, chatroom_id: str, admin_wxid: str, admin_nickname: str, user_wxid: str):
        """管理员强制设置麦序"""
        # 检查权限
        if not await self.is_admin(chatroom_id, admin_wxid):
            await bot.send_text_message(chatroom_id, f"⚠️ 抱歉，只有管理员才能强制设置麦序")
            return
        
        # 检查群聊是否启用麦序
        if not self.db.is_group_maike_enabled(chatroom_id):
            await bot.send_text_message(chatroom_id, f"⚠️ 该群未启用麦序功能")
            return
        
        # 获取用户昵称
        user_nickname = await self.get_nickname(bot, user_wxid, chatroom_id)
        
        # 获取当前时间
        now = datetime.datetime.now()
        current_hour = now.hour
        
        # 获取主持人
        host_name = self.db.get_host_by_hour(chatroom_id, current_hour)
        if not host_name:
            await bot.send_text_message(chatroom_id, f"⚠️ 当前时段没有设置主持人，无法排档")
            return
        
        # 获取设置
        settings = self.db.get_group_settings(chatroom_id)
        
        # 检查是否在补位时间内
        buffer_end_time = settings["end_minute"] + settings["buffer_time"]
        if now.minute < settings["end_minute"] or now.minute >= buffer_end_time:
            await bot.send_text_message(chatroom_id, f"⚠️ 当前不在补位时间内，无法强制设置麦序")
            return
        
        # 获取当前时段的麦序记录
        records = self.db.get_maike_records(chatroom_id, current_hour)
        
        # 检查已排档人数是否达到上限
        if len(records) >= settings["max_slots"]:
            await bot.send_text_message(chatroom_id, f"⚠️ 当前时段麦序已满({len(records)}/{settings['max_slots']})")
            return
        
        # 获取下一个排档号
        slot_number = self.db.get_next_slot_number(chatroom_id, current_hour)
        
        # 排档类型，默认任务
        slot_type = "任务"
        
        # 添加麦序记录
        result = self.db.add_maike_record(
            chatroom_id=chatroom_id,
            user_wxid=user_wxid,
            user_nickname=user_nickname,
            slot_number=slot_number,
            slot_type=slot_type,
            time_hour=current_hour,
            host_name=host_name
        )
        
        if result:
            # 获取当前时段的所有麦序记录
            all_records = self.db.get_maike_records(chatroom_id, current_hour)
            
            # 构建回复消息
            message = f"主持:{host_name}\n时间:{current_hour}-{current_hour+1}\n—————————\n"
            
            for record in all_records:
                message += f"{record['slot_number']}.@{record['user_nickname']} ({record['slot_type']})\n"
            
            # 添加空位信息
            remaining = settings["max_slots"] - len(all_records)
            message += f"   🈳️：{remaining}\n"
            
            # 添加补位时间信息
            message += f"补位中～{current_hour}时{buffer_end_time}分结束"
            
            await bot.send_text_message(chatroom_id, message)
        else:
            await bot.send_text_message(chatroom_id, f"❌ 强制设置 @{user_nickname} 麦序失败，请稍后再试")
    
    # 麦序统计功能
    async def query_maike_statistics(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, param: str = None):
        """查询麦序统计"""
        # 处理时间参数
        if param and param.isdigit():
            # 指定了天数
            days = int(param)
            if days <= 0 or days > 30:
                await bot.send_text_message(chatroom_id, f"⚠️ 查询天数必须在1-30之间")
                return
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=days)
        else:
            # 默认查询7天
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=7)
        
        # 获取统计数据
        statistics = self.db.get_maike_statistics(chatroom_id, start_date, end_date)
        
        if not statistics:
            await bot.send_text_message(chatroom_id, f"📊 该时间段内没有麦序记录")
            return
        
        # 构建回复消息
        message = f"📊 {start_date.strftime('%m-%d')}至{end_date.strftime('%m-%d')}麦序统计：\n\n"
        
        # 按照总次数排序
        sorted_stats = sorted(statistics, key=lambda x: x["total_count"], reverse=True)
        
        for i, stat in enumerate(sorted_stats):
            message += f"{i+1}. @{stat['user_nickname']} - {stat['total_count']}次 (手速{stat['speed_count']}次, 任务{stat['task_count']}次)\n"
        
        await bot.send_text_message(chatroom_id, message)

    async def query_black_maike_statistics(self, bot: WechatAPIClient, chatroom_id: str, sender_wxid: str, nickname: str, param: str = None):
        """查询黑麦统计（未按时上麦的记录）"""
        # 处理时间参数
        if param and param.isdigit():
            # 指定了天数
            days = int(param)
            if days <= 0 or days > 30:
                await bot.send_text_message(chatroom_id, f"⚠️ 查询天数必须在1-30之间")
                return
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=days)
        else:
            # 默认查询7天
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=7)
        
        # 获取黑麦统计数据
        black_statistics = self.db.get_black_maike_statistics(chatroom_id, start_date, end_date)
        
        if not black_statistics:
            await bot.send_text_message(chatroom_id, f"📊 该时间段内没有黑麦记录")
            return
        
        # 构建回复消息
        message = f"⚫ {start_date.strftime('%m-%d')}至{end_date.strftime('%m-%d')}黑麦统计：\n\n"
        
        # 按照总次数排序
        sorted_stats = sorted(black_statistics, key=lambda x: x["black_count"], reverse=True)
        
        for i, stat in enumerate(sorted_stats):
            message += f"{i+1}. @{stat['user_nickname']} - {stat['black_count']}次\n"
        
        await bot.send_text_message(chatroom_id, message)

    # 定时任务功能

    @schedule('cron', minute=0)
    async def auto_send_maike_table(self, bot: WechatAPIClient):
        """整点自动发送麦序表"""
        if not self.enable:
            return
        
        try:
            # 获取当前时间
            now = datetime.datetime.now()
            current_hour = now.hour
            current_date = now.date()
            
            # 日期_群组ID_小时
            for chatroom_id in self.db.get_enabled_groups():
                # 检查当前时段是否有主持人
                host_name = self.db.get_host_by_hour(chatroom_id, current_hour)
                if not host_name:
                    # 没有主持人的时段不发麦序表
                    continue
                
                # 获取设置
                settings = self.db.get_group_settings(chatroom_id)
                
                # 检查是否已经发过
                date_key = f"{current_date.strftime('%Y%m%d')}_{chatroom_id}_{current_hour}"
                if date_key in self.sent_maike_tables:
                    continue
                
                # 标记为已发送
                self.sent_maike_tables[date_key] = True
                
                # 清理过期的记录（超过24小时的）
                expired_keys = []
                for key in self.sent_maike_tables:
                    date_str, _, hour = key.split('_')
                    key_date = datetime.datetime.strptime(date_str, '%Y%m%d').date()
                    key_hour = int(hour)
                    
                    # 计算时间差
                    if (current_date - key_date).days > 0 or (
                        current_date == key_date and current_hour - key_hour > 24
                    ):
                        expired_keys.append(key)
                
                for key in expired_keys:
                    del self.sent_maike_tables[key]
                
                # 获取当前时段的麦序记录
                records = self.db.get_maike_records(chatroom_id, current_hour)
                
                # 构建麦序表消息
                message = f"🔔 整点麦序提醒\n主持:{host_name}\n时间:{current_hour}-{current_hour+1}\n—————————\n"
                
                if not records:
                    message += "暂无排档记录\n"
                else:
                    for record in records:
                        message += f"{record['slot_number']}.@{record['user_nickname']} ({record['slot_type']})\n"
                
                # 添加空位信息
                remaining = settings["max_slots"] - len(records)
                message += f"   🈳️：{remaining}\n"
                
                # 添加补位时间信息
                buffer_end_time = settings["end_minute"] + settings["buffer_time"]
                message += f"～{current_hour}时{settings['end_minute']}分之前🉑️补"
                
                # 发送消息
                await bot.send_text_message(chatroom_id, message)
        except Exception as e:
            logger.error(f"自动发送麦序表失败: {str(e)}")

    @schedule('cron', minute='*/10')
    async def check_black_maike(self, bot: WechatAPIClient):
        """定时检查黑麦情况并记录"""
        if not self.enable:
            return
        
        try:
            # 获取当前时间
            now = datetime.datetime.now()
            current_hour = now.hour
            
            # 只检查前一个小时的麦序记录
            check_hour = (current_hour - 1) % 24
            
            # 遍历所有启用麦序的群组
            for chatroom_id in self.db.get_enabled_groups():
                # 检查前一个时段的主持人
                host_name = self.db.get_host_by_hour(chatroom_id, check_hour)
                if not host_name:
                    # 没有主持人的时段不检查
                    continue
                
                # 获取前一个时段的麦序记录
                records = self.db.get_maike_records(chatroom_id, check_hour)
                
                # 更新黑麦记录
                for record in records:
                    # 检查是否已经标记为黑麦
                    if not record.get("is_black", False):
                        # 标记为黑麦并统计
                        self.db.mark_as_black_maike(
                            chatroom_id=chatroom_id,
                            record_id=record["id"],
                            user_wxid=record["user_wxid"],
                            user_nickname=record["user_nickname"]
                        )
        except Exception as e:
            logger.error(f"检查黑麦情况失败: {str(e)}")
        
    # 预留完善的功能，如撤销排档、修改排档顺序等 