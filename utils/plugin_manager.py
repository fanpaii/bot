import importlib
import inspect
import os
import sys
import tomllib
import traceback
from typing import Dict, Type, List, Union, Optional

from loguru import logger

from WechatAPI import WechatAPIClient
from .event_manager import EventManager
from .plugin_base import PluginBase


class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, PluginBase] = {}
        self.plugin_classes: Dict[str, Type[PluginBase]] = {}
        self.plugin_info: Dict[str, dict] = {}  # 新增：存储所有插件信息

        # 载入配置文件
        try:
            with open("main_config.toml", "rb") as f:
                main_config = tomllib.load(f)
            # 获取禁用插件列表
            self.excluded_plugins = main_config["XYBot"]["disabled-plugins"]
            logger.info(f"从配置文件加载禁用插件列表: {self.excluded_plugins}")
        except Exception as e:
            logger.error(f"加载禁用插件列表失败: {e}")
            self.excluded_plugins = []

    async def load_plugin(self, bot: WechatAPIClient, plugin_class: Type[PluginBase],
                          is_disabled: bool = False) -> bool:
        """加载单个插件，接受Type[PluginBase]"""
        try:
            plugin_name = plugin_class.__name__

            # 防止重复加载插件
            if plugin_name in self.plugins:
                return False

            # 记录插件信息，即使插件被禁用也会记录
            self.plugin_info[plugin_name] = {
                "name": plugin_name,
                "description": plugin_class.description,
                "author": plugin_class.author,
                "version": plugin_class.version,
                "enabled": False,
                "class": plugin_class,
                "is_ai_platform": getattr(plugin_class, 'is_ai_platform', False)  # 检查是否为AI平台插件
            }

            # 如果插件被禁用则不加载
            if is_disabled:
                return False

            plugin = plugin_class()
            EventManager.bind_instance(plugin)
            await plugin.on_enable(bot)
            
            # 使用inspect检查async_init方法的参数，并根据参数数量决定如何调用
            method_sig = inspect.signature(plugin.async_init)
            if len(method_sig.parameters) > 1:  # self + 至少一个参数
                await plugin.async_init(bot)
            else:
                await plugin.async_init()
                
            self.plugins[plugin_name] = plugin
            self.plugin_classes[plugin_name] = plugin_class
            self.plugin_info[plugin_name]["enabled"] = True
            return True
        except:
            logger.error(f"加载插件时发生错误: {traceback.format_exc()}")
            return False

    async def unload_plugin(self, plugin_name: str, add_to_excluded: bool = False) -> bool:
        """卸载单个插件

        Args:
            plugin_name: 插件名称
            add_to_excluded: 是否将插件添加到禁用列表中，默认为 False
                          只有在用户主动禁用插件时才应该设置为 True
        """
        if plugin_name not in self.plugins:
            return False

        # 防止卸载 ManagePlugin
        if plugin_name == "ManagePlugin":
            logger.warning("ManagePlugin 不能被卸载")
            return False

        try:
            plugin = self.plugins[plugin_name]
            await plugin.on_disable()
            EventManager.unbind_instance(plugin)
            del self.plugins[plugin_name]
            del self.plugin_classes[plugin_name]
            if plugin_name in self.plugin_info.keys():
                self.plugin_info[plugin_name]["enabled"] = False

            # 只有在用户主动禁用插件时，才将插件添加到禁用列表中
            if add_to_excluded and plugin_name not in self.excluded_plugins:
                self.excluded_plugins.append(plugin_name)
                # 保存禁用插件列表到配置文件
                self._save_disabled_plugins_to_config()
                logger.info(f"将插件 {plugin_name} 添加到禁用列表并保存到配置文件")

            return True
        except:
            logger.error(f"卸载插件 {plugin_name} 时发生错误: {traceback.format_exc()}")
            return False

    async def load_plugins_from_directory(self, bot: WechatAPIClient, load_disabled_plugin: bool = True) -> Union[
        List[str], bool]:
        """从plugins目录批量加载插件"""
        loaded_plugins = []

        for dirname in os.listdir("plugins"):
            if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                try:
                    module = importlib.import_module(f"plugins.{dirname}.main")
                    for name, obj in inspect.getmembers(module):
                        if inspect.isclass(obj) and issubclass(obj, PluginBase) and obj != PluginBase:
                            is_disabled = False
                            if not load_disabled_plugin:
                                is_disabled = obj.__name__ in self.excluded_plugins

                            if await self.load_plugin(bot, obj, is_disabled=is_disabled):
                                loaded_plugins.append(obj.__name__)

                except:
                    logger.error(f"加载 {dirname} 时发生错误: {traceback.format_exc()}")
                    return False

        return loaded_plugins

    async def load_plugin_from_directory(self, bot: WechatAPIClient, plugin_name: str) -> bool:
        """从plugins目录加载单个插件

        Args:
            bot: 机器人实例
            plugin_name: 插件类名称（不是文件名）

        Returns:
            bool: 是否成功加载插件
        """
        found = False
        for dirname in os.listdir("plugins"):
            try:
                if os.path.isdir(f"plugins/{dirname}") and os.path.exists(f"plugins/{dirname}/main.py"):
                    module = importlib.import_module(f"plugins.{dirname}.main")
                    importlib.reload(module)

                    for name, obj in inspect.getmembers(module):
                        if (inspect.isclass(obj) and
                                issubclass(obj, PluginBase) and
                                obj != PluginBase and
                                obj.__name__ == plugin_name):
                            found = True

                            # 检查是否为AI平台插件
                            is_ai_platform = getattr(obj, 'is_ai_platform', False)

                            # 如果是AI平台插件，先禁用其他所有AI平台插件
                            if is_ai_platform:
                                logger.info(f"启用AI平台插件 {plugin_name}，将禁用其他AI平台插件")

                                # 遍历已启用的插件，禁用其他AI平台插件
                                for plugin in list(self.plugins):
                                    if getattr(plugin.__class__, 'is_ai_platform', False) and plugin.__class__.__name__ != plugin_name:
                                        logger.info(f"禁用AI平台插件: {plugin.__class__.__name__}")
                                        await self.unload_plugin(plugin.__class__.__name__)

                            # 如果插件在禁用列表中，将其移除
                            if plugin_name in self.excluded_plugins:
                                self.excluded_plugins.remove(plugin_name)
                                # 保存禁用插件列表到配置文件
                                self._save_disabled_plugins_to_config()
                                logger.info(f"将插件 {plugin_name} 从禁用列表中移除并保存到配置文件")

                            return await self.load_plugin(bot, obj)
            except:
                logger.error(f"检查 {dirname} 时发生错误: {traceback.format_exc()}")
                continue

        if not found:
            logger.warning(f"未找到插件类 {plugin_name}")
            return False


    async def unload_all_plugins(self) -> tuple[List[str], List[str]]:
        """卸载所有插件"""
        unloaded_plugins = []
        failed_unloads = []
        for plugin_name in list(self.plugins.keys()):
            if await self.unload_plugin(plugin_name):
                unloaded_plugins.append(plugin_name)
            else:
                failed_unloads.append(plugin_name)
        return unloaded_plugins, failed_unloads

    async def reload_plugin(self, bot: WechatAPIClient, plugin_name: str) -> bool:
        """重载单个插件"""
        if plugin_name not in self.plugin_classes:
            return False

        # 防止重载 ManagePlugin
        if plugin_name == "ManagePlugin":
            logger.warning("ManagePlugin 不能被重载")
            return False

        try:
            # 获取插件类所在的模块
            plugin_class = self.plugin_classes[plugin_name]
            module_name = plugin_class.__module__

            # 先卸载插件
            if not await self.unload_plugin(plugin_name):
                return False

            # 重新导入模块
            module = importlib.import_module(module_name)
            importlib.reload(module)

            # 从重新加载的模块中获取插件类
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and
                        issubclass(obj, PluginBase) and
                        obj != PluginBase and
                        obj.__name__ == plugin_name):
                    # 使用新的插件类而不是旧的
                    return await self.load_plugin(bot, obj)

            return False
        except Exception as e:
            logger.error(f"重载插件 {plugin_name} 时发生错误: {e}")
            return False

    async def reload_all_plugins(self, bot: WechatAPIClient) -> List[str]:
        """重载所有插件

        Returns:
            List[str]: 成功重载的插件名称列表
        """
        try:
            # 记录当前加载的插件名称，排除 ManagePlugin
            original_plugins = [name for name in self.plugins.keys() if name != "ManagePlugin"]

            # 我们不在这里更新禁用插件列表
            # 因为这会导致所有当前未启用的插件都被添加到禁用列表中
            # 包括那些只是暂时未加载的插件
            # 我们只在 unload_plugin 方法中更新禁用列表，即用户主动禁用插件时

            # 卸载除 ManagePlugin 外的所有插件
            # 注意这里不将插件添加到禁用列表中，因为这只是重载而非禁用
            for plugin_name in original_plugins:
                await self.unload_plugin(plugin_name, add_to_excluded=False)

            # 重新加载所有模块
            for module_name in list(sys.modules.keys()):
                if module_name.startswith('plugins.') and not module_name.endswith('ManagePlugin'):
                    del sys.modules[module_name]

            # 从目录重新加载插件，不加载禁用的插件
            return await self.load_plugins_from_directory(bot, load_disabled_plugin=False)

        except:
            logger.error(f"重载所有插件时发生错误: {traceback.format_exc()}")
            return []

    def get_plugin_info(self, plugin_name: str = None) -> Union[dict, List[dict]]:
        """获取插件信息

        Args:
            plugin_name: 插件名称，如果为None则返回所有插件信息

        Returns:
            如果指定插件名，返回单个插件信息字典；否则返回所有插件信息列表
        """
        # 创建一个可以JSON序列化的信息副本
        def clean_plugin_info(info):
            result = {
                "id": info.get("name", "unknown"),
                "name": info.get("name", "unknown"),
                "description": info.get("description", ""),
                "author": info.get("author", ""),
                "version": info.get("version", "1.0.0"),
                "enabled": info.get("enabled", False),
                "is_ai_platform": info.get("is_ai_platform", False)  # 添加AI平台标识
            }
            return result

        # 如果请求指定插件信息
        if plugin_name:
            info = self.plugin_info.get(plugin_name)
            if info:
                return clean_plugin_info(info)
            
            # 如果插件未加载，尝试从目录中查找信息
            return self._scan_plugin_from_directory(plugin_name)
        
        # 如果请求所有插件信息，先获取已加载插件
        result = [clean_plugin_info(info) for info in self.plugin_info.values()]
        
        # 然后扫描目录查找所有插件
        all_plugins = self._scan_plugins_directory()
        
        # 合并结果，避免重复
        existing_names = {plugin["name"] for plugin in result}
        for plugin in all_plugins:
            if plugin["name"] not in existing_names:
                result.append(plugin)
        
        return result
    
    def _scan_plugins_directory(self) -> List[dict]:
        """扫描plugins目录，查找所有可能的插件

        Returns:
            List[dict]: 所有发现的插件信息列表
        """
        result = []
        try:
            for dirname in os.listdir("plugins"):
                plugin_path = f"plugins/{dirname}"
                plugin_main = f"{plugin_path}/main.py"
                
                if os.path.isdir(plugin_path) and os.path.exists(plugin_main):
                    # 尝试解析插件main.py文件
                    plugin_info = self._parse_plugin_file(plugin_main)
                    if plugin_info:
                        # 检查该插件是否已加载
                        plugin_name = plugin_info.get("name")
                        if plugin_name:
                            plugin_info["enabled"] = plugin_name in self.plugins
                            plugin_info["id"] = plugin_name
                            result.append(plugin_info)
        except Exception as e:
            logger.error(f"扫描插件目录时发生错误: {e}")
        
        return result
    
    def _scan_plugin_from_directory(self, plugin_name: str) -> Optional[dict]:
        """从目录中扫描指定名称的插件

        Args:
            plugin_name: 插件名称

        Returns:
            Optional[dict]: 插件信息，如果未找到则返回None
        """
        try:
            for dirname in os.listdir("plugins"):
                plugin_path = f"plugins/{dirname}"
                plugin_main = f"{plugin_path}/main.py"
                
                if os.path.isdir(plugin_path) and os.path.exists(plugin_main):
                    # 读取文件内容，查找插件类
                    with open(plugin_main, "r", encoding="utf-8") as f:
                        content = f.read()
                        if f"class {plugin_name}(" in content:
                            # 找到了对应的插件类
                            plugin_info = self._parse_plugin_file(plugin_main)
                            if plugin_info:
                                plugin_info["enabled"] = plugin_name in self.plugins
                                plugin_info["id"] = plugin_name
                                return plugin_info
        except Exception as e:
            logger.error(f"扫描插件 {plugin_name} 时发生错误: {e}")
        
        return None
    
    def _parse_plugin_file(self, plugin_file: str) -> Optional[dict]:
        """解析插件文件，提取插件信息

        Args:
            plugin_file: 插件文件路径

        Returns:
            Optional[dict]: 插件信息，如果解析失败则返回None
        """
        try:
            with open(plugin_file, "r", encoding="utf-8") as f:
                content = f.read()
            
            # 提取插件信息
            plugin_info = {
                "name": self._extract_class_name(content),
                "description": self._extract_description(content),
                "author": self._extract_author(content),
                "version": self._extract_version(content),
                "is_ai_platform": "is_ai_platform = True" in content
            }
            
            # 确保至少有插件名
            if not plugin_info["name"]:
                return None
                
            return plugin_info
        except Exception as e:
            logger.error(f"解析插件文件 {plugin_file} 时发生错误: {e}")
            return None
    
    def _extract_class_name(self, content: str) -> str:
        """从文件内容中提取插件类名

        Args:
            content: 文件内容

        Returns:
            str: 插件类名，如果未找到则返回空字符串
        """
        import re
        pattern = r"class\s+(\w+)\s*\(\s*PluginBase\s*\)"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
        return ""
    
    def _extract_description(self, content: str) -> str:
        """从文件内容中提取插件描述

        Args:
            content: 文件内容

        Returns:
            str: 插件描述，如果未找到则返回默认描述
        """
        import re
        # 尝试匹配插件类中的description属性
        pattern = r"description\s*=\s*['\"](.+?)['\"]"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
        return "未提供描述"
    
    def _extract_author(self, content: str) -> str:
        """从文件内容中提取插件作者

        Args:
            content: 文件内容

        Returns:
            str: 插件作者，如果未找到则返回默认作者
        """
        import re
        pattern = r"author\s*=\s*['\"](.+?)['\"]"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
        return "未知作者"
    
    def _extract_version(self, content: str) -> str:
        """从文件内容中提取插件版本

        Args:
            content: 文件内容

        Returns:
            str: 插件版本，如果未找到则返回默认版本
        """
        import re
        pattern = r"version\s*=\s*['\"](.+?)['\"]"
        match = re.search(pattern, content)
        if match:
            return match.group(1)
        return "1.0.0"

    def _save_disabled_plugins_to_config(self):
        """将禁用的插件列表保存到配置文件中"""
        try:
            import tomli_w

            # 读取当前配置
            with open("main_config.toml", "rb") as f:
                config = tomllib.load(f)

            # 更新禁用插件列表
            config["XYBot"]["disabled-plugins"] = self.excluded_plugins

            # 写回配置文件
            with open("main_config.toml", "wb") as f:
                tomli_w.dump(config, f)

            logger.info(f"成功将禁用插件列表保存到配置文件: {self.excluded_plugins}")
        except Exception as e:
            logger.error(f"保存禁用插件列表到配置文件失败: {e}")

    def get_ai_platform_plugins(self) -> List[dict]:
        """获取所有AI平台插件信息"""
        # 使用与 get_plugin_info 相同的格式化方法
        def clean_plugin_info(info):
            result = {
                "id": info.get("name", "unknown"),
                "name": info.get("name", "unknown"),
                "description": info.get("description", ""),
                "author": info.get("author", ""),
                "version": info.get("version", "1.0.0"),
                "enabled": info.get("enabled", False),
                "is_ai_platform": info.get("is_ai_platform", False)
            }
            return result

        # 过滤出AI平台插件
        return [clean_plugin_info(info) for info in self.plugin_info.values()
                if info.get("is_ai_platform", False)]


plugin_manager = PluginManager()
