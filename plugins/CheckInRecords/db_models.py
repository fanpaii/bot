import tomllib
import datetime
from typing import List, Optional, Dict, Tuple

from loguru import logger
from sqlalchemy import Column, String, Integer, DateTime, Boolean, create_engine, JSON, Float, Time, Date
from sqlalchemy import update, select, func, and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from concurrent.futures import ThreadPoolExecutor

from utils.singleton import Singleton

Base = declarative_base()


class CheckInSetting(Base):
    """群聊打卡设置表"""
    __tablename__ = 'checkin_setting'

    chatroom_id = Column(String(30), primary_key=True, nullable=False, comment='群聊ID')
    enabled = Column(Boolean, default=False, nullable=False, comment='是否启用打卡')
    checkin_time = Column(Time, default=datetime.time(9, 0), nullable=False, comment='打卡时间')


class CheckInRecord(Base):
    """打卡记录表"""
    __tablename__ = 'checkin_record'

    id = Column(Integer, primary_key=True, autoincrement=True)
    chatroom_id = Column(String(30), nullable=False, index=True, comment='群聊ID')
    member_wxid = Column(String(30), nullable=False, index=True, comment='成员wxid')
    checkin_date = Column(Date, nullable=False, index=True, comment='打卡日期')
    checkin_time = Column(Time, nullable=False, comment='打卡时间')
    is_late = Column(Boolean, default=False, comment='是否迟到')
    late_minutes = Column(Integer, default=0, comment='迟到分钟数')
    nickname = Column(String(50), default='', comment='打卡时的群昵称')


class CheckInDB(metaclass=Singleton):
    """打卡记录数据库管理类"""
    
    def __init__(self):
        # 读取主配置
        with open("main_config.toml", "rb") as f:
            main_config = tomllib.load(f)
        
        # 使用XYBot数据库连接
        self.database_url = main_config["XYBot"]["XYBotDB-url"]
        self.engine = create_engine(self.database_url)
        self.DBSession = sessionmaker(bind=self.engine)
        
        # 创建表
        Base.metadata.create_all(self.engine)
        logger.success("打卡记录数据库初始化成功")
        
        # 创建线程池执行器
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="checkin_db")
    
    def _execute_in_queue(self, method, *args, **kwargs):
        """在队列中执行数据库操作"""
        future = self.executor.submit(method, *args, **kwargs)
        try:
            return future.result(timeout=20)  # 20秒超时
        except Exception as e:
            logger.error(f"数据库操作失败: {method.__name__} - {str(e)}")
            raise
    
    # 群聊打卡设置相关方法
    
    def enable_group_checkin(self, chatroom_id: str) -> bool:
        """启用群聊打卡功能"""
        return self._execute_in_queue(self._enable_group_checkin, chatroom_id)
    
    def _enable_group_checkin(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.enabled = True
            else:
                setting = CheckInSetting(chatroom_id=chatroom_id, enabled=True)
                session.add(setting)
            
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"启用群聊{chatroom_id}打卡功能失败: {e}")
            return False
        finally:
            session.close()
    
    def disable_group_checkin(self, chatroom_id: str) -> bool:
        """禁用群聊打卡功能"""
        return self._execute_in_queue(self._disable_group_checkin, chatroom_id)
    
    def _disable_group_checkin(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.enabled = False
                session.commit()
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"禁用群聊{chatroom_id}打卡功能失败: {e}")
            return False
        finally:
            session.close()
    
    def is_group_checkin_enabled(self, chatroom_id: str) -> bool:
        """检查群聊是否启用打卡功能"""
        return self._execute_in_queue(self._is_group_checkin_enabled, chatroom_id)
    
    def _is_group_checkin_enabled(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            return setting.enabled if setting else False
        finally:
            session.close()
    
    def set_checkin_time(self, chatroom_id: str, checkin_time: datetime.time) -> bool:
        """设置群聊打卡时间"""
        return self._execute_in_queue(self._set_checkin_time, chatroom_id, checkin_time)
    
    def _set_checkin_time(self, chatroom_id: str, checkin_time: datetime.time) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.checkin_time = checkin_time
            else:
                setting = CheckInSetting(
                    chatroom_id=chatroom_id,
                    enabled=True,
                    checkin_time=checkin_time
                )
                session.add(setting)
            
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"设置群聊{chatroom_id}打卡时间失败: {e}")
            return False
        finally:
            session.close()
    
    def get_checkin_time(self, chatroom_id: str) -> datetime.time:
        """获取群聊打卡时间"""
        return self._execute_in_queue(self._get_checkin_time, chatroom_id)
    
    def _get_checkin_time(self, chatroom_id: str) -> datetime.time:
        session = self.DBSession()
        try:
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            return setting.checkin_time if setting else datetime.time(9, 0)  # 默认9:00
        finally:
            session.close()
    
    # 打卡记录相关方法
    
    def record_checkin(self, chatroom_id: str, member_wxid: str, nickname: str) -> Tuple[bool, dict]:
        """记录成员打卡"""
        return self._execute_in_queue(self._record_checkin, chatroom_id, member_wxid, nickname)
    
    def _record_checkin(self, chatroom_id: str, member_wxid: str, nickname: str) -> Tuple[bool, dict]:
        session = self.DBSession()
        try:
            # 获取当前日期和时间
            now = datetime.datetime.now()
            today = now.date()
            current_time = now.time()
            
            # 检查是否已打卡
            existing_record = session.query(CheckInRecord).filter(
                CheckInRecord.chatroom_id == chatroom_id,
                CheckInRecord.member_wxid == member_wxid,
                CheckInRecord.checkin_date == today
            ).first()
            
            if existing_record:
                return False, {
                    "already_checked": True,
                    "checkin_time": existing_record.checkin_time,
                    "is_late": existing_record.is_late,
                    "late_minutes": existing_record.late_minutes
                }
            
            # 获取打卡时间设置
            setting = session.query(CheckInSetting).filter_by(chatroom_id=chatroom_id).first()
            checkin_deadline = setting.checkin_time if setting else datetime.time(9, 0)
            
            # 计算是否迟到
            is_late = current_time > checkin_deadline
            late_minutes = 0
            if is_late:
                # 计算迟到分钟数
                current_minutes = current_time.hour * 60 + current_time.minute
                deadline_minutes = checkin_deadline.hour * 60 + checkin_deadline.minute
                late_minutes = current_minutes - deadline_minutes
            
            # 创建打卡记录
            record = CheckInRecord(
                chatroom_id=chatroom_id,
                member_wxid=member_wxid,
                checkin_date=today,
                checkin_time=current_time,
                is_late=is_late,
                late_minutes=late_minutes,
                nickname=nickname
            )
            session.add(record)
            
            # 获取本月打卡次数
            month_start = datetime.date(today.year, today.month, 1)
            month_records_count = session.query(CheckInRecord).filter(
                CheckInRecord.chatroom_id == chatroom_id,
                CheckInRecord.member_wxid == member_wxid,
                CheckInRecord.checkin_date >= month_start,
                CheckInRecord.checkin_date <= today
            ).count()
            
            session.commit()
            
            return True, {
                "checkin_time": current_time,
                "is_late": is_late,
                "late_minutes": late_minutes,
                "month_count": month_records_count
            }
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"记录成员{member_wxid}在群{chatroom_id}的打卡失败: {e}")
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def get_today_records(self, chatroom_id: str, check_date: Optional[datetime.date] = None) -> List[dict]:
        """获取当日打卡记录"""
        return self._execute_in_queue(self._get_today_records, chatroom_id, check_date)
    
    def _get_today_records(self, chatroom_id: str, check_date: Optional[datetime.date] = None) -> List[dict]:
        session = self.DBSession()
        try:
            target_date = check_date or datetime.date.today()
            records = session.query(CheckInRecord).filter(
                CheckInRecord.chatroom_id == chatroom_id,
                CheckInRecord.checkin_date == target_date
            ).order_by(CheckInRecord.checkin_time).all()
            
            result = []
            for record in records:
                result.append({
                    "member_wxid": record.member_wxid,
                    "nickname": record.nickname,
                    "checkin_time": record.checkin_time,
                    "is_late": record.is_late,
                    "late_minutes": record.late_minutes
                })
            return result
        finally:
            session.close()
    
    def get_member_month_stats(self, chatroom_id: str, member_wxid: str) -> dict:
        """获取成员本月打卡统计"""
        return self._execute_in_queue(self._get_member_month_stats, chatroom_id, member_wxid)
    
    def _get_member_month_stats(self, chatroom_id: str, member_wxid: str) -> dict:
        session = self.DBSession()
        try:
            today = datetime.date.today()
            month_start = datetime.date(today.year, today.month, 1)
            
            # 获取本月打卡记录
            records = session.query(CheckInRecord).filter(
                CheckInRecord.chatroom_id == chatroom_id,
                CheckInRecord.member_wxid == member_wxid,
                CheckInRecord.checkin_date >= month_start,
                CheckInRecord.checkin_date <= today
            ).all()
            
            late_count = sum(1 for record in records if record.is_late)
            late_total_minutes = sum(record.late_minutes for record in records if record.is_late)
            
            return {
                "total_count": len(records),
                "late_count": late_count,
                "on_time_count": len(records) - late_count,
                "late_total_minutes": late_total_minutes
            }
        finally:
            session.close()
    
    def __del__(self):
        """关闭线程池"""
        if hasattr(self, 'executor'):
            self.executor.shutdown() 