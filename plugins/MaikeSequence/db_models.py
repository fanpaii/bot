import os
import sqlite3
import datetime
from typing import List, Dict, Any, Optional, Union, Tuple
from contextlib import contextmanager
from loguru import logger


class MaikeDB:
    """麦序管理数据库"""
    
    def __init__(self):
        """初始化数据库"""
        # 获取数据库路径
        self.db_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")
        os.makedirs(self.db_dir, exist_ok=True)
        self.db_path = os.path.join(self.db_dir, "maike.db")
        
        # 初始化数据库
        self._init_db()
    
    @contextmanager
    def _get_connection(self):
        """获取数据库连接"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            # 启用外键约束
            conn.execute("PRAGMA foreign_keys = ON")
            # 行工厂设置为字典
            conn.row_factory = sqlite3.Row
            yield conn
        except Exception as e:
            logger.error(f"数据库连接失败: {str(e)}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 群组设置表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_settings (
                    group_id TEXT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT 1,
                    max_slots INTEGER DEFAULT 8,
                    start_minute INTEGER DEFAULT 0,
                    end_minute INTEGER DEFAULT 45,
                    buffer_time INTEGER DEFAULT 10,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """)
                
                # 主持人表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    time_range TEXT NOT NULL,
                    host_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES group_settings(group_id) ON DELETE CASCADE
                )
                """)
                
                # 麦序文档表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS maike_docs (
                    group_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES group_settings(group_id) ON DELETE CASCADE
                )
                """)
                
                # 麦序记录表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS maike_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_wxid TEXT NOT NULL,
                    user_nickname TEXT NOT NULL,
                    slot_number INTEGER NOT NULL,
                    slot_type TEXT NOT NULL,
                    time_date DATE NOT NULL,
                    time_hour INTEGER NOT NULL,
                    host_name TEXT NOT NULL,
                    is_black BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES group_settings(group_id) ON DELETE CASCADE
                )
                """)
                
                # 黑麦记录表
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS black_maike (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    user_wxid TEXT NOT NULL,
                    user_nickname TEXT NOT NULL,
                    record_id INTEGER NOT NULL,
                    time_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES group_settings(group_id) ON DELETE CASCADE,
                    FOREIGN KEY (record_id) REFERENCES maike_records(id) ON DELETE CASCADE
                )
                """)
                
                conn.commit()
                logger.success("麦序管理数据库初始化成功")
        except Exception as e:
            logger.error(f"初始化数据库失败: {str(e)}")
    
    # 群组设置相关方法
    
    def enable_group_maike(self, group_id: str) -> bool:
        """启用群组麦序功能"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否存在
                cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    # 更新设置
                    cursor.execute("""
                    UPDATE group_settings 
                    SET enabled = 1, updated_at = CURRENT_TIMESTAMP 
                    WHERE group_id = ?
                    """, (group_id,))
                else:
                    # 插入新设置
                    cursor.execute("""
                    INSERT INTO group_settings (group_id, enabled) 
                    VALUES (?, 1)
                    """, (group_id,))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"启用群组麦序功能失败: {str(e)}")
            return False
    
    def disable_group_maike(self, group_id: str) -> bool:
        """禁用群组麦序功能"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否存在
                cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    # 更新设置
                    cursor.execute("""
                    UPDATE group_settings 
                    SET enabled = 0, updated_at = CURRENT_TIMESTAMP 
                    WHERE group_id = ?
                    """, (group_id,))
                else:
                    # 插入新设置
                    cursor.execute("""
                    INSERT INTO group_settings (group_id, enabled) 
                    VALUES (?, 0)
                    """, (group_id,))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"禁用群组麦序功能失败: {str(e)}")
            return False
    
    def is_group_maike_enabled(self, group_id: str) -> bool:
        """检查群组是否启用麦序功能"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组设置
                cursor.execute("SELECT enabled FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    return bool(row["enabled"])
                else:
                    # 默认未启用
                    return False
        except Exception as e:
            logger.error(f"检查群组麦序功能状态失败: {str(e)}")
            return False
    
    def get_enabled_groups(self) -> List[str]:
        """获取所有启用麦序功能的群组"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT group_id FROM group_settings WHERE enabled = 1")
                rows = cursor.fetchall()
                
                return [row["group_id"] for row in rows]
        except Exception as e:
            logger.error(f"获取启用麦序功能的群组失败: {str(e)}")
            return []
    
    def get_group_settings(self, group_id: str) -> Dict[str, Any]:
        """获取群组设置"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    return dict(row)
                else:
                    # 返回默认设置
                    return {
                        "group_id": group_id,
                        "enabled": False,
                        "max_slots": 8,
                        "start_minute": 0,
                        "end_minute": 45,
                        "buffer_time": 10,
                        "created_at": datetime.datetime.now(),
                        "updated_at": datetime.datetime.now()
                    }
        except Exception as e:
            logger.error(f"获取群组设置失败: {str(e)}")
            return {
                "group_id": group_id,
                "enabled": False,
                "max_slots": 8,
                "start_minute": 0,
                "end_minute": 45,
                "buffer_time": 10,
                "created_at": datetime.datetime.now(),
                "updated_at": datetime.datetime.now()
            }
    
    def update_group_settings(self, group_id: str, **kwargs) -> bool:
        """更新群组设置"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否存在
                cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    # 构建更新语句
                    update_fields = []
                    update_values = []
                    
                    for key, value in kwargs.items():
                        if key in ["max_slots", "start_minute", "end_minute", "buffer_time", "enabled"]:
                            update_fields.append(f"{key} = ?")
                            update_values.append(value)
                    
                    if not update_fields:
                        return True
                    
                    update_fields.append("updated_at = CURRENT_TIMESTAMP")
                    update_values.append(group_id)
                    
                    sql = f"UPDATE group_settings SET {', '.join(update_fields)} WHERE group_id = ?"
                    cursor.execute(sql, update_values)
                else:
                    # 构建插入语句
                    default_settings = {
                        "enabled": True,
                        "max_slots": 8,
                        "start_minute": 0,
                        "end_minute": 45,
                        "buffer_time": 10
                    }
                    
                    # 更新默认设置
                    for key, value in kwargs.items():
                        if key in default_settings:
                            default_settings[key] = value
                    
                    # 插入新设置
                    cursor.execute("""
                    INSERT INTO group_settings 
                    (group_id, enabled, max_slots, start_minute, end_minute, buffer_time) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        group_id,
                        default_settings["enabled"],
                        default_settings["max_slots"],
                        default_settings["start_minute"],
                        default_settings["end_minute"],
                        default_settings["buffer_time"]
                    ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"更新群组设置失败: {str(e)}")
            return False
    
    # 主持人相关方法
    
    def set_host(self, group_id: str, time_range: str, host_name: str) -> bool:
        """设置主持人"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否启用
                self._ensure_group_exists(cursor, group_id)
                
                # 检查是否已存在相同时间段的主持人
                cursor.execute("""
                SELECT * FROM hosts 
                WHERE group_id = ? AND time_range = ?
                """, (group_id, time_range))
                row = cursor.fetchone()
                
                if row:
                    # 更新主持人
                    cursor.execute("""
                    UPDATE hosts 
                    SET host_name = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE group_id = ? AND time_range = ?
                    """, (host_name, group_id, time_range))
                else:
                    # 添加新主持人
                    cursor.execute("""
                    INSERT INTO hosts (group_id, time_range, host_name) 
                    VALUES (?, ?, ?)
                    """, (group_id, time_range, host_name))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"设置主持人失败: {str(e)}")
            return False
    
    def get_hosts(self, group_id: str) -> List[Dict[str, Any]]:
        """获取群组所有主持人"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                SELECT * FROM hosts 
                WHERE group_id = ? 
                ORDER BY time_range
                """, (group_id,))
                rows = cursor.fetchall()
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取主持人失败: {str(e)}")
            return []
    
    def get_host_by_hour(self, group_id: str, hour: int) -> Optional[str]:
        """根据小时获取主持人"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 获取所有主持人
                cursor.execute("""
                SELECT * FROM hosts 
                WHERE group_id = ?
                """, (group_id,))
                rows = cursor.fetchall()
                
                # 匹配当前小时的主持人
                for row in rows:
                    time_range = row["time_range"]
                    start_hour, end_hour = map(int, time_range.split("-"))
                    
                    # 处理跨天情况
                    if end_hour < start_hour:
                        if start_hour <= hour < 24 or 0 <= hour <= end_hour:
                            return row["host_name"]
                    else:
                        if start_hour <= hour <= end_hour:
                            return row["host_name"]
                
                return None
        except Exception as e:
            logger.error(f"获取指定小时主持人失败: {str(e)}")
            return None
    
    # 麦序文档相关方法
    
    def set_maike_document(self, group_id: str, content: str) -> bool:
        """设置麦序文档"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否启用
                self._ensure_group_exists(cursor, group_id)
                
                # 检查是否已存在麦序文档
                cursor.execute("SELECT * FROM maike_docs WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    # 更新麦序文档
                    cursor.execute("""
                    UPDATE maike_docs 
                    SET content = ?, updated_at = CURRENT_TIMESTAMP 
                    WHERE group_id = ?
                    """, (content, group_id))
                else:
                    # 添加新麦序文档
                    cursor.execute("""
                    INSERT INTO maike_docs (group_id, content) 
                    VALUES (?, ?)
                    """, (group_id, content))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"设置麦序文档失败: {str(e)}")
            return False
    
    def get_maike_document(self, group_id: str) -> Optional[str]:
        """获取麦序文档"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("SELECT content FROM maike_docs WHERE group_id = ?", (group_id,))
                row = cursor.fetchone()
                
                if row:
                    return row["content"]
                else:
                    return None
        except Exception as e:
            logger.error(f"获取麦序文档失败: {str(e)}")
            return None
    
    # 麦序记录相关方法
    
    def add_maike_record(self, chatroom_id: str, user_wxid: str, user_nickname: str, 
                         slot_number: int, slot_type: str, time_hour: int, host_name: str) -> bool:
        """添加麦序记录"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 检查群组是否启用
                self._ensure_group_exists(cursor, chatroom_id)
                
                # 获取当前日期
                today = datetime.date.today()
                
                # 插入麦序记录
                cursor.execute("""
                INSERT INTO maike_records 
                (group_id, user_wxid, user_nickname, slot_number, slot_type, time_date, time_hour, host_name) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chatroom_id, user_wxid, user_nickname, 
                    slot_number, slot_type, today, time_hour, host_name
                ))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"添加麦序记录失败: {str(e)}")
            return False
    
    def get_maike_records(self, group_id: str, time_hour: int) -> List[Dict[str, Any]]:
        """获取指定时段的麦序记录"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 获取当天指定小时的麦序记录
                today = datetime.date.today()
                cursor.execute("""
                SELECT * FROM maike_records 
                WHERE group_id = ? AND time_date = ? AND time_hour = ? 
                ORDER BY slot_number
                """, (group_id, today, time_hour))
                rows = cursor.fetchall()
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取麦序记录失败: {str(e)}")
            return []
    
    def get_next_slot_number(self, group_id: str, time_hour: int) -> int:
        """获取下一个排档号"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 获取当前最大排档号
                today = datetime.date.today()
                cursor.execute("""
                SELECT MAX(slot_number) as max_slot FROM maike_records 
                WHERE group_id = ? AND time_date = ? AND time_hour = ?
                """, (group_id, today, time_hour))
                row = cursor.fetchone()
                
                if row and row["max_slot"] is not None:
                    return row["max_slot"] + 1
                else:
                    return 1
        except Exception as e:
            logger.error(f"获取下一个排档号失败: {str(e)}")
            return 1
    
    def mark_as_black_maike(self, chatroom_id: str, record_id: int, user_wxid: str, user_nickname: str) -> bool:
        """标记黑麦"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 更新麦序记录为黑麦
                cursor.execute("""
                UPDATE maike_records 
                SET is_black = 1 
                WHERE id = ?
                """, (record_id,))
                
                # 获取当前日期
                today = datetime.date.today()
                
                # 添加黑麦记录
                cursor.execute("""
                INSERT INTO black_maike 
                (group_id, user_wxid, user_nickname, record_id, time_date) 
                VALUES (?, ?, ?, ?, ?)
                """, (chatroom_id, user_wxid, user_nickname, record_id, today))
                
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"标记黑麦失败: {str(e)}")
            return False
    
    # 统计相关方法
    
    def get_maike_statistics(self, group_id: str, start_date: datetime.date, end_date: datetime.date) -> List[Dict[str, Any]]:
        """获取麦序统计数据"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 统计用户麦序记录
                cursor.execute("""
                SELECT 
                    user_wxid, 
                    user_nickname, 
                    COUNT(*) as total_count,
                    SUM(CASE WHEN slot_type = '手速' THEN 1 ELSE 0 END) as speed_count,
                    SUM(CASE WHEN slot_type = '任务' THEN 1 ELSE 0 END) as task_count
                FROM maike_records 
                WHERE group_id = ? AND time_date BETWEEN ? AND ? 
                GROUP BY user_wxid
                ORDER BY total_count DESC
                """, (group_id, start_date, end_date))
                rows = cursor.fetchall()
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取麦序统计数据失败: {str(e)}")
            return []
    
    def get_black_maike_statistics(self, group_id: str, start_date: datetime.date, end_date: datetime.date) -> List[Dict[str, Any]]:
        """获取黑麦统计数据"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 统计用户黑麦记录
                cursor.execute("""
                SELECT 
                    user_wxid, 
                    user_nickname, 
                    COUNT(*) as black_count
                FROM black_maike 
                WHERE group_id = ? AND time_date BETWEEN ? AND ? 
                GROUP BY user_wxid
                ORDER BY black_count DESC
                """, (group_id, start_date, end_date))
                rows = cursor.fetchall()
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"获取黑麦统计数据失败: {str(e)}")
            return []
    
    # 辅助方法
    
    def _ensure_group_exists(self, cursor, group_id: str) -> None:
        """确保群组记录存在"""
        cursor.execute("SELECT * FROM group_settings WHERE group_id = ?", (group_id,))
        row = cursor.fetchone()
        
        if not row:
            # 插入默认群组设置
            cursor.execute("""
            INSERT INTO group_settings 
            (group_id, enabled, max_slots, start_minute, end_minute, buffer_time) 
            VALUES (?, 1, 8, 0, 45, 10)
            """, (group_id,)) 