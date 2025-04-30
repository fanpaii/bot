from loguru import logger
import tomllib
import os
from typing import Dict, Any, Optional, List, Union

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase


class PluginTemplate(PluginBase):
    description = "插件模板 - 快速开发新插件的基础模板"
    author = "XYBot Developer"
    version = "1.0.0"

    # 同步初始化
    def __init__(self):
        super().__init__()

        # 获取配置文件路径
        config_path = os.path.join(os.path.dirname(__file__), "config.toml")
        
        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
                
            # 读取基本配置
            basic_config = config.get("basic", {})
            self.enable = basic_config.get("enable", False)  # 读取插件开关
            self.trigger_keyword = basic_config.get("trigger_keyword", "你好")  # 读取触发关键词

            # 读取其他配置
            # self.other_config = config.get("other_section", {}).get("other_option", "默认值")

        except Exception as e:
            logger.error(f"加载{self.__class__.__name__}配置文件失败: {str(e)}")
            self.enable = False  # 如果加载失败，禁用插件

    # 异步初始化
    async def async_init(self, bot=None):
        """在机器人启动时异步初始化插件"""
        try:
            # 在这里执行异步初始化操作，例如：
            # - 连接到外部服务
            # - 加载大型数据集
            # - 初始化需要异步操作的组件
            logger.info(f"{self.__class__.__name__} 异步初始化完成")
        except Exception as e:
            logger.error(f"{self.__class__.__name__} 异步初始化失败: {str(e)}")
        return

    @on_text_message(priority=50)
    async def handle_text(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        """处理文本消息
        
        Args:
            bot: 微信API客户端
            message: 消息内容
        
        Returns:
            bool: 是否阻止后续插件处理
        """
        if not self.enable:
            return True  # 插件未启用，允许后续插件处理
        
        content = str(message.get("Content", "")).strip()
        from_wxid = message.get("FromWxid", "")
        
        # 检查是否包含触发关键词
        if self.trigger_keyword in content:
            logger.info(f"检测到触发关键词: {self.trigger_keyword}")
            response = f"你好！我是一个插件模板。\n你发送的消息是: {content}"
            await bot.send_text_message(from_wxid, response)
            return False  # 阻止后续插件处理
        
        return True  # 允许后续插件处理

    @on_image_message(priority=50)
    async def handle_image(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
        """处理图片消息"""
        if not self.enable:
            return True
        
        # 在这里处理图片消息
        logger.debug(f"收到图片消息: {message.get('MsgId')}")
        
        return True  # 允许后续插件处理

    @schedule('interval', minutes=30)
    async def periodic_task(self, bot: WechatAPIClient):
        """定期执行的任务，每30分钟执行一次"""
        if not self.enable:
            return
        
        logger.debug(f"{self.__class__.__name__} 执行定期任务")
        # 在这里执行你的定期任务

    async def on_enable(self, bot):
        """插件启用时的回调函数"""
        logger.info(f"{self.__class__.__name__} 已启用")
        # 执行启用时的操作，如初始化资源等

    async def on_disable(self):
        """插件禁用时的回调函数"""
        logger.info(f"{self.__class__.__name__} 已禁用")
        # 执行禁用时的操作，如清理资源等 