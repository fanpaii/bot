from WechatAPI.errors import *
from .base import WechatAPIClientBase, Proxy, Section
from .chatroom import ChatroomMixin
from .friend import FriendMixin
from .hongbao import HongBaoMixin
from .login import LoginMixin
from .message import MessageMixin
from .protect import protector
from .tool import ToolMixin
from .tool_extension import ToolExtensionMixin
from .user import UserMixin
from .pyq import PyqMixin
import sqlite3
import os
from loguru import logger

class WechatAPIClient(LoginMixin, MessageMixin, FriendMixin, ChatroomMixin, UserMixin,
                      ToolMixin, ToolExtensionMixin, HongBaoMixin, PyqMixin):

    # 这里都是需要结合多个功能的方法
    
    def __init__(self, ip: str, port: int):
        super().__init__(ip, port)
        self.contacts_db = None
    
    def get_contacts_db(self):
        """连接到contacts.db数据库"""
        if self.contacts_db is None:
            try:
                # 适配不同环境的路径
                if os.path.exists("/app/database"):
                    db_path = "/app/database/contacts.db"
                else:
                    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    db_path = os.path.join(base_dir, "database", "contacts.db")
                
                self.contacts_db = sqlite3.connect(db_path)
                logger.info(f"联系人数据库初始化成功: {db_path}")
            except Exception as e:
                logger.error(f"初始化联系人数据库失败: {str(e)}")
                self.contacts_db = None
        return self.contacts_db
    
    def get_local_nickname(self, wxid: str, chatroom_id: str = None):
        """从本地contacts.db获取用户昵称"""
        if not wxid:
            return None
        
        # 从contacts.db的group_members表获取成员信息
        if chatroom_id and "@chatroom" in chatroom_id:
            try:
                conn = self.get_contacts_db()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                    SELECT member_wxid, nickname, display_name FROM group_members 
                    WHERE group_wxid = ? AND member_wxid = ?
                    """, (chatroom_id, wxid))
                    
                    result = cursor.fetchone()
                    if result:
                        # 优先使用display_name，如果为空再使用nickname
                        if result[2]:  # display_name
                            return result[2]
                        elif result[1]:  # nickname
                            return result[1]
            except Exception as e:
                logger.error(f"从contacts.db获取昵称失败: {str(e)}")
        
        return None

    async def send_at_message(self, wxid: str, content: str, at: list[str]) -> tuple[int, int, int]:
        """发送@消息

        Args:
            wxid (str): 接收人
            content (str): 消息内容
            at (list[str]): 要@的用户ID列表

        Returns:
            tuple[int, int, int]: 包含以下三个值的元组:
                - ClientMsgid (int): 客户端消息ID
                - CreateTime (int): 创建时间
                - NewMsgId (int): 新消息ID

        Raises:
            UserLoggedOut: 用户未登录时抛出
            BanProtection: 新设备登录4小时内操作时抛出
        """
        if not self.wxid:
            raise UserLoggedOut("请先登录")
        elif not self.ignore_protect and protector.check(14400):
            raise BanProtection("风控保护: 新设备登录后4小时内请挂机")

        output = ""
        for id in at:
            # 优先从contacts.db获取昵称
            local_nickname = self.get_local_nickname(id, wxid)
            if local_nickname:
                nickname = local_nickname
                logger.debug(f"使用本地数据库昵称 @{local_nickname}")
            else:
                # 如果本地数据库没有，再通过API获取
                nickname = await self.get_nickname(id)
                logger.debug(f"使用API昵称 @{nickname}")
            
            output += f"@{nickname}\u2005"

        output += content

        return await self.send_text_message(wxid, output, at)
        
    def __del__(self):
        """清理资源"""
        if hasattr(self, 'contacts_db') and self.contacts_db:
            try:
                self.contacts_db.close()
            except:
                pass

    # 添加获取群昵称方法
    async def get_chatroom_nickname(self, chatroom_id: str, wxid: str) -> str:
        """获取用户在群聊中的昵称
        
        Args:
            chatroom_id (str): 群聊ID
            wxid (str): 用户wxid
            
        Returns:
            str: 用户在群聊中的昵称，如果获取失败则返回用户的昵称或"用户"
        """
        try:
            # 直接获取群成员列表，从中筛选出目标成员
            member_list = await self.get_chatroom_member_list(chatroom_id)
            if member_list and isinstance(member_list, list):
                for member in member_list:
                    # 处理可能的不同字段名
                    member_wxid = (member.get("wxid") or member.get("Wxid") or 
                                  member.get("UserName") or "")
                    if member_wxid == wxid:
                        # 优先使用群昵称（DisplayName），然后是微信昵称（NickName）
                        if member.get("DisplayName"):
                            logger.debug(f"获取到群昵称: {member.get('DisplayName')}")
                            return member.get("DisplayName")
                        elif member.get("NickName"):
                            logger.debug(f"获取到微信昵称: {member.get('NickName')}")
                            return member.get("NickName")
                        elif member.get("display_name"):
                            logger.debug(f"获取到群昵称(小写): {member.get('display_name')}")
                            return member.get("display_name")
                        elif member.get("nickname"):
                            logger.debug(f"获取到微信昵称(小写): {member.get('nickname')}")
                            return member.get("nickname")
                        break
            
            # 如果在群成员列表中未找到，尝试获取普通昵称
            nickname = await self.get_nickname(wxid)
            if nickname:
                return nickname
                
        except Exception as e:
            logger.error(f"获取用户群昵称失败: {e}")
        
        # 如果所有方法都失败，返回默认值
        return "用户"
