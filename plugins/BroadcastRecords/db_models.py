import os
import datetime
import sqlite3
import threading
import asyncio
from typing import List, Dict, Any, Tuple, Optional, Union
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime, Time, Date, Float, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from loguru import logger

# 创建数据库基类
Base = declarative_base()

# 定义数据库表结构
class BroadcastSetting(Base):
    """群聊开播记录设置表"""
    __tablename__ = "broadcast_settings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatroom_id = Column(String(64), unique=True, nullable=False, index=True)
    enabled = Column(Boolean, default=True)  # 是否启用
    required_duration = Column(Integer, default=240)  # 要求开播时长(分钟)，默认4小时
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    
    def __repr__(self):
        return f"<BroadcastSetting(chatroom_id='{self.chatroom_id}', enabled={self.enabled})>"

class BroadcasterInfo(Base):
    """主播信息表"""
    __tablename__ = "broadcaster_info"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatroom_id = Column(String(64), nullable=False, index=True)
    broadcaster_wxid = Column(String(64), nullable=False, index=True)
    broadcaster_nickname = Column(String(128), nullable=False)
    register_time = Column(DateTime, default=datetime.datetime.now)
    
    __table_args__ = (
        # 联合索引，加速查询
        {"sqlite_autoincrement": True},
    )
    
    def __repr__(self):
        return f"<BroadcasterInfo(chatroom_id='{self.chatroom_id}', broadcaster_wxid='{self.broadcaster_wxid}')>"

class BroadcasterTimeSlot(Base):
    """主播时间段表，存储多个开播时间段"""
    __tablename__ = "broadcaster_time_slots"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    # 关联到主播信息表
    broadcaster_id = Column(Integer, ForeignKey('broadcaster_info.id'), nullable=False, index=True)
    broadcast_time_start = Column(Time, nullable=False)  # 开播时间(开始)
    broadcast_time_end = Column(Time, nullable=False)    # 开播时间(结束)
    slot_name = Column(String(64), nullable=True)        # 时间段名称，便于识别，可选
    created_at = Column(DateTime, default=datetime.datetime.now)
    
    def __repr__(self):
        return f"<BroadcasterTimeSlot(slot_name='{self.slot_name}', start='{self.broadcast_time_start}', end='{self.broadcast_time_end}')>"

class BroadcastTempTime(Base):
    """临时开播时间表"""
    __tablename__ = "broadcast_temp_times"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatroom_id = Column(String(64), nullable=False, index=True)
    broadcaster_wxid = Column(String(64), nullable=False, index=True)
    broadcast_name = Column(String(128), nullable=False)
    broadcast_time_start = Column(Time, nullable=False)  # 临时开播时间(开始)
    broadcast_time_end = Column(Time, nullable=False)    # 临时开播时间(结束)
    temp_date = Column(Date, nullable=False, default=datetime.date.today)
    created_at = Column(DateTime, default=datetime.datetime.now)
    
    def __repr__(self):
        return f"<BroadcastTempTime(broadcast_name='{self.broadcast_name}', temp_date='{self.temp_date}')>"

class BroadcastRecord(Base):
    """开播记录表"""
    __tablename__ = "broadcast_records"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    chatroom_id = Column(String(64), nullable=False, index=True)
    broadcaster_wxid = Column(String(64), nullable=False, index=True)
    broadcaster_nickname = Column(String(128), nullable=False)
    broadcast_name = Column(String(128), nullable=False)
    start_time = Column(Time, nullable=False)        # 开播时间
    end_time = Column(Time, nullable=True)           # 下播时间
    broadcast_date = Column(Date, nullable=False, default=datetime.date.today, index=True)
    duration = Column(Integer, nullable=True)        # 开播时长(分钟)
    is_late = Column(Boolean, default=False)         # 是否迟到
    late_minutes = Column(Integer, default=0)        # 迟到分钟数
    is_active = Column(Boolean, default=True)        # 是否处于开播状态
    broadcast_time_start = Column(Time, nullable=False)  # 当时设置的开播时间(开始)
    broadcast_time_end = Column(Time, nullable=False)    # 当时设置的开播时间(结束)
    is_temp_time = Column(Boolean, default=False)    # 是否使用了临时时间
    time_slot_id = Column(Integer, nullable=True)    # 对应的时间段ID
    created_at = Column(DateTime, default=datetime.datetime.now)
    updated_at = Column(DateTime, default=datetime.datetime.now, onupdate=datetime.datetime.now)
    
    def __repr__(self):
        return f"<BroadcastRecord(broadcast_name='{self.broadcast_name}', is_active={self.is_active})>"

class BroadcastDB:
    """开播记录数据库操作类"""
    
    def __init__(self):
        """初始化数据库连接"""
        # 创建数据库目录
        self.db_dir = os.path.join(os.path.dirname(__file__), "resources")
        os.makedirs(self.db_dir, exist_ok=True)
        
        # 数据库文件路径
        self.db_file = os.path.join(self.db_dir, "broadcast_records.db")
        
        # 创建数据库引擎
        self.engine = create_engine(f"sqlite:///{self.db_file}", echo=False)
        
        # 创建表
        Base.metadata.create_all(self.engine)
        
        # 创建会话工厂
        self.DBSession = sessionmaker(bind=self.engine)
        
        # 创建线程锁
        self.lock = threading.Lock()
        
        # 创建线程池
        self.executor = ThreadPoolExecutor(max_workers=5)
        
        logger.success(f"开播记录数据库初始化成功: {self.db_file}")
    
    def _execute_in_queue(self, func, *args, **kwargs):
        """将操作放入队列执行，避免并发问题"""
        future = self.executor.submit(func, *args, **kwargs)
        return future.result()
    
    def enable_group_broadcast(self, chatroom_id: str) -> bool:
        """启用群聊开播记录功能"""
        return self._execute_in_queue(self._enable_group_broadcast, chatroom_id)
    
    def _enable_group_broadcast(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            # 查询是否已存在设置
            setting = session.query(BroadcastSetting).filter_by(chatroom_id=chatroom_id).first()
            
            if setting:
                # 更新设置
                setting.enabled = True
                setting.updated_at = datetime.datetime.now()
            else:
                # 创建新设置
                setting = BroadcastSetting(
                    chatroom_id=chatroom_id,
                    enabled=True
                )
                session.add(setting)
            
            session.commit()
            return True
        except Exception as e:
            logger.error(f"启用群聊开播记录功能失败: {str(e)}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def disable_group_broadcast(self, chatroom_id: str) -> bool:
        """禁用群聊开播记录功能"""
        return self._execute_in_queue(self._disable_group_broadcast, chatroom_id)
    
    def _disable_group_broadcast(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            # 查询是否已存在设置
            setting = session.query(BroadcastSetting).filter_by(chatroom_id=chatroom_id).first()
            
            if setting:
                # 更新设置
                setting.enabled = False
                setting.updated_at = datetime.datetime.now()
                session.commit()
                return True
            else:
                # 创建禁用设置
                setting = BroadcastSetting(
                    chatroom_id=chatroom_id,
                    enabled=False
                )
                session.add(setting)
                session.commit()
                return True
        except Exception as e:
            logger.error(f"禁用群聊开播记录功能失败: {str(e)}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def is_group_broadcast_enabled(self, chatroom_id: str) -> bool:
        """检查群聊是否启用开播记录功能"""
        return self._execute_in_queue(self._is_group_broadcast_enabled, chatroom_id)
    
    def _is_group_broadcast_enabled(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            # 查询设置
            setting = session.query(BroadcastSetting).filter_by(chatroom_id=chatroom_id).first()
            
            # 如果没有设置，默认为未启用
            if not setting:
                return False
            
            return setting.enabled
        finally:
            session.close()
            
    def set_required_duration(self, chatroom_id: str, minutes: int) -> bool:
        """设置要求开播时长"""
        return self._execute_in_queue(self._set_required_duration, chatroom_id, minutes)
    
    def _set_required_duration(self, chatroom_id: str, minutes: int) -> bool:
        session = self.DBSession()
        try:
            # 查询是否已存在设置
            setting = session.query(BroadcastSetting).filter_by(chatroom_id=chatroom_id).first()
            
            if setting:
                # 更新设置
                setting.required_duration = minutes
                setting.updated_at = datetime.datetime.now()
            else:
                # 创建新设置
                setting = BroadcastSetting(
                    chatroom_id=chatroom_id,
                    required_duration=minutes
                )
                session.add(setting)
            
            session.commit()
            return True
        except Exception as e:
            logger.error(f"设置要求开播时长失败: {str(e)}")
            session.rollback()
            return False
        finally:
            session.close()
            
    def get_required_duration(self, chatroom_id: str) -> int:
        """获取要求开播时长"""
        return self._execute_in_queue(self._get_required_duration, chatroom_id)
    
    def _get_required_duration(self, chatroom_id: str) -> int:
        session = self.DBSession()
        try:
            # 查询设置
            setting = session.query(BroadcastSetting).filter_by(chatroom_id=chatroom_id).first()
            
            # 如果没有设置，返回默认值
            if not setting:
                return 240  # 默认4小时
            
            return setting.required_duration
        finally:
            session.close()
    
    def register_broadcaster(self, chatroom_id: str, broadcaster_wxid: str, 
                         broadcaster_nickname: str, broadcast_time_start: datetime.time,
                         broadcast_time_end: datetime.time, slot_name: str = None,
                         update_on_conflict: bool = False) -> Tuple[bool, Dict]:
        """注册主播信息和时间段"""
        return self._execute_in_queue(self._register_broadcaster, chatroom_id, broadcaster_wxid,
                                  broadcaster_nickname, broadcast_time_start, broadcast_time_end, 
                                  slot_name, update_on_conflict)

    def _register_broadcaster(self, chatroom_id: str, broadcaster_wxid: str, 
                          broadcaster_nickname: str, broadcast_time_start: datetime.time,
                          broadcast_time_end: datetime.time, slot_name: str = None, 
                          update_on_conflict: bool = False) -> Tuple[bool, Dict]:
        """注册主播信息和时间段的实际方法"""
        # 检查参数类型
        if not isinstance(broadcast_time_start, datetime.time):
            raise TypeError("broadcast_time_start需要是datetime.time类型")
        if not isinstance(broadcast_time_end, datetime.time):
            raise TypeError("broadcast_time_end需要是datetime.time类型")
            
        session = self.DBSession()
        try:
            # 查询是否已存在该主播
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            # 根据结果决定是注册新主播还是更新现有主播
            if not broadcaster:
                # 创建新主播
                broadcaster = BroadcasterInfo(
                    chatroom_id=chatroom_id,
                    broadcaster_wxid=broadcaster_wxid,
                    broadcaster_nickname=broadcaster_nickname
                )
                session.add(broadcaster)
                session.flush()  # 获取自增ID
                action = "create"
            else:
                # 更新昵称
                broadcaster.broadcaster_nickname = broadcaster_nickname
                action = "update"
            
            # 检查时间段是否有冲突
            existing_slots = self._get_broadcaster_time_slots(session, broadcaster.id)
            
            # 检查新时间段是否与已有时间段冲突
            conflicting_slot = None
            for slot in existing_slots:
                if self._is_time_slot_overlapping(
                    slot['broadcast_time_start'], 
                    slot['broadcast_time_end'], 
                    broadcast_time_start, 
                    broadcast_time_end
                ):
                    if update_on_conflict:
                        # 记录冲突的时间段，稍后更新
                        conflicting_slot = slot
                        break
                    else:
                        # 拒绝添加冲突的时间段
                        return False, {"error": f"时间段冲突，与已有时间段 {slot['broadcast_time_start'].strftime('%H:%M')}-{slot['broadcast_time_end'].strftime('%H:%M')} 重叠"}
            
            # 处理冲突的情况
            if conflicting_slot and update_on_conflict:
                # 找到对应的数据库记录
                time_slot = session.query(BroadcasterTimeSlot).filter_by(
                    id=conflicting_slot['id']
                ).first()
                
                # 更新时间段
                time_slot.broadcast_time_start = broadcast_time_start
                time_slot.broadcast_time_end = broadcast_time_end
                if slot_name:  # 仅当提供了slot_name才更新
                    time_slot.slot_name = slot_name
                
                session.commit()
                return True, {
                    "action": "update",
                    "broadcaster_id": broadcaster.id,
                    "broadcast_time_start": broadcast_time_start.strftime("%H:%M"),
                    "broadcast_time_end": broadcast_time_end.strftime("%H:%M"),
                    "slot_name": time_slot.slot_name
                }
            else:
                # 创建新的时间段
                time_slot = BroadcasterTimeSlot(
                    broadcaster_id=broadcaster.id,
                    broadcast_time_start=broadcast_time_start,
                    broadcast_time_end=broadcast_time_end,
                    slot_name=slot_name
                )
                session.add(time_slot)
            
            session.commit()
            return True, {
                "action": action,
                "broadcaster_id": broadcaster.id,
                "broadcast_time_start": broadcast_time_start.strftime("%H:%M"),
                "broadcast_time_end": broadcast_time_end.strftime("%H:%M"),
                "slot_name": slot_name
            }
        except Exception as e:
            logger.error(f"注册主播开播时间失败: {str(e)}")
            session.rollback()
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def _is_time_slot_overlapping(self, slot1_start: datetime.time, slot1_end: datetime.time, 
                               slot2_start: datetime.time, slot2_end: datetime.time) -> bool:
        """检查两个时间段是否重叠"""
        # 处理跨天的情况
        today = datetime.date.today()
        
        # 转换为datetime对象方便比较
        slot1_start_dt = datetime.datetime.combine(today, slot1_start)
        slot1_end_dt = datetime.datetime.combine(today, slot1_end)
        slot2_start_dt = datetime.datetime.combine(today, slot2_start)
        slot2_end_dt = datetime.datetime.combine(today, slot2_end)
        
        # 如果结束时间小于开始时间，说明跨天，加一天
        if slot1_end_dt < slot1_start_dt:
            slot1_end_dt += datetime.timedelta(days=1)
        if slot2_end_dt < slot2_start_dt:
            slot2_end_dt += datetime.timedelta(days=1)
        
        # 检查时间段是否重叠
        return (slot1_start_dt < slot2_end_dt and slot1_end_dt > slot2_start_dt)
    
    def _get_broadcaster_time_slots(self, session, broadcaster_id: int) -> List[Dict]:
        """获取主播的所有时间段"""
        time_slots = session.query(BroadcasterTimeSlot).filter(
            BroadcasterTimeSlot.broadcaster_id == broadcaster_id
        ).all()
        
        return [{
            'id': slot.id,
            'broadcaster_id': slot.broadcaster_id,
            'broadcast_time_start': slot.broadcast_time_start,
            'broadcast_time_end': slot.broadcast_time_end,
            'slot_name': slot.slot_name
        } for slot in time_slots]
    
    def set_temp_time(self, chatroom_id: str, broadcaster_wxid: str, broadcast_name: str,
                     broadcast_time_start: datetime.time, broadcast_time_end: datetime.time,
                     date: datetime.date = None) -> Tuple[bool, Dict]:
        """设置临时开播时间"""
        return self._execute_in_queue(self._set_temp_time, chatroom_id, broadcaster_wxid,
                                     broadcast_name, broadcast_time_start, broadcast_time_end, date)
    
    def _set_temp_time(self, chatroom_id: str, broadcaster_wxid: str, 
                    broadcast_name: str, broadcast_time_start: datetime.time,
                    broadcast_time_end: datetime.time, date: datetime.date = None) -> Tuple[bool, Dict]:
        """设置临时开播时间的实际方法"""
        # 检查参数类型
        if not isinstance(broadcast_time_start, datetime.time):
            raise TypeError("SQLite Time type only accepts Python time objects as input. broadcast_time_start需要是datetime.time类型")
        if not isinstance(broadcast_time_end, datetime.time):
            raise TypeError("SQLite Time type only accepts Python time objects as input. broadcast_time_end需要是datetime.time类型")
            
        # 设置日期，默认为今天
        if date is None:
            date = datetime.date.today()
            
        session = self.DBSession()
        try:
            # 查询是否已存在临时时间
            temp_time = session.query(BroadcastTempTime).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid,
                temp_date=date
            ).first()
            
            # 根据结果决定是更新还是新增
            if temp_time:
                # 更新现有记录
                temp_time.broadcast_name = broadcast_name
                temp_time.broadcast_time_start = broadcast_time_start
                temp_time.broadcast_time_end = broadcast_time_end
                result = {
                    "action": "update",
                    "broadcast_time_start": broadcast_time_start.strftime("%H:%M"),
                    "broadcast_time_end": broadcast_time_end.strftime("%H:%M")
                }
            else:
                # 创建新记录
                temp_time = BroadcastTempTime(
                    chatroom_id=chatroom_id,
                    broadcaster_wxid=broadcaster_wxid,
                    broadcast_name=broadcast_name,
                    broadcast_time_start=broadcast_time_start,
                    broadcast_time_end=broadcast_time_end,
                    temp_date=date
                )
                session.add(temp_time)
                result = {
                    "action": "create",
                    "broadcast_time_start": broadcast_time_start.strftime("%H:%M"),
                    "broadcast_time_end": broadcast_time_end.strftime("%H:%M")
                }
            
            session.commit()
            return True, result
        except Exception as e:
            logger.error(f"设置临时开播时间失败: {str(e)}")
            session.rollback()
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def get_broadcaster_by_wxid(self, chatroom_id: str, broadcaster_wxid: str) -> Dict:
        """根据wxid获取主播信息"""
        return self._execute_in_queue(self._get_broadcaster_by_wxid, chatroom_id, broadcaster_wxid)
    
    def _get_broadcaster_by_wxid(self, chatroom_id: str, broadcaster_wxid: str) -> Dict:
        session = self.DBSession()
        try:
            # 查询主播信息
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            if not broadcaster:
                return None
            
            # 查询所有时间段
            time_slots = session.query(BroadcasterTimeSlot).filter(
                BroadcasterTimeSlot.broadcaster_id == broadcaster.id
            ).all()
            
            # 查询是否有当日临时时间设置
            today = datetime.date.today()
            temp_time = session.query(BroadcastTempTime).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid,
                temp_date=today
            ).first()
            
            result = {
                "broadcaster_wxid": broadcaster.broadcaster_wxid,
                "broadcaster_nickname": broadcaster.broadcaster_nickname,
                "register_time": broadcaster.register_time,
                "has_temp_time": temp_time is not None,
                "time_slots": [{
                    "id": slot.id,
                    "broadcast_time_start": slot.broadcast_time_start,
                    "broadcast_time_end": slot.broadcast_time_end,
                    "slot_name": slot.slot_name
                } for slot in time_slots]
            }
            
            if temp_time:
                result["temp_time_start"] = temp_time.broadcast_time_start
                result["temp_time_end"] = temp_time.broadcast_time_end
            
            # 获取当前时间所属的时间段
            now = datetime.datetime.now().time()
            current_slot = self._find_current_time_slot(time_slots, now)
            if current_slot:
                result["current_slot"] = {
                    "id": current_slot.id,
                    "broadcast_time_start": current_slot.broadcast_time_start,
                    "broadcast_time_end": current_slot.broadcast_time_end,
                    "slot_name": current_slot.slot_name
                }
            else:
                result["current_slot"] = None
            
            # 计算今日开播总时间
            result["today_required_duration"] = self._calculate_total_required_duration(time_slots)
            
            return result
        finally:
            session.close()
    
    def _find_current_time_slot(self, time_slots, current_time: datetime.time) -> Optional[BroadcasterTimeSlot]:
        """查找当前时间所属的时间段"""
        today = datetime.date.today()
        current_dt = datetime.datetime.combine(today, current_time)
        
        for slot in time_slots:
            start_dt = datetime.datetime.combine(today, slot.broadcast_time_start)
            end_dt = datetime.datetime.combine(today, slot.broadcast_time_end)
            
            # 处理跨天的情况
            if end_dt < start_dt:
                end_dt += datetime.timedelta(days=1)
                
            # 当前时间是否在时间段内
            if start_dt <= current_dt <= end_dt:
                return slot
                
            # 检查是否接近开播时间（30分钟内）
            if start_dt > current_dt and (start_dt - current_dt).total_seconds() <= 1800:
                return slot
        
        return None
    
    def _calculate_total_required_duration(self, time_slots) -> int:
        """计算所有时间段的总时长（分钟）"""
        today = datetime.date.today()
        total_minutes = 0
        
        for slot in time_slots:
            start_dt = datetime.datetime.combine(today, slot.broadcast_time_start)
            end_dt = datetime.datetime.combine(today, slot.broadcast_time_end)
            
            # 处理跨天的情况
            if end_dt < start_dt:
                end_dt += datetime.timedelta(days=1)
                
            # 计算分钟数
            duration = (end_dt - start_dt).total_seconds() / 60
            total_minutes += duration
        
        return int(total_minutes)
    
    def get_all_broadcasters(self, chatroom_id: str) -> List[Dict]:
        """获取群聊所有已注册主播的信息"""
        return self._execute_in_queue(self._get_all_broadcasters, chatroom_id)
    
    def _get_all_broadcasters(self, chatroom_id: str) -> List[Dict]:
        session = self.DBSession()
        try:
            # 查询所有主播信息
            broadcasters = session.query(BroadcasterInfo).filter(
                BroadcasterInfo.chatroom_id == chatroom_id
            ).all()
            
            # 构建今日信息
            today = datetime.date.today()
            result = []
            
            for broadcaster in broadcasters:
                # 检查是否有临时时间设置
                temp = session.query(BroadcastTempTime).filter(
                    BroadcastTempTime.chatroom_id == chatroom_id,
                    BroadcastTempTime.broadcaster_wxid == broadcaster.broadcaster_wxid,
                    BroadcastTempTime.temp_date == today
                ).first()
                
                # 获取所有时间段
                time_slots = session.query(BroadcasterTimeSlot).filter(
                    BroadcasterTimeSlot.broadcaster_id == broadcaster.id
                ).all()
                
                # 检查今日是否有活跃的开播记录
                active_record = session.query(BroadcastRecord).filter(
                    BroadcastRecord.chatroom_id == chatroom_id,
                    BroadcastRecord.broadcaster_wxid == broadcaster.broadcaster_wxid,
                    BroadcastRecord.broadcast_date == today,
                    BroadcastRecord.is_active == True
                ).first()
                
                # 构建主播信息
                broadcaster_info = {
                    "broadcaster_nickname": broadcaster.broadcaster_nickname,
                    "broadcaster_wxid": broadcaster.broadcaster_wxid,
                    "register_time": broadcaster.register_time,
                    "temp_time_start": temp.broadcast_time_start if temp else None,
                    "temp_time_end": temp.broadcast_time_end if temp else None,
                    "has_temp_time": temp is not None,
                    "is_active": active_record is not None,
                    "start_time": active_record.start_time if active_record else None,
                    "time_slots": [{
                        "id": slot.id,
                        "broadcast_time_start": slot.broadcast_time_start,
                        "broadcast_time_end": slot.broadcast_time_end,
                        "slot_name": slot.slot_name
                    } for slot in time_slots]
                }
                
                # 计算所有时间段总时长
                broadcaster_info["total_required_duration"] = self._calculate_total_required_duration(time_slots)
                
                # 获取当前时间所属的时间段
                now = datetime.datetime.now().time()
                current_slot = self._find_current_time_slot(time_slots, now)
                if current_slot:
                    broadcaster_info["current_slot"] = {
                        "id": current_slot.id,
                        "broadcast_time_start": current_slot.broadcast_time_start,
                        "broadcast_time_end": current_slot.broadcast_time_end,
                        "slot_name": current_slot.slot_name
                    }
                else:
                    broadcaster_info["current_slot"] = None
                
                result.append(broadcaster_info)
                
            return result
        finally:
            session.close()
            
    def get_temp_time_records(self, chatroom_id: str, date: datetime.date = None) -> List[Dict]:
        """获取指定日期的临时时间设置记录"""
        return self._execute_in_queue(self._get_temp_time_records, chatroom_id, date)
    
    def _get_temp_time_records(self, chatroom_id: str, date: datetime.date = None) -> List[Dict]:
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 查询指定日期的临时时间设置
            temp_records = session.query(BroadcastTempTime).filter(
                BroadcastTempTime.chatroom_id == chatroom_id,
                BroadcastTempTime.temp_date == date
            ).all()
            
            # 如果没有临时时间设置，返回空列表
            if not temp_records:
                return []
                
            # 构建结果
            result = []
            for temp in temp_records:
                # 获取主播的注册信息
                broadcaster = session.query(BroadcasterInfo).filter(
                    BroadcasterInfo.chatroom_id == chatroom_id,
                    BroadcasterInfo.broadcaster_wxid == temp.broadcaster_wxid
                ).first()
                
                if broadcaster:
                    temp_dict = {
                        "broadcast_name": temp.broadcast_name,
                        "broadcaster_wxid": temp.broadcaster_wxid,
                        "broadcaster_nickname": broadcaster.broadcaster_nickname,
                        "temp_time_start": temp.broadcast_time_start,
                        "temp_time_end": temp.broadcast_time_end,
                        "set_date": temp.temp_date
                    }
                    result.append(temp_dict)
            
            return result
        finally:
            session.close()
    
    def start_broadcast(self, broadcast_name: str, broadcaster_wxid: str, 
                       broadcaster_nickname: str, chatroom_id: str) -> Tuple[bool, Dict]:
        """记录开播"""
        return self._execute_in_queue(self._start_broadcast, broadcast_name, broadcaster_wxid,
                                     broadcaster_nickname, chatroom_id)
    
    def _start_broadcast(self, broadcast_name: str, broadcaster_wxid: str, 
                        broadcaster_nickname: str, chatroom_id: str) -> Tuple[bool, Dict]:
        session = self.DBSession()
        try:
            # 获取当前时间
            now = datetime.datetime.now()
            today = datetime.date.today()
            current_time = now.time()
            
            # 检查今日是否已经开播
            active_record = session.query(BroadcastRecord).filter(
                BroadcastRecord.chatroom_id == chatroom_id,
                BroadcastRecord.broadcaster_wxid == broadcaster_wxid,
                BroadcastRecord.broadcast_date == today,
                BroadcastRecord.is_active == True
            ).first()
            
            if active_record:
                # 已经开播，返回错误
                return False, {
                    "already_started": True,
                    "broadcast_name": active_record.broadcast_name,
                    "start_time": active_record.start_time
                }
            
            # 查询主播信息
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            if not broadcaster:
                # 主播信息不存在，返回错误
                return False, {
                    "error": "主播信息不存在，请先设置开播时间"
                }
            
            # 查询所有时间段
            time_slots = session.query(BroadcasterTimeSlot).filter(
                BroadcasterTimeSlot.broadcaster_id == broadcaster.id
            ).all()
            
            if not time_slots:
                # 没有设置时间段，返回错误
                return False, {
                    "error": "没有设置开播时间段，请先设置开播时间"
                }
            
            # 查询是否有临时时间设置
            temp_time = session.query(BroadcastTempTime).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid,
                temp_date=today
            ).first()
            
            # 优先使用临时时间
            is_temp_time = False
            if temp_time:
                broadcast_time_start = temp_time.broadcast_time_start
                broadcast_time_end = temp_time.broadcast_time_end
                is_temp_time = True
                time_slot_id = None  # 临时时间没有对应的时间段ID
            else:
                # 查找当前时间所在的时间段
                current_slot = None
                for slot in time_slots:
                    start_dt = datetime.datetime.combine(today, slot.broadcast_time_start)
                    end_dt = datetime.datetime.combine(today, slot.broadcast_time_end)
                    
                    # 处理跨天情况
                    if end_dt < start_dt:
                        end_dt += datetime.timedelta(days=1)
                    
                    # 当前时间在时间段内，或者是即将开始的时间段（30分钟内）
                    if (start_dt <= now <= end_dt) or (start_dt > now and (start_dt - now).total_seconds() <= 1800):
                        current_slot = slot
                        break
                
                # 如果没有找到匹配的时间段，使用最近的一个时间段
                if not current_slot and time_slots:
                    # 按开始时间排序
                    sorted_slots = sorted(time_slots, key=lambda x: x.broadcast_time_start)
                    
                    # 找到今天最近的时间段
                    closest_slot = None
                    min_diff = float('inf')
                    
                    for slot in sorted_slots:
                        slot_start = datetime.datetime.combine(today, slot.broadcast_time_start)
                        diff = abs((slot_start - now).total_seconds())
                        
                        if diff < min_diff:
                            min_diff = diff
                            closest_slot = slot
                    
                    current_slot = closest_slot
                
                if current_slot:
                    broadcast_time_start = current_slot.broadcast_time_start
                    broadcast_time_end = current_slot.broadcast_time_end
                    time_slot_id = current_slot.id
                else:
                    # 没有找到合适的时间段，返回错误
                    return False, {
                        "error": "没有找到当前时间对应的开播时间段"
                    }
            
            # 检查是否迟到
            is_late = False
            late_minutes = 0
            if current_time > broadcast_time_start:
                is_late = True
                late_datetime = datetime.datetime.combine(today, current_time)
                scheduled_datetime = datetime.datetime.combine(today, broadcast_time_start)
                
                # 处理跨天的情况
                if late_datetime < scheduled_datetime:
                    scheduled_datetime -= datetime.timedelta(days=1)
                
                late_minutes = int((late_datetime - scheduled_datetime).total_seconds() / 60)
            
            # 计算所有时间段的总时长作为要求时长
            total_required_minutes = 0
            for slot in time_slots:
                start_dt = datetime.datetime.combine(today, slot.broadcast_time_start)
                end_dt = datetime.datetime.combine(today, slot.broadcast_time_end)
                
                # 处理跨天的情况
                if end_dt < start_dt:
                    end_dt += datetime.timedelta(days=1)
                
                duration = int((end_dt - start_dt).total_seconds() / 60)
                total_required_minutes += duration
            
            # 创建开播记录
            record = BroadcastRecord(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid,
                broadcaster_nickname=broadcaster_nickname,
                broadcast_name=broadcast_name,
                start_time=current_time,
                broadcast_date=today,
                is_late=is_late,
                late_minutes=late_minutes,
                is_active=True,
                broadcast_time_start=broadcast_time_start,
                broadcast_time_end=broadcast_time_end,
                is_temp_time=is_temp_time,
                time_slot_id=time_slot_id
            )
            
            session.add(record)
            session.commit()
            
            # 返回成功信息
            return True, {
                "broadcast_name": broadcast_name,
                "start_time": current_time,
                "is_late": is_late,
                "late_minutes": late_minutes,
                "broadcast_time_start": broadcast_time_start,
                "broadcast_time_end": broadcast_time_end,
                "is_temp_time": is_temp_time,
                "time_slot_id": time_slot_id,
                "required_duration": total_required_minutes
            }
            
        except Exception as e:
            logger.error(f"记录开播失败: {str(e)}")
            session.rollback()
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def end_broadcast(self, broadcaster_wxid: str, chatroom_id: str) -> Tuple[bool, Dict]:
        """记录下播"""
        return self._execute_in_queue(self._end_broadcast, broadcaster_wxid, chatroom_id)
    
    def _end_broadcast(self, broadcaster_wxid: str, chatroom_id: str) -> Tuple[bool, Dict]:
        session = self.DBSession()
        try:
            # 获取当前时间
            now = datetime.datetime.now()
            today = datetime.date.today()
            current_time = now.time()
            
            # 查询当前开播记录
            record = session.query(BroadcastRecord).filter(
                BroadcastRecord.chatroom_id == chatroom_id,
                BroadcastRecord.broadcaster_wxid == broadcaster_wxid,
                BroadcastRecord.broadcast_date == today,
                BroadcastRecord.is_active == True
            ).first()
            
            if not record:
                # 没有开播记录，返回错误
                return False, {
                    "not_started": True,
                    "error": "没有找到开播记录，请先开播"
                }
            
            # 计算开播时长
            start_datetime = datetime.datetime.combine(today, record.start_time)
            end_datetime = datetime.datetime.combine(today, current_time)
            
            # 如果结束时间小于开始时间，可能跨天了，加一天
            if end_datetime < start_datetime:
                end_datetime = end_datetime + datetime.timedelta(days=1)
                
            duration = int((end_datetime - start_datetime).total_seconds() / 60)
            
            # 更新记录
            record.end_time = current_time
            record.duration = duration
            record.is_active = False
            
            # 获取主播信息
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            # 获取该主播的所有时间段
            total_required_duration = 0
            if broadcaster:
                time_slots = session.query(BroadcasterTimeSlot).filter(
                    BroadcasterTimeSlot.broadcaster_id == broadcaster.id
                ).all()
                
                # 计算所有时间段的总时长
                for slot in time_slots:
                    start_dt = datetime.datetime.combine(today, slot.broadcast_time_start)
                    end_dt = datetime.datetime.combine(today, slot.broadcast_time_end)
                    
                    # 处理跨天的情况
                    if end_dt < start_dt:
                        end_dt += datetime.timedelta(days=1)
                        
                    slot_duration = int((end_dt - start_dt).total_seconds() / 60)
                    total_required_duration += slot_duration
            
            # 今日总开播时长
            today_duration = self._get_today_total_duration(chatroom_id, broadcaster_wxid, today) + duration
            
            # 检查是否提前下播
            is_early = False
            early_minutes = 0
            if current_time < record.broadcast_time_end:
                is_early = True
                scheduled_end = datetime.datetime.combine(today, record.broadcast_time_end)
                actual_end = datetime.datetime.combine(today, current_time)
                # 如果结束时间小于设定结束，可能跨天了，加一天
                if actual_end.time() < record.start_time:
                    actual_end = actual_end + datetime.timedelta(days=1)
                early_minutes = int((scheduled_end - actual_end).total_seconds() / 60)
            
            # 计算是否满足要求时长（使用主播所有时间段累计时长）
            is_meet_required = today_duration >= total_required_duration
            remain_minutes = max(0, total_required_duration - today_duration)
            
            session.commit()
            
            # 返回成功信息
            return True, {
                "broadcast_name": record.broadcast_name,
                "start_time": record.start_time,
                "end_time": current_time,
                "duration": duration,
                "today_duration": today_duration,
                "is_early": is_early,
                "early_minutes": early_minutes,
                "is_meet_required": is_meet_required,
                "remain_minutes": remain_minutes,
                "required_duration": total_required_duration,
                "time_slot_id": record.time_slot_id
            }
            
        except Exception as e:
            logger.error(f"记录下播失败: {str(e)}")
            session.rollback()
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def get_today_total_duration(self, chatroom_id: str, broadcaster_wxid: str, 
                               date: datetime.date = None) -> int:
        """获取指定日期的总开播时长"""
        return self._execute_in_queue(self._get_today_total_duration, 
                                     chatroom_id, broadcaster_wxid, date)
    
    def _get_today_total_duration(self, chatroom_id: str, broadcaster_wxid: str, 
                                date: datetime.date = None) -> int:
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 查询指定日期的所有记录
            records = session.query(BroadcastRecord).filter(
                BroadcastRecord.chatroom_id == chatroom_id,
                BroadcastRecord.broadcaster_wxid == broadcaster_wxid,
                BroadcastRecord.broadcast_date == date
            ).all()
            
            # 计算总时长
            total_duration = sum(record.duration or 0 for record in records if not record.is_active)
            
            return total_duration
        finally:
            session.close()
    
    def get_broadcast_records(self, chatroom_id: str, broadcast_name: str = None, 
                             date: datetime.date = None) -> List[Dict]:
        """获取指定日期的开播记录"""
        return self._execute_in_queue(self._get_broadcast_records, chatroom_id, broadcast_name, date)
    
    def _get_broadcast_records(self, chatroom_id: str, broadcast_name: str = None, 
                              date: datetime.date = None) -> List[Dict]:
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 构建查询
            query = session.query(BroadcastRecord).filter(
                BroadcastRecord.chatroom_id == chatroom_id,
                BroadcastRecord.broadcast_date == date
            )
            
            # 如果指定了主播名，则添加过滤条件
            if broadcast_name:
                query = query.filter(BroadcastRecord.broadcast_name == broadcast_name)
                
            # 执行查询
            records = query.all()
            
            # 转换为字典列表
            result = []
            for record in records:
                record_dict = {
                    "broadcast_name": record.broadcast_name,
                    "broadcaster_nickname": record.broadcaster_nickname,
                    "start_time": record.start_time,
                    "end_time": record.end_time,
                    "duration": record.duration,
                    "is_late": record.is_late,
                    "late_minutes": record.late_minutes,
                    "is_active": record.is_active
                }
                result.append(record_dict)
                
            return result
        finally:
            session.close()
            
    def get_enabled_groups(self) -> List[Dict]:
        """获取所有启用了开播记录的群组"""
        return self._execute_in_queue(self._get_enabled_groups)
    
    def _get_enabled_groups(self) -> List[Dict]:
        session = self.DBSession()
        try:
            # 查询所有启用了开播记录的群组
            settings = session.query(BroadcastSetting).filter(
                BroadcastSetting.enabled == True
            ).all()
            
            result = []
            for setting in settings:
                result.append({
                    "chatroom_id": setting.chatroom_id,
                    "required_duration": setting.required_duration
                })
            
            return result
        finally:
            session.close()
    
    def get_broadcaster_time_slots(self, chatroom_id: str, broadcaster_wxid: str) -> List[Dict]:
        """获取主播的所有时间段(按开始时间排序)"""
        return self._execute_in_queue(self._get_broadcaster_time_slots_sorted, chatroom_id, broadcaster_wxid)

    def _get_broadcaster_time_slots_sorted(self, chatroom_id: str, broadcaster_wxid: str) -> List[Dict]:
        """获取主播的所有时间段，按开始时间排序"""
        session = self.DBSession()
        try:
            # 查询主播信息
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            if not broadcaster:
                return []
            
            # 查询所有时间段
            time_slots = session.query(BroadcasterTimeSlot).filter(
                BroadcasterTimeSlot.broadcaster_id == broadcaster.id
            ).all()
            
            # 转换为字典并按开始时间排序
            result = [{
                'id': slot.id,
                'broadcaster_id': slot.broadcaster_id,
                'broadcast_time_start': slot.broadcast_time_start,
                'broadcast_time_end': slot.broadcast_time_end,
                'slot_name': slot.slot_name
            } for slot in time_slots]
            
            # 按开始时间排序
            result.sort(key=lambda x: x['broadcast_time_start'])
            
            return result
        finally:
            session.close()

    def check_time_slots_broadcast_status(self, chatroom_id: str, broadcaster_wxid: str, date: datetime.date = None) -> List[Dict]:
        """检查主播所有时间段的开播状态"""
        return self._execute_in_queue(self._check_time_slots_broadcast_status, chatroom_id, broadcaster_wxid, date)

    def _check_time_slots_broadcast_status(self, chatroom_id: str, broadcaster_wxid: str, date: datetime.date = None) -> List[Dict]:
        """检查主播所有时间段的开播状态"""
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
            
            # 获取主播信息
            broadcaster = session.query(BroadcasterInfo).filter_by(
                chatroom_id=chatroom_id,
                broadcaster_wxid=broadcaster_wxid
            ).first()
            
            if not broadcaster:
                return []
            
            # 获取主播的所有时间段，按开始时间排序
            time_slots = session.query(BroadcasterTimeSlot).filter(
                BroadcasterTimeSlot.broadcaster_id == broadcaster.id
            ).order_by(BroadcasterTimeSlot.broadcast_time_start).all()
            
            # 查询该日的所有开播记录
            records = session.query(BroadcastRecord).filter(
                BroadcastRecord.chatroom_id == chatroom_id,
                BroadcastRecord.broadcaster_wxid == broadcaster_wxid,
                BroadcastRecord.broadcast_date == date
            ).all()
            
            # 检查每个时间段是否有对应的开播记录
            result = []
            for slot in time_slots:
                slot_status = {
                    'id': slot.id,
                    'broadcaster_id': slot.broadcaster_id,
                    'broadcast_time_start': slot.broadcast_time_start,
                    'broadcast_time_end': slot.broadcast_time_end,
                    'slot_name': slot.slot_name,
                    'is_broadcasted': False,
                    'start_time': None,
                    'end_time': None,
                    'duration': None,
                    'is_late': False,
                    'late_minutes': 0,
                    'is_active': False,
                    'current_slot': False
                }
                
                # 检查当前时间是否处于该时间段
                now = datetime.datetime.now().time()
                today = datetime.datetime.now().date()
                
                if today == date:  # 只有当查询今天的记录时才检查是否是当前时间段
                    start_dt = datetime.datetime.combine(date, slot.broadcast_time_start)
                    end_dt = datetime.datetime.combine(date, slot.broadcast_time_end)
                    now_dt = datetime.datetime.combine(date, now)
                    
                    # 处理跨天的情况
                    if end_dt < start_dt:
                        end_dt += datetime.timedelta(days=1)
                    
                    if start_dt <= now_dt <= end_dt:
                        slot_status['current_slot'] = True
                
                # 查找对应该时间段的记录
                for record in records:
                    # 检查记录是否对应该时间段
                    if record.time_slot_id == slot.id:
                        slot_status['is_broadcasted'] = True
                        slot_status['start_time'] = record.start_time
                        slot_status['end_time'] = record.end_time
                        slot_status['duration'] = record.duration
                        slot_status['is_late'] = record.is_late
                        slot_status['late_minutes'] = record.late_minutes
                        slot_status['is_active'] = record.is_active
                        break
                
                result.append(slot_status)
            
            return result
        finally:
            session.close()
    
    def __del__(self):
        """析构函数，关闭线程池"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False)