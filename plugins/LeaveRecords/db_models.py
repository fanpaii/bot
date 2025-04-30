import datetime
import os
import sqlite3
from typing import Dict, List, Optional, Any, Union
from concurrent.futures import ThreadPoolExecutor

from loguru import logger
from utils.singleton import Singleton


class LeaveRecordsDB(metaclass=Singleton):
    """请假记录数据库管理类"""
    
    # 日志前缀
    LOG_PREFIX = "【请假记录DB】"
    
    def __init__(self):
        """初始化数据库"""
        # 确保resources目录存在
        resources_dir = os.path.join(os.path.dirname(__file__), "resources")
        os.makedirs(resources_dir, exist_ok=True)
        
        # 数据库路径
        self.db_path = os.path.join(resources_dir, "leave_records.db")
        self.conn = None
        self.cursor = None
        
        # 初始化数据库
        self._init_db()
        
        # 创建线程池执行器
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="leave_records_db")
        
    def log_debug(self, message: str):
        """记录调试日志"""
        logger.debug(f"{self.LOG_PREFIX} {message}")
        
    def log_info(self, message: str):
        """记录信息日志"""
        logger.info(f"{self.LOG_PREFIX} {message}")
        
    def log_success(self, message: str):
        """记录成功日志"""
        logger.success(f"{self.LOG_PREFIX} {message}")
        
    def log_warning(self, message: str):
        """记录警告日志"""
        logger.warning(f"{self.LOG_PREFIX} {message}")
        
    def log_error(self, message: str):
        """记录错误日志"""
        logger.error(f"{self.LOG_PREFIX} {message}")
        
    def _init_db(self):
        """初始化数据库"""
        try:
            # 连接数据库
            self.conn = sqlite3.connect(self.db_path)
            self.cursor = self.conn.cursor()
            
            # 创建群组表
            self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS leave_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 创建请假记录表
            self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS leave_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_nickname TEXT NOT NULL,
                leave_date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, user_id, leave_date)
            )
            ''')
            
            # 创建索引
            self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_leave_records_group_date
            ON leave_records (group_id, leave_date)
            ''')
            
            self.cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_leave_records_user_date
            ON leave_records (user_id, leave_date)
            ''')
            
            # 提交事务
            self.conn.commit()
            self.log_success("请假记录数据库初始化成功")
        except Exception as e:
            self.log_error(f"初始化数据库失败: {str(e)}")
            if self.conn:
                self.conn.rollback()
                
    def _execute_in_queue(self, method, *args, **kwargs):
        """在队列中执行数据库操作"""
        future = self.executor.submit(method, *args, **kwargs)
        try:
            return future.result(timeout=20)  # 20秒超时
        except Exception as e:
            self.log_error(f"数据库操作失败: {method.__name__} - {str(e)}")
            raise
            
    # 群组设置
    def set_group_enabled(self, group_id: str, enabled: bool) -> bool:
        """设置群聊是否启用请假记录功能"""
        return self._execute_in_queue(self._set_group_enabled, group_id, enabled)
        
    def _set_group_enabled(self, group_id: str, enabled: bool) -> bool:
        """设置群聊是否启用请假记录功能的实际执行方法"""
        try:
            # 检查群组是否存在
            self.cursor.execute(
                "SELECT id FROM leave_groups WHERE group_id = ?",
                (group_id,)
            )
            result = self.cursor.fetchone()
            
            if result:
                # 更新现有记录
                self.cursor.execute(
                    "UPDATE leave_groups SET enabled = ?, updated_at = CURRENT_TIMESTAMP WHERE group_id = ?",
                    (1 if enabled else 0, group_id)
                )
            else:
                # 插入新记录
                self.cursor.execute(
                    "INSERT INTO leave_groups (group_id, enabled) VALUES (?, ?)",
                    (group_id, 1 if enabled else 0)
                )
                
            self.conn.commit()
            self.log_info(f"群聊 {group_id} 设置请假记录功能状态为 {enabled}")
            return True
        except Exception as e:
            self.log_error(f"设置群聊请假记录功能状态失败: {str(e)}")
            self.conn.rollback()
            return False
            
    def is_group_enabled(self, group_id: str) -> bool:
        """检查群聊是否启用请假记录功能"""
        return self._execute_in_queue(self._is_group_enabled, group_id)
        
    def _is_group_enabled(self, group_id: str) -> bool:
        """检查群聊是否启用请假记录功能的实际执行方法"""
        try:
            self.cursor.execute(
                "SELECT enabled FROM leave_groups WHERE group_id = ?",
                (group_id,)
            )
            result = self.cursor.fetchone()
            return bool(result[0]) if result else False
        except Exception as e:
            self.log_error(f"检查群聊请假记录功能状态失败: {str(e)}")
            return False
            
    # 请假记录操作
    def add_leave_record(self, group_id: str, user_id: str, user_nickname: str, leave_date: datetime.date) -> bool:
        """添加请假记录"""
        return self._execute_in_queue(self._add_leave_record, group_id, user_id, user_nickname, leave_date)
        
    def _add_leave_record(self, group_id: str, user_id: str, user_nickname: str, leave_date: datetime.date) -> bool:
        """添加请假记录的实际执行方法"""
        try:
            # 转换日期为字符串格式
            leave_date_str = leave_date.strftime("%Y-%m-%d")
            
            # 尝试插入记录
            self.cursor.execute(
                """
                INSERT OR REPLACE INTO leave_records
                (group_id, user_id, user_nickname, leave_date, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (group_id, user_id, user_nickname, leave_date_str)
            )
            
            self.conn.commit()
            self.log_info(f"用户 {user_nickname}({user_id}) 在群 {group_id} 添加了 {leave_date_str} 的请假记录")
            return True
        except Exception as e:
            self.log_error(f"添加请假记录失败: {str(e)}")
            self.conn.rollback()
            return False
            
    def get_leave_by_date(self, group_id: str, user_id: str, leave_date: datetime.date) -> Optional[Dict[str, Any]]:
        """获取用户特定日期的请假记录"""
        return self._execute_in_queue(self._get_leave_by_date, group_id, user_id, leave_date)
        
    def _get_leave_by_date(self, group_id: str, user_id: str, leave_date: datetime.date) -> Optional[Dict[str, Any]]:
        """获取用户特定日期的请假记录的实际执行方法"""
        try:
            # 转换日期为字符串格式
            leave_date_str = leave_date.strftime("%Y-%m-%d")
            
            self.cursor.execute(
                """
                SELECT id, group_id, user_id, user_nickname, leave_date, created_at
                FROM leave_records
                WHERE group_id = ? AND user_id = ? AND leave_date = ?
                """,
                (group_id, user_id, leave_date_str)
            )
            
            result = self.cursor.fetchone()
            if result:
                return {
                    "id": result[0],
                    "group_id": result[1],
                    "user_id": result[2],
                    "user_nickname": result[3],
                    "leave_date": result[4],
                    "created_at": result[5]
                }
            return None
        except Exception as e:
            self.log_error(f"获取请假记录失败: {str(e)}")
            return None
            
    def get_leaves_by_date(self, group_id: str, leave_date: datetime.date) -> List[Dict[str, Any]]:
        """获取群组特定日期的所有请假记录"""
        return self._execute_in_queue(self._get_leaves_by_date, group_id, leave_date)
        
    def _get_leaves_by_date(self, group_id: str, leave_date: datetime.date) -> List[Dict[str, Any]]:
        """获取群组特定日期的所有请假记录的实际执行方法"""
        try:
            # 转换日期为字符串格式
            leave_date_str = leave_date.strftime("%Y-%m-%d")
            
            self.cursor.execute(
                """
                SELECT id, group_id, user_id, user_nickname, leave_date, created_at
                FROM leave_records
                WHERE group_id = ? AND leave_date = ?
                ORDER BY created_at ASC
                """,
                (group_id, leave_date_str)
            )
            
            results = self.cursor.fetchall()
            return [
                {
                    "id": result[0],
                    "group_id": result[1],
                    "user_id": result[2],
                    "user_nickname": result[3],
                    "leave_date": result[4],
                    "created_at": result[5]
                }
                for result in results
            ]
        except Exception as e:
            self.log_error(f"获取群组请假记录失败: {str(e)}")
            return []
            
    def delete_leave_record(self, record_id: int) -> bool:
        """删除请假记录"""
        return self._execute_in_queue(self._delete_leave_record, record_id)
        
    def _delete_leave_record(self, record_id: int) -> bool:
        """删除请假记录的实际执行方法"""
        try:
            # 获取记录信息用于日志
            self.cursor.execute(
                """
                SELECT group_id, user_id, user_nickname, leave_date
                FROM leave_records
                WHERE id = ?
                """,
                (record_id,)
            )
            record = self.cursor.fetchone()
            
            if not record:
                self.log_warning(f"要删除的记录不存在: {record_id}")
                return False
            
            # 删除记录
            self.cursor.execute(
                "DELETE FROM leave_records WHERE id = ?",
                (record_id,)
            )
            
            self.conn.commit()
            self.log_info(f"删除了群 {record[0]} 中用户 {record[2]}({record[1]}) 在 {record[3]} 的请假记录")
            return True
        except Exception as e:
            self.log_error(f"删除请假记录失败: {str(e)}")
            self.conn.rollback()
            return False
            
    def __del__(self):
        """析构函数，关闭数据库连接"""
        try:
            if self.conn:
                self.conn.close()
                self.conn = None
                self.cursor = None
        except Exception as e:
            self.log_error(f"关闭数据库连接失败: {str(e)}") 