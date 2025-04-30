import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import os
import json
import sqlite3
import asyncio
from pathlib import Path

from loguru import logger

from WechatAPI import WechatAPIClient
from utils.decorators import on_system_message, on_text_message, schedule
from utils.plugin_base import PluginBase


class GroupWelcome(PluginBase):
    description = "进群欢迎与记录"
    author = "HenryXiaoYang & Claude"
    version = "2.0.0"

    def __init__(self):
        super().__init__()

        with open("plugins/GroupWelcome/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        config = plugin_config["GroupWelcome"]

        self.enable = config["enable"]
        self.welcome_message = config["welcome-message"]
        self.url = config["url"]
        self.welcome_reminder_enabled = config.get("welcome_reminder_enabled", True)
        self.database_path = config.get("database_path", "plugins/GroupWelcome/database/group_records.db")
        
        # 提醒用户列表 - 格式: {group_wxid: [user_wxid1, user_wxid2, ...]}
        self.reminder_users = {}
        
        # 确保必要的目录存在
        self._ensure_directories()
        
        # 初始化数据库
        self._init_database()

    def _ensure_directories(self):
        """确保必要的目录存在"""
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        
    def _init_database(self):
        """初始化数据库"""
        try:
            # 确保数据库文件存在
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
            
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            # 创建进群记录表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_join_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_wxid TEXT NOT NULL,
                group_name TEXT,
                member_wxid TEXT NOT NULL,
                member_nickname TEXT,
                inviter_wxid TEXT,
                inviter_nickname TEXT,
                join_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                join_method TEXT
            )
            ''')
            
            # 创建群组提醒配置表
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminder_settings (
                group_wxid TEXT NOT NULL,
                user_wxid TEXT NOT NULL,
                added_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (group_wxid, user_wxid)
            )
            ''')
            
            # 加载已有的提醒设置
            cursor.execute("SELECT group_wxid, user_wxid FROM reminder_settings")
            for row in cursor.fetchall():
                group_wxid, user_wxid = row
                if group_wxid not in self.reminder_users:
                    self.reminder_users[group_wxid] = []
                self.reminder_users[group_wxid].append(user_wxid)
            
            conn.commit()
            conn.close()
            logger.info("成功初始化进群欢迎插件数据库")
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")
            
    def _record_join_event(self, group_wxid, group_name, member_info, inviter_info=None, join_method="邀请"):
        """记录进群事件到数据库"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            member_wxid = member_info.get("wxid", "")
            member_nickname = member_info.get("nickname", "")
            
            inviter_wxid = inviter_info.get("wxid", "") if inviter_info else ""
            inviter_nickname = inviter_info.get("nickname", "") if inviter_info else ""
            
            cursor.execute('''
            INSERT INTO group_join_records 
            (group_wxid, group_name, member_wxid, member_nickname, inviter_wxid, inviter_nickname, join_method)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (group_wxid, group_name, member_wxid, member_nickname, inviter_wxid, inviter_nickname, join_method))
            
            conn.commit()
            conn.close()
            logger.info(f"成功记录进群事件: {member_nickname}({member_wxid}) 加入群 {group_name}({group_wxid})")
            return True
        except Exception as e:
            logger.error(f"记录进群事件失败: {e}")
            return False
            
    def _get_join_statistics(self, group_wxid):
        """获取进群统计数据"""
        try:
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            today = datetime.now().strftime("%Y-%m-%d")
            month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
            
            # 获取今日进群人数
            cursor.execute('''
            SELECT COUNT(*) FROM group_join_records 
            WHERE group_wxid = ? AND date(join_time) = ?
            ''', (group_wxid, today))
            today_count = cursor.fetchone()[0]
            
            # 获取本月进群人数
            cursor.execute('''
            SELECT COUNT(*) FROM group_join_records 
            WHERE group_wxid = ? AND date(join_time) >= ?
            ''', (group_wxid, month_start))
            month_count = cursor.fetchone()[0]
            
            conn.close()
            return today_count, month_count
        except Exception as e:
            logger.error(f"获取进群统计数据失败: {e}")
            return 0, 0
            
    def _get_join_records(self, group_wxid, limit=10, date_filter=None):
        """获取进群记录
        
        Args:
            group_wxid: 群聊ID
            limit: 限制记录数量
            date_filter: 日期过滤条件，格式为 "YYYY-MM-DD" 或 "YYYY-MM"
        
        Returns:
            记录列表
        """
        try:
            conn = sqlite3.connect(self.database_path)
            conn.row_factory = sqlite3.Row  # 使用字典方式访问查询结果
            cursor = conn.cursor()
            
            query = "SELECT * FROM group_join_records WHERE group_wxid = ?"
            params = [group_wxid]
            
            if date_filter:
                if len(date_filter) == 10:  # YYYY-MM-DD 格式
                    query += " AND date(join_time) = ?"
                    params.append(date_filter)
                elif len(date_filter) == 7:  # YYYY-MM 格式
                    query += " AND strftime('%Y-%m', join_time) = ?"
                    params.append(date_filter)
                    
            query += " ORDER BY join_time DESC"
            
            if limit > 0:
                query += " LIMIT ?"
                params.append(limit)
                
            cursor.execute(query, params)
            
            records = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return records
        except Exception as e:
            logger.error(f"获取进群记录失败: {e}")
            return []
            
    def _add_reminder_user(self, group_wxid, user_wxid):
        """添加进群提醒用户"""
        try:
            # 更新内存中的列表
            if group_wxid not in self.reminder_users:
                self.reminder_users[group_wxid] = []
                
            if user_wxid in self.reminder_users[group_wxid]:
                return False, "该用户已在提醒列表中"
                
            self.reminder_users[group_wxid].append(user_wxid)
            
            # 更新数据库
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT OR REPLACE INTO reminder_settings (group_wxid, user_wxid)
            VALUES (?, ?)
            ''', (group_wxid, user_wxid))
            
            conn.commit()
            conn.close()
            return True, "成功添加进群提醒用户"
        except Exception as e:
            logger.error(f"添加进群提醒用户失败: {e}")
            return False, f"添加失败: {str(e)}"
            
    def _remove_reminder_user(self, group_wxid, user_wxid):
        """移除进群提醒用户"""
        try:
            # 更新内存中的列表
            if group_wxid not in self.reminder_users or user_wxid not in self.reminder_users[group_wxid]:
                return False, "该用户不在提醒列表中"
                
            self.reminder_users[group_wxid].remove(user_wxid)
            
            # 更新数据库
            conn = sqlite3.connect(self.database_path)
            cursor = conn.cursor()
            
            cursor.execute('''
            DELETE FROM reminder_settings
            WHERE group_wxid = ? AND user_wxid = ?
            ''', (group_wxid, user_wxid))
            
            conn.commit()
            conn.close()
            return True, "成功移除进群提醒用户"
        except Exception as e:
            logger.error(f"移除进群提醒用户失败: {e}")
            return False, f"移除失败: {str(e)}"
            
    @on_system_message
    async def group_welcome(self, bot: WechatAPIClient, message: dict):
        """处理进群消息，发送欢迎并记录"""
        if not message["IsGroup"]:
            return

        xml_content = str(message["Content"]).strip().replace("\n", "").replace("\t", "")
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return

        if root.tag != "sysmsg":
            return

        # 检查是否是进群消息
        if root.attrib.get("type") == "sysmsgtemplate":
            sys_msg_template = root.find("sysmsgtemplate")
            if sys_msg_template is None:
                return

            template = sys_msg_template.find("content_template")
            if template is None:
                return

            template_type = template.attrib.get("type")
            if template_type not in ["tmpl_type_profile", "tmpl_type_profilewithrevoke"]:
                return

            template_text = template.find("template").text
            
            # 解析不同类型的进群方式和成员信息
            new_members = []
            inviter_info = None
            join_method = "未知"
            
            if '"$names$"加入了群聊' in template_text:  # 直接加入群聊
                new_members = self._parse_member_info(root, "names")
                join_method = "直接加入"
            elif '"$username$"邀请"$names$"加入了群聊' in template_text:  # 通过邀请加入群聊
                new_members = self._parse_member_info(root, "names")
                inviter_info = self._parse_member_info(root, "username")[0] if self._parse_member_info(root, "username") else None
                join_method = "被邀请"
            elif '你邀请"$names$"加入了群聊' in template_text:  # 自己邀请成员加入群聊
                new_members = self._parse_member_info(root, "names")
                # 自己是邀请人，尝试获取自己的信息
                try:
                    self_profile = await bot.get_profile()
                    inviter_info = {
                        "wxid": self_profile.get("wxid", ""),
                        "nickname": self_profile.get("nickname", "")
                    }
                except Exception as e:
                    logger.error(f"获取自己的个人信息失败: {e}")
                join_method = "被邀请"
            elif '"$adder$"通过扫描"$from$"分享的二维码加入群聊' in template_text:  # 通过二维码加入群聊
                new_members = self._parse_member_info(root, "adder")
                from_info = self._parse_member_info(root, "from")[0] if self._parse_member_info(root, "from") else None
                inviter_info = from_info
                join_method = "扫描二维码"
            elif '"$adder$"通过"$from$"的邀请二维码加入群聊' in template_text:
                new_members = self._parse_member_info(root, "adder")
                from_info = self._parse_member_info(root, "from")[0] if self._parse_member_info(root, "from") else None
                inviter_info = from_info
                join_method = "邀请二维码"
            else:
                logger.warning(f"未知的入群方式: {template_text}")
                return

            if not new_members:
                return
            
            # 获取群名称
            group_wxid = message["FromWxid"]
            group_name = message.get("Group_Name", "") or group_wxid
            
            # 获取统计数据
            today_count, month_count = self._get_join_statistics(group_wxid)
            
            # 为每个新成员处理
            for member in new_members:
                wxid = member["wxid"]
                nickname = member["nickname"]
                
                # 记录加入事件
                self._record_join_event(group_wxid, group_name, member, inviter_info, join_method)
                
                # 更新统计数据 (当前新加入的成员要算在内)
                today_count += 1
                month_count += 1
                
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                try:
                    # 获取头像地址
                    avatar_url = await self._get_member_avatar(bot, message["FromWxid"], wxid)
                    
                    # 发送欢迎消息
                    if self.enable:
                        stats_text = f"📊统计: 今日新成员{today_count}位，本月新成员{month_count}位"
                        await bot.send_link_message(message["FromWxid"],
                                                title=f"👏欢迎 {nickname} 加入群聊！🎉",
                                                description=f"⌚时间：{now}\n{stats_text}\n{self.welcome_message}",
                                                url=self.url,
                                                thumb_url=avatar_url
                                                )

                    # 发送进群提醒 - 修改提醒消息格式和确保@被提醒用户
                    if self.welcome_reminder_enabled and group_wxid in self.reminder_users and self.reminder_users[group_wxid]:
                        # 构建提醒消息内容
                        reminder_text = f"有新成员加入群聊\n➡️ 成员: {nickname}\n➡️ 加入方式: {join_method}"
                        if inviter_info:
                            reminder_text += f"\n➡️ 邀请人: {inviter_info['nickname']}"
                        reminder_text += f"\n➡️ 时间: {now}"
                        
                        # 使用send_at_message正确@提醒用户
                        await bot.send_at_message(message["FromWxid"], reminder_text, self.reminder_users[group_wxid])
                        
                except Exception as e:
                    logger.error(f"处理进群欢迎失败: {e}")
                    # 如果获取失败，使用默认头像发送欢迎消息
                    if self.enable:
                        stats_text = f"📊统计: 今日新成员{today_count}位，本月新成员{month_count}位"
                        await bot.send_link_message(message["FromWxid"],
                                                title=f"👏欢迎 {nickname} 加入群聊！🎉",
                                                description=f"⌚时间：{now}\n{stats_text}\n{self.welcome_message}",
                                                url=self.url,
                                                thumb_url=""
                                                )

    async def _get_member_avatar(self, bot: WechatAPIClient, group_wxid: str, member_wxid: str) -> str:
        """获取群成员头像地址"""
        try:
            # 直接使用 API 调用获取群成员信息
            import aiohttp

            # 构造请求参数
            json_param = {"QID": group_wxid, "Wxid": bot.wxid}

            # 确定 API 基础路径
            api_base = f"http://{bot.ip}:{bot.port}"

            # 根据协议版本选择正确的 API 前缀
            import tomllib
            try:
                with open("main_config.toml", "rb") as f:
                    config = tomllib.load(f)
                    protocol_version = config.get("Protocol", {}).get("version", "855")

                    # 根据协议版本选择前缀
                    if protocol_version == "849":
                        api_prefix = "/VXAPI"
                    else:  # 855 或 ipad
                        api_prefix = "/api"
            except Exception as e:
                logger.warning(f"读取协议版本失败，使用默认前缀: {e}")
                # 默认使用 855 的前缀
                api_prefix = "/api"

            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{api_base}{api_prefix}/Group/GetChatRoomMemberDetail",
                    json=json_param,
                    headers={"Content-Type": "application/json"}
                )

                # 检查响应状态
                if response.status != 200:
                    logger.error(f"获取群成员列表失败: HTTP状态码 {response.status}")
                    raise Exception(f"HTTP状态码: {response.status}")

                # 解析响应数据
                json_resp = await response.json()

                if json_resp.get("Success"):
                    # 获取群成员列表
                    group_data = json_resp.get("Data", {})

                    # 正确提取ChatRoomMember列表
                    if "NewChatroomData" in group_data and "ChatRoomMember" in group_data["NewChatroomData"]:
                        group_members = group_data["NewChatroomData"]["ChatRoomMember"]

                        if isinstance(group_members, list) and group_members:
                            # 在群成员列表中查找指定成员
                            for member_data in group_members:
                                # 尝试多种可能的字段名
                                member_wxid = member_data.get("UserName") or member_data.get("Wxid") or member_data.get("wxid") or ""

                                if member_wxid == member_wxid:
                                    # 获取头像地址
                                    avatar_url = member_data.get("BigHeadImgUrl") or member_data.get("SmallHeadImgUrl") or ""
                                    return avatar_url
            return ""  # 如果没有找到，返回空字符串
        except Exception as e:
            logger.error(f"获取群成员头像失败: {e}")
            return ""

    @on_text_message
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理文本命令"""
        # 检查是否是群消息
        if not message.get("IsGroup", False):
            return
            
        content = message.get("Content", "").strip()
        group_wxid = message.get("FromWxid", "")
        sender_wxid = message.get("SenderWxid", "")
        
        # 查询提醒列表
        if content == "提醒列表":
            # 检查是否设置了提醒用户
            if group_wxid not in self.reminder_users or not self.reminder_users[group_wxid]:
                await bot.send_text_message(group_wxid, "当前群聊未设置进群提醒用户")
                return
                
            try:
                # 获取所有提醒用户信息
                names = []
                for user_wxid in self.reminder_users[group_wxid]:
                    try:
                        # 尝试获取用户在群内的昵称
                        member_info = await self._get_member_info(bot, group_wxid, user_wxid)
                        if member_info and (member_info.get("DisplayName") or member_info.get("NickName")):
                            # 优先使用群昵称，其次是微信昵称
                            nickname = member_info.get("DisplayName") or member_info.get("NickName")
                            names.append(nickname)
                        else:
                            # 如果无法获取群昵称，尝试获取微信昵称
                            user_profile = await bot.get_profile(user_wxid)
                            nickname = user_profile.get("nickname", "未知用户")
                            names.append(nickname)
                    except Exception as e:
                        logger.error(f"获取用户昵称失败: {e}")
                        names.append("未知用户")
                        
                # 构建响应消息
                response = f"📋 当前群聊进群提醒用户列表 ({len(names)}人):\n"
                for idx, name in enumerate(names, 1):
                    response += f"{idx}. {name}\n"
                    
                await bot.send_text_message(group_wxid, response)
            except Exception as e:
                logger.error(f"获取提醒列表失败: {e}")
                await bot.send_text_message(group_wxid, f"获取提醒列表失败: {str(e)}")
            return
            
        # 查询进群记录
        if content.startswith("查进群记录"):
            # 解析参数
            parts = content.split()
            date_filter = None
            limit = 30  # 默认显示更多记录
            
            if len(parts) > 1:
                date_param = parts[1].strip()
                # 转换日期格式 MM-DD 到 YYYY-MM-DD
                if len(date_param) == 5 and date_param[2] == '-':
                    current_year = datetime.now().year
                    date_filter = f"{current_year}-{date_param}"
                # 转换日期格式 MM 到 YYYY-MM
                elif len(date_param) == 2 and date_param.isdigit():
                    current_year = datetime.now().year
                    date_filter = f"{current_year}-{date_param}"
                else:
                    date_filter = date_param
            else:
                # 不带参数时，默认查询当天记录
                date_filter = datetime.now().strftime("%Y-%m-%d")
                
            records = self._get_join_records(group_wxid, limit, date_filter)
            
            if not records:
                if date_filter and len(date_filter) == 7:  # 月份查询
                    month_display = date_filter[5:] + "月"
                    await bot.send_text_message(group_wxid, f"暂无{month_display}进群记录")
                elif date_filter and len(date_filter) == 10:  # 日期查询
                    date_display = date_filter[5:].replace("-", "月") + "日"
                    await bot.send_text_message(group_wxid, f"暂无{date_display}进群记录")
                else:
                    await bot.send_text_message(group_wxid, "暂无进群记录")
                return
                
            # 构建响应消息
            if date_filter and len(date_filter) == 7:  # 月份查询
                month_display = date_filter[5:] + "月"
                record_text = f"📋 {month_display}进群记录 (最多显示{limit}条):\n"
            elif date_filter and len(date_filter) == 10:  # 日期查询
                date_display = date_filter[5:].replace("-", "月") + "日"
                record_text = f"📋 {date_display}进群记录 (最多显示{limit}条):\n"
            else:
                record_text = f"📋 进群记录 (最多显示{limit}条):\n"
                
            for idx, record in enumerate(records, 1):
                join_time = record.get("join_time", "")
                member_name = record.get("member_nickname", "")
                inviter_name = record.get("inviter_nickname", "未知")
                join_method = record.get("join_method", "未知")
                
                record_text += f"{idx}. {member_name} - {join_method}\n"
                record_text += f"   邀请人: {inviter_name}\n"
                record_text += f"   时间: {join_time}\n"
                
            await bot.send_text_message(group_wxid, record_text)
            return
            
        # 进群提醒
        if content.startswith("进群提醒"):
            # 检查是否有@的用户
            at_users = message.get("Ats", [])
            
            if not at_users:
                await bot.send_text_message(group_wxid, "请@要接收提醒的用户")
                return
                
            success_users = []
            fail_users = []
            
            for user_wxid in at_users:
                success, msg = self._add_reminder_user(group_wxid, user_wxid)
                try:
                    # 尝试获取用户在群内的昵称
                    member_info = await self._get_member_info(bot, group_wxid, user_wxid)
                    if member_info and (member_info.get("DisplayName") or member_info.get("NickName")):
                        # 优先使用群昵称，其次是微信昵称
                        nickname = member_info.get("DisplayName") or member_info.get("NickName")
                    else:
                        # 如果无法获取群昵称，尝试获取微信昵称
                        user_profile = await bot.get_profile(user_wxid)
                        nickname = user_profile.get("nickname", "未知用户")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    nickname = "未知用户"
                
                if success:
                    success_users.append(nickname)
                else:
                    fail_users.append(nickname)
                    
            # 构建结果消息
            result = "添加进群提醒结果:\n"
            if success_users:
                result += f"✅ 成功添加 ({len(success_users)}人):\n"
                for idx, name in enumerate(success_users, 1):
                    result += f"  {idx}. {name}\n"
            if fail_users:
                result += f"❌ 添加失败 ({len(fail_users)}人):\n"
                for idx, name in enumerate(fail_users, 1):
                    result += f"  {idx}. {name}\n"
                    
            await bot.send_text_message(group_wxid, result)
            return
            
        # 移除进群提醒
        if content.startswith("移除进群提醒"):
            # 检查是否有@的用户
            at_users = message.get("Ats", [])
            
            if not at_users:
                await bot.send_text_message(group_wxid, "请@要移除提醒的用户")
                return
                
            success_users = []
            fail_users = []
            
            for user_wxid in at_users:
                success, msg = self._remove_reminder_user(group_wxid, user_wxid)
                try:
                    # 尝试获取用户在群内的昵称
                    member_info = await self._get_member_info(bot, group_wxid, user_wxid)
                    if member_info and (member_info.get("DisplayName") or member_info.get("NickName")):
                        # 优先使用群昵称，其次是微信昵称
                        nickname = member_info.get("DisplayName") or member_info.get("NickName")
                    else:
                        # 如果无法获取群昵称，尝试获取微信昵称
                        user_profile = await bot.get_profile(user_wxid)
                        nickname = user_profile.get("nickname", "未知用户")
                except Exception as e:
                    logger.error(f"获取用户昵称失败: {e}")
                    nickname = "未知用户"
                
                if success:
                    success_users.append(nickname)
                else:
                    fail_users.append(nickname)
                    
            # 构建结果消息
            result = "移除进群提醒结果:\n"
            if success_users:
                result += f"✅ 成功移除 ({len(success_users)}人):\n"
                for idx, name in enumerate(success_users, 1):
                    result += f"  {idx}. {name}\n"
            if fail_users:
                result += f"❌ 移除失败 ({len(fail_users)}人):\n"
                for idx, name in enumerate(fail_users, 1):
                    result += f"  {idx}. {name}\n"
                    
            await bot.send_text_message(group_wxid, result)
            return
            
        # 启用进群欢迎
        if content == "启用进群欢迎":
            self.enable = True
            await bot.send_text_message(group_wxid, "已启用进群欢迎")
            return
            
        # 禁用进群欢迎
        if content == "禁用进群欢迎":
            self.enable = False
            await bot.send_text_message(group_wxid, "已禁用进群欢迎")
            return
            
        # 启用进群提醒
        if content == "启用进群提醒":
            self.welcome_reminder_enabled = True
            await bot.send_text_message(group_wxid, "已启用进群提醒")
            return
            
        # 禁用进群提醒
        if content == "禁用进群提醒":
            self.welcome_reminder_enabled = False
            await bot.send_text_message(group_wxid, "已禁用进群提醒")
            return
            
    @staticmethod
    def _parse_member_info(root: ET.Element, link_name: str = "names") -> list[dict]:
        """解析新成员信息"""
        new_members = []
        try:
            # 查找指定链接中的成员列表
            names_link = root.find(f".//link[@name='{link_name}']")
            if names_link is None:
                return new_members

            memberlist = names_link.find("memberlist")

            if memberlist is None:
                return new_members

            for member in memberlist.findall("member"):
                username = member.find("username").text
                nickname = member.find("nickname").text
                new_members.append({
                    "wxid": username,
                    "nickname": nickname
                })

        except Exception as e:
            logger.warning(f"解析新成员信息失败: {e}")

        return new_members

    # 每天凌晨清理过期记录
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
            DELETE FROM group_join_records
            WHERE date(join_time) < ?
            ''', (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            if deleted_count > 0:
                logger.info(f"已清理 {deleted_count} 条过期进群记录")
                
        except Exception as e:
            logger.error(f"清理过期记录失败: {e}")
            
    async def _get_member_info(self, bot: WechatAPIClient, group_wxid: str, member_wxid: str) -> dict:
        """获取群成员信息
        
        Args:
            bot: 微信API客户端
            group_wxid: 群ID
            member_wxid: 成员wxid
            
        Returns:
            dict: 成员信息，包含DisplayName(群昵称)和NickName(微信昵称)
        """
        try:
            # 构造请求参数
            json_param = {"QID": group_wxid, "Wxid": bot.wxid}
            
            # 确定 API 基础路径
            api_base = f"http://{bot.ip}:{bot.port}"
            
            # 根据协议版本选择正确的 API 前缀
            import tomllib
            try:
                with open("main_config.toml", "rb") as f:
                    config = tomllib.load(f)
                    protocol_version = config.get("Protocol", {}).get("version", "855")
                    
                    # 根据协议版本选择前缀
                    if protocol_version == "849":
                        api_prefix = "/VXAPI"
                    else:  # 855 或 ipad
                        api_prefix = "/api"
            except Exception as e:
                logger.warning(f"读取协议版本失败，使用默认前缀: {e}")
                # 默认使用 855 的前缀
                api_prefix = "/api"
            
            import aiohttp
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{api_base}{api_prefix}/Group/GetChatRoomMemberDetail",
                    json=json_param,
                    headers={"Content-Type": "application/json"}
                )
                
                # 检查响应状态
                if response.status != 200:
                    logger.error(f"获取群成员列表失败: HTTP状态码 {response.status}")
                    return {}
                
                # 解析响应数据
                json_resp = await response.json()
                
                if json_resp.get("Success"):
                    # 获取群成员列表
                    group_data = json_resp.get("Data", {})
                    
                    # 正确提取ChatRoomMember列表
                    if "NewChatroomData" in group_data and "ChatRoomMember" in group_data["NewChatroomData"]:
                        group_members = group_data["NewChatroomData"]["ChatRoomMember"]
                        
                        if isinstance(group_members, list) and group_members:
                            # 在群成员列表中查找指定成员
                            for member_data in group_members:
                                # 尝试多种可能的字段名
                                current_wxid = member_data.get("UserName") or member_data.get("Wxid") or member_data.get("wxid") or ""
                                
                                if current_wxid == member_wxid:
                                    # 提取有用信息
                                    return {
                                        "DisplayName": member_data.get("DisplayName", ""),
                                        "NickName": member_data.get("NickName", ""),
                                        "BigHeadImgUrl": member_data.get("BigHeadImgUrl", ""),
                                        "SmallHeadImgUrl": member_data.get("SmallHeadImgUrl", "")
                                    }
            
            return {}
        except Exception as e:
            logger.error(f"获取群成员信息失败: {e}")
            return {}