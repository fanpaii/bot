import sqlite3
import xml.etree.ElementTree as ET
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger
from typing import Dict, List, Tuple, Optional, Any, Union
import tomllib
import re

from WechatAPI.Client import WechatAPIClient
from utils.plugin_base import PluginBase
from utils.decorators import on_system_message, on_text_message, schedule


class GroupQuitMonitor(PluginBase):
    description = "群成员退出监控"
    author = "Claude"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        self.enable = True  # 默认启用
        self.notification_groups = {}  # 存储通知配置 {群ID: {是否开启: bool, 通知接收人: list}}
        self.admins = []  # 管理员列表
        
        # 创建数据库和表结构
        self.db_dir = Path("plugins/GroupQuitMonitor/data")
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = str(self.db_dir / "quit_records.db")
        
        # 加载管理员列表
        self._load_admins()
        
        self._init_database()
        self._load_config()
        
    def _init_database(self):
        """初始化数据库表结构"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # 创建退群记录表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_quit_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_wxid TEXT NOT NULL,
                group_name TEXT,
                member_wxid TEXT NOT NULL,
                member_nickname TEXT,
                quit_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                quit_method TEXT DEFAULT '主动退出'
            )
            ''')
            
            # 创建通知配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_settings (
                group_wxid TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 创建通知接收人表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS notification_receivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_wxid TEXT NOT NULL,
                receiver_wxid TEXT NOT NULL,
                UNIQUE(group_wxid, receiver_wxid)
            )
            ''')
            
            conn.commit()
            conn.close()
            logger.success("退群监控数据库初始化完成")
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
    
    def _load_config(self):
        """从数据库加载通知配置"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # 加载通知设置
            cursor.execute("SELECT group_wxid, enabled FROM notification_settings")
            settings = cursor.fetchall()
            
            for group_wxid, enabled in settings:
                self.notification_groups[group_wxid] = {"enabled": bool(enabled), "receivers": []}
            
            # 加载通知接收人
            cursor.execute("SELECT group_wxid, receiver_wxid FROM notification_receivers")
            receivers = cursor.fetchall()
            
            for group_wxid, receiver_wxid in receivers:
                if group_wxid in self.notification_groups:
                    self.notification_groups[group_wxid]["receivers"].append(receiver_wxid)
                else:
                    # 如果设置表中没有该群，则创建默认设置
                    self.notification_groups[group_wxid] = {"enabled": True, "receivers": [receiver_wxid]}
            
            conn.close()
            logger.info(f"已加载 {len(self.notification_groups)} 个群聊的退群通知配置")
        except Exception as e:
            logger.error(f"加载通知配置失败: {e}")
    
    def _save_notification_setting(self, group_wxid: str, enabled: bool):
        """保存群通知设置"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR REPLACE INTO notification_settings 
            (group_wxid, enabled, last_updated) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ''', (group_wxid, int(enabled)))
            
            conn.commit()
            conn.close()
            
            # 更新内存中的配置
            if group_wxid not in self.notification_groups:
                self.notification_groups[group_wxid] = {"enabled": enabled, "receivers": []}
            else:
                self.notification_groups[group_wxid]["enabled"] = enabled
                
            return True
        except Exception as e:
            logger.error(f"保存通知设置失败: {e}")
            return False
    
    def _add_notification_receiver(self, group_wxid: str, receiver_wxid: str) -> Tuple[bool, str]:
        """添加通知接收人"""
        try:
            # 检查群是否已经有通知设置
            if group_wxid not in self.notification_groups:
                self._save_notification_setting(group_wxid, True)
            
            # 检查用户是否已在接收列表中
            if group_wxid in self.notification_groups and receiver_wxid in self.notification_groups[group_wxid]["receivers"]:
                return False, "该用户已在通知接收列表中"
            
            # 添加到数据库
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR IGNORE INTO notification_receivers 
            (group_wxid, receiver_wxid) 
            VALUES (?, ?)
            ''', (group_wxid, receiver_wxid))
            
            conn.commit()
            conn.close()
            
            # 更新内存中的配置
            if group_wxid not in self.notification_groups:
                self.notification_groups[group_wxid] = {"enabled": True, "receivers": [receiver_wxid]}
            else:
                if receiver_wxid not in self.notification_groups[group_wxid]["receivers"]:
                    self.notification_groups[group_wxid]["receivers"].append(receiver_wxid)
            
            return True, "成功添加通知接收人"
        except Exception as e:
            logger.error(f"添加通知接收人失败: {e}")
            return False, f"添加失败: {str(e)}"
    
    def _remove_notification_receiver(self, group_wxid: str, receiver_wxid: str) -> Tuple[bool, str]:
        """移除通知接收人"""
        try:
            # 检查用户是否在接收列表中
            if group_wxid not in self.notification_groups or receiver_wxid not in self.notification_groups[group_wxid]["receivers"]:
                return False, "该用户不在通知接收列表中"
            
            # 从数据库中移除
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            DELETE FROM notification_receivers
            WHERE group_wxid = ? AND receiver_wxid = ?
            ''', (group_wxid, receiver_wxid))
            
            conn.commit()
            conn.close()
            
            # 更新内存中的配置
            self.notification_groups[group_wxid]["receivers"].remove(receiver_wxid)
            
            return True, "成功移除通知接收人"
        except Exception as e:
            logger.error(f"移除通知接收人失败: {e}")
            return False, f"移除失败: {str(e)}"
    
    def _record_quit_event(self, group_wxid: str, group_name: str, member_info: Dict[str, str], quit_method: str = "主动退出"):
        """记录退群事件到数据库"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            member_wxid = member_info.get("wxid", "")
            member_nickname = member_info.get("nickname", "")
            
            cursor.execute('''
            INSERT INTO group_quit_records 
            (group_wxid, group_name, member_wxid, member_nickname, quit_method)
            VALUES (?, ?, ?, ?, ?)
            ''', (group_wxid, group_name, member_wxid, member_nickname, quit_method))
            
            conn.commit()
            conn.close()
            logger.info(f"成功记录退群事件: {member_nickname}({member_wxid}) 退出群 {group_name}({group_wxid})")
            return True
        except Exception as e:
            logger.error(f"记录退群事件失败: {e}")
            return False
    
    async def _get_member_info(self, bot: WechatAPIClient, group_wxid: str, member_wxid: str) -> Optional[Dict]:
        """获取群成员信息"""
        try:
            member_info = await bot.get_some_member_info(group_wxid, member_wxid)
            if member_info:
                return member_info
        except Exception as e:
            logger.error(f"获取群成员信息失败: {e}")
        
        # 如果从群成员接口获取失败，尝试使用通用接口
        try:
            profile = await bot.get_profile(member_wxid)
            if profile:
                return profile
        except Exception as e:
            logger.error(f"获取用户个人信息失败: {e}")
        
        return None
    
    def _parse_member_info(self, root: ET.Element, link_name: str = "names") -> List[Dict[str, str]]:
        """解析成员信息"""
        members = []
        try:
            # 记录要查找的链接名称
            logger.debug(f"解析成员信息，查找链接名称: {link_name}")
            
            # 查找指定链接中的成员列表
            names_link = root.find(f".//link[@name='{link_name}']")
            if names_link is None:
                logger.warning(f"未找到link[@name='{link_name}']节点")
                # 尝试查找子元素信息
                links = root.findall(".//link")
                if links:
                    link_names = [link.get("name", "无名称") for link in links]
                    logger.info(f"可用的link名称: {link_names}")
                return members

            memberlist = names_link.find("memberlist")
            if memberlist is None:
                logger.warning(f"在{link_name}链接中未找到memberlist节点")
                return members

            for member in memberlist.findall("member"):
                username = member.find("username").text
                nickname = member.find("nickname").text
                members.append({
                    "wxid": username,
                    "nickname": nickname
                })
                
            logger.info(f"成功解析到{len(members)}个成员信息")

        except Exception as e:
            logger.warning(f"解析成员信息失败: {e}")

        return members
    
    def _get_quit_statistics(self, group_wxid: str) -> Tuple[int, int]:
        """获取退群统计数据"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # 获取今日退出的人数
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
            SELECT COUNT(*) FROM group_quit_records 
            WHERE group_wxid = ? AND quit_time >= ?
            ''', (group_wxid, today_start))
            today_count = cursor.fetchone()[0]
            
            # 获取本月退出的人数
            month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
            SELECT COUNT(*) FROM group_quit_records 
            WHERE group_wxid = ? AND quit_time >= ?
            ''', (group_wxid, month_start))
            month_count = cursor.fetchone()[0]
            
            conn.close()
            return today_count, month_count
        except Exception as e:
            logger.error(f"获取退群统计数据失败: {e}")
            return 0, 0
    
    @on_system_message
    async def monitor_group_quit(self, bot: WechatAPIClient, message: dict):
        """监控退群消息，记录并发送通知"""
        # 增加调试日志
        logger.debug(f"收到系统消息: {message.get('FromWxid')}, 内容类型: {type(message.get('Content'))}")
        
        if not message["IsGroup"]:
            return

        if not self.enable:
            return
            
        xml_content = str(message["Content"]).strip().replace("\n", "").replace("\t", "")
        # 记录XML原始内容用于调试
        logger.debug(f"系统消息XML内容: {xml_content[:200]}...")
        
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            logger.error(f"XML解析失败: {e}, 内容: {xml_content[:100]}...")
            return

        if root.tag != "sysmsg":
            logger.debug(f"非sysmsg消息: {root.tag}")
            return

        # 检查是否是退群消息
        if root.attrib.get("type") == "sysmsgtemplate":
            sys_msg_template = root.find("sysmsgtemplate")
            if sys_msg_template is None:
                logger.debug("未找到sysmsgtemplate节点")
                return

            template = sys_msg_template.find("content_template")
            if template is None:
                logger.debug("未找到content_template节点")
                return

            template_type = template.attrib.get("type")
            if template_type not in ["tmpl_type_profile", "tmpl_type_profilewithrevoke"]:
                logger.debug(f"模板类型不匹配: {template_type}")
                return

            template_text = template.find("template").text
            # 记录模板文本用于调试
            logger.info(f"系统消息模板: {template_text}")
            
            # 解析不同类型的退群方式和成员信息
            quit_members = []
            remover_info = None
            quit_method = "主动退出"
            
            # 记录完整模板信息
            logger.info(f"尝试匹配退群模板: {template_text}")
            
            # 成员主动退出群聊
            if '"$kickee$"退出了群聊' in template_text:
                logger.info("匹配到主动退群模板")
                quit_members = self._parse_member_info(root, "kickee")
                quit_method = "主动退出"
            # 成员被移出群聊
            elif '"$remover$"将"$kickee$"移出了群聊' in template_text:
                logger.info("匹配到被移出群聊模板")
                quit_members = self._parse_member_info(root, "kickee")
                remover_info = self._parse_member_info(root, "remover")[0] if self._parse_member_info(root, "remover") else None
                quit_method = "被移出"
            # 你将成员移出群聊
            elif '你将"$kickee$"移出了群聊' in template_text:
                logger.info("匹配到自己移出成员模板")
                quit_members = self._parse_member_info(root, "kickee")
                try:
                    self_profile = await bot.get_profile()
                    remover_info = {
                        "wxid": self_profile.get("wxid", ""),
                        "nickname": self_profile.get("nickname", "")
                    }
                except Exception as e:
                    logger.error(f"获取自己的个人信息失败: {e}")
                quit_method = "被移出"
            # 尝试匹配其他可能的退群模式
            elif '"$notusername$"退出该群聊' in template_text:
                logger.info("匹配到替代退群模板1")
                quit_members = self._parse_member_info(root, "notusername")
                quit_method = "主动退出"
            elif '"$kickee$"退出该群' in template_text:
                logger.info("匹配到替代退群模板2")
                quit_members = self._parse_member_info(root, "kickee") 
                quit_method = "主动退出"
            elif "退出群聊" in template_text and "$" in template_text:
                # 通用退群模板尝试
                logger.info("尝试匹配通用退群模板")
                # 提取模板中的所有变量名
                variables = re.findall(r'\$([^$]+)\$', template_text)
                logger.info(f"模板中的变量: {variables}")
                
                # 尝试遍历所有变量找到可能的成员信息
                for var in variables:
                    if var != "remover" and var != "username":  # 排除已知的非退出成员变量
                        potential_members = self._parse_member_info(root, var)
                        if potential_members:
                            logger.info(f"在变量{var}中找到可能的退群成员")
                            quit_members = potential_members
                            quit_method = "主动退出"
                            break
            elif "$username$" in template_text and "群聊" in template_text:
                # 尝试直接用username作为退群成员
                logger.info("尝试使用username作为退群成员")
                quit_members = self._parse_member_info(root, "username")
                quit_method = "主动退出"
            else:
                # 不是退群消息
                logger.info(f"未匹配到退群模板，原始模板: {template_text}")
                return

            if not quit_members:
                logger.warning("未能解析到退群成员信息")
                return
                
            logger.info(f"成功解析退群信息: {len(quit_members)}人, 退群方式: {quit_method}")
            
            # 获取群名称
            group_wxid = message["FromWxid"]
            group_name = message.get("Group_Name", "") or group_wxid
            
            # 获取统计数据
            today_count, month_count = self._get_quit_statistics(group_wxid)
            
            # 为每个退出成员处理
            for member in quit_members:
                wxid = member["wxid"]
                nickname = member["nickname"]
                
                # 记录退出事件
                self._record_quit_event(group_wxid, group_name, member, quit_method)
                
                # 更新统计数据 (当前退出的成员要算在内)
                today_count += 1
                month_count += 1
                
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                # 检查是否需要发送通知
                if (group_wxid in self.notification_groups and 
                    self.notification_groups[group_wxid]["enabled"] and 
                    self.notification_groups[group_wxid]["receivers"]):
                    
                    # 构建通知消息
                    notification_text = f"⚠️ 退群提醒\n➡️ 成员: {nickname}\n➡️ 退出方式: {quit_method}"
                    if remover_info and quit_method == "被移出":
                        notification_text += f"\n➡️ 操作人: {remover_info['nickname']}"
                    notification_text += f"\n➡️ 时间: {now}"
                    notification_text += f"\n📊 统计: 今日退群{today_count}人，本月退群{month_count}人"
                    
                    # 发送通知给所有接收人
                    receivers = self.notification_groups[group_wxid]["receivers"]
                    try:
                        await bot.send_at_message(group_wxid, notification_text, receivers)
                        logger.info(f"已发送退群通知，群: {group_name}，退出成员: {nickname}")
                    except Exception as e:
                        logger.error(f"发送退群通知失败: {e}")
    
    @on_text_message
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本命令"""
        if not message["IsGroup"]:
            return
            
        content = message.get("Content", "").strip()
        group_wxid = message["FromWxid"]
        user_wxid = message["SenderWxid"]
        
        # 检查是否是超级管理员
        is_admin = user_wxid in self.admins
        
        # 启用退群监控
        if content == "启用退群监控":
            if is_admin:
                success = self._save_notification_setting(group_wxid, True)
                if success:
                    await bot.send_text_message(group_wxid, "已启用退群监控")
                else:
                    await bot.send_text_message(group_wxid, "启用退群监控失败")
            else:
                await bot.send_at_message(group_wxid, "\n只有超级管理员才能使用此命令", [user_wxid])
            return
        
        # 禁用退群监控
        if content == "禁用退群监控":
            if is_admin:
                success = self._save_notification_setting(group_wxid, False)
                if success:
                    await bot.send_text_message(group_wxid, "已禁用退群监控")
                else:
                    await bot.send_text_message(group_wxid, "禁用退群监控失败")
            else:
                await bot.send_at_message(group_wxid, "\n只有超级管理员才能使用此命令", [user_wxid])
            return
            
        # 添加退群通知
        if content.startswith("添加退群通知"):
            if not is_admin:
                await bot.send_at_message(group_wxid, "\n只有超级管理员才能使用此命令", [user_wxid])
                return
                
            at_users = message.get("Ats", [])
            if not at_users:
                await bot.send_at_message(group_wxid, "\n请@要添加的用户", [user_wxid])
                return
                
            success_users = []
            fail_users = []
            
            for at_wxid in at_users:
                success, msg = self._add_notification_receiver(group_wxid, at_wxid)
                try:
                    # 获取用户昵称
                    member_info = await self._get_member_info(bot, group_wxid, at_wxid)
                    if member_info:
                        nickname = member_info.get("DisplayName") or member_info.get("NickName") or "未知用户"
                    else:
                        # 如果获取不到，尝试获取个人信息
                        user_profile = await bot.get_profile(at_wxid)
                        nickname = user_profile.get("nickname", "未知用户")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    nickname = "未知用户"
                
                if success:
                    success_users.append(nickname)
                else:
                    fail_users.append(nickname)
            
            # 构建结果消息
            result = "添加退群通知结果:\n"
            if success_users:
                result += f"✅ 添加成功 ({len(success_users)}人):\n"
                for idx, name in enumerate(success_users, 1):
                    result += f"  {idx}. {name}\n"
            if fail_users:
                result += f"❌ 添加失败 ({len(fail_users)}人):\n"
                for idx, name in enumerate(fail_users, 1):
                    result += f"  {idx}. {name}\n"
                    
            await bot.send_text_message(group_wxid, result)
            return
            
        # 移除退群通知
        if content.startswith("移除退群通知"):
            if not is_admin:
                await bot.send_at_message(group_wxid, "\n只有超级管理员才能使用此命令", [user_wxid])
                return
                
            at_users = message.get("Ats", [])
            if not at_users:
                await bot.send_at_message(group_wxid, "\n请@要移除的用户", [user_wxid])
                return
                
            success_users = []
            fail_users = []
            
            for at_wxid in at_users:
                success, msg = self._remove_notification_receiver(group_wxid, at_wxid)
                try:
                    # 获取用户昵称
                    member_info = await self._get_member_info(bot, group_wxid, at_wxid)
                    if member_info:
                        nickname = member_info.get("DisplayName") or member_info.get("NickName") or "未知用户"
                    else:
                        # 如果获取不到，尝试获取个人信息
                        user_profile = await bot.get_profile(at_wxid)
                        nickname = user_profile.get("nickname", "未知用户")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    nickname = "未知用户"
                
                if success:
                    success_users.append(nickname)
                else:
                    fail_users.append(nickname)
            
            # 构建结果消息
            result = "移除退群通知结果:\n"
            if success_users:
                result += f"✅ 移除成功 ({len(success_users)}人):\n"
                for idx, name in enumerate(success_users, 1):
                    result += f"  {idx}. {name}\n"
            if fail_users:
                result += f"❌ 移除失败 ({len(fail_users)}人):\n"
                for idx, name in enumerate(fail_users, 1):
                    result += f"  {idx}. {name}\n"
                    
            await bot.send_text_message(group_wxid, result)
            return
            
        # 查看退群统计
        if content == "退群统计":
            # 任何人都可以查看统计，无需管理员权限
            today_count, month_count = self._get_quit_statistics(group_wxid)
            
            try:
                # 获取群名称
                group_info = await bot.get_chatroom_info(group_wxid)
                group_name = group_info.get("NickName", group_wxid)
            except Exception as e:
                logger.error(f"获取群信息失败: {e}")
                group_name = group_wxid
                
            # 查询最近5条退群记录
            try:
                conn = sqlite3.connect(self.database_path)
                cursor = conn.cursor()
                
                cursor.execute('''
                SELECT member_nickname, quit_method, quit_time 
                FROM group_quit_records 
                WHERE group_wxid = ? 
                ORDER BY quit_time DESC 
                LIMIT 5
                ''', (group_wxid,))
                
                recent_records = cursor.fetchall()
                conn.close()
                
                # 构建统计消息
                stat_msg = f"📊 {group_name} 退群统计\n"
                stat_msg += f"━━━━━━━━━━━━━━\n"
                stat_msg += f"• 今日退群: {today_count} 人\n"
                stat_msg += f"• 本月退群: {month_count} 人\n"
                
                if recent_records:
                    stat_msg += f"\n📋 最近退群记录:\n"
                    for idx, (nickname, method, quit_time) in enumerate(recent_records, 1):
                        # 格式化时间
                        try:
                            quit_dt = datetime.strptime(quit_time, "%Y-%m-%d %H:%M:%S")
                            quit_time_str = quit_dt.strftime("%m-%d %H:%M")
                        except:
                            quit_time_str = quit_time
                            
                        stat_msg += f"{idx}. {nickname} ({method}) {quit_time_str}\n"
                
                await bot.send_at_message(group_wxid, f"\n{stat_msg}", [user_wxid])
            except Exception as e:
                logger.error(f"查询退群统计失败: {e}")
                await bot.send_at_message(group_wxid, f"\n查询退群统计失败: {str(e)}", [user_wxid])
            return
            
        # 查看退群通知设置
        if content == "退群通知设置":
            # 任何人都可以查看设置，无需管理员权限
            if group_wxid not in self.notification_groups:
                await bot.send_at_message(group_wxid, "\n此群未设置退群通知", [user_wxid])
                return
                
            setting = self.notification_groups[group_wxid]
            enabled = setting.get("enabled", False)
            receivers = setting.get("receivers", [])
            
            # 获取接收人昵称
            receiver_names = []
            for r_wxid in receivers:
                try:
                    member_info = await self._get_member_info(bot, group_wxid, r_wxid)
                    if member_info:
                        nickname = member_info.get("DisplayName") or member_info.get("NickName") or r_wxid
                    else:
                        user_profile = await bot.get_profile(r_wxid)
                        nickname = user_profile.get("nickname", r_wxid)
                    receiver_names.append(nickname)
                except Exception as e:
                    logger.error(f"获取接收人昵称失败: {e}")
                    receiver_names.append(r_wxid)
            
            # 构建设置消息
            setting_msg = "📢 退群通知设置\n"
            setting_msg += f"━━━━━━━━━━━━━━\n"
            setting_msg += f"• 通知状态: {'✅ 已启用' if enabled else '❌ 已禁用'}\n"
            
            if receiver_names:
                setting_msg += f"• 接收用户 ({len(receiver_names)}人):\n"
                for idx, name in enumerate(receiver_names, 1):
                    setting_msg += f"  {idx}. {name}\n"
            else:
                setting_msg += "• 接收用户: 无\n"
                
            setting_msg += f"\n📝 管理命令:\n"
            setting_msg += "• 启用退群监控 (仅超级管理员)\n"
            setting_msg += "• 禁用退群监控 (仅超级管理员)\n"
            setting_msg += "• 添加退群通知 @用户 (仅超级管理员)\n"
            setting_msg += "• 移除退群通知 @用户 (仅超级管理员)\n"
            setting_msg += "• 退群统计 (所有人可用)"
                
            await bot.send_at_message(group_wxid, f"\n{setting_msg}", [user_wxid])
            return
            
    @schedule("cron", hour=0, minute=0)
    async def cleanup_old_records(self):
        """定期清理过期记录"""
        try:
            # 保留180天的记录，删除更早的
            keep_days = 180
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
            
            cursor.execute('''
            DELETE FROM group_quit_records
            WHERE date(quit_time) < ?
            ''', (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted_count > 0:
                logger.info(f"已清理 {deleted_count} 条过期退群记录")
                
        except Exception as e:
            logger.error(f"清理过期记录失败: {e}")

    def _load_admins(self):
        """从主配置文件加载管理员列表"""
        try:
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)
                
            # 从主配置获取管理员列表
            self.admins = config.get("XYBot", {}).get("admins", [])
            logger.info(f"从配置文件加载了 {len(self.admins)} 个管理员")
        except Exception as e:
            logger.error(f"加载管理员列表失败: {e}")
            self.admins = [] 