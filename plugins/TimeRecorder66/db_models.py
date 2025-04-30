import datetime
import tomllib
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Any, Optional, Union

from loguru import logger
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, Date, Time, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.exc import SQLAlchemyError

from utils.singleton import Singleton

# 创建数据库基类
Base = declarative_base()

class TimeRecord66Group(Base):
    """66时长记录插件群组设置表"""
    __tablename__ = "time_recorder66_groups"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String(64), unique=True, nullable=False, index=True, comment="群聊ID")
    enabled = Column(Boolean, default=True, comment="是否启用")
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    
    def __repr__(self):
        return f"<TimeRecord66Group(group_id='{self.group_id}', enabled={self.enabled})>"

class TimeRecord66Entry(Base):
    """66时长记录条目表"""
    __tablename__ = "time_recorder66_entries"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String(64), nullable=False, index=True, comment="群聊ID")
    user_id = Column(String(64), nullable=False, index=True, comment="用户wxid")
    user_nickname = Column(String(128), nullable=False, comment="用户群昵称")
    start_time = Column(DateTime, nullable=False, comment="开始时间")
    end_time = Column(DateTime, nullable=True, comment="结束时间")
    duration = Column(Integer, nullable=True, comment="时长(秒)")
    record_date = Column(Date, nullable=False, default=datetime.date.today, index=True, comment="记录日期")
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now, comment="更新时间")
    
    def __repr__(self):
        return f"<TimeRecord66Entry(user_nickname='{self.user_nickname}', duration={self.duration})>"

class TimeRecord66Active(Base):
    """66时长记录活动表"""
    __tablename__ = "time_recorder66_active"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(String(64), nullable=False, index=True, comment="群聊ID")
    user_id = Column(String(64), nullable=False, index=True, comment="用户wxid")
    user_nickname = Column(String(128), nullable=False, comment="用户群昵称")
    start_time = Column(DateTime, nullable=False, comment="开始时间")
    created_at = Column(DateTime, default=datetime.datetime.now, comment="创建时间")
    
    def __repr__(self):
        return f"<TimeRecord66Active(user_nickname='{self.user_nickname}', start_time='{self.start_time}')>"


class TimeRecorder66DB(metaclass=Singleton):
    """66时长记录数据库管理类"""
    
    # 日志前缀
    LOG_PREFIX = "【66时长记录DB】"
    
    def __init__(self):
        # 读取主配置
        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)
        
        # 使用XYBotDB相同的数据库连接
        self.database_url = main_config["XYBot"]["XYBotDB-url"]
        self.engine = create_engine(self.database_url)
        self.DBSession = sessionmaker(bind=self.engine)
        
        # 创建表
        Base.metadata.create_all(self.engine)
        self.log_success("66时长记录数据库初始化成功")
        
        # 创建线程池执行器
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timerecorder66_db")
    
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
    
    def _execute_in_queue(self, method, *args, **kwargs):
        """在队列中执行数据库操作"""
        future = self.executor.submit(method, *args, **kwargs)
        try:
            return future.result(timeout=20)  # 20秒超时
        except Exception as e:
            self.log_error(f"数据库操作失败: {method.__name__} - {str(e)}")
            raise
    
    # Group settings
    def set_group_enabled(self, group_id: str, enabled: bool) -> bool:
        """设置群聊是否启用66时长记录"""
        return self._execute_in_queue(self._set_group_enabled, group_id, enabled)
    
    def _set_group_enabled(self, group_id: str, enabled: bool) -> bool:
        session = self.DBSession()
        try:
            group = session.query(TimeRecord66Group).filter_by(group_id=group_id).first()
            if not group:
                group = TimeRecord66Group(group_id=group_id, enabled=enabled)
                session.add(group)
            else:
                group.enabled = enabled
                group.updated_at = datetime.datetime.now()
            
            session.commit()
            self.log_info(f"群聊 {group_id} 设置66时长记录状态为 {enabled}")
            return True
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"设置群聊66时长记录状态失败, 错误: {e}")
            return False
        finally:
            session.close()
    
    def is_group_enabled(self, group_id: str) -> bool:
        """检查群聊是否启用66时长记录"""
        return self._execute_in_queue(self._is_group_enabled, group_id)
    
    def _is_group_enabled(self, group_id: str) -> bool:
        session = self.DBSession()
        try:
            group = session.query(TimeRecord66Group).filter_by(group_id=group_id).first()
            return group.enabled if group else False
        finally:
            session.close()
    
    def get_enabled_groups(self) -> List[str]:
        """获取所有启用66时长记录的群聊ID"""
        return self._execute_in_queue(self._get_enabled_groups)
    
    def _get_enabled_groups(self) -> List[str]:
        session = self.DBSession()
        try:
            groups = session.query(TimeRecord66Group).filter_by(enabled=True).all()
            return [group.group_id for group in groups]
        finally:
            session.close()
    
    # Active records
    def start_recording(self, group_id: str, user_id: str, user_nickname: str) -> bool:
        """开始66时长记录"""
        return self._execute_in_queue(self._start_recording, group_id, user_id, user_nickname)
    
    def _start_recording(self, group_id: str, user_id: str, user_nickname: str) -> bool:
        session = self.DBSession()
        try:
            # 检查是否已经在记录中
            active = session.query(TimeRecord66Active).filter_by(group_id=group_id, user_id=user_id).first()
            if active:
                return False
            
            # 创建新记录
            now = datetime.datetime.now()
            active = TimeRecord66Active(
                group_id=group_id,
                user_id=user_id,
                user_nickname=user_nickname,
                start_time=now
            )
            session.add(active)
            session.commit()
            self.log_info(f"用户 {user_nickname}({user_id}) 在群 {group_id} 开始66时长记录")
            return True
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"开始66时长记录失败, 错误: {e}")
            return False
        finally:
            session.close()
    
    def get_active_recording(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """获取用户活动记录"""
        return self._execute_in_queue(self._get_active_recording, group_id, user_id)
    
    def _get_active_recording(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        session = self.DBSession()
        try:
            active = session.query(TimeRecord66Active).filter_by(group_id=group_id, user_id=user_id).first()
            if not active:
                return None
            
            return {
                "id": active.id,
                "group_id": active.group_id,
                "user_id": active.user_id,
                "user_nickname": active.user_nickname,
                "start_time": active.start_time
            }
        finally:
            session.close()
    
    def end_recording(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """结束66时长记录"""
        return self._execute_in_queue(self._end_recording, group_id, user_id)
    
    def _end_recording(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        session = self.DBSession()
        try:
            # 查找活动记录
            active = session.query(TimeRecord66Active).filter_by(group_id=group_id, user_id=user_id).first()
            if not active:
                return None
            
            # 创建完成记录
            now = datetime.datetime.now()
            duration = int((now - active.start_time).total_seconds())
            
            entry = TimeRecord66Entry(
                group_id=active.group_id,
                user_id=active.user_id,
                user_nickname=active.user_nickname,
                start_time=active.start_time,
                end_time=now,
                duration=duration,
                record_date=active.start_time.date()
            )
            session.add(entry)
            
            # 删除活动记录
            session.delete(active)
            session.commit()
            
            self.log_info(f"用户 {active.user_nickname}({active.user_id}) 在群 {active.group_id} 结束66时长记录，时长: {duration}秒")
            
            return {
                "group_id": entry.group_id,
                "user_id": entry.user_id,
                "user_nickname": entry.user_nickname,
                "start_time": entry.start_time,
                "end_time": entry.end_time,
                "duration": entry.duration
            }
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"结束66时长记录失败, 错误: {e}")
            return None
        finally:
            session.close()
    
    # Records query
    def get_records_by_date(self, group_id: str, date: datetime.date) -> List[Dict[str, Any]]:
        """获取指定日期的记录"""
        return self._execute_in_queue(self._get_records_by_date, group_id, date)
    
    def _get_records_by_date(self, group_id: str, date: datetime.date) -> List[Dict[str, Any]]:
        session = self.DBSession()
        try:
            records = session.query(TimeRecord66Entry).filter_by(
                group_id=group_id,
                record_date=date
            ).all()
            
            return [{
                "id": record.id,
                "group_id": record.group_id,
                "user_id": record.user_id,
                "user_nickname": record.user_nickname,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "duration": record.duration,
                "record_date": record.record_date
            } for record in records]
        finally:
            session.close()
    
    def get_records_by_date_range(self, group_id: str, start_date: datetime.date, end_date: datetime.date) -> List[Dict[str, Any]]:
        """获取指定日期范围的记录"""
        return self._execute_in_queue(self._get_records_by_date_range, group_id, start_date, end_date)
    
    def _get_records_by_date_range(self, group_id: str, start_date: datetime.date, end_date: datetime.date) -> List[Dict[str, Any]]:
        session = self.DBSession()
        try:
            records = session.query(TimeRecord66Entry).filter(
                TimeRecord66Entry.group_id == group_id,
                TimeRecord66Entry.record_date >= start_date,
                TimeRecord66Entry.record_date <= end_date
            ).all()
            
            return [{
                "id": record.id,
                "group_id": record.group_id,
                "user_id": record.user_id,
                "user_nickname": record.user_nickname,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "duration": record.duration,
                "record_date": record.record_date
            } for record in records]
        finally:
            session.close()
            
    def get_today_active_recordings(self, group_id: str) -> List[Dict[str, Any]]:
        """获取当前群聊所有活动记录"""
        return self._execute_in_queue(self._get_today_active_recordings, group_id)
    
    def _get_today_active_recordings(self, group_id: str) -> List[Dict[str, Any]]:
        session = self.DBSession()
        try:
            actives = session.query(TimeRecord66Active).filter_by(group_id=group_id).all()
            
            return [{
                "id": active.id,
                "group_id": active.group_id,
                "user_id": active.user_id,
                "user_nickname": active.user_nickname,
                "start_time": active.start_time,
                "current_duration": int((datetime.datetime.now() - active.start_time).total_seconds())
            } for active in actives]
        finally:
            session.close()

    def __del__(self):
        """确保关闭时清理资源"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=True)
        if hasattr(self, 'engine'):
            self.engine.dispose() 