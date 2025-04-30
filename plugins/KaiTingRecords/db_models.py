import tomllib
import datetime
from typing import List, Optional, Dict, Tuple, Union

from loguru import logger
from sqlalchemy import Column, String, Integer, DateTime, Boolean, create_engine, JSON, Float, Time, Date, inspect, text
from sqlalchemy import update, select, func, and_, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from concurrent.futures import ThreadPoolExecutor

from utils.singleton import Singleton

Base = declarative_base()


class HallSetting(Base):
    """厅设置表"""
    __tablename__ = 'hall_setting'

    chatroom_id = Column(String(30), primary_key=True, nullable=False, comment='群聊ID')
    enabled = Column(Boolean, default=False, nullable=False, comment='是否启用开厅记录')
    required_duration = Column(Integer, default=240, nullable=False, comment='要求开厅时长(分钟)')


class HallRegistration(Base):
    """厅注册信息表"""
    __tablename__ = 'hall_registration'

    id = Column(Integer, primary_key=True, autoincrement=True)
    hall_id = Column(String(4), nullable=True, index=True, comment='厅唯一ID编号')
    hall_name = Column(String(50), nullable=False, index=True, comment='厅名')
    owner_wxid = Column(String(30), nullable=False, index=True, comment='厅主wxid')
    owner_nickname = Column(String(50), default='', comment='厅主昵称')
    chatroom_id = Column(String(30), nullable=False, index=True, comment='所属群聊ID')
    register_time = Column(DateTime, default=datetime.datetime.now, nullable=False, comment='注册时间')
    open_time_start = Column(Time, default=datetime.time(18, 0), nullable=False, comment='默认开厅开始时间')
    open_time_end = Column(Time, default=datetime.time(22, 0), nullable=False, comment='默认开厅结束时间')


class HallTempTime(Base):
    """厅临时时间表"""
    __tablename__ = 'hall_temp_time'

    id = Column(Integer, primary_key=True, autoincrement=True)
    hall_name = Column(String(50), nullable=False, index=True, comment='厅名')
    chatroom_id = Column(String(30), nullable=False, index=True, comment='所属群聊ID')
    temp_date = Column(Date, nullable=False, index=True, comment='临时时间日期')
    open_time_start = Column(Time, nullable=False, comment='临时开厅开始时间')
    open_time_end = Column(Time, nullable=False, comment='临时开厅结束时间')


class HallRecord(Base):
    """厅开关记录表"""
    __tablename__ = 'hall_record'

    id = Column(Integer, primary_key=True, autoincrement=True)
    hall_name = Column(String(50), nullable=False, index=True, comment='厅名')
    owner_wxid = Column(String(30), nullable=False, index=True, comment='厅主wxid')
    owner_nickname = Column(String(50), default='', comment='厅主昵称')
    chatroom_id = Column(String(30), nullable=False, index=True, comment='所属群聊ID')
    open_date = Column(Date, nullable=False, index=True, comment='开厅日期')
    open_time = Column(Time, nullable=False, comment='开厅时间')
    close_time = Column(Time, nullable=True, comment='关厅时间')
    duration = Column(Integer, default=0, comment='开厅时长(分钟)')
    is_late = Column(Boolean, default=False, comment='是否迟到')
    late_minutes = Column(Integer, default=0, comment='迟到分钟数')
    is_active = Column(Boolean, default=True, comment='是否处于开厅状态')
    is_cross_day = Column(Boolean, default=False, comment='是否跨天')


class KaiTingDB(metaclass=Singleton):
    """开厅记录数据库管理类"""
    
    # 日志前缀
    LOG_PREFIX = "【开厅记录DB】"
    
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
        
        # 检查数据库字段更新
        self._check_and_update_schema()
        
        self.log_success("开厅记录数据库初始化成功")
        
        # 创建线程池执行器
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kaiting_db")
    
    def _check_and_update_schema(self):
        """检查并更新数据库结构"""
        try:
            # 获取检查器
            inspector = inspect(self.engine)
            
            # 检查hall_record表中是否存在is_cross_day列
            columns = [col['name'] for col in inspector.get_columns('hall_record')]
            if 'is_cross_day' not in columns:
                self.log_info("检测到数据库结构需要更新: 添加is_cross_day字段")
                
                # 使用更可靠的方式更新表结构
                try:
                    # 方法1: 直接使用ALTER TABLE语句
                    with self.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE hall_record ADD COLUMN is_cross_day BOOLEAN DEFAULT 0"))
                        conn.commit()
                    self.log_success("数据库结构更新成功: 已添加is_cross_day字段(方法1)")
                except Exception as e1:
                    self.log_warning(f"使用ALTER TABLE添加列失败: {e1}")
                    
                    try:
                        # 方法2: 使用表复制的方式添加新列
                        self.log_info("尝试使用表复制方式添加新列...")
                        # 创建连接并开启事务
                        conn = self.engine.connect()
                        trans = conn.begin()
                        
                        try:
                            # 2.1 获取所有现有数据
                            result = conn.execute(text("SELECT * FROM hall_record"))
                            records = result.fetchall()
                            column_names = result.keys()
                            
                            # 2.2 重命名旧表
                            conn.execute(text("ALTER TABLE hall_record RENAME TO hall_record_old"))
                            
                            # 2.3 通过原始SQL创建表
                            conn.execute(text("""
                            CREATE TABLE hall_record (
                                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                                hall_name VARCHAR(50) NOT NULL,
                                owner_wxid VARCHAR(30) NOT NULL,
                                owner_nickname VARCHAR(50) DEFAULT '',
                                chatroom_id VARCHAR(30) NOT NULL,
                                open_date DATE NOT NULL,
                                open_time TIME NOT NULL,
                                close_time TIME,
                                duration INTEGER DEFAULT 0,
                                is_late BOOLEAN DEFAULT 0,
                                late_minutes INTEGER DEFAULT 0,
                                is_active BOOLEAN DEFAULT 1,
                                is_cross_day BOOLEAN DEFAULT 0
                            )
                            """))
                            
                            # 2.4 创建索引
                            conn.execute(text("CREATE INDEX ix_hall_record_hall_name ON hall_record (hall_name)"))
                            conn.execute(text("CREATE INDEX ix_hall_record_owner_wxid ON hall_record (owner_wxid)"))
                            conn.execute(text("CREATE INDEX ix_hall_record_chatroom_id ON hall_record (chatroom_id)"))
                            conn.execute(text("CREATE INDEX ix_hall_record_open_date ON hall_record (open_date)"))
                            
                            # 2.5 复制数据
                            if records:
                                placeholders = ", ".join(["?"] * len(column_names))
                                insert_sql = f"INSERT INTO hall_record ({', '.join(column_names)}) VALUES ({placeholders})"
                                
                                for record in records:
                                    conn.execute(text(insert_sql), record)
                            
                            # 2.6 删除旧表
                            conn.execute(text("DROP TABLE hall_record_old"))
                            
                            # 提交事务
                            trans.commit()
                            self.log_success("数据库结构更新成功: 已添加is_cross_day字段(方法2)")
                        
                        except Exception as e:
                            trans.rollback()
                            self.log_error(f"表复制方式添加列失败: {e}")
                            raise
                    
                    except Exception as e2:
                        self.log_error(f"所有方法都失败，无法更新数据库结构: {e2}")
                        # 继续执行程序，允许其他操作进行，但部分功能可能不可用
                        self.log_warning("数据库结构更新失败，部分功能可能不可用")
            
            # 检查hall_registration表中是否存在hall_id列
            hall_reg_columns = [col['name'] for col in inspector.get_columns('hall_registration')]
            if 'hall_id' not in hall_reg_columns:
                self.log_info("检测到数据库结构需要更新: 添加hall_id字段")
                
                try:
                    # 方法1: 直接使用ALTER TABLE语句
                    with self.engine.connect() as conn:
                        conn.execute(text("ALTER TABLE hall_registration ADD COLUMN hall_id VARCHAR(4)"))
                        conn.commit()
                    self.log_success("数据库结构更新成功: 已添加hall_id字段")
                    
                    # 为现有记录生成唯一ID
                    self._generate_hall_ids_for_existing_records()
                except Exception as e:
                    self.log_error(f"使用ALTER TABLE添加hall_id列失败: {e}")
                    self.log_warning("无法为厅添加唯一ID，部分功能可能不可用")
                    
        except Exception as e:
            self.log_error(f"检查/更新数据库结构失败: {str(e)}")
    
    def _generate_hall_ids_for_existing_records(self):
        """为现有的厅记录生成唯一ID"""
        try:
            self.log_info("开始为现有厅记录生成唯一ID...")
            session = self.DBSession()
            
            # 获取所有没有hall_id的记录
            halls = session.query(HallRegistration).filter(
                or_(
                    HallRegistration.hall_id == None,
                    HallRegistration.hall_id == ""
                )
            ).all()
            
            if not halls:
                self.log_info("没有需要生成ID的厅记录")
                return
                
            self.log_info(f"找到 {len(halls)} 条需要生成ID的厅记录")
            
            import random
            updated_count = 0
            
            for hall in halls:
                # 生成一个随机的4位数ID
                while True:
                    hall_id = f"{random.randint(1000, 9999)}"
                    
                    # 检查ID是否已存在
                    exists = session.query(HallRegistration).filter(
                        HallRegistration.hall_id == hall_id,
                        HallRegistration.id != hall.id  # 排除当前记录
                    ).first()
                    
                    if not exists:
                        break
                
                # 更新记录
                hall.hall_id = hall_id
                updated_count += 1
            
            # 提交更改
            session.commit()
            self.log_success(f"成功为 {updated_count} 条厅记录生成了唯一ID")
            
            # 添加唯一索引
            with self.engine.connect() as conn:
                try:
                    # 先检查是否已存在索引
                    indices = inspector.get_indexes('hall_registration')
                    has_index = any(idx.get('name') == 'ix_hall_registration_hall_id' for idx in indices)
                    
                    if not has_index:
                        conn.execute(text("CREATE UNIQUE INDEX ix_hall_registration_hall_id ON hall_registration (hall_id)"))
                        conn.commit()
                        self.log_success("成功为hall_id创建唯一索引")
                except Exception as e:
                    self.log_error(f"为hall_id创建唯一索引失败: {e}")
            
        except Exception as e:
            self.log_error(f"生成厅记录唯一ID失败: {e}")
        finally:
            session.close()
    
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
    
    # 群聊开厅设置相关方法
    
    def enable_group_kaiting(self, chatroom_id: str) -> bool:
        """启用群聊开厅记录功能"""
        return self._execute_in_queue(self._enable_group_kaiting, chatroom_id)
    
    def _enable_group_kaiting(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(HallSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.enabled = True
            else:
                setting = HallSetting(chatroom_id=chatroom_id, enabled=True)
                session.add(setting)
            
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"启用群聊{chatroom_id}开厅记录功能失败: {e}")
            return False
        finally:
            session.close()
    
    def disable_group_kaiting(self, chatroom_id: str) -> bool:
        """禁用群聊开厅记录功能"""
        return self._execute_in_queue(self._disable_group_kaiting, chatroom_id)
    
    def _disable_group_kaiting(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(HallSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.enabled = False
                session.commit()
                return True
            return False
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"禁用群聊{chatroom_id}开厅记录功能失败: {e}")
            return False
        finally:
            session.close()
    
    def is_group_kaiting_enabled(self, chatroom_id: str) -> bool:
        """检查群聊是否启用开厅记录功能"""
        return self._execute_in_queue(self._is_group_kaiting_enabled, chatroom_id)
    
    def _is_group_kaiting_enabled(self, chatroom_id: str) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(HallSetting).filter_by(chatroom_id=chatroom_id).first()
            return setting.enabled if setting else False
        finally:
            session.close()
    
    def set_required_duration(self, chatroom_id: str, duration: int) -> bool:
        """设置群聊要求开厅时长"""
        return self._execute_in_queue(self._set_required_duration, chatroom_id, duration)
    
    def _set_required_duration(self, chatroom_id: str, duration: int) -> bool:
        session = self.DBSession()
        try:
            setting = session.query(HallSetting).filter_by(chatroom_id=chatroom_id).first()
            if setting:
                setting.required_duration = duration
            else:
                setting = HallSetting(
                    chatroom_id=chatroom_id,
                    enabled=True,
                    required_duration=duration
                )
                session.add(setting)
            
            session.commit()
            return True
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"设置群聊{chatroom_id}要求开厅时长失败: {e}")
            return False
        finally:
            session.close()
    
    def get_required_duration(self, chatroom_id: str) -> int:
        """获取群聊要求开厅时长"""
        return self._execute_in_queue(self._get_required_duration, chatroom_id)
    
    def _get_required_duration(self, chatroom_id: str) -> int:
        session = self.DBSession()
        try:
            setting = session.query(HallSetting).filter_by(chatroom_id=chatroom_id).first()
            return setting.required_duration if setting else 240  # 默认4小时
        finally:
            session.close()
    
    # 厅注册相关方法
    
    def register_hall(self, hall_name: str, owner_wxid: str, owner_nickname: str, 
                     chatroom_id: str, open_time_start: datetime.time = None, 
                     open_time_end: datetime.time = None) -> Tuple[bool, str]:
        """注册厅"""
        return self._execute_in_queue(self._register_hall, hall_name, owner_wxid, 
                                     owner_nickname, chatroom_id, open_time_start, open_time_end)
    
    def _register_hall(self, hall_name: str, owner_wxid: str, owner_nickname: str, 
                      chatroom_id: str, open_time_start: datetime.time = None, 
                      open_time_end: datetime.time = None) -> Tuple[bool, str]:
        """注册新厅"""
        try:
            # 检查厅名是否已经存在
            session = self.DBSession()
            
            # 检查厅名是否已经存在
            exists = session.query(HallRegistration).filter(
                HallRegistration.hall_name == hall_name,
                HallRegistration.chatroom_id == chatroom_id
            ).first()
            
            if exists:
                return False, "该厅名已被注册"
                
            # 生成厅唯一ID
            # 生成一个随机的4位数ID，确保不重复
            import random
            while True:
                # 生成随机4位数ID
                hall_id = f"{random.randint(1000, 9999)}"
                
                # 检查ID是否已存在
                exists_id = session.query(HallRegistration).filter(
                    HallRegistration.hall_id == hall_id
                ).first()
                
                if not exists_id:
                    break
            
            # 设置开厅时间
            if not open_time_start:
                open_time_start = datetime.time(18, 0)  # 默认18:00
            if not open_time_end:
                open_time_end = datetime.time(22, 0)  # 默认22:00
            
            # 创建记录
            new_hall = HallRegistration(
                hall_id=hall_id,
                hall_name=hall_name,
                owner_wxid=owner_wxid,
                owner_nickname=owner_nickname,
                chatroom_id=chatroom_id,
                open_time_start=open_time_start,
                open_time_end=open_time_end
            )
            
            session.add(new_hall)
            session.commit()
            
            return True, hall_id
        except SQLAlchemyError as e:
            self.log_error(f"注册厅名失败: {e}")
            return False, f"数据库错误: {str(e)}"
        finally:
            session.close()
    
    def set_hall_time(self, hall_name: str, chatroom_id: str, 
                     open_time_start: datetime.time, open_time_end: datetime.time) -> Tuple[bool, str]:
        """设置厅开厅时间"""
        return self._execute_in_queue(self._set_hall_time, hall_name, chatroom_id, 
                                     open_time_start, open_time_end)
    
    def _set_hall_time(self, hall_name: str, chatroom_id: str, 
                      open_time_start: datetime.time, open_time_end: datetime.time) -> Tuple[bool, str]:
        session = self.DBSession()
        try:
            hall = session.query(HallRegistration).filter(
                HallRegistration.chatroom_id == chatroom_id,
                HallRegistration.hall_name == hall_name
            ).first()
            
            if not hall:
                return False, f"未找到厅名 '{hall_name}'"
                
            hall.open_time_start = open_time_start
            hall.open_time_end = open_time_end
            
            session.commit()
            return True, f"厅名 '{hall_name}' 开厅时间设置成功"
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"设置厅开厅时间失败: {e}")
            return False, f"设置厅开厅时间失败: {str(e)}"
        finally:
            session.close()
    
    def set_temp_time(self, hall_name: str, chatroom_id: str, 
                     open_time_start: datetime.time, open_time_end: datetime.time, 
                     temp_date: datetime.date = None) -> Tuple[bool, str]:
        """设置厅临时开厅时间"""
        return self._execute_in_queue(self._set_temp_time, hall_name, chatroom_id, 
                                     open_time_start, open_time_end, temp_date)
    
    def _set_temp_time(self, hall_name: str, chatroom_id: str, 
                      open_time_start: datetime.time, open_time_end: datetime.time, 
                      temp_date: datetime.date = None) -> Tuple[bool, str]:
        session = self.DBSession()
        try:
            # 验证厅存在
            hall = session.query(HallRegistration).filter(
                HallRegistration.chatroom_id == chatroom_id,
                HallRegistration.hall_name == hall_name
            ).first()
            
            if not hall:
                return False, f"未找到厅名 '{hall_name}'"
            
            # 设置当天日期
            if not temp_date:
                temp_date = datetime.date.today()
                
            # 检查是否已有临时时间设置
            temp = session.query(HallTempTime).filter(
                HallTempTime.chatroom_id == chatroom_id,
                HallTempTime.hall_name == hall_name,
                HallTempTime.temp_date == temp_date
            ).first()
            
            if temp:
                # 更新现有临时时间
                temp.open_time_start = open_time_start
                temp.open_time_end = open_time_end
            else:
                # 创建新临时时间
                temp = HallTempTime(
                    hall_name=hall_name,
                    chatroom_id=chatroom_id,
                    temp_date=temp_date,
                    open_time_start=open_time_start,
                    open_time_end=open_time_end
                )
                session.add(temp)
                
            session.commit()
            return True, f"厅名 '{hall_name}' 临时开厅时间设置成功"
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"设置厅临时开厅时间失败: {e}")
            return False, f"设置厅临时开厅时间失败: {str(e)}"
        finally:
            session.close()
    
    def get_hall_by_owner(self, chatroom_id: str, owner_wxid: str) -> Optional[Dict]:
        """根据用户获取厅信息"""
        return self._execute_in_queue(self._get_hall_by_owner, chatroom_id, owner_wxid)
    
    def _get_hall_by_owner(self, chatroom_id: str, owner_wxid: str) -> Optional[Dict]:
        session = self.DBSession()
        try:
            hall = session.query(HallRegistration).filter(
                HallRegistration.chatroom_id == chatroom_id,
                HallRegistration.owner_wxid == owner_wxid
            ).first()
            
            if not hall:
                return None
                
            # 查询当天是否有临时时间设置
            today = datetime.date.today()
            temp = session.query(HallTempTime).filter(
                HallTempTime.chatroom_id == chatroom_id,
                HallTempTime.hall_name == hall.hall_name,
                HallTempTime.temp_date == today
            ).first()
            
            # 返回厅信息
            result = {
                "hall_name": hall.hall_name,
                "owner_wxid": hall.owner_wxid,
                "owner_nickname": hall.owner_nickname,
                "register_time": hall.register_time,
                "open_time_start": temp.open_time_start if temp else hall.open_time_start,
                "open_time_end": temp.open_time_end if temp else hall.open_time_end,
                "is_temp_time": temp is not None
            }
            
            return result
        finally:
            session.close()
    
    def get_hall_by_name(self, chatroom_id: str, hall_name: str) -> Optional[Dict]:
        """根据厅名获取厅信息"""
        return self._execute_in_queue(self._get_hall_by_name, chatroom_id, hall_name)
    
    def _get_hall_by_name(self, chatroom_id: str, hall_name: str) -> Optional[Dict]:
        """根据厅名获取厅信息"""
        try:
            session = self.DBSession()
            
            # 查询厅注册信息
            hall = session.query(HallRegistration).filter(
                HallRegistration.chatroom_id == chatroom_id,
                HallRegistration.hall_name == hall_name
            ).first()
            
            if not hall:
                return None
                
            # 查询是否有活跃记录
            active_record = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.hall_name == hall_name,
                HallRecord.is_active == True
            ).first()
            
            # 查询今日临时时间设置
            today = datetime.date.today()
            temp_time = session.query(HallTempTime).filter(
                HallTempTime.chatroom_id == chatroom_id,
                HallTempTime.hall_name == hall_name,
                HallTempTime.temp_date == today
            ).first()
            
            # 构建返回数据
            result = {
                "hall_id": hall.hall_id,
                "hall_name": hall.hall_name,
                "owner_wxid": hall.owner_wxid,
                "owner_nickname": hall.owner_nickname,
                "chatroom_id": hall.chatroom_id,
                "register_time": hall.register_time,
                "open_time_start": hall.open_time_start,
                "open_time_end": hall.open_time_end,
                "is_active": active_record is not None,
                "has_temp_time": temp_time is not None,
            }
            
            # 添加活跃记录信息
            if active_record:
                result.update({
                    "open_date": active_record.open_date,
                    "open_time": active_record.open_time,
                    "record_id": active_record.id,
                })
            
            # 添加临时时间信息
            if temp_time:
                result.update({
                    "temp_time_start": temp_time.open_time_start,
                    "temp_time_end": temp_time.open_time_end,
                    "temp_date": temp_time.temp_date,
                })
                
            return result
        except Exception as e:
            self.log_error(f"获取厅信息失败: {e}")
            return None
        finally:
            session.close()
    
    def delete_hall(self, chatroom_id: str, hall_name: str) -> Tuple[bool, str]:
        """删除厅名和相关数据
        
        Args:
            chatroom_id: 群聊ID
            hall_name: 厅名
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        return self._execute_in_queue(self._delete_hall, chatroom_id, hall_name)
    
    def _delete_hall(self, chatroom_id: str, hall_name: str) -> Tuple[bool, str]:
        """删除厅名和相关数据（内部方法）"""
        session = self.DBSession()
        try:
            # 先检查厅是否存在
            hall_reg = session.query(HallRegistration).filter_by(
                chatroom_id=chatroom_id,
                hall_name=hall_name
            ).first()
            
            if not hall_reg:
                return False, f"厅名 {hall_name} 不存在"
            
            # 检查是否正在开厅
            active_record = session.query(HallRecord).filter_by(
                chatroom_id=chatroom_id,
                hall_name=hall_name,
                is_active=True
            ).first()
            
            if active_record:
                return False, f"厅 {hall_name} 当前处于开厅状态，请先关厅后再删除"
            
            # 1. 删除临时时间设置
            temp_times = session.query(HallTempTime).filter_by(
                chatroom_id=chatroom_id,
                hall_name=hall_name
            ).all()
            
            for temp_time in temp_times:
                session.delete(temp_time)
            
            # 2. 删除开厅记录
            # 注意：我们保留历史记录，但将厅名标记为已删除
            # 这样可以保留开厅历史统计，但无法再用该厅名
            records = session.query(HallRecord).filter_by(
                chatroom_id=chatroom_id,
                hall_name=hall_name
            ).all()
            
            record_count = len(records)
            
            # 3. 删除厅注册信息
            session.delete(hall_reg)
            
            # 提交事务
            session.commit()
            return True, f"厅 {hall_name} 删除成功，保留了{record_count}条历史记录"
        
        except Exception as e:
            session.rollback()
            self.log_error(f"删除厅 {hall_name} 失败: {e}")
            return False, f"删除厅失败: {str(e)}"
        
        finally:
            session.close()
    
    # 开厅记录相关方法
    
    def open_hall(self, hall_name: str, owner_wxid: str, owner_nickname: str, 
                 chatroom_id: str) -> Tuple[bool, Dict]:
        """记录开厅"""
        return self._execute_in_queue(self._open_hall, hall_name, owner_wxid, 
                                     owner_nickname, chatroom_id)
    
    def _open_hall(self, hall_name: str, owner_wxid: str, owner_nickname: str, 
                  chatroom_id: str) -> Tuple[bool, Dict]:
        session = self.DBSession()
        try:
            # 检查用户是否有任何未关闭的厅（包括之前日期的）
            any_active_record = session.query(HallRecord).filter(
                HallRecord.owner_wxid == owner_wxid,
                HallRecord.is_active == True
            ).first()
            
            if any_active_record:
                # 用户有未关闭的厅，返回错误信息
                return False, {
                    "already_opened": True,
                    "hall_name": any_active_record.hall_name,
                    "open_time": any_active_record.open_time,
                    "open_date": any_active_record.open_date
                }
            
            # 检查今日是否已经开过厅
            today = datetime.date.today()
            existing_record = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.owner_wxid == owner_wxid,
                HallRecord.open_date == today
            ).first()
            
            if existing_record:
                # 如果已经关厅，则允许重新开厅
                if not existing_record.is_active:
                    # 将旧记录标记为非活跃
                    existing_record.is_active = False
                    session.commit()
                else:
                    # 如果还在开厅中，则返回已开厅信息
                    return False, {
                        "already_opened": True,
                        "hall_name": existing_record.hall_name,
                        "open_time": existing_record.open_time
                    }
            
            # 获取厅信息
            hall_info = self._get_hall_by_name(chatroom_id, hall_name)
            if not hall_info:
                return False, {"error": f"未找到厅信息"}
            
            # 获取当前时间
            now = datetime.datetime.now()
            current_time = now.time()
            
            # 检查是否在规定时间内
            open_time_start = hall_info["open_time_start"]
            open_time_end = hall_info["open_time_end"]
            
            # 计算是否迟到
            is_late = current_time > open_time_start
            late_minutes = 0
            
            if is_late:
                # 计算迟到分钟数
                today_date = datetime.date.today()
                start_datetime = datetime.datetime.combine(today_date, open_time_start)
                current_datetime = datetime.datetime.combine(today_date, current_time)
                
                if current_datetime > start_datetime:
                    late_minutes = int((current_datetime - start_datetime).total_seconds() / 60)
            
            # 创建开厅记录
            record = HallRecord(
                hall_name=hall_name,
                owner_wxid=owner_wxid,
                owner_nickname=owner_nickname,
                chatroom_id=chatroom_id,
                open_date=today,
                open_time=current_time,
                is_late=is_late,
                late_minutes=late_minutes,
                is_active=True
            )
            
            session.add(record)
            session.commit()
            
            # 获取记录ID
            record_id = record.id
            
            return True, {
                "hall_name": hall_name,
                "open_time": current_time,
                "is_late": is_late,
                "late_minutes": late_minutes,
                "open_time_start": hall_info["open_time_start"],
                "open_time_end": hall_info["open_time_end"],
                "is_temp_time": hall_info["is_temp_time"],
                "record_id": record_id
            }
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"记录开厅失败: {e}")
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def close_hall(self, chatroom_id: str, owner_wxid: str) -> Tuple[bool, Dict]:
        """记录关厅"""
        return self._execute_in_queue(self._close_hall, chatroom_id, owner_wxid)
    
    def _close_hall(self, chatroom_id: str, owner_wxid: str) -> Tuple[bool, Dict]:
        """记录关厅 (内部方法)"""
        session = self.DBSession()
        try:
            # 查找该用户最新的开厅记录
            today = datetime.date.today()
            record = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.owner_wxid == owner_wxid,
                HallRecord.open_date == today,
                HallRecord.is_active == True
            ).first()
            
            if not record:
                return False, {"not_opened": True, "error": "您当前没有进行中的开厅记录"}
            
            # 获取基本信息
            hall_name = record.hall_name
            open_time = record.open_time
            
            # 记录关厅时间
            now = datetime.datetime.now()
            close_time = now.time()
            record.close_time = close_time
            record.is_active = False
            
            # 计算开厅时长
            open_datetime = datetime.datetime.combine(record.open_date, open_time)
            close_datetime = datetime.datetime.combine(today, close_time)
            
            # 修改：不再截断跨天时间，使用实际关厅时间计算时长
            # 计算两个datetime之间的实际差值
            duration_minutes = int((close_datetime - open_datetime).total_seconds() / 60)
            record.duration = duration_minutes
            
            # 添加记录跨天标记字段，便于前端显示
            record.is_cross_day = close_datetime.date() > open_datetime.date()
            
            # 保存记录
            session.commit()
            
            # 获取最新的记录ID
            record_id = record.id
            
            # 返回结果中添加是否跨天标记
            return True, {
                "hall_name": hall_name,
                "open_time": open_time,
                "close_time": close_time,
                "duration": duration_minutes,
                "record_id": record_id,
                "is_cross_day": close_datetime.date() > open_datetime.date()
            }
        except SQLAlchemyError as e:
            session.rollback()
            self.log_error(f"记录关厅失败: {e}")
            return False, {"error": str(e)}
        finally:
            session.close()
    
    def get_today_total_duration(self, chatroom_id: str, owner_wxid: str, 
                               date: datetime.date = None) -> int:
        """获取指定日期的总开厅时长"""
        return self._execute_in_queue(self._get_today_total_duration, 
                                     chatroom_id, owner_wxid, date)
    
    def _get_today_total_duration(self, chatroom_id: str, owner_wxid: str, 
                                date: datetime.date = None) -> int:
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 查询指定日期的所有记录
            records = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.owner_wxid == owner_wxid,
                HallRecord.open_date == date
            ).all()
            
            # 计算总时长
            total_duration = sum(record.duration for record in records if not record.is_active)
            
            return total_duration
        finally:
            session.close()
    
    def get_hall_records(self, chatroom_id: str, hall_name: str = None, 
                        date: datetime.date = None) -> List[Dict]:
        """获取指定日期的厅记录"""
        return self._execute_in_queue(self._get_hall_records, chatroom_id, hall_name, date)
    
    def _get_hall_records(self, chatroom_id: str, hall_name: str = None, 
                         date: datetime.date = None) -> List[Dict]:
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 构建查询
            query = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.open_date == date
            )
            
            # 如果指定了厅名，则添加过滤条件
            if hall_name:
                query = query.filter(HallRecord.hall_name == hall_name)
                
            records = query.all()
            
            # 转换为字典列表
            result = []
            for record in records:
                record_dict = {
                    "id": record.id,
                    "hall_name": record.hall_name,
                    "owner_wxid": record.owner_wxid,
                    "owner_nickname": record.owner_nickname,
                    "open_date": record.open_date,
                    "open_time": record.open_time,
                    "close_time": record.close_time,
                    "duration": record.duration,
                    "is_late": record.is_late,
                    "late_minutes": record.late_minutes,
                    "is_active": record.is_active,
                    "is_cross_day": record.is_cross_day
                }
                result.append(record_dict)
            
            return result
        except Exception as e:
            self.log_error(f"获取厅记录失败: {e}")
            return []
        finally:
            session.close()
    
    def get_all_halls(self, chatroom_id: str, date: datetime.date = None, 
                      include_inactive: bool = False) -> List[Dict]:
        """获取群的所有厅（内部方法）"""
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 构建过滤器
            filters = [
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.open_date == date
            ]
            
            # 是否包含非活跃厅
            if not include_inactive:
                filters.append(HallRecord.is_active == True)
                
            # 执行查询
            records = session.query(HallRecord).filter(*filters).all()
            
            # 转换为字典
            result = []
            for record in records:
                hall_dict = {
                    "id": record.id,
                    "hall_name": record.hall_name,
                    "owner_wxid": record.owner_wxid,
                    "owner_nickname": record.owner_nickname,
                    "chatroom_id": record.chatroom_id,
                    "open_date": record.open_date,
                    "open_time": record.open_time,
                    "close_time": record.close_time,
                    "duration": record.duration,
                    "is_late": record.is_late, 
                    "late_minutes": record.late_minutes,
                    "is_active": record.is_active,
                    "is_cross_day": record.is_cross_day
                }
                result.append(hall_dict)
                
            return result
        except Exception as e:
            self.log_error(f"获取群厅列表失败: {e}")
            # 如果错误与is_cross_day有关，尝试兼容模式
            if "is_cross_day" in str(e):
                return self._get_all_halls_compatible(chatroom_id, date, include_inactive)
            raise
        finally:
            session.close()
            
    def _get_all_halls_compatible(self, chatroom_id: str, date: datetime.date = None, 
                               include_inactive: bool = False) -> List[Dict]:
        """获取群的所有厅的兼容模式（不使用is_cross_day字段）"""
        session = self.DBSession()
        try:
            if date is None:
                date = datetime.date.today()
                
            # 使用原始SQL查询避开ORM映射问题
            sql = """
            SELECT id, hall_name, owner_wxid, owner_nickname, chatroom_id, 
                   open_date, open_time, close_time, duration, is_late, 
                   late_minutes, is_active
            FROM hall_record
            WHERE chatroom_id = :chatroom_id AND open_date = :date
            """
            
            # 是否包含非活跃厅
            if not include_inactive:
                sql += " AND is_active = 1"
                
            # 执行查询
            result = session.execute(text(sql), {
                "chatroom_id": chatroom_id,
                "date": date
            })
            
            # 转换为字典
            halls = []
            for row in result:
                hall_dict = {
                    "id": row[0],
                    "hall_name": row[1],
                    "owner_wxid": row[2],
                    "owner_nickname": row[3],
                    "chatroom_id": row[4],
                    "open_date": row[5],
                    "open_time": row[6],
                    "close_time": row[7],
                    "duration": row[8],
                    "is_late": row[9],
                    "late_minutes": row[10],
                    "is_active": row[11],
                    # 兼容模式下填充默认值
                    "is_cross_day": False
                }
                halls.append(hall_dict)
                
            return halls
        except Exception as e:
            self.log_error(f"兼容模式获取群厅列表失败: {e}")
            raise
        finally:
            session.close()
            
    def _get_enabled_groups(self) -> List[Dict]:
        """获取所有启用开厅记录的群（内部方法）"""
        session = self.DBSession()
        try:
            settings = session.query(HallSetting).filter(
                HallSetting.enabled == True
            ).all()
            
            return [{"chatroom_id": setting.chatroom_id} for setting in settings]
        except Exception as e:
            self.log_error(f"获取启用开厅记录的群列表失败: {e}")
            raise
        finally:
            session.close()
    
    def get_all_halls_all_groups(self) -> Dict[str, List[Dict]]:
        """获取所有群聊的所有厅信息"""
        try:
            session = self.DBSession()
            
            # 获取所有厅注册信息
            all_halls = session.query(HallRegistration).all()
            
            if not all_halls:
                return {}
                
            # 按群组分类结果
            results = {}
            
            for hall in all_halls:
                chatroom_id = hall.chatroom_id
                
                # 初始化群组数据
                if chatroom_id not in results:
                    results[chatroom_id] = []
                
                # 基本数据
                hall_data = {
                    "hall_id": hall.hall_id,
                    "hall_name": hall.hall_name,
                    "owner_wxid": hall.owner_wxid,
                    "owner_nickname": hall.owner_nickname,
                    "chatroom_id": hall.chatroom_id,
                    "register_time": hall.register_time,
                    "open_time_start": hall.open_time_start,
                    "open_time_end": hall.open_time_end,
                    "is_active": False
                }
                
                # 查询活跃状态
                active_record = session.query(HallRecord).filter(
                    HallRecord.chatroom_id == chatroom_id,
                    HallRecord.hall_name == hall.hall_name,
                    HallRecord.is_active == True
                ).first()
                
                if active_record:
                    hall_data["is_active"] = True
                    hall_data["open_date"] = active_record.open_date
                    hall_data["open_time"] = active_record.open_time
                
                results[chatroom_id].append(hall_data)
            
            return results
        except Exception as e:
            self.log_error(f"获取所有群聊的厅信息失败: {e}")
            import traceback
            self.log_error(traceback.format_exc())
            return {}
        finally:
            session.close()
    
    def is_record_active(self, record_id: int) -> bool:
        """检查记录是否处于活跃状态
        
        Args:
            record_id: 记录ID
            
        Returns:
            bool: 如果记录存在且处于活跃状态返回True，否则返回False
        """
        session = self.DBSession()
        try:
            record = session.query(HallRecord).filter(
                HallRecord.id == record_id
            ).first()
            
            return record is not None and record.is_active
        except SQLAlchemyError as e:
            self.log_error(f"检查记录活跃状态失败: {e}")
            return False
        finally:
            session.close()
    
    def get_hall_record_info(self, record_id: int) -> Optional[Dict]:
        """获取开厅记录信息
        
        Args:
            record_id: 记录ID
            
        Returns:
            Dict: 记录信息字典，包含hall_name, open_time等
        """
        session = self.DBSession()
        try:
            record = session.query(HallRecord).filter(
                HallRecord.id == record_id
            ).first()
            
            if not record:
                return None
                
            return {
                "hall_name": record.hall_name,
                "owner_wxid": record.owner_wxid,
                "owner_nickname": record.owner_nickname,
                "open_time": record.open_time,
                "close_time": record.close_time,
                "is_active": record.is_active,
                "is_late": record.is_late,
                "late_minutes": record.late_minutes
            }
        except SQLAlchemyError as e:
            self.log_error(f"获取开厅记录信息失败: {e}")
            return None
        finally:
            session.close()
    
    def get_halls_by_owner(self, chatroom_id: str, owner_wxid: str) -> List[Dict]:
        """获取用户在特定群聊的所有厅信息"""
        return self._execute_in_queue(self._get_halls_by_owner, chatroom_id, owner_wxid)
    
    def _get_halls_by_owner(self, chatroom_id: str, owner_wxid: str) -> List[Dict]:
        """获取用户在指定群聊中注册的所有厅"""
        try:
            session = self.DBSession()
            
            # 查询用户注册的所有厅
            halls = session.query(HallRegistration).filter(
                HallRegistration.chatroom_id == chatroom_id,
                HallRegistration.owner_wxid == owner_wxid
            ).all()
            
            if not halls:
                return []
                
            results = []
            
            for hall in halls:
                # 默认数据
                hall_data = {
                    "hall_id": hall.hall_id,
                    "hall_name": hall.hall_name,
                    "owner_wxid": hall.owner_wxid,
                    "owner_nickname": hall.owner_nickname,
                    "chatroom_id": hall.chatroom_id,
                    "register_time": hall.register_time,
                    "open_time_start": hall.open_time_start,
                    "open_time_end": hall.open_time_end,
                    "is_active": False,
                    "has_temp_time": False
                }
                
                # 查询是否有活跃记录
                active_record = session.query(HallRecord).filter(
                    HallRecord.chatroom_id == chatroom_id,
                    HallRecord.hall_name == hall.hall_name,
                    HallRecord.is_active == True
                ).first()
                
                if active_record:
                    hall_data["is_active"] = True
                    hall_data["open_date"] = active_record.open_date
                    hall_data["open_time"] = active_record.open_time
                    hall_data["record_id"] = active_record.id
                
                # 查询今日临时时间设置
                today = datetime.date.today()
                temp_time = session.query(HallTempTime).filter(
                    HallTempTime.chatroom_id == chatroom_id,
                    HallTempTime.hall_name == hall.hall_name,
                    HallTempTime.temp_date == today
                ).first()
                
                if temp_time:
                    hall_data["has_temp_time"] = True
                    hall_data["temp_time_start"] = temp_time.open_time_start
                    hall_data["temp_time_end"] = temp_time.open_time_end
                    hall_data["temp_date"] = temp_time.temp_date
                
                results.append(hall_data)
            
            return results
        except Exception as e:
            self.log_error(f"获取用户厅列表失败: {e}")
            return []
        finally:
            session.close()
    
    def get_hall_by_id(self, hall_id: str) -> Optional[Dict]:
        """根据厅唯一ID获取厅信息"""
        return self._execute_in_queue(self._get_hall_by_id, hall_id)

    def _get_hall_by_id(self, hall_id: str) -> Optional[Dict]:
        """根据厅唯一ID获取厅信息"""
        try:
            session = self.DBSession()
            
            # 查询厅注册信息
            hall = session.query(HallRegistration).filter(
                HallRegistration.hall_id == hall_id
            ).first()
            
            if not hall:
                return None
            
            chatroom_id = hall.chatroom_id
            hall_name = hall.hall_name
                
            # 查询是否有活跃记录
            active_record = session.query(HallRecord).filter(
                HallRecord.chatroom_id == chatroom_id,
                HallRecord.hall_name == hall_name,
                HallRecord.is_active == True
            ).first()
            
            # 查询今日临时时间设置
            today = datetime.date.today()
            temp_time = session.query(HallTempTime).filter(
                HallTempTime.chatroom_id == chatroom_id,
                HallTempTime.hall_name == hall_name,
                HallTempTime.temp_date == today
            ).first()
            
            # 构建返回数据
            result = {
                "hall_id": hall.hall_id,
                "hall_name": hall.hall_name,
                "owner_wxid": hall.owner_wxid,
                "owner_nickname": hall.owner_nickname,
                "chatroom_id": hall.chatroom_id,
                "register_time": hall.register_time,
                "open_time_start": hall.open_time_start,
                "open_time_end": hall.open_time_end,
                "is_active": active_record is not None,
                "has_temp_time": temp_time is not None,
            }
            
            # 添加活跃记录信息
            if active_record:
                result.update({
                    "open_date": active_record.open_date,
                    "open_time": active_record.open_time,
                    "record_id": active_record.id,
                })
            
            # 添加临时时间信息
            if temp_time:
                result.update({
                    "temp_time_start": temp_time.open_time_start,
                    "temp_time_end": temp_time.open_time_end,
                    "temp_date": temp_time.temp_date,
                })
                
            return result
        except Exception as e:
            self.log_error(f"根据ID获取厅信息失败: {e}")
            return None
        finally:
            session.close()
    
    def check_and_fill_all_hall_ids(self) -> Tuple[bool, Dict]:
        """检查所有厅并为没有唯一ID的厅生成ID
        
        返回:
            Tuple[bool, Dict]: 是否成功和结果统计
        """
        return self._execute_in_queue(self._check_and_fill_all_hall_ids)
    
    def _check_and_fill_all_hall_ids(self) -> Tuple[bool, Dict]:
        """检查所有厅并为没有唯一ID的厅生成ID (内部方法)"""
        try:
            self.log_info("开始检查所有厅并为缺失ID的厅生成唯一ID...")
            session = self.DBSession()
            
            # 获取所有没有有效ID的厅记录
            halls = session.query(HallRegistration).filter(
                or_(
                    HallRegistration.hall_id == None,
                    HallRegistration.hall_id == ""
                )
            ).all()
            
            total_halls = session.query(HallRegistration).count()
            missing_ids_count = len(halls)
            
            self.log_info(f"总计 {total_halls} 个厅，其中 {missing_ids_count} 个缺少唯一ID")
            
            if not halls:
                return True, {
                    "total": total_halls,
                    "missing": 0,
                    "updated": 0,
                    "message": "所有厅均已有唯一ID"
                }
            
            import random
            updated_count = 0
            
            # 记录更新的厅
            updated_halls = []
            
            for hall in halls:
                # 生成一个随机的4位数ID
                while True:
                    hall_id = f"{random.randint(1000, 9999)}"
                    
                    # 检查ID是否已存在
                    exists = session.query(HallRegistration).filter(
                        HallRegistration.hall_id == hall_id,
                        HallRegistration.id != hall.id  # 排除当前记录
                    ).first()
                    
                    if not exists:
                        break
                
                # 更新记录
                hall.hall_id = hall_id
                updated_count += 1
                
                # 记录这个厅
                updated_halls.append({
                    "hall_name": hall.hall_name,
                    "hall_id": hall_id,
                    "owner_nickname": hall.owner_nickname,
                    "chatroom_id": hall.chatroom_id
                })
            
            # 提交更改
            session.commit()
            self.log_success(f"成功为 {updated_count} 条厅记录生成了唯一ID")
            
            # 检查并添加唯一索引
            try:
                inspector = inspect(self.engine)
                indices = inspector.get_indexes('hall_registration')
                has_index = any(idx.get('name') == 'ix_hall_registration_hall_id' for idx in indices)
                
                if not has_index:
                    with self.engine.connect() as conn:
                        conn.execute(text("CREATE UNIQUE INDEX ix_hall_registration_hall_id ON hall_registration (hall_id)"))
                        conn.commit()
                    self.log_success("成功为hall_id创建唯一索引")
            except Exception as e:
                self.log_error(f"为hall_id创建唯一索引失败: {e}")
            
            return True, {
                "total": total_halls,
                "missing": missing_ids_count,
                "updated": updated_count,
                "updated_halls": updated_halls,
                "message": f"成功为 {updated_count} 个厅生成唯一ID"
            }
        
        except Exception as e:
            self.log_error(f"检查和生成厅记录唯一ID失败: {e}")
            import traceback
            self.log_error(traceback.format_exc())
            return False, {
                "error": str(e),
                "message": "检查和生成厅唯一ID失败"
            }
        finally:
            session.close()
    
    def __del__(self):
        """析构函数，关闭线程池"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=False) 