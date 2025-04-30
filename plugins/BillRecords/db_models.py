import sqlite3
import os
import datetime
import json
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path

from loguru import logger


class BillDB:
    """账单记录数据库"""
    
    def __init__(self):
        """初始化数据库"""
        self.db_path = os.path.join(os.path.dirname(__file__), "resources", "bill_records.db")
        self.conn = None
        self.cursor = None
    
    async def init_db(self):
        """异步初始化数据库"""
        try:
            # 确保资源目录存在
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            # 连接数据库
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            
            # 创建账单记录表
            self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bill_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chatroom_id TEXT NOT NULL,           -- 群聊ID
                user_wxid TEXT NOT NULL,             -- 用户wxid
                user_nickname TEXT NOT NULL,         -- 用户昵称
                operator_wxid TEXT NOT NULL,         -- 操作者wxid
                operator_nickname TEXT NOT NULL,     -- 操作者昵称
                amount REAL NOT NULL,                -- 金额（正为奖励，负为惩罚）
                remark TEXT,                         -- 备注
                create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 创建时间
                year INTEGER NOT NULL,               -- 年份
                month INTEGER NOT NULL               -- 月份
            )
            ''')
            
            # 创建索引
            self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_bill_records_chatroom_user_time 
            ON bill_records (chatroom_id, user_wxid, year, month)
            ''')
            
            # 提交事务
            self.conn.commit()
            logger.success("账单记录数据库初始化成功")
            return True
        except Exception as e:
            logger.error(f"初始化账单记录数据库失败: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def _ensure_connection(self):
        """确保数据库连接有效"""
        if not self.conn:
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
    
    def add_bill_record(self, chatroom_id: str, user_wxid: str, user_nickname: str, 
                        operator_wxid: str, operator_nickname: str, amount: float, 
                        remark: str = "") -> bool:
        """添加账单记录
        
        Args:
            chatroom_id: 群聊ID
            user_wxid: 用户wxid
            user_nickname: 用户昵称
            operator_wxid: 操作者wxid
            operator_nickname: 操作者昵称
            amount: 金额（正为奖励，负为惩罚）
            remark: 备注
        
        Returns:
            bool: 添加成功返回True，否则返回False
        """
        try:
            self._ensure_connection()
            
            # 获取当前日期
            now = datetime.datetime.now()
            year = now.year
            month = now.month
            
            # 插入记录
            self.cursor.execute('''
            INSERT INTO bill_records 
            (chatroom_id, user_wxid, user_nickname, operator_wxid, operator_nickname, 
             amount, remark, year, month) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (chatroom_id, user_wxid, user_nickname, operator_wxid, 
                  operator_nickname, amount, remark, year, month))
            
            # 提交事务
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"添加账单记录失败: {e}")
            if self.conn:
                self.conn.rollback()
            return False
    
    def get_monthly_bills(self, chatroom_id: str, year: int = None, month: int = None) -> List[Dict]:
        """获取指定群聊的月度账单记录
        
        Args:
            chatroom_id: 群聊ID
            year: 年份，默认为当前年份
            month: 月份，默认为当前月份
        
        Returns:
            List[Dict]: 账单记录列表
        """
        try:
            self._ensure_connection()
            
            # 默认使用当前年月
            if year is None or month is None:
                now = datetime.datetime.now()
                year = year or now.year
                month = month or now.month
            
            # 查询记录
            self.cursor.execute('''
            SELECT id, chatroom_id, user_wxid, user_nickname, operator_wxid, operator_nickname,
                   amount, remark, create_time, year, month
            FROM bill_records
            WHERE chatroom_id = ? AND year = ? AND month = ?
            ORDER BY create_time DESC
            ''', (chatroom_id, year, month))
            
            # 处理结果
            records = []
            for row in self.cursor.fetchall():
                record = {
                    "id": row[0],
                    "chatroom_id": row[1],
                    "user_wxid": row[2],
                    "user_nickname": row[3],
                    "operator_wxid": row[4],
                    "operator_nickname": row[5],
                    "amount": row[6],
                    "remark": row[7],
                    "create_time": row[8],
                    "year": row[9],
                    "month": row[10]
                }
                records.append(record)
            
            return records
        except Exception as e:
            logger.error(f"获取月度账单记录失败: {e}")
            return []
    
    def get_user_monthly_balance(self, chatroom_id: str, user_wxid: str, 
                               year: int = None, month: int = None) -> float:
        """获取用户月度余额（奖励和惩罚的总和）
        
        Args:
            chatroom_id: 群聊ID
            user_wxid: 用户wxid
            year: 年份，默认为当前年份
            month: 月份，默认为当前月份
        
        Returns:
            float: 用户月度余额
        """
        try:
            self._ensure_connection()
            
            # 默认使用当前年月
            if year is None or month is None:
                now = datetime.datetime.now()
                year = year or now.year
                month = month or now.month
            
            # 查询用户本月的账单总额
            self.cursor.execute('''
            SELECT SUM(amount)
            FROM bill_records
            WHERE chatroom_id = ? AND user_wxid = ? AND year = ? AND month = ?
            ''', (chatroom_id, user_wxid, year, month))
            
            result = self.cursor.fetchone()
            balance = result[0] if result[0] is not None else 0.0
            
            return float(balance)
        except Exception as e:
            logger.error(f"获取用户月度余额失败: {e}")
            return 0.0
    
    def get_chatroom_users(self, chatroom_id: str, year: int = None, month: int = None) -> List[Dict]:
        """获取指定群聊中有账单记录的所有用户
        
        Args:
            chatroom_id: 群聊ID
            year: 年份，默认为当前年份
            month: 月份，默认为当前月份
        
        Returns:
            List[Dict]: 用户信息列表，包括wxid、昵称和余额
        """
        try:
            self._ensure_connection()
            
            # 默认使用当前年月
            if year is None or month is None:
                now = datetime.datetime.now()
                year = year or now.year
                month = month or now.month
            
            # 查询该群聊中所有有记录的用户
            self.cursor.execute('''
            SELECT DISTINCT user_wxid, user_nickname
            FROM bill_records
            WHERE chatroom_id = ? AND year = ? AND month = ?
            ''', (chatroom_id, year, month))
            
            users = []
            for row in self.cursor.fetchall():
                user_wxid = row[0]
                user_nickname = row[1]
                
                # 获取用户余额
                balance = self.get_user_monthly_balance(chatroom_id, user_wxid, year, month)
                
                user_info = {
                    "wxid": user_wxid,
                    "nickname": user_nickname,
                    "balance": balance
                }
                users.append(user_info)
            
            return users
        except Exception as e:
            logger.error(f"获取群聊用户列表失败: {e}")
            return []
    
    def get_user_bill_records(self, chatroom_id: str, user_wxid: str, 
                            year: int = None, month: int = None) -> List[Dict]:
        """获取指定用户的账单记录
        
        Args:
            chatroom_id: 群聊ID
            user_wxid: 用户wxid
            year: 年份，默认为当前年份
            month: 月份，默认为当前月份
        
        Returns:
            List[Dict]: 账单记录列表
        """
        try:
            self._ensure_connection()
            
            # 默认使用当前年月
            if year is None or month is None:
                now = datetime.datetime.now()
                year = year or now.year
                month = month or now.month
            
            # 查询记录
            self.cursor.execute('''
            SELECT id, chatroom_id, user_wxid, user_nickname, operator_wxid, operator_nickname,
                   amount, remark, create_time, year, month
            FROM bill_records
            WHERE chatroom_id = ? AND user_wxid = ? AND year = ? AND month = ?
            ORDER BY create_time DESC
            ''', (chatroom_id, user_wxid, year, month))
            
            # 处理结果
            records = []
            for row in self.cursor.fetchall():
                record = {
                    "id": row[0],
                    "chatroom_id": row[1],
                    "user_wxid": row[2],
                    "user_nickname": row[3],
                    "operator_wxid": row[4],
                    "operator_nickname": row[5],
                    "amount": row[6],
                    "remark": row[7],
                    "create_time": row[8],
                    "year": row[9],
                    "month": row[10]
                }
                records.append(record)
            
            return records
        except Exception as e:
            logger.error(f"获取用户账单记录失败: {e}")
            return []
    
    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None
            self.cursor = None 