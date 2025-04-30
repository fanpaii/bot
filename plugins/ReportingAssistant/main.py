import asyncio
import datetime
import json
import os
import re
import tomllib
import traceback
from typing import Dict, List, Set, Optional, Tuple, Union

from loguru import logger
from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase
from database.keyvalDB import KeyvalDB
from utils.admin_manager import admin_manager  # 导入项目级管理员模块


class ReportingAssistant(PluginBase):
    description = "微信群报备助手"
    author = "AI助手"
    version = "2.2.0"

    # 日志辅助函数
    def log_info(self, message):
        """输出信息级别日志，添加[报备助手]前缀"""
        logger.info(f"[报备助手] {message}")
    
    def log_debug(self, message):
        """输出调试级别日志，添加[报备助手]前缀"""
        logger.debug(f"[报备助手] {message}")
    
    def log_warning(self, message):
        """输出警告级别日志，添加[报备助手]前缀"""
        logger.warning(f"[报备助手] {message}")
    
    def log_error(self, message, with_traceback=False):
        """输出错误级别日志，添加[报备助手]前缀，可选输出堆栈跟踪"""
        logger.error(f"[报备助手] {message}")
        if with_traceback:
            logger.error(f"[报备助手] 堆栈跟踪:\n{traceback.format_exc()}")
    
    # 同步初始化
    def __init__(self):
        super().__init__()
        
        # 获取配置文件路径
        self.config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        self.command_map_path = os.path.join(os.path.dirname(__file__), "command_map.toml")
        
        # 默认启用
        self.enable = True
        
        # 命令映射列表
        self.commands = []
        
        # 报备相关命令类型分组（将从命令映射自动分类）
        self.report_commands = []
        self.back_commands = []
        self.admin_commands = []
        
        # 初始化报备提醒任务字典 {group_wxid:user_wxid: job_id}
        self.reminder_tasks = {}
        
        try:
            # 加载基本配置
            self._load_config()
            # 加载命令映射
            self._load_command_map()
            
            # 读取默认时限和提醒时间设置
            self.default_time_limit = 120  # 默认报备时限（分钟）
            self.default_reminder_minutes = 10  # 默认提醒时间（分钟）
            
            logger.info(f"报备助手插件初始化成功")
            logger.debug(f"报备命令: {self.report_commands}")
            logger.debug(f"回来命令: {self.back_commands}")
            logger.debug(f"管理命令: {self.admin_commands}")
            
        except Exception as e:
            logger.error(f"加载报备助手插件配置文件失败: {str(e)}")
            self.enable = False
        
        # 初始化数据库和bot
        self.db = KeyvalDB()
        self.bot = None  # 将在handle_text中设置
    
    def _load_config(self):
        """加载插件配置"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "rb") as f:
                    config = tomllib.load(f)
                    
                # 读取基本配置
                report_config = config.get("ReportingAssistant", {})
                self.enable = report_config.get("enable", True)
                
                # 读取默认时限和提醒时间设置
                self.default_time_limit = report_config.get("default_time_limit", 120)
                self.default_reminder_minutes = report_config.get("default_reminder_minutes", 10)
            else:
                self.log_warning(f"配置文件 {self.config_path} 不存在，使用默认配置")
        except Exception as e:
            self.log_error(f"加载配置文件失败: {str(e)}")
    
    def _load_command_map(self):
        """加载命令映射"""
        try:
            if os.path.exists(self.command_map_path):
                with open(self.command_map_path, "rb") as f:
                    config = tomllib.load(f)
                    self.commands = config.get("commands", [])
                
                # 根据命令类型自动分类
                self._categorize_commands()
                
                self.log_info(f"已加载 {len(self.commands)} 条命令映射")
            else:
                self.log_warning(f"命令映射文件 {self.command_map_path} 不存在")
                # 这里不会创建默认映射文件，因为我们已经手动创建了
        except Exception as e:
            self.log_error(f"加载命令映射失败: {str(e)}")
            # 使用空列表
            self.commands = []
    
    def _categorize_commands(self):
        """根据命令功能分类"""
        self.report_commands.clear()
        self.back_commands.clear()
        self.admin_commands.clear()
        
        # 报备相关命令
        report_cmd_names = ["报备", "bb", "我报备", "报备一下", "开始报备"]
        back_cmd_names = ["回", "h"]
        
        for cmd in self.commands:
            cmd_name = cmd.get("name", "")
            
            # 根据命令名称分类
            if cmd_name in report_cmd_names:
                self.report_commands.append(cmd_name)
            elif cmd_name in back_cmd_names:
                self.back_commands.append(cmd_name)
            elif cmd.get("admin_only", False):
                self.admin_commands.append(cmd_name)
        
        # 确保bb命令在报备命令列表中
        if "bb" not in self.report_commands:
            self.report_commands.append("bb")
        
        # 确保h命令在回来命令列表中
        if "h" not in self.back_commands:
            self.back_commands.append("h")
    
    def _get_command_config(self, command_name: str) -> dict:
        """获取命令配置
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令配置字典，如果命令不存在则返回空字典
        """
        for command in self.commands:
            if command.get("name") == command_name:
                return command
        return {}
    
    def _is_command_admin_only(self, command_name: str) -> bool:
        """检查命令是否仅限管理员使用
        
        Args:
            command_name: 命令名称
            
        Returns:
            是否仅限管理员使用
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("admin_only", False)
    
    def _get_command_usage(self, command_name: str) -> str:
        """获取命令使用说明
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令使用说明
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("usage", command_name)
    
    def _get_command_description(self, command_name: str) -> str:
        """获取命令描述
        
        Args:
            command_name: 命令名称
            
        Returns:
            命令描述
        """
        command_config = self._get_command_config(command_name)
        return command_config.get("description", "")
    
    # 异步初始化
    async def async_init(self, bot=None):
        try:
            # 异步初始化数据库连接 
            await self.db.initialize()
            # 初始化管理员管理器
            await admin_manager.initialize()
            
            # 初始化数据库和管理员
            await self.init_global_configs()
            
            # 从数据库加载配置参数
            await self.load_configs_from_db()
            
            # 数据迁移：将旧键名的数据复制到新键名下
            await self.migrate_old_data()
            
            self.log_info("报备助手数据库和管理员管理器初始化成功")
        except Exception as e:
            self.log_error(f"报备助手初始化失败: {str(e)}")
    
    # 数据迁移方法，处理键名不一致的问题
    async def migrate_old_data(self):
        """将旧格式键名的数据迁移到新格式键名下"""
        try:
            # 获取所有可能包含群组ID的键
            all_keys = await self.db.keys("report:*")
            migrated_count = 0
            
            # 旧键名格式与新键名格式的映射
            key_formats = {
                "report:enabled:": "report:{}:enabled",
                "report:time_limit:": "report:{}:time_limit",
                "report:time_limit_enabled:": "report:{}:time_limit_enabled",
                "report:reminder_enabled:": "report:{}:reminder_enabled",
                "report:reminder_minutes:": "report:{}:reminder_minutes"
            }
            
            for key in all_keys:
                for old_prefix, new_format in key_formats.items():
                    if key.startswith(old_prefix):
                        # 提取群组ID
                        group_id = key[len(old_prefix):]
                        new_key = new_format.format(group_id)
                        
                        # 检查新键是否已存在
                        if not await self.db.exists(new_key):
                            # 复制数据
                            value = await self.db.get(key)
                            if value:
                                await self.db.set(new_key, value)
                                migrated_count += 1
                                self.log_debug(f"数据迁移: {key} -> {new_key}, 值: {value}")
            
            if migrated_count > 0:
                self.log_info(f"成功迁移 {migrated_count} 条配置数据")
        except Exception as e:
            self.log_error(f"数据迁移失败: {str(e)}", with_traceback=True)
    
    # 添加插件启用时的处理
    async def on_enable(self, bot=None):
        """插件启用时调用，恢复未完成的提醒任务"""
        # 调用父类方法处理定时任务
        await super().on_enable(bot)
        
        # 恢复未完成的提醒任务
        if bot:
            await self.restore_reminder_tasks(bot)
            
    # 添加插件禁用时的处理
    async def on_disable(self):
        """插件禁用时调用，取消所有提醒任务"""
        # 取消所有提醒任务
        try:
            # 取消所有提醒任务
            for task_key, job_id in list(self.reminder_tasks.items()):
                try:
                    from utils.decorators import remove_job_safe, scheduler
                    remove_job_safe(scheduler, job_id)
                except Exception as e:
                    self.log_error(f"取消提醒任务失败: {str(e)}")
            
            # 清空任务字典
            self.reminder_tasks.clear()
            
            self.log_info("已取消所有报备提醒任务")
        except Exception as e:
            self.log_error(f"取消所有提醒任务失败: {str(e)}")
            
        # 调用父类方法处理定时任务
        await super().on_disable()

    # 添加全局配置初始化方法
    async def init_global_configs(self):
        """将插件配置参数存储到数据库中，仅在第一次运行或配置更新时执行"""
        try:
            # 检查数据库中是否已有配置版本记录
            db_version = await self.db.get("report:config:version")
            current_version = self.version  # 使用插件版本作为配置版本
            
            # 如果数据库中没有配置版本或版本不匹配，则更新配置
            if not db_version or db_version != current_version:
                self.log_info(f"正在更新报备助手配置参数 (版本: {current_version})")
                
                # 确保"bb"命令在报备命令列表中
                if "bb" not in self.report_commands:
                    self.report_commands.append("bb")
                    self.log_info(f"已添加'bb'到报备命令列表: {self.report_commands}")
                
                # 不再存储命令列表到数据库
                # 只存储默认时限和提醒时间参数
                await self.db.set("report:config:default_time_limit", str(self.default_time_limit))
                await self.db.set("report:config:default_reminder_minutes", str(self.default_reminder_minutes))
                
                # 更新配置版本
                await self.db.set("report:config:version", current_version)
                self.log_info("报备助手配置参数已更新到数据库")
            else:
                # 如果版本匹配，则只加载时限和提醒时间参数
                await self.load_configs_from_db()
                self.log_info("从数据库加载报备助手配置参数成功")
        except Exception as e:
            self.log_error(f"初始化全局配置参数失败: {str(e)}", with_traceback=True)

    # 添加从数据库加载配置的方法
    async def load_configs_from_db(self):
        """从数据库加载插件配置参数"""
        try:
            # 确保"bb"命令在报备命令列表中
            if "bb" not in self.report_commands:
                self.report_commands.append("bb")
                logger.info(f"加载配置时添加'bb'到报备命令列表: {self.report_commands}")
            
            # 不再从数据库加载命令列表
            # 只加载默认时限和提醒时间参数
            default_time_limit = await self.db.get("report:config:default_time_limit")
            if default_time_limit:
                self.default_time_limit = int(default_time_limit)
            
            default_reminder_minutes = await self.db.get("report:config:default_reminder_minutes")
            if default_reminder_minutes:
                self.default_reminder_minutes = int(default_reminder_minutes)
            
            logger.debug(f"从数据库加载的配置: 默认时限={self.default_time_limit}, 默认提醒时间={self.default_reminder_minutes}")
        except Exception as e:
            logger.error(f"从数据库加载配置参数失败: {str(e)}")

    # 添加检查群聊是否启用报备功能的方法
    async def is_group_enabled(self, group_wxid: str) -> bool:
        """检查群聊是否启用了报备功能
        
        Args:
            group_wxid: 群聊的wxid
            
        Returns:
            bool: 如果启用了报备功能返回True，否则返回False
        """
        try:
            # 修改：使用一致的键名格式
            report_enabled = await self.db.get(f"report:{group_wxid}:enabled")
            return report_enabled == "true" or report_enabled == "1"  # 同时兼容 "true" 和 "1" 两种值
        except Exception as e:
            logger.error(f"检查群聊报备功能状态失败: {str(e)}")
        
        # 默认启用
        return True

    # 添加获取群聊时限设置的方法
    async def get_group_time_limit(self, group_wxid: str) -> Tuple[int, bool]:
        """获取群聊的报备时限设置
        
        Args:
            group_wxid: 群聊的wxid
            
        Returns:
            Tuple[int, bool]: 时限(分钟)和是否启用时限
        """
        time_limit = self.default_time_limit
        time_limit_enabled = False
        
        try:
            # 读取群聊时限设置
            time_limit_str = await self.db.get(f"report:{group_wxid}:time_limit")
            if time_limit_str:
                time_limit = int(time_limit_str)
            
            # 读取时限是否启用
            time_limit_enabled_str = await self.db.get(f"report:{group_wxid}:time_limit_enabled")
            time_limit_enabled = time_limit_enabled_str == "true" or time_limit_enabled_str == "1"  # 同时兼容 "true" 和 "1" 两种值
        except Exception as e:
            logger.error(f"获取群聊时限设置失败: {str(e)}")
        
        return time_limit, time_limit_enabled

    # 添加是否启用提醒功能的方法
    async def is_reminder_enabled(self, group_wxid: str) -> bool:
        """检查群聊是否启用了报备提醒功能
        
        Args:
            group_wxid: 群聊的wxid
            
        Returns:
            bool: 如果启用了提醒功能返回True，否则返回False
        """
        try:
            reminder_enabled = await self.db.get(f"report:{group_wxid}:reminder_enabled")
            return reminder_enabled == "true" or reminder_enabled == "1"  # 同时兼容 "true" 和 "1" 两种值
        except Exception as e:
            logger.error(f"检查群聊报备提醒功能状态失败: {str(e)}")
            return False  # 出错时默认为禁用状态

    # 添加获取提醒时间的方法
    async def get_reminder_minutes(self, group_wxid: str) -> int:
        """获取群聊的报备提醒时间设置
        
        Args:
            group_wxid: 群聊的wxid
            
        Returns:
            int: 提醒时间(分钟)
        """
        reminder_minutes = self.default_reminder_minutes
        
        try:
            reminder_minutes_str = await self.db.get(f"report:{group_wxid}:reminder_minutes")
            if reminder_minutes_str:
                reminder_minutes = int(reminder_minutes_str)
        except Exception as e:
            logger.error(f"获取群聊提醒时间设置失败: {str(e)}")
        
        return reminder_minutes

    # 添加获取用户每日报备统计的方法
    async def get_user_daily_report_stats(self, group_wxid: str, user_wxid: str) -> tuple:
        """
        获取用户今日报备统计信息
        
        返回:
            tuple: (报备次数, 总报备时长(分钟))
        """
        today = datetime.date.today().isoformat()
        # 使用统一方法获取键名
        record_key = self.get_records_key(group_wxid, user_wxid, today)
        
        # 记录当前检索的键名，便于调试
        self.log_debug(f"检索报备记录键: {record_key}")
        
        # 获取今日记录
        records_json = await self.db.get(record_key)
        if not records_json:
            self.log_debug(f"未找到报备记录: {record_key}")
            return 0, 0
            
        try:
            records = json.loads(records_json)
            if not records:
                self.log_debug(f"报备记录为空: {record_key}")
                return 0, 0
                
            # 计算报备次数和总时长
            report_count = len(records)
            
            # 确保每个记录的duration都是整数
            total_duration = 0
            has_string_duration = False
            
            # 详细记录每条记录
            for i, record in enumerate(records):
                # 获取时长，确保是整数
                duration = record.get("duration", 0)
                if isinstance(duration, str):
                    has_string_duration = True
                    try:
                        duration = int(duration)
                        # 更新记录中的duration为int类型，确保类型一致
                        records[i]["duration"] = duration
                    except ValueError:
                        self.log_warning(f"无效的报备时长: {duration}，已设为0")
                        duration = 0
                        records[i]["duration"] = 0
                
                self.log_debug(f"记录{i+1}: 开始={record.get('start_time', '未知')}, 结束={record.get('end_time', '未知')}, 时长={duration}分钟")
                total_duration += duration
            
            # 如果发现记录中的duration类型被修正，重新保存记录
            if has_string_duration:
                self.log_debug(f"修正记录中的时长类型并重新保存")
                await self.db.set(record_key, json.dumps(records))
            
            self.log_debug(f"用户 {user_wxid} 在群 {group_wxid} 的报备统计: {report_count}次, 总时长{total_duration}分钟")
            return report_count, total_duration
        except Exception as e:
            self.log_error(f"计算用户报备统计失败: {e}", with_traceback=True)
            return 0, 0

    # 添加设置提醒任务的方法
    async def set_reminder_task(self, bot: WechatAPIClient, group_wxid: str, user_wxid: str, 
                               username: str, remaining_minutes: int, reminder_minutes: int):
        """设置报备提醒任务
        
        Args:
            bot: 微信API客户端
            group_wxid: 群聊的wxid
            user_wxid: 用户的wxid
            username: 用户昵称
            remaining_minutes: 剩余时间(分钟)
            reminder_minutes: 提醒时间(分钟)
        """
        try:
            # 确保用户昵称不为空
            if not username or username == "未知用户":
                try:
                    # 首先尝试获取群昵称
                    nickname = await bot.get_chatroom_nickname(group_wxid, user_wxid)
                    if nickname and nickname != "用户" and nickname != user_wxid:
                        username = nickname
                        self.log_info(f"从API获取到用户群昵称: {username}")
                    else:
                        # 如果无法获取群昵称，尝试从群成员列表获取
                        try:
                            member_list = await bot.get_chatroom_member_list(group_wxid)
                            for member in member_list:
                                member_wxid = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
                                if member_wxid == user_wxid:
                                    nickname = member.get("DisplayName") or member.get("NickName") or member.get("nickname") or ""
                                    if nickname:
                                        username = nickname
                                        self.log_info(f"从群成员列表获取到用户昵称: {username}")
                                    break
                        except Exception as e:
                            self.log_warning(f"从群成员列表获取群成员昵称失败: {e}")
                except Exception as e:
                    self.log_warning(f"获取群成员昵称失败: {e}")
            
            # 取消已有的提醒任务
            await self.cancel_reminder_task(group_wxid, user_wxid)
            
            # 计算延迟时间（避免超时的情况下设置提醒）
            delay_seconds = remaining_minutes * 60
            if delay_seconds <= 0:
                self.log_info(f"不设置提醒任务: 用户 {username}({user_wxid}) 剩余时间({remaining_minutes}分钟)不足")
                return
            
            # 保存提醒信息到数据库，以便调度器使用
            trigger_time = datetime.datetime.now() + datetime.timedelta(seconds=delay_seconds)
            reminder_info = {
                "group_wxid": group_wxid,
                "user_wxid": user_wxid,
                "username": username,
                "reminder_minutes": reminder_minutes,
                "scheduled_time": trigger_time.isoformat(),
                "bot_id": bot.wxid  # 添加机器人ID，以便任务恢复时能找到正确的机器人
            }
            
            reminder_key = f"report:reminder:{group_wxid}:{user_wxid}"
            await self.db.set(reminder_key, json.dumps(reminder_info))
            
            # 使用schedule的date触发器安排一次性任务
            from apscheduler.triggers.date import DateTrigger
            job_id = f"report_reminder:{group_wxid}:{user_wxid}"
            
            # 使用apscheduler的add_job方法添加任务，而不是使用asyncio.create_task
            from utils.decorators import add_job_safe, scheduler
            
            # 添加任务到调度器
            add_job_safe(
                scheduler=scheduler,
                job_id=job_id,
                func=self.send_reminder,
                bot=bot,
                trigger=DateTrigger(run_date=trigger_time),
                group_wxid=group_wxid, 
                user_wxid=user_wxid, 
                username=username, 
                reminder_minutes=reminder_minutes, 
                bot_wxid=bot.wxid
            )
            
            # 添加job_id到任务映射中
            task_key = f"{group_wxid}:{user_wxid}"
            self.reminder_tasks[task_key] = job_id
            
            self.log_debug(f"已为用户 {username} 设置报备提醒任务, {reminder_minutes}分钟前提醒，将在{remaining_minutes}分钟后触发 (job_id: {job_id})")
        except Exception as e:
            self.log_error(f"设置提醒任务失败: {str(e)}", with_traceback=True)

    # 新的提醒发送方法，替代_reminder_task
    async def send_reminder(self, bot: WechatAPIClient, group_wxid: str, user_wxid: str, 
                           username: str, reminder_minutes: int, bot_wxid: str = None):
        """发送报备提醒
        
        Args:
            bot: 微信API客户端
            group_wxid: 群聊的wxid
            user_wxid: 用户的wxid
            username: 用户昵称
            reminder_minutes: 提醒时间(分钟)
            bot_wxid: 机器人wxid，用于校验bot对象
        """
        try:
            # 任务可能在不同的bot实例中恢复，需要确认bot是正确的
            if bot_wxid and bot.wxid != bot_wxid:
                self.log_warning(f"机器人ID不匹配，期望: {bot_wxid}, 实际: {bot.wxid}，可能是任务在重启后恢复")
                # 这里可以考虑尝试获取正确的bot实例，但目前简化处理
            
            # 获取时限设置
            time_limit, time_limit_enabled = await self.get_group_time_limit(group_wxid)
            
            # 检查用户是否仍在报备状态
            report_key = self.get_status_key(group_wxid, user_wxid)
            is_reporting = await self.db.exists(report_key)
            
            if is_reporting:
                # 获取报备信息
                report_info_json = await self.db.get(report_key)
                start_time_str = ""
                current_duration = 0
                
                try:
                    report_info = json.loads(report_info_json)
                    start_time = report_info.get("start_time", "")
                    matter = report_info.get("matter", "未说明事务")
                    
                    if start_time:
                        # 计算当前已报备时长，支持多种时间格式
                        try:
                            # 先尝试ISO格式
                            try:
                                start_time_obj = datetime.datetime.fromisoformat(start_time)
                            except ValueError:
                                # 尝试其他常见格式
                                start_time_obj = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
                            
                            current_time = datetime.datetime.now()
                            duration_seconds = (current_time - start_time_obj).total_seconds()
                            current_duration = int(duration_seconds / 60)
                            start_time_str = start_time_obj.strftime("%H:%M:%S")
                        except Exception as e:
                            self.log_error(f"计算报备时长失败: {e}")
                            current_duration = 0
                except Exception as e:
                    self.log_error(f"解析报备信息失败: {str(e)}")
                
                # 构建提醒消息
                reminder_msg = f"\n⏰ 报备提醒 ⏰\n"
                reminder_msg += f"您已报备 {current_duration} 分钟"
                
                if start_time_str:
                    reminder_msg += f"（开始于 {start_time_str}）"
                
                reminder_msg += f"\n距离报备时限（{time_limit}分钟）结束还剩 {reminder_minutes} 分钟"
                reminder_msg += f"\n\n完成后请发送「回」或「h」结束报备"
                
                # 发送提醒消息
                try:
                    await bot.send_at_message(group_wxid, reminder_msg, [user_wxid])
                    self.log_debug(f"已向用户 {username}({user_wxid}) 发送报备提醒")
                except Exception as e:
                    self.log_error(f"发送报备提醒失败: {str(e)}")
            else:
                self.log_debug(f"用户 {username}({user_wxid}) 已不在报备状态，不发送提醒")
        except Exception as e:
            self.log_error(f"执行提醒任务失败: {str(e)}")
        finally:
            # 清理任务引用
            task_key = f"{group_wxid}:{user_wxid}"
            if task_key in self.reminder_tasks:
                del self.reminder_tasks[task_key]
                
            # 清理数据库中的提醒信息
            reminder_key = f"report:reminder:{group_wxid}:{user_wxid}"
            await self.db.delete(reminder_key)

    # 添加取消提醒任务的方法
    async def cancel_reminder_task(self, group_wxid: str, user_wxid: str):
        """取消用户的报备提醒任务
        
        Args:
            group_wxid: 群聊的wxid
            user_wxid: 用户的wxid
        """
        try:
            task_key = f"{group_wxid}:{user_wxid}"
            if task_key in self.reminder_tasks:
                # 获取任务ID
                job_id = self.reminder_tasks[task_key]
                
                # 取消定时任务
                from utils.decorators import remove_job_safe, scheduler
                remove_job_safe(scheduler, job_id)
                
                # 移除任务引用
                del self.reminder_tasks[task_key]
                
                # 删除提醒信息
                reminder_key = f"report:reminder:{group_wxid}:{user_wxid}"
                await self.db.delete(reminder_key)
                
                self.log_debug(f"已取消用户 {user_wxid} 的报备提醒任务 (job_id: {job_id})")
        except Exception as e:
            self.log_error(f"取消提醒任务失败: {str(e)}")

    # 添加取消所有提醒任务的方法
    async def cancel_all_reminder_tasks(self, group_wxid: str):
        """取消群聊的所有报备提醒任务
        
        Args:
            group_wxid: 群聊的wxid
        """
        try:
            # 创建要删除的键列表
            keys_to_delete = []
            
            # 查找该群的所有任务
            for task_key, job_id in self.reminder_tasks.items():
                if task_key.startswith(f"{group_wxid}:"):
                    # 取消调度器中的任务
                    from utils.decorators import remove_job_safe, scheduler
                    remove_job_safe(scheduler, job_id)
                    
                    # 标记为删除
                    keys_to_delete.append(task_key)
                    
                    # 删除数据库中的提醒信息
                    parts = task_key.split(":")
                    if len(parts) > 1:
                        user_wxid = parts[1]
                        reminder_key = f"report:reminder:{group_wxid}:{user_wxid}"
                        await self.db.delete(reminder_key)
            
            # 删除标记的任务
            for key in keys_to_delete:
                del self.reminder_tasks[key]
                
            if keys_to_delete:
                self.log_debug(f"已取消群 {group_wxid} 的 {len(keys_to_delete)} 个报备提醒任务")
        except Exception as e:
            self.log_error(f"取消所有提醒任务失败: {str(e)}")
            
    # 添加恢复所有未完成提醒任务的方法（在启用插件时调用）
    async def restore_reminder_tasks(self, bot: WechatAPIClient):
        """恢复所有未完成的提醒任务
        
        在插件启用或机器人重启后调用，从数据库中恢复所有未完成的提醒任务
        """
        try:
            # 获取所有提醒任务信息
            reminder_keys = await self.db.keys("report:reminder:*")
            restored_count = 0
            
            for key in reminder_keys:
                try:
                    reminder_info_json = await self.db.get(key)
                    if not reminder_info_json:
                        continue
                        
                    reminder_info = json.loads(reminder_info_json)
                    group_wxid = reminder_info.get("group_wxid")
                    user_wxid = reminder_info.get("user_wxid")
                    username = reminder_info.get("username", "未知用户")
                    reminder_minutes = reminder_info.get("reminder_minutes", 10)
                    scheduled_time_str = reminder_info.get("scheduled_time")
                    
                    if not all([group_wxid, user_wxid, scheduled_time_str]):
                        self.log_warning(f"提醒任务信息不完整: {reminder_info}")
                        await self.db.delete(key)
                        continue
                    
                    # 计算任务应该在未来多长时间触发
                    try:
                        scheduled_time = datetime.datetime.fromisoformat(scheduled_time_str)
                        now = datetime.datetime.now()
                        
                        # 如果任务已经过期，直接发送提醒
                        if scheduled_time <= now:
                            self.log_debug(f"提醒任务已过期，立即发送提醒: {reminder_info}")
                            asyncio.create_task(
                                self.send_reminder(bot, group_wxid, user_wxid, username, reminder_minutes)
                            )
                            continue
                        
                        # 计算延迟时间
                        delay_seconds = (scheduled_time - now).total_seconds()
                        if delay_seconds <= 0:
                            self.log_debug(f"提醒任务延迟时间为负值，跳过恢复: {reminder_info}")
                            await self.db.delete(key)
                            continue
                        
                        # 使用调度器安排任务
                        from apscheduler.triggers.date import DateTrigger
                        job_id = f"report_reminder:{group_wxid}:{user_wxid}"
                        
                        from utils.decorators import add_job_safe, scheduler
                        add_job_safe(
                            scheduler=scheduler,
                            job_id=job_id,
                            func=self.send_reminder,
                            bot=bot,
                            trigger=DateTrigger(run_date=scheduled_time),
                            args=[group_wxid, user_wxid, username, reminder_minutes, bot.wxid]
                        )
                        
                        # 记录任务引用
                        task_key = f"{group_wxid}:{user_wxid}"
                        self.reminder_tasks[task_key] = job_id
                        
                        restored_count += 1
                        self.log_debug(f"已恢复提醒任务: {task_key}, 将在 {int(delay_seconds/60)} 分钟后触发")
                    except Exception as e:
                        self.log_error(f"恢复提醒任务失败: {str(e)}")
                        await self.db.delete(key)
                except Exception as e:
                    self.log_error(f"处理提醒任务信息失败: {str(e)}")
            
            if restored_count > 0:
                self.log_info(f"已恢复 {restored_count} 个报备提醒任务")
        except Exception as e:
            self.log_error(f"恢复提醒任务失败: {str(e)}")
            
    # 添加超级管理员检查方法
    async def is_super_admin(self, user_wxid: str) -> bool:
        """检查用户是否为超级管理员
        
        Args:
            user_wxid: 用户的wxid
            
        Returns:
            bool: 如果用户是超级管理员则返回True，否则返回False
        """
        try:
            # 从配置文件直接读取超级管理员列表
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            
            # 优先检查超级管理员
            super_admins = main_config.get("XYBot", {}).get("admins", [])
            
            is_super_admin = user_wxid in super_admins
            self.log_debug(f"检查用户 {user_wxid} 是否为超级管理员: {is_super_admin}")
            return is_super_admin
        except Exception as e:
            self.log_error(f"检查超级管理员失败: {str(e)}")
            self.log_error(f"超级管理员检查错误详情: {traceback.format_exc()}")
            return False
    
    # 处理文本消息
    @on_text_message
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True
        
        # 保存bot实例以供其他方法使用
        self.bot = bot
        
        try:
            # 提取消息内容和命令
            content = message.get("Content", "").strip()
            from_wxid = message.get("FromWxid", "")  # 群聊ID或私聊用户ID
            sender_wxid = message.get("SenderWxid", "")  # 发送消息的用户ID
            is_group = message.get("IsGroup", False)  # 是否群聊
            
            # 只处理群聊消息
            if not is_group:
                return True
            
            # 消息内容转小写，用于不区分大小写匹配
            content_lower = content.lower()
            
            # 检查是否为报备指令(简化的快速检查)
            is_report_cmd = False
            report_cmd_used = ""
            
            # 检查是否以报备命令开头(包括大小写变体)
            for cmd in self.report_commands:
                cmd_lower = cmd.lower()
                if content_lower.startswith(cmd_lower):
                    is_report_cmd = True
                    report_cmd_used = cmd
                    self.log_debug(f"匹配到报备命令: {cmd}, 原始消息: {content}")
                    break
            
            # 特殊处理bb和其变体(BB, Bb, bB)
            if not is_report_cmd and content_lower.startswith("bb"):
                is_report_cmd = True
                report_cmd_used = "bb"
                self.log_debug(f"匹配到bb命令变体, 原始消息: {content}")
            
            # 处理报备命令
            if is_report_cmd:
                await self.handle_report(bot, message, sender_wxid, from_wxid)
                return False
            
            # 检查是否为回来指令
            is_back_cmd = False
            back_cmd_used = ""
            
            # 精确匹配回来命令，仅接受完整匹配"回"、"h"或"H"
            if content == "回" or content == "h" or content == "H":
                is_back_cmd = True
                back_cmd_used = content
                self.log_debug(f"精确匹配到回来命令: {content}")
            
            # 处理回来命令
            if is_back_cmd:
                await self.handle_back(bot, message, sender_wxid, from_wxid)
                return False
            
            # 检查是否为管理命令或特殊命令
            first_word = content.split(" ", 1)[0].strip()
            command_config = self._get_command_config(first_word)
            
            # 处理特殊情况：提醒分钟
            if content.startswith("提醒 分钟"):
                command_name = "提醒分钟"
                command_config = self._get_command_config(command_name)
                self.log_debug(f"检测到带空格的命令格式: '{content}' => '{command_name}'")
            
            # 检查是否为管理员命令，且用户是否有权限
            if command_config:
                command_name = command_config.get("name", "")
                
                if command_config.get("admin_only", False):
                    # 检查管理员权限
                    is_admin = await admin_manager.is_admin(from_wxid, sender_wxid)
                    is_super_admin = await self.is_super_admin(sender_wxid)
                    
                    if not (is_admin or is_super_admin):
                        await bot.send_text_message(from_wxid, "⚠️ 权限不足，只有管理员可以执行此操作")
                        return False
                
                # 特殊命令处理 - 移到这里，确保优先于普通管理命令处理
                if command_name == "报备设置":
                    await self.handle_report_settings(bot, message)
                    return False
                elif command_name == "查报备记录":
                    await self.handle_report_query(bot, message)
                    return False
                
                # 处理管理命令
                if command_name in self.admin_commands:
                    self.log_debug(f"匹配到管理命令: {command_name}")
                    # 增加额外的日志记录
                    self.log_info(f"接收到管理命令: '{command_name}', 内容: '{content}', 来自: {sender_wxid}, 群: {from_wxid}")
                    await self.handle_admin_command(bot, message, command_name, content)
                    return False
            
            # 如果命令不匹配，则继续传递给其他插件处理
            return True
                
        except Exception as e:
            self.log_error(f"处理命令时发生错误: {str(e)}", with_traceback=True)
            return True
    
    # 处理报备命令
    async def handle_report(self, bot: WechatAPIClient, message: dict, user_wxid: str, group_wxid: str):
        """
        处理报备命令
        """
        # 获取内容和用户昵称
        content = message.get('Content', '')
        user_nickname = message.get('ActualNickName', '')
        
        # 如果用户昵称为空，尝试从不同字段获取
        if not user_nickname or user_nickname == "未知用户":
            if 'PushContent' in message and '：' in message['PushContent']:
                user_nickname = message['PushContent'].split('：')[0]
            elif 'NickName' in message:
                user_nickname = message['NickName']
            
            # 如果从消息字段获取失败，尝试从微信API获取
            if not user_nickname or user_nickname == "未知用户":
                try:
                    # 首先尝试获取群昵称
                    nickname = await bot.get_chatroom_nickname(group_wxid, user_wxid)
                    if nickname and nickname != "用户" and nickname != user_wxid:
                        user_nickname = nickname
                        self.log_info(f"从API获取到用户群昵称: {user_nickname}")
                    else:
                        # 如果无法获取群昵称，则尝试获取普通昵称
                        try:
                            user_profile = await bot.get_profile(user_wxid)
                            if user_profile and "nickname" in user_profile:
                                user_nickname = user_profile.get("nickname", "")
                                self.log_info(f"从API获取到用户普通昵称: {user_nickname}")
                        except Exception as e:
                            self.log_warning(f"从API获取用户普通昵称失败: {e}")
                            
                        # 如果仍然失败，尝试从群成员列表中查找
                        if not user_nickname or user_nickname == "未知用户":
                            try:
                                member_list = await bot.get_chatroom_member_list(group_wxid)
                                if member_list and isinstance(member_list, list):
                                    for member in member_list:
                                        member_wxid = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
                                        if member_wxid == user_wxid:
                                            nickname = member.get("DisplayName") or member.get("NickName") or member.get("nickname") or ""
                                            if nickname:
                                                user_nickname = nickname
                                                self.log_info(f"从群成员列表获取到用户昵称: {user_nickname}")
                                            break
                            except Exception as e:
                                self.log_warning(f"从群成员列表获取用户昵称失败: {e}")
                except Exception as e:
                    self.log_warning(f"从API获取用户昵称失败: {e}")
        
        # 如果仍然无法获取昵称，使用默认值
        if not user_nickname:
            user_nickname = "未知用户"
            
        self.log_info(f"获取到用户昵称: {user_nickname}  (wxid: {user_wxid})  ")
        
        # 检查用户是否已经在报备状态
        user_status_key = self.get_status_key(group_wxid, user_wxid)
        user_status = await self.db.get(user_status_key)
        
        if user_status:
            user_status = json.loads(user_status)
            start_time = user_status.get("start_time", "")
            matter = user_status.get("matter", "未说明事务")
            start_time_str = datetime.datetime.fromisoformat(start_time).strftime("%H:%M:%S")
            
            reply = f"⚠️ 您已经在报备中，无需重复报备。\n⏰ 开始时间: {start_time_str}\n"
            if matter != "未说明事务":
                reply += f"📝 事务: {matter}\n"
            reply += "\n如需结束报备，请发送「回」或「h」。"
            
            await bot.send_at_message(group_wxid, reply, [user_wxid])
            return
        
        # 解析报备内容
        matter = "未说明事务"
        content = content.strip()
        # 检查是否包含报备内容
        for cmd in self.report_commands:
            if content.startswith(cmd):
                # 提取报备内容
                remaining_content = content[len(cmd):].strip()
                if remaining_content:
                    matter = remaining_content
                break
        
        # 记录报备开始状态
        now = datetime.datetime.now()
        start_time = now.isoformat()  # 使用ISO格式
        start_time_str = now.strftime("%H:%M:%S")  # 用于显示
        
        # 保存用户状态
        user_status = {
            "start_time": start_time,
            "matter": matter,
            "user_nickname": user_nickname
        }
        
        # 记录报备信息到数据库
        self.log_debug(f"存储报备状态 键: {user_status_key}, 值: {json.dumps(user_status)}")
        await self.db.set(user_status_key, json.dumps(user_status))
        
        # 获取当日累计报备情况
        today_report_count, today_total_duration = await self.get_user_daily_report_stats(group_wxid, user_wxid)
        
        # 获取时限设置
        time_limit, time_limit_enabled = await self.get_group_time_limit(group_wxid)
        
        # 构建回复消息
        reply = f"✅ 报备成功！\n⏰ 开始时间: {start_time_str}\n"
        
        # 添加报备事务信息（无论是否为默认值）
        reply += f"📝 事务: {matter}\n"
        
        # 添加今日报备统计信息
        if today_report_count > 0:
            hours = today_total_duration // 60
            mins = today_total_duration % 60
            if hours > 0:
                duration_str = f"{hours}小时{mins}分钟"
            else:
                duration_str = f"{today_total_duration}分钟"
            reply += f"📊 今日已报备: {today_report_count}次 (共{duration_str})\n"
        
        # 获取提醒时间
        is_reminder_enabled = await self.is_reminder_enabled(group_wxid)
        reminder_minutes = await self.get_reminder_minutes(group_wxid)
        
        # 检查是否已经超过时限
        remaining_minutes = time_limit
        is_overtime = False
        
        if time_limit_enabled:
            # 计算今日累计报备时长是否已超过时限
            if today_total_duration >= time_limit:
                is_overtime = True
                overtime_minutes = today_total_duration - time_limit
                reply += f"\n⏱️ 报备信息:\n⏳ 报备时限: {time_limit} 分钟\n⚠️ 您已超时 {overtime_minutes} 分钟"
            else:
                # 计算剩余时间
                remaining_minutes = time_limit - today_total_duration
                
                # 计算应该结束的时间
                end_time = now + datetime.timedelta(minutes=remaining_minutes)
                end_time_str = end_time.strftime("%H:%M:%S")
                
                # 计算提醒时间
                reminder_time = end_time - datetime.timedelta(minutes=reminder_minutes)
                reminder_time_str = reminder_time.strftime("%H:%M:%S")
                
                reply += f"\n⏱️ 报备信息:\n⏳ 报备时限: {time_limit} 分钟\n⌛ 距离时限还剩: {remaining_minutes} 分钟\n"
                reply += f"🕙 最晚应在 {end_time_str} 前结束报备\n"
                
                # 只有未超时且启用了提醒功能才添加提醒信息
                if is_reminder_enabled and not is_overtime:
                    reply += f"🔔 将在 {reminder_time_str} 提醒您 (结束前{reminder_minutes}分钟)\n"
        
        reply += "\n✨ 完成后请发送「回」或「h」结束报备"
        
        await bot.send_at_message(group_wxid, reply, [user_wxid])
        
        # 设置提醒任务，只对未超时的用户设置提醒
        if is_reminder_enabled and time_limit_enabled and not is_overtime:
            self.log_debug(f"群{group_wxid}的提醒状态: enabled={is_reminder_enabled}, time_limit_enabled={time_limit_enabled}")
            self.log_debug(f"群{group_wxid}的提醒分钟数: {reminder_minutes}")
            
            # 如果剩余时间大于提醒时间，设置提醒任务
            actual_remaining_minutes = remaining_minutes - reminder_minutes
            
            if actual_remaining_minutes > 0:
                try:
                    # 尝试获取更可靠的用户昵称
                    if user_nickname == "未知用户":
                        try:
                            nickname = await bot.get_chatroom_nickname(group_wxid, user_wxid)
                            if nickname and nickname != user_wxid:
                                user_nickname = nickname
                            else:
                                user_nickname = "未知用户"
                        except Exception as e:
                            self.log_warning(f"获取群成员昵称失败: {e}")
                    
                    # 设置提醒任务
                    await self.set_reminder_task(bot, group_wxid, user_wxid, user_nickname, actual_remaining_minutes, reminder_minutes)
                    self.log_debug(f"已为用户 {user_nickname} 设置报备提醒任务, {reminder_minutes}分钟前提醒")
                except Exception as e:
                    self.log_error(f"设置提醒任务失败: {e}", with_traceback=True)
            else:
                self.log_debug(f"用户 {user_nickname}(wxid_{user_wxid}) 剩余时间不足，不设置提醒任务")
    
        # 在方法末尾添加日志信息
        self.log_info(f"用户 {user_nickname}({user_wxid}) 在群 {group_wxid} 开始报备， 事务: {matter}")
    
    # 处理回来命令
    async def handle_back(self, bot: WechatAPIClient, message: dict, user_wxid: str, group_wxid: str):
        """
        处理回来命令
        """
        user_nickname = message.get("ActualNickName", "")
        
        # 如果用户昵称为空，尝试从不同字段获取
        if not user_nickname or user_nickname == "未知用户":
            if 'PushContent' in message and '：' in message['PushContent']:
                user_nickname = message['PushContent'].split('：')[0]
            elif 'NickName' in message:
                user_nickname = message['NickName']
            
            # 如果从消息字段获取失败，尝试从微信API获取
            if not user_nickname or user_nickname == "未知用户":
                try:
                    # 首先尝试获取群昵称
                    nickname = await bot.get_chatroom_nickname(group_wxid, user_wxid)
                    if nickname and nickname != "用户" and nickname != user_wxid:
                        user_nickname = nickname
                        self.log_info(f"从API获取到用户群昵称: {user_nickname}")
                    else:
                        # 如果无法获取群昵称，则尝试获取普通昵称
                        try:
                            user_profile = await bot.get_profile(user_wxid)
                            if user_profile and "nickname" in user_profile:
                                user_nickname = user_profile.get("nickname", "")
                                self.log_info(f"从API获取到用户普通昵称: {user_nickname}")
                        except Exception as e:
                            self.log_warning(f"从API获取用户普通昵称失败: {e}")
                            
                        # 如果仍然失败，尝试从群成员列表中查找
                        if not user_nickname or user_nickname == "未知用户":
                            try:
                                member_list = await bot.get_chatroom_member_list(group_wxid)
                                if member_list and isinstance(member_list, list):
                                    for member in member_list:
                                        member_wxid = member.get("wxid") or member.get("Wxid") or member.get("UserName") or ""
                                        if member_wxid == user_wxid:
                                            nickname = member.get("DisplayName") or member.get("NickName") or member.get("nickname") or ""
                                            if nickname:
                                                user_nickname = nickname
                                                self.log_info(f"从群成员列表获取到用户昵称: {user_nickname}")
                                            break
                            except Exception as e:
                                self.log_warning(f"从群成员列表获取用户昵称失败: {e}")
                except Exception as e:
                    self.log_warning(f"从API获取用户昵称失败: {e}")
                
        # 如果仍然无法获取昵称，使用默认值
        if not user_nickname:
            user_nickname = "未知用户"
            
        self.log_info(f"回来命令 - 获取到用户昵称: {user_nickname}  (wxid: {user_wxid})  ")
        
        # 检查用户是否在报备状态
        user_status_key = self.get_status_key(group_wxid, user_wxid)
        self.log_debug(f"检查用户报备状态，键: {user_status_key}")
        
        user_status = await self.db.get(user_status_key)
        
        if not user_status:
            self.log_debug(f"未找到用户报备状态: {user_status_key}")
            # 用户没有在报备中
            await bot.send_at_message(
                group_wxid,
                f"⚠️ 您当前没有正在进行的报备。\n如要开始报备，请发送「报备」或「bb」。",
                [user_wxid]
            )
            return
        
        try:
            # 解析报备状态
            report_info = json.loads(user_status)
            start_time_str = report_info.get("start_time", "")
            matter = report_info.get("matter", "未说明事务")
            
            self.log_debug(f"获取到报备状态: 开始时间={start_time_str}, 事务={matter}")
            
            # 计算报备时长
            try:
                # 先尝试直接解析ISO格式
                try:
                    start_time = datetime.datetime.fromisoformat(start_time_str)
                except ValueError:
                    # 如果失败，尝试解析其他格式
                    self.log_warning(f"解析ISO格式时间失败，尝试其他格式: {start_time_str}")
                    try:
                        # 尝试 %Y-%m-%d %H:%M:%S 格式
                        start_time = datetime.datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        # 如果还是失败，记录当前时间减10分钟作为开始时间（避免出错）
                        self.log_error(f"解析时间失败: {start_time_str}，使用默认时间")
                        start_time = datetime.datetime.now() - datetime.timedelta(minutes=10)
                
                end_time = datetime.datetime.now()
                
                # 详细记录时间差计算过程，便于调试
                time_diff_seconds = (end_time - start_time).total_seconds()
                
                # 不再将时间取整为分钟，而是保留秒级精度
                duration_minutes = time_diff_seconds / 60  # 不使用整除，保留小数
                duration_seconds = int(time_diff_seconds)  # 总秒数，用于显示
                
                self.log_debug(f"时长计算: 开始时间={start_time}, 结束时间={end_time}, 差值={time_diff_seconds}秒, 持续时间={duration_minutes}分钟")
                
                # 不再强制设置最小为1分钟
                if duration_minutes < 0:
                    self.log_warning(f"计算得到负时长: {duration_minutes}分钟，将重置为0")
                    duration_minutes = 0
                    duration_seconds = 0
            except Exception as e:
                self.log_error(f"计算时长时出错: {e}", with_traceback=True)
                duration_minutes = 0
                duration_seconds = 0
            
            # 添加报备记录
            today = datetime.date.today().isoformat()
            record_key = self.get_records_key(group_wxid, user_wxid, today)
            
            # 记录键名便于调试
            self.log_debug(f"报备结束，将添加记录到键: {record_key}")
            
            # 获取今日已有记录
            existing_records = await self.db.get(record_key)
            records = []
            
            if existing_records:
                try:
                    records = json.loads(existing_records)
                    self.log_debug(f"获取到现有记录: {len(records)}条")
                except:
                    self.log_error(f"解析现有报备记录失败: {existing_records}")
            
            # 添加新记录，确保所有时间字段使用ISO格式
            # 为了保持与数据库统一，duration仍使用整数分钟，但添加seconds字段存储精确秒数
            new_record = {
                "start_time": start_time.isoformat(),  # 确保使用ISO格式
                "end_time": end_time.isoformat(),
                "duration": int(duration_minutes),  # 向下取整为分钟
                "seconds": duration_seconds,  # 存储精确的秒数
                "matter": matter,
                "user_nickname": user_nickname
            }
            
            self.log_debug(f"新增记录: {json.dumps(new_record)}")
            records.append(new_record)
            
            # 保存记录前先尝试手动计算总时长，确保数据一致性
            total_duration_from_records = 0
            total_seconds_from_records = 0
            for record in records:
                rec_duration = record.get("duration", 0)
                if isinstance(rec_duration, str):
                    try:
                        rec_duration = int(rec_duration)
                    except ValueError:
                        rec_duration = 0
                total_duration_from_records += rec_duration
                
                # 累加精确的秒数
                rec_seconds = record.get("seconds", rec_duration * 60)  # 兼容旧记录
                if isinstance(rec_seconds, str):
                    try:
                        rec_seconds = int(rec_seconds)
                    except ValueError:
                        rec_seconds = rec_duration * 60
                total_seconds_from_records += rec_seconds
            
            self.log_debug(f"手动计算所有记录总时长: {total_duration_from_records}分钟 ({total_seconds_from_records}秒)，记录数: {len(records)}")
            
            # 保存记录
            self.log_debug(f"保存报备记录: {json.dumps(records)}")
            await self.db.set(record_key, json.dumps(records))
            
            # 删除报备状态
            await self.db.delete(user_status_key)
            
            # 取消提醒任务
            await self.cancel_reminder_task(group_wxid, user_wxid)
            
            # 获取用户今日累计报备时长和次数
            today_report_count, today_total_duration = await self.get_user_daily_report_stats(group_wxid, user_wxid)
            
            # 如果从数据库读取的总时长与手动计算的不一致，使用手动计算的结果
            if today_total_duration != total_duration_from_records:
                self.log_warning(f"数据库读取总时长({today_total_duration})与手动计算总时长({total_duration_from_records})不一致，使用手动计算结果")
                today_total_duration = total_duration_from_records
            
            # 确保今日报备次数正确
            if today_report_count != len(records):
                self.log_debug(f"修正报备次数: 数据库结果={today_report_count}，实际记录数={len(records)}")
                today_report_count = len(records)
            
            # 格式化时间
            start_time_fmt = start_time.strftime("%H:%M:%S")
            end_time_fmt = end_time.strftime("%H:%M:%S")
            
            # 格式化累计时长（小时、分钟和秒）
            hours = today_total_duration // 60
            mins = today_total_duration % 60
            
            # 格式化当前报备时长（包括秒）
            duration_hours = duration_seconds // 3600
            duration_mins = (duration_seconds % 3600) // 60
            duration_secs = duration_seconds % 60
            
            # 构建当前报备时长显示字符串
            if duration_hours > 0:
                duration_str = f"{duration_hours}小时{duration_mins}分{duration_secs}秒"
            elif duration_mins > 0:
                duration_str = f"{duration_mins}分{duration_secs}秒"
            else:
                duration_str = f"{duration_seconds}秒"
            
            # 构建累计时长显示字符串
            if hours > 0:
                total_duration_str = f"{hours}小时{mins}分钟"
            else:
                total_duration_str = f"{today_total_duration}分钟"
            
            # 构建回复
            reply = f"✅ 报备结束！\n⏰ 开始时间: {start_time_fmt}\n⏰ 结束时间: {end_time_fmt}\n⌛ 持续时间: {duration_str}\n"
            
            # 添加事件内容（如果有）
            if matter != "未说明事务":
                reply += f"📝 事务: {matter}\n"
            
            # 检查时限
            time_limit, time_limit_enabled = await self.get_group_time_limit(group_wxid)
            
            # 添加今日统计信息
            if today_report_count > 0:
                reply += f"\n📊 今日已完成报备: {today_report_count}次 (共{total_duration_str})\n"
                
                # 时限信息放在累计时间后面
                if time_limit_enabled:
                    # 检查今日累计报备时长是否达到时限
                    if today_total_duration > time_limit:
                        overtime_minutes = today_total_duration - time_limit
                        # 显示超时信息
                        reply += f"⚠️ 您已超时 {overtime_minutes} 分钟"
                    elif today_total_duration == time_limit:
                        # 刚好达到时限
                        reply += f"✅ 时限已用完"
                    else:
                        # 未达到时限
                        remaining_minutes = time_limit - today_total_duration
                        reply += f"⏳ 距离报备时限({time_limit}分钟)还剩 {remaining_minutes} 分钟"
            
            # 发送回复
            await bot.send_at_message(group_wxid, reply, [user_wxid])
            
            self.log_info(f"用户 {user_nickname} (wxid_{user_wxid}) 在群 {group_wxid} 结束报备，持续时间: {duration_str}，今日累计: {total_duration_str}")
            
        except Exception as e:
            self.log_error(f"处理回来命令失败: {e}", with_traceback=True)
            await bot.send_at_message(
                group_wxid,
                f"❌ 处理回来命令失败: {str(e)}",
                [user_wxid]
            )

    # 处理管理命令
    async def handle_admin_command(self, bot: WechatAPIClient, message: dict, command_name: str, content: str):
        """
        处理管理命令
        
        Args:
            bot: 微信API客户端
            message: 消息字典
            command_name: 命令名称
            content: 消息内容
        """
        from_wxid = message.get("FromWxid", "")  # 群聊ID
        sender_wxid = message.get("SenderWxid", "")  # 发送消息的用户ID
        
        try:
            # 检查是否为超级管理员或普通管理员
            is_super_admin = await self.is_super_admin(sender_wxid)
            is_group_admin = await admin_manager.is_admin(from_wxid, sender_wxid)
            
            # 添加调试日志
            self.log_debug(f"管理命令权限检查 - 用户: {sender_wxid}, 超级管理员: {is_super_admin}, 群管理员: {is_group_admin}")
            
            # 如果既不是超级管理员也不是群管理员，则拒绝操作
            if not is_super_admin and not is_group_admin:
                self.log_warning(f"用户 {sender_wxid} 尝试执行管理命令 {command_name} 但权限不足")
                await bot.send_text_message(from_wxid, "⚠️ 您不是管理员，无法执行此命令")
                return
                
            # 记录权限通过信息
            self.log_debug(f"用户 {sender_wxid} 权限检查通过，执行管理命令 {command_name}")
            
            # 根据命令进行处理
            if command_name == "启用报备":
                await self.db.set(f"report:{from_wxid}:enabled", "true")
                await bot.send_text_message(from_wxid, "✅ 已启用本群报备功能")
                self.log_info(f"管理员 {sender_wxid} 已启用群 {from_wxid} 的报备功能")
                
            elif command_name == "禁用报备":
                await self.db.set(f"report:{from_wxid}:enabled", "false")
                await bot.send_text_message(from_wxid, "🚫 已禁用本群报备功能")
                self.log_info(f"管理员 {sender_wxid} 已禁用群 {from_wxid} 的报备功能")
                
            elif command_name == "设置时限":
                # 解析时限参数
                params = content.split(" ", 1)
                if len(params) > 1:
                    try:
                        time_limit = int(params[1].strip())
                        if time_limit <= 0:
                            await bot.send_text_message(from_wxid, "⚠️ 时限必须大于0分钟")
                            return
                        
                        # 修改：仅使用一个一致的键名
                        await self.db.set(f"report:{from_wxid}:time_limit", str(time_limit))
                        await bot.send_text_message(from_wxid, f"⏱️ 已设置报备时限为 {time_limit} 分钟")
                        self.log_info(f"管理员 {sender_wxid} 已设置群 {from_wxid} 的报备时限为 {time_limit} 分钟")
                    except ValueError:
                        await bot.send_text_message(from_wxid, "⚠️ 时限格式不正确，请输入数字")
                else:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「设置时限 分钟数」")
                
            elif command_name == "启用报备时限":
                # 修改：使用一致的键名
                await self.db.set(f"report:{from_wxid}:time_limit_enabled", "1")
                await bot.send_text_message(from_wxid, "✅ 已启用报备时限功能")
                self.log_info(f"管理员 {sender_wxid} 已启用群 {from_wxid} 的报备时限功能")
                
            elif command_name == "禁用报备时限":
                # 修改：使用一致的键名
                await self.db.set(f"report:{from_wxid}:time_limit_enabled", "0")
                await bot.send_text_message(from_wxid, "🚫 已禁用报备时限功能")
                self.log_info(f"管理员 {sender_wxid} 已禁用群 {from_wxid} 的报备时限功能")
                
            elif command_name == "启用报备提醒":
                # 修改：使用一致的键名
                await self.db.set(f"report:{from_wxid}:reminder_enabled", "1")
                await bot.send_text_message(from_wxid, "✅ 已启用报备提醒功能")
                self.log_info(f"管理员 {sender_wxid} 已启用群 {from_wxid} 的报备提醒功能")
                
            elif command_name == "禁用报备提醒":
                # 修改：使用一致的键名
                await self.db.set(f"report:{from_wxid}:reminder_enabled", "0")
                # 取消所有提醒任务
                await self.cancel_all_reminder_tasks(from_wxid)
                await bot.send_text_message(from_wxid, "🚫 已禁用报备提醒功能")
                self.log_info(f"管理员 {sender_wxid} 已禁用群 {from_wxid} 的报备提醒功能")
                
            elif command_name == "提醒分钟" or command_name.startswith("提醒 分钟"):
                # 解析提醒时间参数
                try:
                    if command_name == "提醒分钟":
                        params = content.split(" ", 1)
                        if len(params) <= 1:
                            await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「提醒分钟 分钟数」")
                            return
                        reminder_minutes = int(params[1].strip())
                    else:
                        parts = content.split(" ")
                        if len(parts) < 3:
                            await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「提醒 分钟 分钟数」")
                            return
                        reminder_minutes = int(parts[2].strip())
                    
                    if reminder_minutes <= 0:
                        await bot.send_text_message(from_wxid, "⚠️ 提醒时间必须大于0分钟")
                        return
                    
                    # 修改：使用一致的键名    
                    await self.db.set(f"report:{from_wxid}:reminder_minutes", str(reminder_minutes))
                    await bot.send_text_message(from_wxid, f"⏰ 已设置提前 {reminder_minutes} 分钟发送报备提醒")
                    self.log_info(f"管理员 {sender_wxid} 已设置群 {from_wxid} 的报备提醒时间为 {reminder_minutes} 分钟")
                except ValueError:
                    await bot.send_text_message(from_wxid, "⚠️ 时间格式不正确，请输入数字")
                
            elif command_name == "设置默认时限":
                # 解析时限参数
                params = content.split(" ", 1)
                if len(params) > 1:
                    try:
                        time_limit = int(params[1].strip())
                        if time_limit <= 0:
                            await bot.send_text_message(from_wxid, "⚠️ 默认时限必须大于0分钟")
                            return
                        
                        await self.db.set("report:default_time_limit", str(time_limit))
                        await bot.send_text_message(from_wxid, f"⏱️ 已设置默认报备时限为 {time_limit} 分钟")
                        self.log_info(f"管理员 {sender_wxid} 已设置全局默认报备时限为 {time_limit} 分钟")
                    except ValueError:
                        await bot.send_text_message(from_wxid, "⚠️ 时限格式不正确，请输入数字")
                else:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「设置默认时限 分钟数」")
                
            elif command_name == "设置默认提醒":
                # 解析提醒时间参数
                params = content.split(" ", 1)
                if len(params) > 1:
                    try:
                        reminder_minutes = int(params[1].strip())
                        if reminder_minutes <= 0:
                            await bot.send_text_message(from_wxid, "⚠️ 默认提醒时间必须大于0分钟")
                            return
                        
                        await self.db.set("report:default_reminder_minutes", str(reminder_minutes))
                        await bot.send_text_message(from_wxid, f"⏰ 已设置默认提前 {reminder_minutes} 分钟发送报备提醒")
                        self.log_info(f"管理员 {sender_wxid} 已设置全局默认报备提醒时间为 {reminder_minutes} 分钟")
                    except ValueError:
                        await bot.send_text_message(from_wxid, "⚠️ 时间格式不正确，请输入数字")
                else:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「设置默认提醒 分钟数」")
                
            elif command_name == "设置报备":
                # 格式检查：设置报备 @成员 [事由] [时长]
                # 解析@的成员
                match = re.search(r'@(.+?)(?:\u2005|\s)', content)
                if not match:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「设置报备 @成员 [事由] [时长(分钟)]」")
                    return
                
                # 获取@的用户名和后面的内容
                at_username = match.group(1).strip()
                content_after_at = content[match.end():].strip()
                
                # 通过名称查找群成员wxid
                try:
                    group_members = await bot.get_group_members(from_wxid)
                    target_wxid = None
                    
                    for member in group_members:
                        nickname = member.get("nickname", "")
                        if nickname == at_username:
                            target_wxid = member.get("wxid", "")
                            break
                    
                    if not target_wxid:
                        await bot.send_text_message(from_wxid, f"⚠️ 未找到名为「{at_username}」的群成员")
                        return
                    
                    # 检查用户是否已经在报备状态
                    user_status_key = self.get_status_key(from_wxid, target_wxid)
                    user_status = await self.db.get(user_status_key)
                    
                    if user_status:
                        user_status = json.loads(user_status)
                        start_time = user_status.get("start_time", "")
                        matter = user_status.get("matter", "未说明事务")
                        start_time_str = datetime.datetime.fromisoformat(start_time).strftime("%H:%M:%S")
                        
                        await bot.send_text_message(from_wxid, f"⚠️ 用户「{at_username}」已经在报备中，无法重复报备。\n⏰ 开始时间: {start_time_str}\n📝 事务: {matter}")
                        return
                    
                    # 解析事由和时长
                    parts = content_after_at.split()
                    matter = "未说明事务"
                    time_limit_minutes = self.default_time_limit  # 默认使用系统设置
                    
                    # 获取群组的报备时间设置
                    group_time_limit, is_time_limit_enabled = await self.get_group_time_limit(from_wxid)
                    if is_time_limit_enabled:
                        time_limit_minutes = group_time_limit
                    
                    if parts:
                        # 检查最后一个参数是否为数字(时长)
                        try:
                            last_part = parts[-1].rstrip('分钟').strip()
                            if last_part.isdigit():
                                time_limit_minutes = int(last_part)
                                # 如果有时长，则事由为除了最后一部分的所有内容
                                if len(parts) > 1:
                                    matter = ' '.join(parts[:-1])
                            else:
                                # 如果最后一部分不是数字，则整个内容都是事由
                                matter = content_after_at
                        except:
                            # 出错时，使用整个内容作为事由
                            matter = content_after_at
                    
                    # 获取当前时间
                    now = datetime.datetime.now()
                    # 计算预计结束时间
                    end_time = now + datetime.timedelta(minutes=time_limit_minutes)
                    
                    # 记录报备信息到数据库
                    report_data = {
                        "user_wxid": target_wxid,
                        "username": at_username,
                        "start_time": now.isoformat(),
                        "end_time": end_time.isoformat(),
                        "matter": matter,
                        "group_wxid": from_wxid,
                        "time_limit": time_limit_minutes,
                        "by_admin": True,
                        "admin_wxid": sender_wxid
                    }
                    
                    # 保存到当前报备状态
                    await self.db.set(user_status_key, json.dumps(report_data))
                    
                    # 记录到当日报备记录
                    today = datetime.date.today().strftime("%Y-%m-%d")
                    records_key = self.get_records_key(from_wxid, target_wxid, today)
                    
                    # 获取现有记录
                    existing_records = await self.db.get(records_key)
                    if existing_records:
                        try:
                            records = json.loads(existing_records)
                            records.append(report_data)
                            await self.db.set(records_key, json.dumps(records))
                        except:
                            # 出错时创建新记录
                            await self.db.set(records_key, json.dumps([report_data]))
                    else:
                        # 没有记录时创建新记录
                        await self.db.set(records_key, json.dumps([report_data]))
                    
                    # 设置提醒
                    if await self.is_reminder_enabled(from_wxid):
                        reminder_minutes = await self.get_reminder_minutes(from_wxid)
                        await self.set_reminder_task(
                            bot, from_wxid, target_wxid, at_username, 
                            time_limit_minutes, reminder_minutes
                        )
                    
                    # 构建回复消息
                    start_time_str = now.strftime("%H:%M:%S")
                    end_time_str = end_time.strftime("%H:%M:%S")
                    
                    reply = f"✅ 已为 {at_username} 设置报备\n"
                    reply += f"⏰ 开始时间: {start_time_str}\n"
                    reply += f"⌛ 预计时长: {time_limit_minutes}分钟\n"
                    
                    # 检查是否启用了时限功能
                    if is_time_limit_enabled:
                        reply += f"🔔 预计结束: {end_time_str}\n"
                    
                    # 添加事务信息
                    reply += f"📝 事务: {matter}"
                    
                    await bot.send_text_message(from_wxid, reply)
                    self.log_info(f"管理员 {sender_wxid} 为用户 {at_username}({target_wxid}) 设置了报备，事由: {matter}, 时长: {time_limit_minutes}分钟")
                    
                except Exception as e:
                    self.log_error(f"设置用户报备时出错: {str(e)}", with_traceback=True)
                    await bot.send_text_message(from_wxid, f"⚠️ 设置报备失败: {str(e)}")
            
            elif command_name == "结束报备":
                # 格式检查：结束报备 @成员 [备注]
                # 解析@的成员
                match = re.search(r'@(.+?)(?:\u2005|\s)', content)
                if not match:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「结束报备 @成员 [备注]」")
                    return
                
                # 获取@的用户名和后面可能的备注
                at_username = match.group(1).strip()
                remark = content[match.end():].strip() or "无备注"
                
                # 通过名称查找群成员wxid
                try:
                    group_members = await bot.get_group_members(from_wxid)
                    target_wxid = None
                    
                    for member in group_members:
                        nickname = member.get("nickname", "")
                        if nickname == at_username:
                            target_wxid = member.get("wxid", "")
                            break
                    
                    if not target_wxid:
                        await bot.send_text_message(from_wxid, f"⚠️ 未找到名为「{at_username}」的群成员")
                        return
                    
                    # 检查用户是否在报备状态
                    user_status_key = self.get_status_key(from_wxid, target_wxid)
                    user_status = await self.db.get(user_status_key)
                    
                    if not user_status:
                        await bot.send_text_message(from_wxid, f"⚠️ 用户「{at_username}」当前没有进行中的报备")
                        return
                    
                    # 获取报备信息
                    user_status = json.loads(user_status)
                    start_time_str = user_status.get("start_time", "")
                    matter = user_status.get("matter", "未说明事务")
                    
                    if not start_time_str:
                        await bot.send_text_message(from_wxid, f"⚠️ 用户「{at_username}」的报备信息不完整")
                        await self.db.delete(user_status_key)
                        return
                    
                    # 解析开始时间
                    start_time = datetime.datetime.fromisoformat(start_time_str)
                    # 获取当前时间作为结束时间
                    end_time = datetime.datetime.now()
                    # 计算报备持续时间(分钟)
                    duration_minutes = round((end_time - start_time).total_seconds() / 60)
                    
                    # 更新报备记录
                    user_status["end_time"] = end_time.isoformat()
                    user_status["duration"] = duration_minutes
                    user_status["remark"] = remark
                    user_status["completed"] = True
                    user_status["completed_by_admin"] = True
                    user_status["admin_wxid"] = sender_wxid
                    
                    # 更新当日报备记录
                    today = datetime.date.today().strftime("%Y-%m-%d")
                    records_key = self.get_records_key(from_wxid, target_wxid, today)
                    
                    # 获取现有记录并更新
                    existing_records = await self.db.get(records_key)
                    if existing_records:
                        try:
                            records = json.loads(existing_records)
                            
                            # 查找并更新当前报备记录
                            for i, record in enumerate(records):
                                if record.get("start_time") == start_time_str:
                                    records[i] = user_status
                                    break
                            else:
                                # 未找到对应开始时间的记录，追加新记录
                                records.append(user_status)
                                
                            await self.db.set(records_key, json.dumps(records))
                        except Exception as e:
                            self.log_error(f"更新报备记录失败: {str(e)}")
                    
                    # 删除当前报备状态
                    await self.db.delete(user_status_key)
                    
                    # 取消提醒任务
                    await self.cancel_reminder_task(from_wxid, target_wxid)
                    
                    # 发送完成报备通知
                    start_time_fmt = start_time.strftime("%H:%M:%S")
                    end_time_fmt = end_time.strftime("%H:%M:%S")
                    
                    reply = f"✅ 管理员已为「{at_username}」结束报备\n"
                    reply += f"⏰ 开始时间: {start_time_fmt}\n"
                    reply += f"🔔 结束时间: {end_time_fmt}\n"
                    reply += f"⌛ 持续时间: {duration_minutes}分钟\n"
                    reply += f"📝 报备事务: {matter}"
                    
                    # 仅当有备注且不是默认值时才添加备注信息
                    if remark and remark != "无备注":
                        reply += f"\n📋 结束备注: {remark}"
                    
                    await bot.send_text_message(from_wxid, reply)
                    self.log_info(f"管理员 {sender_wxid} 为用户 {at_username}({target_wxid}) 结束了报备，持续时间: {duration_minutes}分钟, 备注: {remark}")
                    
                except Exception as e:
                    self.log_error(f"结束用户报备时出错: {str(e)}", with_traceback=True)
                    await bot.send_text_message(from_wxid, f"⚠️ 结束报备失败: {str(e)}")
            
            elif command_name == "查报备":
                # 格式检查：查报备 @成员
                # 解析@的成员
                match = re.search(r'@(.+?)(?:\u2005|\s|$)', content)
                if not match:
                    await bot.send_text_message(from_wxid, "⚠️ 格式不正确，请使用「查报备 @成员」")
                    return
                
                # 获取@的用户名
                at_username = match.group(1).strip()
                
                # 通过名称查找群成员wxid
                try:
                    group_members = await bot.get_group_members(from_wxid)
                    target_wxid = None
                    
                    for member in group_members:
                        nickname = member.get("nickname", "")
                        if nickname == at_username:
                            target_wxid = member.get("wxid", "")
                            break
                    
                    if not target_wxid:
                        await bot.send_text_message(from_wxid, f"⚠️ 未找到名为「{at_username}」的群成员")
                        return
                    
                    # 检查用户当前报备状态
                    user_status_key = self.get_status_key(from_wxid, target_wxid)
                    user_status = await self.db.get(user_status_key)
                    
                    if user_status:
                        # 用户当前正在报备中
                        user_status = json.loads(user_status)
                        start_time_str = user_status.get("start_time", "")
                        matter = user_status.get("matter", "未说明事务")
                        time_limit = user_status.get("time_limit", 0)
                        
                        if start_time_str:
                            start_time = datetime.datetime.fromisoformat(start_time_str)
                            start_time_fmt = start_time.strftime("%H:%M:%S")
                            
                            # 计算已报备时间
                            now = datetime.datetime.now()
                            elapsed_minutes = round((now - start_time).total_seconds() / 60)
                            
                            # 计算剩余时间(如果设置了时限)
                            remaining_minutes = time_limit - elapsed_minutes if time_limit > 0 else 0
                            
                            reply = f"📊 「{at_username}」当前报备状态\n"
                            reply += f"✅ 状态: 报备中\n"
                            reply += f"⏰ 开始时间: {start_time_fmt}\n"
                            reply += f"⌛ 已报备: {elapsed_minutes}分钟\n"
                            
                            if time_limit > 0:
                                reply += f"⏳ 报备时限: {time_limit}分钟\n"
                                reply += f"🔔 剩余时间: {remaining_minutes}分钟\n"
                            
                            reply += f"📝 报备事务: {matter}"
                            
                            await bot.send_text_message(from_wxid, reply)
                        else:
                            await bot.send_text_message(from_wxid, f"⚠️ 用户「{at_username}」的报备信息不完整")
                    else:
                        # 用户当前不在报备中，获取今日报备记录
                        today = datetime.date.today().strftime("%Y-%m-%d")
                        records_key = self.get_records_key(from_wxid, target_wxid, today)
                        
                        existing_records = await self.db.get(records_key)
                        if existing_records:
                            records = json.loads(existing_records)
                            
                            if records:
                                reply = f"📊 「{at_username}」今日报备记录\n"
                                reply += f"✅ 状态: 当前未报备\n"
                                reply += f"📋 今日报备次数: {len(records)}次\n\n"
                                
                                # 只显示最近的3条记录
                                max_records = min(3, len(records))
                                for i, record in enumerate(records[-max_records:]):
                                    start_time = datetime.datetime.fromisoformat(record.get("start_time", ""))
                                    start_time_fmt = start_time.strftime("%H:%M:%S")
                                    
                                    if "end_time" in record and record["end_time"]:
                                        end_time = datetime.datetime.fromisoformat(record["end_time"])
                                        end_time_fmt = end_time.strftime("%H:%M:%S")
                                        duration = record.get("duration", round((end_time - start_time).total_seconds() / 60))
                                        
                                        reply += f"🔍 记录 {i+1}\n"
                                        reply += f"⏰ 开始: {start_time_fmt}\n"
                                        reply += f"🔔 结束: {end_time_fmt}\n"
                                        reply += f"⌛ 时长: {duration}分钟\n"
                                        reply += f"📝 事务: {record.get('matter', '未说明事务')}\n"
                                        
                                        if "remark" in record:
                                            reply += f"📋 备注: {record.get('remark', '无')}\n"
                                    else:
                                        # 未完成的报备(理论上不应该出现，因为当前状态已检查)
                                        reply += f"🔍 记录 {i+1} (未完成)\n"
                                        reply += f"⏰ 开始: {start_time_fmt}\n"
                                        reply += f"📝 事务: {record.get('matter', '未说明事务')}\n"
                                    
                                    reply += "\n"
                                
                                if len(records) > max_records:
                                    reply += f"... 还有 {len(records) - max_records} 条更早的记录"
                            
                                await bot.send_text_message(from_wxid, reply)
                            else:
                                await bot.send_text_message(from_wxid, f"📊 「{at_username}」今日没有报备记录，当前未报备")
                        else:
                            await bot.send_text_message(from_wxid, f"📊 「{at_username}」今日没有报备记录，当前未报备")
                
                except Exception as e:
                    self.log_error(f"查询用户报备状态时出错: {str(e)}", with_traceback=True)
                    await bot.send_text_message(from_wxid, f"⚠️ 查询报备失败: {str(e)}")
                
            else:
                # 未识别的管理命令
                await bot.send_text_message(from_wxid, f"⚠️ 未知管理命令: {command_name}")
        
        except Exception as e:
            self.log_error(f"处理管理命令时发生错误: {e}", with_traceback=True)
            await bot.send_text_message(from_wxid, f"⚠️ 处理命令时出错: {str(e)}")

    # 处理查看报备记录命令  
    async def handle_report_query(self, bot: WechatAPIClient, message: dict):
        """
        处理查看报备记录命令 - 查询群内所有用户的报备记录
        支持指定日期查询，例如：查报备记录 04-14
        
        Args:
            bot: 微信API客户端
            message: 消息字典
        """
        from_wxid = message.get("FromWxid", "")  # 群聊ID
        sender_wxid = message.get("SenderWxid", "")  # 发送消息的用户ID
        content = message.get("Content", "").strip()  # 获取消息内容
        
        try:
            # 默认使用今日日期
            query_date = datetime.date.today()
            
            # 解析命令中是否包含日期参数，例如 "查报备记录 04-14"
            parts = content.split()
            if len(parts) > 1:
                date_str = parts[1].strip()
                try:
                    # 处理多种日期格式
                    if re.match(r'^\d{1,2}-\d{1,2}$', date_str):  # 04-14 格式
                        month, day = map(int, date_str.split('-'))
                        year = datetime.date.today().year
                        query_date = datetime.date(year, month, day)
                    elif re.match(r'^\d{4}-\d{1,2}-\d{1,2}$', date_str):  # 2023-04-14 格式
                        year, month, day = map(int, date_str.split('-'))
                        query_date = datetime.date(year, month, day)
                    elif re.match(r'^\d{8}$', date_str):  # 20230414 格式
                        year = int(date_str[:4])
                        month = int(date_str[4:6])
                        day = int(date_str[6:8])
                        query_date = datetime.date(year, month, day)
                    else:
                        # 无法识别的日期格式，使用当天日期
                        self.log_warning(f"无法识别的日期格式: {date_str}，使用当天日期")
                        await bot.send_text_message(from_wxid, f"⚠️ 日期格式不正确: {date_str}，将显示今日记录")
                except Exception as e:
                    self.log_warning(f"解析日期参数失败: {e}，使用当天日期")
                    await bot.send_text_message(from_wxid, f"⚠️ 日期解析失败: {date_str}，将显示今日记录")
            
            # 将日期转换为ISO格式字符串
            date_iso = query_date.isoformat()
            
            # 使用模式匹配获取指定日期所有用户的报备记录
            prefix = f"report:records:{from_wxid}:"
            pattern = f"{prefix}*:{date_iso}"
            record_keys = await self.db.keys(pattern)
            
            date_display = query_date.strftime("%Y-%m-%d")
            self.log_debug(f"查询报备记录，日期: {date_display}，使用模式: {pattern}，找到 {len(record_keys)} 条记录")
            
            if not record_keys:
                await bot.send_text_message(from_wxid, f"📊 {date_display} 暂无报备记录")
                return
            
            # 构建用户报备记录
            user_records = []
            total_users = 0
            overtime_users = 0
            overtime_users_list = []
            time_limit, time_limit_enabled = await self.get_group_time_limit(from_wxid)
            
            for key in record_keys:
                try:
                    # 从键名中提取用户ID
                    # 格式为 report:records:群ID:用户ID:日期
                    parts = key.split(":")
                    if len(parts) >= 5:
                        user_wxid = parts[3]
                        record_json = await self.db.get(key)
                        
                        if not record_json:
                            continue
                        
                        records = json.loads(record_json)
                        if not records:
                            continue
                        
                        total_users += 1
                        
                        # 获取用户昵称
                        user_nickname = "未知用户"
                        if records and len(records) > 0:
                            user_nickname = records[0].get("user_nickname", "未知用户")
                        
                        # 如果昵称为空或未知，尝试从群成员列表获取
                        if user_nickname == "未知用户":
                            try:
                                member_list = await bot.get_chatroom_member_list(from_wxid)
                                if member_list and isinstance(member_list, list):
                                    for member in member_list:
                                        if member.get("UserName") == user_wxid:
                                            user_nickname = member.get("NickName", user_nickname)
                                            break
                            except Exception as e:
                                self.log_warning(f"获取群成员昵称失败: {e}")
                        
                        # 计算总报备次数和总时长
                        report_count = len(records)
                        total_duration = 0
                        total_seconds = 0
                        
                        for record in records:
                            # 获取记录中的时长（分钟）
                            duration = record.get("duration", 0)
                            if isinstance(duration, str):
                                try:
                                    duration = int(duration)
                                except ValueError:
                                    duration = 0
                            total_duration += duration
                            
                            # 获取记录中的精确秒数
                            seconds = record.get("seconds", duration * 60)  # 兼容没有seconds字段的旧记录
                            if isinstance(seconds, str):
                                try:
                                    seconds = int(seconds)
                                except ValueError:
                                    seconds = duration * 60
                            total_seconds += seconds
                        
                        # 检查是否超时
                        is_overtime = time_limit_enabled and total_duration > time_limit
                        if is_overtime:
                            overtime_users += 1
                            overtime_users_list.append(user_nickname)
                        
                        # 查询历史记录时不检查用户当前是否正在报备中
                        is_reporting = False
                        current_duration = 0
                        current_seconds = 0
                        current_matter = ""
                        
                        # 如果查询的是今天的记录，才检查用户是否正在报备中
                        if date_iso == datetime.date.today().isoformat():
                            status_key = self.get_status_key(from_wxid, user_wxid)
                            is_reporting = await self.db.exists(status_key)
                            
                            # 如果用户正在报备中，获取当前进行时长
                            if is_reporting:
                                status_json = await self.db.get(status_key)
                                if status_json:
                                    try:
                                        status = json.loads(status_json)
                                        start_time_str = status.get("start_time", "")
                                        current_matter = status.get("matter", "未说明事务")
                                        
                                        if start_time_str:
                                            start_time = datetime.datetime.fromisoformat(start_time_str)
                                            current_time = datetime.datetime.now()
                                            current_seconds = int((current_time - start_time).total_seconds())
                                            current_duration = current_seconds // 60  # 转换为分钟
                                    except Exception as e:
                                        self.log_error(f"计算当前报备时长失败: {e}")
                        
                        # 将用户记录添加到列表中
                        user_records.append({
                            "wxid": user_wxid,
                            "nickname": user_nickname,
                            "report_count": report_count,
                            "total_duration": total_duration,
                            "total_seconds": total_seconds,
                            "is_reporting": is_reporting,
                            "current_duration": current_duration,
                            "current_seconds": current_seconds,
                            "current_matter": current_matter,
                            "is_overtime": is_overtime,
                            "detailed_records": records  # 添加详细记录
                        })
                except Exception as e:
                    self.log_error(f"处理用户报备记录时出错: {e}")
            
            if not user_records:
                await bot.send_text_message(from_wxid, f"📊 {date_display} 暂无有效报备记录")
                return
            
            # 按照总报备时长降序排序
            user_records.sort(key=lambda x: x["total_seconds"], reverse=True)
            
            # 构建回复消息
            reply = f"📊 {date_display} 报备记录:\n"
            reply += f"共 {len(user_records)} 位用户有报备记录\n"
            reply += "------------------------\n"
            
            for idx, user in enumerate(user_records, 1):
                # 格式化总时长（使用精确的秒数）
                total_seconds = user["total_seconds"]
                hours = total_seconds // 3600
                mins = (total_seconds % 3600) // 60
                secs = total_seconds % 60
                
                if hours > 0:
                    duration_str = f"{hours}小时{mins}分{secs}秒"
                elif mins > 0:
                    duration_str = f"{mins}分{secs}秒"
                else:
                    duration_str = f"{secs}秒"
                
                # 添加超时标记
                overtime_mark = " ⚠️超时" if user["is_overtime"] else ""
                
                reply += f"{idx}. {user['nickname']}{overtime_mark}\n"
                reply += f"   📊 报备: {user['report_count']}次\n"
                reply += f"   ⌛ 总时长: {duration_str}\n"
                
                # 显示详细报备记录
                reply += f"   📝 详细记录:\n"
                for i, record in enumerate(user["detailed_records"], 1):
                    start_time = datetime.datetime.fromisoformat(record.get("start_time", ""))
                    end_time = datetime.datetime.fromisoformat(record.get("end_time", ""))
                    
                    # 获取精确的秒数（兼容旧记录）
                    record_duration = record.get("duration", 0)
                    record_seconds = record.get("seconds", record_duration * 60)
                    
                    # 格式化精确的时长
                    r_hours = record_seconds // 3600
                    r_mins = (record_seconds % 3600) // 60
                    r_secs = record_seconds % 60
                    
                    if r_hours > 0:
                        record_duration_str = f"{r_hours}小时{r_mins}分{r_secs}秒"
                    elif r_mins > 0:
                        record_duration_str = f"{r_mins}分{r_secs}秒"
                    else:
                        record_duration_str = f"{r_secs}秒"
                    
                    matter = record.get("matter", "未说明事务")
                    
                    start_str = start_time.strftime("%H:%M:%S")
                    end_str = end_time.strftime("%H:%M:%S")
                    
                    reply += f"      {i}) {start_str}-{end_str} ({record_duration_str})"
                    if matter != "未说明事务":
                        reply += f" [{matter}]"
                    reply += "\n"
                
                # 如果用户正在报备中，显示当前报备状态
                if user["is_reporting"]:
                    # 格式化当前报备时长
                    current_seconds = user["current_seconds"]
                    c_hours = current_seconds // 3600
                    c_mins = (current_seconds % 3600) // 60
                    c_secs = current_seconds % 60
                    
                    if c_hours > 0:
                        current_str = f"{c_hours}小时{c_mins}分{c_secs}秒"
                    elif c_mins > 0:
                        current_str = f"{c_mins}分{c_secs}秒"
                    else:
                        current_str = f"{c_secs}秒"
                    
                    reply += f"   🔄 正在报备: {current_str}"
                    if user["current_matter"] != "未说明事务":
                        reply += f" [{user['current_matter']}]"
                    reply += "\n"
                
                reply += "------------------------\n"
            
            # 添加群报备统计信息
            reply += f"\n📈 群报备统计:\n"
            reply += f"👥 {date_display} 报备人数: {total_users}人\n"
            
            if time_limit_enabled:
                reply += f"⏱️ 报备时限: {time_limit}分钟\n"
                reply += f"⚠️ 超时人数: {overtime_users}人\n"
                
                # 显示超时成员名单
                if overtime_users > 0:
                    reply += f"📋 超时成员: {', '.join(overtime_users_list)}\n"
            
            await bot.send_text_message(from_wxid, reply)
        
        except Exception as e:
            self.log_error(f"处理查看报备记录命令时出错: {e}", with_traceback=True)
            await bot.send_text_message(from_wxid, f"⚠️ 查询报备记录时出错: {str(e)}")

    # 获取状态键名
    def get_status_key(self, group_wxid: str, user_wxid: str) -> str:
        """
        获取用户报备状态的键名
        
        Args:
            group_wxid: 群ID
            user_wxid: 用户ID
            
        Returns:
            状态键名
        """
        return f"report:status:{group_wxid}:{user_wxid}"

    # 处理报备设置命令
    async def handle_report_settings(self, bot: WechatAPIClient, message: dict):
        """
        处理报备设置命令
        
        Args:
            bot: 微信API客户端
            message: 消息字典
        """
        from_wxid = message.get("FromWxid", "")  # 群聊ID
        sender_wxid = message.get("SenderWxid", "")  # 发送消息的用户ID
        
        try:
            # 获取当前群报备设置
            is_enabled = await self.is_group_enabled(from_wxid)
            time_limit, time_limit_enabled = await self.get_group_time_limit(from_wxid)
            is_reminder_enabled = await self.is_reminder_enabled(from_wxid)
            reminder_minutes = await self.get_reminder_minutes(from_wxid)
            
            # 构建设置信息
            settings = [
                f"📋 群 {from_wxid} 报备功能设置:",
                f"------------------------",
                f"✅ 报备功能: {'启用' if is_enabled else '禁用'}",
                f"⏱️ 报备时限: {time_limit} 分钟",
                f"⚙️ 时限功能: {'启用' if time_limit_enabled else '禁用'}",
                f"🔔 提醒功能: {'启用' if is_reminder_enabled else '禁用'}",
                f"⏰ 提醒时间: 报备结束前 {reminder_minutes} 分钟",
                f"------------------------",
                f"📝 管理命令:",
                f"启用报备 - 开启群报备功能",
                f"禁用报备 - 关闭群报备功能",
                f"设置时限 120 - 设置报备时限(分钟)",
                f"启用报备时限 - 开启报备时限功能",
                f"禁用报备时限 - 关闭报备时限功能",
                f"启用报备提醒 - 开启报备提醒",
                f"禁用报备提醒 - 关闭报备提醒",
                f"提醒分钟 10 - 设置提醒时间(分钟)",
                f"查报备记录 - 查看当前报备用户",
            ]
            
            await bot.send_text_message(from_wxid, "\n".join(settings))
        
        except Exception as e:
            self.log_error(f"处理报备设置命令时出错: {e}", with_traceback=True)
            await bot.send_text_message(from_wxid, f"⚠️ 获取报备设置时出错: {str(e)}")

    # 获取记录键名
    def get_records_key(self, group_wxid: str, user_wxid: str, date_str: str) -> str:
        """
        获取用户报备记录的键名
        
        Args:
            group_wxid: 群ID
            user_wxid: 用户ID
            date_str: 日期字符串，格式为YYYY-MM-DD
            
        Returns:
            记录键名
        """
        return f"report:records:{group_wxid}:{user_wxid}:{date_str}"