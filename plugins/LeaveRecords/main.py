import datetime
import functools
import os
import re
import tomllib
from typing import Dict, List, Optional, Any, Union

from loguru import logger
from WechatAPI import WechatAPIClient
from utils.admin_manager import admin_manager
from utils.decorators import on_text_message
from utils.plugin_base import PluginBase

from plugins.LeaveRecords.db_models import LeaveRecordsDB


def require_admin(func):
    """管理员权限检查装饰器"""
    @functools.wraps(func)
    async def wrapper(self, bot, group_id, user_id, *args, **kwargs):
        if not await self.is_admin(bot, group_id, user_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，需要管理员权限",
                at=user_id
            )
            return False
        return await func(self, bot, group_id, user_id, *args, **kwargs)
    return wrapper


class LeaveRecords(PluginBase):
    """请假记录插件"""
    description = "请假记录插件"
    author = "XYBot开发者"
    version = "1.0.0"

    def __init__(self):
        super().__init__()
        
        # 配置文件路径
        self.config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
        
        # 默认启用
        self.enable = True
        
        # 命令映射
        self.commands = []
        
        # 实例化数据库
        self.db = LeaveRecordsDB()
        
        # 删除模式记录 {group_id: {user_id: True/False}}
        self.delete_mode = {}
        
        # 加载配置
        self.load_config()
        self._load_command_map()
        
    def load_config(self):
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
                self.save_config()
        except Exception as e:
            logger.error(f"加载请假记录插件配置文件失败: {str(e)}")
            self.save_config()
            
    def save_config(self):
        """保存插件配置"""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # 构建配置
            config = {"basic": {"enable": self.enable}}
            
            # 保存为TOML格式
            with open(self.config_path, "wb") as f:
                import tomli_w
                tomli_w.dump(config, f)
            logger.info("请假记录插件配置已保存")
        except Exception as e:
            logger.error(f"保存请假记录插件配置失败: {str(e)}")
            
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
            # 使用默认命令列表
            self._create_default_command_map()
            
    def _create_default_command_map(self):
        """创建默认命令映射"""
        default_commands = [
            {
                "name": "请假",
                "description": "记录请假信息",
                "usage": "请假",
                "admin_only": True,
                "hidden": False
            },
            {
                "name": "查请假记录",
                "description": "查询请假记录",
                "usage": "查请假记录 [日期]",
                "admin_only": True,
                "hidden": False
            },
            {
                "name": "删除请假记录",
                "description": "删除请假记录",
                "usage": "删除请假记录 [日期]",
                "admin_only": True,
                "hidden": False
            },
            {
                "name": "启用请假",
                "description": "启用请假记录功能",
                "usage": "启用请假",
                "admin_only": True,
                "hidden": False
            },
            {
                "name": "禁用请假",
                "description": "禁用请假记录功能",
                "usage": "禁用请假",
                "admin_only": True,
                "hidden": False
            }
        ]
        
        self.commands = default_commands
        
        try:
            os.makedirs(os.path.dirname(self.command_map_path), exist_ok=True)
            with open(self.command_map_path, "wb") as f:
                import tomli_w
                tomli_w.dump({"commands": default_commands}, f)
            logger.success("已创建默认命令映射")
        except Exception as e:
            logger.error(f"创建默认命令映射失败: {str(e)}")
            
    async def async_init(self, bot=None):
        """异步初始化"""
        pass
        
    def _get_command_config(self, command_name: str) -> dict:
        """获取命令配置"""
        for command in self.commands:
            if command.get("name") == command_name:
                return command
        return {}
        
    def _is_command_admin_only(self, command_name: str) -> bool:
        """检查命令是否仅限管理员使用"""
        command_config = self._get_command_config(command_name)
        return command_config.get("admin_only", False)
    
    @staticmethod
    def parse_date_from_text(text: str) -> Optional[datetime.date]:
        """从文本中解析日期，支持MM-DD格式"""
        pattern = r'(\d{2})-(\d{2})'
        match = re.search(pattern, text)
        if match:
            month, day = map(int, match.groups())
            try:
                today = datetime.date.today()
                return datetime.date(today.year, month, day)
            except ValueError:
                return None
        return None
    
    @staticmethod
    def format_date(date: datetime.date) -> str:
        """格式化日期为详细格式"""
        # 转换为更详细的日期格式，包含年份和星期几
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[date.weekday()]
        return f"{date.year}年{date.month:02d}月{date.day:02d}日 {weekday}"

    @staticmethod
    def format_datetime(dt: datetime.datetime) -> str:
        """格式化日期时间为详细格式"""
        weekday_names = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekday_names[dt.weekday()]
        return f"{dt.year}年{dt.month:02d}月{dt.day:02d}日 {weekday} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    
    async def get_user_nickname(self, bot, group_id: str, user_id: str) -> str:
        """获取用户在群中的昵称，优先使用群昵称，如果没有则使用微信昵称或wxid"""
        try:
            # 使用get_chatroom_nickname API获取用户的群昵称
            nickname = await bot.get_chatroom_nickname(group_id, user_id)
            return nickname if nickname else user_id
        except Exception as e:
            logger.error(f"获取用户群昵称失败: {str(e)}")
            try:
                # 尝试获取普通昵称作为备选
                nickname = await bot.get_nickname(user_id)
                return nickname if nickname else user_id
            except Exception as e2:
                logger.error(f"获取普通昵称也失败: {str(e2)}")
                return user_id  # 如果获取失败，返回用户wxid
                
    async def is_admin(self, bot, group_id: str, user_id: str) -> bool:
        """检查用户是否为管理员
        
        检查策略：
        1. 首先检查是否是超级管理员
        2. 然后检查是否是群管理员
        """
        try:
            # 检查是否是超级管理员
            try:
                from plugins.AdminManager.main import AdminManagerPlugin
                admin_plugin = AdminManagerPlugin()
                if user_id in admin_plugin.super_admins:
                    logger.info(f"用户 {user_id} 是超级管理员")
                    return True
            except Exception as e:
                logger.error(f"检查超级管理员失败: {e}")
            
            # 检查是否是群管理员
            if await admin_manager.is_admin(group_id, user_id):
                return True
            
            # 检查是否是群主
            try:
                group_info = await bot.get_chatroom_info(group_id)
                if group_info.get("ChatRoomOwner") == user_id:
                    return True
            except Exception as e:
                logger.error(f"获取群信息失败: {str(e)}")
            
            return False
        except Exception as e:
            logger.error(f"检查管理员权限失败: {str(e)}")
            return False
            
    async def handle_leave_request(self, bot: WechatAPIClient, message: dict) -> bool:
        """处理请假请求"""
        group_id = message.get("FromWxid", "")
        user_id = message.get("SenderWxid", "")
        
        # 检查群是否启用
        if not self.db.is_group_enabled(group_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 该群尚未启用请假记录功能，请联系管理员",
                at=user_id
            )
            return True
        
        # 检查今日是否已请假
        today = datetime.date.today()
        today_leave = self.db.get_leave_by_date(group_id, user_id, today)
        if today_leave:
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 您今日已请假，无需重复请假",
                at=user_id
            )
            return True
        
        # 获取用户昵称
        nickname = await self.get_user_nickname(bot, group_id, user_id)
        
        # 当前时间
        now = datetime.datetime.now()
        formatted_datetime = self.format_datetime(now)
        
        # 记录请假
        success = self.db.add_leave_record(group_id, user_id, nickname, today)
        if success:
            await bot.send_text_message(
                wxid=group_id,
                content=f"✅ 请假成功！\n"
                         f"👤 用户: {nickname}\n"
                         f"📅 日期: {self.format_date(today)}\n"
                         f"⏱️ 请假时间: {now.strftime('%H:%M:%S')}\n"
                         f"📝 记录时间: {formatted_datetime}",
                at=user_id
            )
            logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 请假成功")
        else:
            await bot.send_text_message(
                wxid=group_id,
                content="❌ 请假失败，请稍后重试",
                at=user_id
            )
            logger.error(f"用户 {user_id} 在群 {group_id} 请假失败")
            
        return True
        
    async def handle_query_leave(self, bot: WechatAPIClient, message: dict) -> bool:
        """处理查询请假记录"""
        content = message.get("Content", "")
        group_id = message.get("FromWxid", "")
        user_id = message.get("SenderWxid", "")
        
        # 检查管理员权限
        if not await self.is_admin(bot, group_id, user_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，需要管理员权限",
                at=user_id
            )
            return True
        
        # 检查群是否启用
        if not self.db.is_group_enabled(group_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 该群尚未启用请假记录功能，请先启用",
                at=user_id
            )
            return True
        
        # 解析日期
        query_date = self.parse_date_from_text(content)
        if query_date is None:
            query_date = datetime.date.today()
            
        # 获取请假记录
        leave_records = self.db.get_leaves_by_date(group_id, query_date)
        
        if not leave_records:
            await bot.send_text_message(
                wxid=group_id,
                content=f"📅 {self.format_date(query_date)} 无请假记录",
                at=user_id
            )
            return True
        
        # 生成请假记录报告
        report = f"📊 请假记录统计\n\n"
        report += f"📅 查询日期：{self.format_date(query_date)}\n"
        report += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, record in enumerate(leave_records, 1):
            created_time = datetime.datetime.fromisoformat(record['created_at'].replace(' ', 'T'))
            report += f"{i}. {record['user_nickname']}\n"
            report += f"   ⏱️ 记录时间：{created_time.strftime('%H:%M:%S')}\n"
            
        report += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        report += f"📝 统计：共 {len(leave_records)} 人请假\n"
        report += f"🕒 查询时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        await bot.send_text_message(
            wxid=group_id,
            content=report,
            at=user_id
        )
        return True
        
    async def handle_delete_leave(self, bot: WechatAPIClient, message: dict) -> bool:
        """处理删除请假记录"""
        content = message.get("Content", "")
        group_id = message.get("FromWxid", "")
        user_id = message.get("SenderWxid", "")
        
        # 检查管理员权限
        if not await self.is_admin(bot, group_id, user_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，需要管理员权限",
                at=user_id
            )
            return True
        
        # 检查群是否启用
        if not self.db.is_group_enabled(group_id):
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 该群尚未启用请假记录功能",
                at=user_id
            )
            return True
            
        # 如果包含数字，可能是在删除模式中选择序号
        if content.isdigit() and group_id in self.delete_mode and user_id in self.delete_mode[group_id]:
            index = int(content)
            date = self.delete_mode[group_id][user_id]
            
            # 获取该日期的请假记录
            leave_records = self.db.get_leaves_by_date(group_id, date)
            
            if 1 <= index <= len(leave_records):
                record = leave_records[index-1]
                record_id = record["id"]
                
                # 删除记录
                success = self.db.delete_leave_record(record_id)
                if success:
                    await bot.send_text_message(
                        wxid=group_id,
                        content=f"✅ 已删除 {record['user_nickname']} 在 {self.format_date(date)} 的请假记录",
                        at=user_id
                    )
                    logger.info(f"管理员 {user_id} 删除了群 {group_id} 中 {record['user_nickname']} 的请假记录")
                else:
                    await bot.send_text_message(
                        wxid=group_id,
                        content="❌ 删除请假记录失败",
                        at=user_id
                    )
                    logger.error(f"删除请假记录失败: {record_id}")
                
                # 退出删除模式
                if group_id in self.delete_mode and user_id in self.delete_mode[group_id]:
                    del self.delete_mode[group_id][user_id]
                    if not self.delete_mode[group_id]:
                        del self.delete_mode[group_id]
            else:
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"❌ 序号无效，请输入1-{len(leave_records)}之间的数字",
                    at=user_id
                )
            return True
        
        # 解析日期
        query_date = self.parse_date_from_text(content)
        if query_date is None:
            query_date = datetime.date.today()
            
        # 获取请假记录
        leave_records = self.db.get_leaves_by_date(group_id, query_date)
        
        if not leave_records:
            await bot.send_text_message(
                wxid=group_id,
                content=f"📅 {self.format_date(query_date)} 无请假记录",
                at=user_id
            )
            return True
        
        # 生成请假记录报告
        report = f"📊 请假记录管理\n\n"
        report += f"📅 查询日期：{self.format_date(query_date)}\n"
        report += f"━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, record in enumerate(leave_records, 1):
            created_time = datetime.datetime.fromisoformat(record['created_at'].replace(' ', 'T'))
            report += f"{i}. {record['user_nickname']}\n"
            report += f"   ⏱️ 记录时间：{created_time.strftime('%H:%M:%S')}\n"
            
        report += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        report += f"📝 统计：共 {len(leave_records)} 人请假\n"
        report += f"⚠️ 请回复序号删除对应记录，例如：1"
        
        await bot.send_text_message(
            wxid=group_id,
            content=report,
            at=user_id
        )
        
        # 设置删除模式
        if group_id not in self.delete_mode:
            self.delete_mode[group_id] = {}
        self.delete_mode[group_id][user_id] = query_date
        
        return True
        
    @require_admin
    async def handle_enable_leave(self, bot: WechatAPIClient, group_id: str, user_id: str) -> bool:
        """启用请假记录功能"""
        success = self.db.set_group_enabled(group_id, True)
        if success:
            await bot.send_text_message(
                wxid=group_id,
                content="✅ 已启用请假记录功能",
                at=user_id
            )
            logger.info(f"管理员 {user_id} 在群 {group_id} 启用了请假记录功能")
        else:
            await bot.send_text_message(
                wxid=group_id,
                content="❌ 启用请假记录功能失败",
                at=user_id
            )
            logger.error(f"启用群 {group_id} 的请假记录功能失败")
        return True
        
    @require_admin
    async def handle_disable_leave(self, bot: WechatAPIClient, group_id: str, user_id: str) -> bool:
        """禁用请假记录功能"""
        success = self.db.set_group_enabled(group_id, False)
        if success:
            await bot.send_text_message(
                wxid=group_id,
                content="✅ 已禁用请假记录功能",
                at=user_id
            )
            logger.info(f"管理员 {user_id} 在群 {group_id} 禁用了请假记录功能")
        else:
            await bot.send_text_message(
                wxid=group_id,
                content="❌ 禁用请假记录功能失败",
                at=user_id
            )
            logger.error(f"禁用群 {group_id} 的请假记录功能失败")
        return True
        
    @on_text_message(priority=50)
    async def on_text_message(self, bot: WechatAPIClient, message: dict) -> bool:
        """处理文本消息"""
        if not self.enable:
            return True  # 允许其他插件处理
            
        content = message.get("Content", "").strip()
        from_wxid = message.get("FromWxid", "")
        is_group = message.get("IsGroup", False)
        
        # 只处理群聊消息
        if not is_group:
            return True  # 允许其他插件处理
            
        # 处理请假命令
        if content == "请假":
            return await self.handle_leave_request(bot, message)
            
        # 处理查询请假记录命令
        if content.startswith("查请假记录"):
            return await self.handle_query_leave(bot, message)
            
        # 处理删除请假记录命令
        if content.startswith("删除请假记录") or (content.isdigit() and from_wxid in self.delete_mode):
            return await self.handle_delete_leave(bot, message)
            
        # 处理启用请假功能命令
        if content == "启用请假":
            user_id = message.get("SenderWxid", "")
            return await self.handle_enable_leave(bot, from_wxid, user_id)
            
        # 处理禁用请假功能命令
        if content == "禁用请假":
            user_id = message.get("SenderWxid", "")
            return await self.handle_disable_leave(bot, from_wxid, user_id)
            
        return True  # 允许其他插件处理 