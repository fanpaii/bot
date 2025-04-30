"""
管理员管理模块
提供全局管理员权限控制功能
"""
import json
from typing import List, Set, Optional, Union
from loguru import logger

from .singleton import Singleton
from database.keyvalDB import KeyvalDB


class AdminManager(metaclass=Singleton):
    """管理员管理器类，使用单例模式"""

    def __init__(self):
        """初始化管理员管理器"""
        self.db = KeyvalDB()
        self.initialized = False
        
    async def initialize(self):
        """异步初始化"""
        if not self.initialized:
            await self.db.initialize()
            self.initialized = True
            logger.info("管理员管理器初始化成功")

    async def add_admin(self, group_id: str, admin_wxid: str) -> bool:
        """添加群管理员

        Args:
            group_id: 群ID
            admin_wxid: 管理员wxid

        Returns:
            bool: 是否添加成功
        """
        await self.initialize()
        try:
            # 获取现有管理员列表
            admins = await self.get_admins(group_id)
            
            # 添加新管理员
            if admin_wxid not in admins:
                admins.add(admin_wxid)
                # 保存回数据库
                await self.db.set(f"admin:{group_id}:admins", json.dumps(list(admins)))
                logger.info(f"已添加管理员 {admin_wxid} 到群 {group_id}")
                return True
            else:
                logger.info(f"管理员 {admin_wxid} 已存在于群 {group_id}")
                return False
        except Exception as e:
            logger.error(f"添加管理员失败: {e}")
            return False

    async def remove_admin(self, group_id: str, admin_wxid: str) -> bool:
        """移除群管理员

        Args:
            group_id: 群ID
            admin_wxid: 管理员wxid

        Returns:
            bool: 是否移除成功
        """
        await self.initialize()
        try:
            # 获取现有管理员列表
            admins = await self.get_admins(group_id)
            
            # 移除管理员
            if admin_wxid in admins:
                admins.remove(admin_wxid)
                # 保存回数据库
                await self.db.set(f"admin:{group_id}:admins", json.dumps(list(admins)))
                logger.info(f"已移除群 {group_id} 的管理员 {admin_wxid}")
                return True
            else:
                logger.info(f"管理员 {admin_wxid} 不在群 {group_id} 的管理员列表中")
                return False
        except Exception as e:
            logger.error(f"移除管理员失败: {e}")
            return False

    async def get_admins(self, group_id: str) -> Set[str]:
        """获取群管理员列表

        Args:
            group_id: 群ID

        Returns:
            Set[str]: 管理员wxid集合
        """
        await self.initialize()
        try:
            admins_json = await self.db.get(f"admin:{group_id}:admins")
            if admins_json:
                admins = set(json.loads(admins_json))
            else:
                admins = set()
                
            # 尝试获取群主信息
            # 注意：这里暂时不实现，因为需要bot实例
            # 在使用时，如果管理员列表为空，应该通过其他方式获取群主
                
            return admins
        except Exception as e:
            logger.error(f"获取管理员列表失败: {e}")
            return set()

    async def is_admin(self, group_id: str, wxid: str) -> bool:
        """检查用户是否为管理员

        Args:
            group_id: 群ID
            wxid: 用户wxid

        Returns:
            bool: 是否为管理员
        """
        await self.initialize()
        try:
            admins = await self.get_admins(group_id)
            return wxid in admins
        except Exception as e:
            logger.error(f"检查管理员权限失败: {e}")
            return False


# 全局实例
admin_manager = AdminManager() 