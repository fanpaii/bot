import os
import time
import json
import toml
import asyncio
import datetime
import functools
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional, Tuple, Any, Callable

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
        import toml
        # 使用toml库作为替代

from utils.plugin_base import PluginBase
from utils.decorators import on_text_message
from loguru import logger

from .db_models import TimeRecorder66DB

# 权限检查装饰器
def require_admin(func):
    """管理员权限检查装饰器"""
    @functools.wraps(func)
    async def wrapper(self, bot, group_id, user_id, *args, **kwargs):
        is_admin = await self.is_admin(bot, group_id, user_id)
        if not is_admin:
            await bot.send_text_message(
                wxid=group_id,
                content="⚠️ 权限不足，只有管理员才能执行此命令",
                at=user_id
            )
            logger.warning(f"用户 {user_id} 尝试执行管理员命令 {func.__name__} 但没有权限")
            return False
        return await func(self, bot, group_id, user_id, *args, **kwargs)
    return wrapper

class TimeRecorder66(PluginBase):
    """
    66时长记录插件 - 记录用户的66时长和监控活动时间
    """
    description = "66时长记录插件"
    author = "XYBot开发者"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        self.config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
        
        # 默认配置
        self.config = {
            "plugin": {
                "name": "TimeRecorder66",
                "description": "记录群成员66时长的插件",
                "version": "1.0.0",
                "author": "XYBot开发团队"
            },
            "features": {
                "weekly_report": True,
                "monthly_report": True,
                "report_day": 0,
                "report_hour": 20,
                "report_minute": 0
            }
        }
        
        # 初始化数据库
        self.db = TimeRecorder66DB()
        
        # 命令列表
        self.commands = []
        
        # 加载配置和命令映射
        self.load_config()
        self._load_command_map()
    
    def load_config(self):
        """加载配置文件"""
        try:
            if os.path.exists(self.config_path):
                # 根据Python版本选择合适的TOML解析库
                if TOMLLIB_AVAILABLE:
                    with open(self.config_path, "rb") as f:
                        self.config = tomllib.load(f)
                else:
                    self.config = toml.load(self.config_path)
                
                logger.info(f"成功加载配置文件: {self.config}")
                
                # 从config.toml导入已启用的群组到数据库
                if "groups" in self.config and "enabled_groups" in self.config["groups"]:
                    for group_id in self.config["groups"]["enabled_groups"]:
                        self.db.set_group_enabled(group_id, True)
                    
                    # 从配置文件中移除群组设置，现在由数据库管理
                    if "groups" in self.config:
                        del self.config["groups"]
                        self.save_config()
            else:
                # 创建默认配置
                with open(self.config_path, "w", encoding="utf-8") as f:
                    toml.dump(self.config, f)
                logger.info(f"创建默认配置文件: {self.config}")
        except Exception as e:
            logger.error(f"加载配置文件失败: {str(e)}")
    
    def _load_command_map(self):
        """加载命令映射"""
        try:
            if os.path.exists(self.command_map_path):
                # 根据Python版本选择合适的TOML解析库
                if TOMLLIB_AVAILABLE:
                    with open(self.command_map_path, "rb") as f:
                        config = tomllib.load(f)
                else:
                    config = toml.load(self.command_map_path)
                
                self.commands = config.get("commands", [])
                logger.info(f"已加载 {len(self.commands)} 条命令映射")
            else:
                # 如果命令映射文件不存在，记录错误并使用空列表
                logger.error(f"命令映射文件不存在: {self.command_map_path}")
                self.commands = []
        except Exception as e:
            logger.error(f"加载命令映射失败: {str(e)}")
            # 使用空列表
            self.commands = []
    
    def _get_command_config(self, command_name: str) -> dict:
        """获取命令配置"""
        for command in self.commands:
            if command.get("name", "") == command_name:
                return command
        return {}
    
    def _is_command_admin_only(self, command_name: str) -> bool:
        """检查命令是否仅管理员可用"""
        command = self._get_command_config(command_name)
        return command.get("admin_only", False)
    
    def save_config(self):
        """保存配置文件"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                toml.dump(self.config, f)
            logger.info(f"成功保存配置文件: {self.config}")
        except Exception as e:
            logger.error(f"保存配置文件失败: {str(e)}")
    
    async def async_init(self, bot=None):
        """异步初始化"""
        logger.info("66时长记录插件初始化完成")
    
    def is_group_enabled(self, group_id: str) -> bool:
        """检查群是否启用了66时长记录"""
        return self.db.is_group_enabled(group_id)
    
    def format_duration(self, seconds: int) -> str:
        """格式化时长"""
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours)}小时{int(minutes)}分钟{int(seconds)}秒"
    
    @staticmethod
    def get_current_time_str() -> str:
        """获取当前时间字符串"""
        return datetime.datetime.now().strftime("%H:%M:%S")
    
    async def get_user_nickname(self, bot, group_id: str, user_id: str) -> str:
        """获取用户在群中的昵称，优先使用群昵称，如果没有则使用微信昵称或wxid"""
        try:
            # 使用get_chatroom_nickname API获取用户的群昵称
            nickname = await bot.get_chatroom_nickname(group_id, user_id)
            logger.debug(f"获取用户昵称成功: {nickname} (用户: {user_id}, 群: {group_id})")
            return nickname if nickname else user_id
        except Exception as e:
            logger.error(f"获取用户昵称失败: {str(e)}", exc_info=True)
            try:
                # 尝试获取普通昵称作为备选
                nickname = await bot.get_nickname(user_id)
                return nickname if nickname else user_id
            except Exception as e2:
                logger.error(f"获取普通昵称也失败: {str(e2)}", exc_info=True)
                return user_id  # 如果获取失败，返回用户wxid
    
    async def start_recording(self, bot, group_id: str, user_id: str) -> None:
        """开始记录时长"""
        try:
            # 检查群是否启用
            if not self.is_group_enabled(group_id):
                return
            
            # 获取用户群昵称
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            
            # 检查用户是否已经在记录中
            active_recording = self.db.get_active_recording(group_id, user_id)
            if active_recording:
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"你已经开始记录66时长了，请先使用「66结束」命令结束当前记录。",
                    at=user_id
                )
                return
            
            # 开始记录
            if self.db.start_recording(group_id, user_id, nickname):
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"开始记录66时长，当前时间: {self.get_current_time_str()}",
                    at=user_id
                )
                logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 开始记录66时长")
            else:
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"开始记录66时长失败，请稍后重试。",
                    at=user_id
                )
        except Exception as e:
            logger.error(f"开始记录66时长失败: {str(e)}", exc_info=True)
    
    async def end_recording(self, bot, group_id: str, user_id: str) -> None:
        """结束记录时长"""
        try:
            # 检查群是否启用
            if not self.is_group_enabled(group_id):
                return
            
            # 获取用户群昵称
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            
            # 检查用户是否在记录中
            active_recording = self.db.get_active_recording(group_id, user_id)
            if not active_recording:
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"你没有正在进行的66时长记录，请先使用「66报备」命令开始记录。",
                    at=user_id
                )
                return
            
            # 结束记录
            record = self.db.end_recording(group_id, user_id)
            if record:
                formatted_duration = self.format_duration(record["duration"])
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"结束66时长记录，本次时长: {formatted_duration}，结束时间: {self.get_current_time_str()}",
                    at=user_id
                )
                logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 结束66时长记录，时长: {formatted_duration}")
            else:
                await bot.send_text_message(
                    wxid=group_id,
                    content=f"结束66时长记录失败，请稍后重试。",
                    at=user_id
                )
        except Exception as e:
            logger.error(f"结束记录66时长失败: {str(e)}", exc_info=True)
    
    async def query_records(self, bot, group_id: str, user_id: str, content: str) -> None:
        """查询66时长记录"""
        try:
            # 检查群是否启用
            if not self.is_group_enabled(group_id):
                return
            
            # 解析查询参数
            parts = content.split()
            today = datetime.date.today()
            query_date = today  # 默认查询今天
            
            if len(parts) > 1:
                query_param = parts[1]
                
                # 处理特定日期的情况
                date_pattern = re.compile(r'^(\d{1,2})-(\d{1,2})$')
                date_match = date_pattern.match(query_param)
                
                if date_match:
                    # 匹配到MM-DD格式
                    month = int(date_match.group(1))
                    day = int(date_match.group(2))
                    
                    # 验证月份和日期的有效性
                    if 1 <= month <= 12 and 1 <= day <= 31:
                        try:
                            # 创建日期对象
                            current_year = today.year
                            query_date = datetime.date(current_year, month, day)
                            
                            # 如果指定日期比今天还未来，很可能是去年的日期
                            if query_date > today:
                                query_date = datetime.date(current_year - 1, month, day)
                                
                            response = await self.generate_daily_report(group_id, query_date)
                            await bot.send_text_message(wxid=group_id, content=response, at=user_id)
                            
                            # 获取发送请求用户的昵称用于日志
                            nickname = await self.get_user_nickname(bot, group_id, user_id)
                            logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 查询 {query_date.strftime('%Y-%m-%d')} 的66时长记录")
                            return
                        except ValueError:
                            await bot.send_text_message(
                                wxid=group_id, 
                                content=f"日期格式无效: {month}月{day}日不是有效日期", 
                                at=user_id
                            )
                            return
                    else:
                        await bot.send_text_message(
                            wxid=group_id, 
                            content="日期格式无效，月份应在1-12之间，日应在1-31之间", 
                            at=user_id
                        )
                        return
                elif query_param == "昨天":
                    yesterday = today - datetime.timedelta(days=1)
                    query_date = yesterday
                    response = await self.generate_daily_report(group_id, query_date)
                    await bot.send_text_message(wxid=group_id, content=response, at=user_id)
                    return
                elif query_param == "本周":
                    # 实现本周查询逻辑
                    start_of_week = today - datetime.timedelta(days=today.weekday())
                    response = await self.generate_weekly_report(group_id, start_of_week)
                    await bot.send_text_message(wxid=group_id, content=response, at=user_id)
                    return
                elif query_param == "本月":
                    # 实现本月查询逻辑
                    start_of_month = datetime.date(today.year, today.month, 1)
                    response = await self.generate_monthly_report(group_id, start_of_month)
                    await bot.send_text_message(wxid=group_id, content=response, at=user_id)
                    return
            
            # 如果没有特定参数，生成今天的报告
            response = await self.generate_daily_report(group_id, query_date)
            await bot.send_text_message(wxid=group_id, content=response, at=user_id)
            
            # 获取发送请求用户的昵称用于日志
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 查询 {query_date} 的66时长记录")
        except Exception as e:
            logger.error(f"查询66时长记录失败: {str(e)}", exc_info=True)
    
    async def generate_daily_report(self, group_id: str, date: datetime.date) -> str:
        """生成每日报告"""
        try:
            response = f"📊 {date.strftime('%Y-%m-%d')} 66时长记录统计:\n\n"
            
            # 获取已完成的记录
            records = self.db.get_records_by_date(group_id, date)
            
            # 获取当前活跃的记录(如果是今天)
            active_records = []
            if date == datetime.date.today():
                active_records = self.db.get_today_active_recordings(group_id)
            
            if not records and not active_records:
                return f"没有找到 {date.strftime('%Y-%m-%d')} 的66时长记录。"
            
            # 合并统计数据
            user_stats = {}
            
            # 处理已完成的记录
            for record in records:
                user_id = record["user_id"]
                user_name = record["user_nickname"]
                duration = record["duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += duration
                user_stats[user_id]["count"] += 1
            
            # 处理活跃的记录
            for active in active_records:
                user_id = active["user_id"]
                user_name = active["user_nickname"]
                current_duration = active["current_duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += current_duration
                user_stats[user_id]["count"] += 1
                user_stats[user_id]["active"] = True
            
            # 排序并生成报告
            sorted_users = sorted(user_stats.items(), key=lambda x: x[1]["total_duration"], reverse=True)
            
            for i, (uid, stats) in enumerate(sorted_users, 1):
                formatted_duration = self.format_duration(stats["total_duration"])
                active_mark = "⏱️ " if stats.get("active", False) else ""
                response += f"{i}. {active_mark}{stats['name']}: {formatted_duration} ({stats['count']}次)\n"
            
            # 添加活跃用户说明
            if any(stats.get("active", False) for _, stats in sorted_users):
                response += "\n⏱️ 表示该用户当前正在记录中"
                
            return response
        except Exception as e:
            logger.error(f"生成日报告失败: {str(e)}", exc_info=True)
            return "生成报告时出错，请联系管理员。"
    
    async def generate_weekly_report(self, group_id: str, start_date: datetime.date) -> str:
        """生成周报告"""
        try:
            response = f"📊 本周66时长记录统计:\n\n"
            
            # 计算一周的日期范围
            end_date = start_date + datetime.timedelta(days=6)
            
            # 获取日期范围内的记录
            records = self.db.get_records_by_date_range(group_id, start_date, end_date)
            
            # 获取当前活跃的记录
            active_records = self.db.get_today_active_recordings(group_id)
            
            if not records and not active_records:
                return "本周没有找到任何66时长记录。"
            
            # 合并统计数据
            user_stats = {}
            
            # 处理已完成的记录
            for record in records:
                user_id = record["user_id"]
                user_name = record["user_nickname"]
                duration = record["duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += duration
                user_stats[user_id]["count"] += 1
            
            # 处理活跃的记录
            for active in active_records:
                user_id = active["user_id"]
                user_name = active["user_nickname"]
                current_duration = active["current_duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += current_duration
                user_stats[user_id]["count"] += 1
                user_stats[user_id]["active"] = True
            
            # 排序并生成报告
            sorted_users = sorted(user_stats.items(), key=lambda x: x[1]["total_duration"], reverse=True)
            
            for i, (uid, stats) in enumerate(sorted_users, 1):
                formatted_duration = self.format_duration(stats["total_duration"])
                active_mark = "⏱️ " if stats.get("active", False) else ""
                response += f"{i}. {active_mark}{stats['name']}: {formatted_duration} ({stats['count']}次)\n"
            
            # 添加活跃用户说明
            if any(stats.get("active", False) for _, stats in sorted_users):
                response += "\n⏱️ 表示该用户当前正在记录中"
                
            # 添加日期范围说明
            response += f"\n\n统计期间: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"
            
            return response
        except Exception as e:
            logger.error(f"生成周报告失败: {str(e)}", exc_info=True)
            return "生成报告时出错，请联系管理员。"
    
    async def generate_monthly_report(self, group_id: str, start_date: datetime.date) -> str:
        """生成月报告"""
        try:
            response = f"📊 本月66时长记录统计:\n\n"
            
            # 计算当月的最后一天
            if start_date.month == 12:
                next_month_year = start_date.year + 1
                next_month = 1
            else:
                next_month_year = start_date.year
                next_month = start_date.month + 1
                
            end_date = datetime.date(next_month_year, next_month, 1) - datetime.timedelta(days=1)
            
            # 获取日期范围内的记录
            records = self.db.get_records_by_date_range(group_id, start_date, end_date)
            
            # 获取当前活跃的记录
            active_records = self.db.get_today_active_recordings(group_id)
            
            if not records and not active_records:
                return "本月没有找到任何66时长记录。"
            
            # 合并统计数据
            user_stats = {}
            
            # 处理已完成的记录
            for record in records:
                user_id = record["user_id"]
                user_name = record["user_nickname"]
                duration = record["duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += duration
                user_stats[user_id]["count"] += 1
            
            # 处理活跃的记录
            for active in active_records:
                user_id = active["user_id"]
                user_name = active["user_nickname"]
                current_duration = active["current_duration"]
                
                if user_id not in user_stats:
                    user_stats[user_id] = {"name": user_name, "total_duration": 0, "count": 0}
                
                user_stats[user_id]["total_duration"] += current_duration
                user_stats[user_id]["count"] += 1
                user_stats[user_id]["active"] = True
            
            # 排序并生成报告
            sorted_users = sorted(user_stats.items(), key=lambda x: x[1]["total_duration"], reverse=True)
            
            for i, (uid, stats) in enumerate(sorted_users, 1):
                formatted_duration = self.format_duration(stats["total_duration"])
                active_mark = "⏱️ " if stats.get("active", False) else ""
                response += f"{i}. {active_mark}{stats['name']}: {formatted_duration} ({stats['count']}次)\n"
            
            # 添加活跃用户说明
            if any(stats.get("active", False) for _, stats in sorted_users):
                response += "\n⏱️ 表示该用户当前正在记录中"
                
            # 添加日期范围说明
            response += f"\n\n统计期间: {start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}"
            
            return response
        except Exception as e:
            logger.error(f"生成月报告失败: {str(e)}", exc_info=True)
            return "生成报告时出错，请联系管理员。"
    
    @require_admin
    async def enable_recording(self, bot, group_id: str, user_id: str) -> None:
        """启用66时长记录"""
        try:
            # 获取用户群昵称
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            
            # 权限检查由装饰器处理，这里不需要再次检查
            if self.db.set_group_enabled(group_id, True):
                await bot.send_text_message(
                    wxid=group_id,
                    content="✅ 66时长记录功能已启用，现在可以使用以下命令：\n"
                            "「66报备」- 开始记录时长\n"
                            "「66结束」- 结束记录时长\n"
                            "「查66记录」- 查询今天的时长记录\n"
                            "「查66记录 昨天/本周/本月」- 查询指定时间段的记录"
                )
                logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 启用了66时长记录功能")
            else:
                await bot.send_text_message(
                    wxid=group_id,
                    content="启用66时长记录功能失败，请稍后重试。",
                    at=user_id
                )
        except Exception as e:
            logger.error(f"启用66时长记录失败: {str(e)}", exc_info=True)
    
    @require_admin
    async def disable_recording(self, bot, group_id: str, user_id: str) -> None:
        """禁用66时长记录"""
        try:
            # 获取用户群昵称
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            
            # 权限检查由装饰器处理，这里不需要再次检查
            if self.db.set_group_enabled(group_id, False):
                await bot.send_text_message(
                    wxid=group_id,
                    content="❌ 66时长记录功能已禁用"
                )
                logger.info(f"用户 {nickname}({user_id}) 在群 {group_id} 禁用了66时长记录功能")
            else:
                await bot.send_text_message(
                    wxid=group_id,
                    content="禁用66时长记录功能失败，请稍后重试。",
                    at=user_id
                )
        except Exception as e:
            logger.error(f"禁用66时长记录失败: {str(e)}", exc_info=True)
    
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
            from utils.admin_manager import admin_manager
            
            if await admin_manager.is_admin(group_id, user_id):
                return True
                
            # 获取群信息，检查用户是否为群主
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
    
    async def show_help(self, bot, group_id: str, user_id: str) -> None:
        """显示66时长记录插件的帮助信息"""
        try:
            # 获取用户群昵称
            nickname = await self.get_user_nickname(bot, group_id, user_id)
            
            # 构建帮助信息
            help_text = "📋 66时长记录插件使用说明\n\n"
            help_text += "🔸 用户命令:\n"
            
            # 添加非管理员命令
            for command in self.commands:
                if not command.get("hidden", False) and not command.get("admin_only", False):
                    cmd_name = command.get("name", "")
                    cmd_desc = command.get("description", "")
                    cmd_usage = command.get("usage", "")
                    help_text += f"  • {cmd_usage}: {cmd_desc}\n"
            
            # 检查用户是否为管理员，如果是则显示管理员命令
            is_admin = await self.is_admin(bot, group_id, user_id)
            if is_admin:
                help_text += "\n🔸 管理员命令:\n"
                for command in self.commands:
                    if not command.get("hidden", False) and command.get("admin_only", True):
                        cmd_name = command.get("name", "")
                        cmd_desc = command.get("description", "")
                        cmd_usage = command.get("usage", "")
                        help_text += f"  • {cmd_usage}: {cmd_desc}\n"
            
            # 发送帮助信息
            await bot.send_text_message(
                wxid=group_id,
                content=help_text,
                at=user_id
            )
            logger.info(f"向用户 {nickname}({user_id}) 发送66时长记录插件帮助信息")
        except Exception as e:
            logger.error(f"显示帮助信息失败: {str(e)}", exc_info=True)
    
    @on_text_message
    async def on_text_message(self, bot, message: dict) -> None:
        """处理文本消息"""
        try:
            # 获取消息内容
            content = message.get("Content", "").strip()
            
            # 忽略非群聊消息
            if not message.get("IsGroup", False):
                return True
            
            group_id = message.get("FromWxid", "")
            user_id = message.get("SenderWxid", "")
            
            # 记录消息处理日志
            logger.debug(f"66时长记录插件收到消息: {content} 从群 {group_id} 用户 {user_id}")
            
            # 使用命令映射处理命令
            for command in self.commands:
                cmd_name = command.get("name", "")
                
                # 检查是否匹配命令
                if content.startswith(cmd_name):
                    logger.info(f"66时长记录插件匹配到命令: {cmd_name}")
                    
                    # 提取命令参数
                    params = content[len(cmd_name):].strip()
                    
                    # 检查命令权限 - 某些命令需要管理员权限
                    admin_only = command.get("admin_only", False)
                    
                    # 根据命令名称分发处理
                    if cmd_name in ["66报备", "66开始"]:
                        await self.start_recording(bot, group_id, user_id)
                        return False
                    elif cmd_name == "66结束":
                        await self.end_recording(bot, group_id, user_id)
                        return False
                    elif cmd_name == "查66记录":
                        await self.query_records(bot, group_id, user_id, content)
                        return False
                    elif cmd_name == "启用66记录":
                        # 管理员命令已通过装饰器处理权限
                        await self.enable_recording(bot, group_id, user_id)
                        return False
                    elif cmd_name == "禁用66记录":
                        # 管理员命令已通过装饰器处理权限
                        await self.disable_recording(bot, group_id, user_id)
                        return False
                    elif cmd_name == "66帮助":
                        await self.show_help(bot, group_id, user_id)
                        return False
            
            # 如果不是插件处理的命令，返回True继续后续插件处理
            return True
        except Exception as e:
            logger.error(f"处理66时长记录消息失败: {str(e)}", exc_info=True)
            return True  # 发生错误时继续让其他插件处理
