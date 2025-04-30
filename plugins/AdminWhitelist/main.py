import tomllib
import os
import toml
import re

from WechatAPI import WechatAPIClient
from database.XYBotDB import XYBotDB
from utils.decorators import *
from utils.plugin_base import PluginBase
from loguru import logger


class AdminWhitelist(PluginBase):
    description = "管理白名单"
    author = "XYBotV2"
    version = "1.3.0"

    def __init__(self):
        super().__init__()

        with open("plugins/AdminWhitelist/config.toml", "rb") as f:
            plugin_config = tomllib.load(f)

        with open("main_config.toml", "rb") as f:
            self.main_config = tomllib.load(f)

        config = plugin_config["AdminWhitelist"]
        self.main_config_data = self.main_config["XYBot"]  # 使用XYBot作为配置键

        self.enable = config["enable"]
        self.command_format = config["command-format"]

        self.admins = self.main_config_data["admins"]
        self.whitelist = self.main_config_data.get("whitelist", [])
        
        # 保存白名单索引信息
        self.groups_index = {}  # 群聊索引 {序号: wxid}
        self.users_index = {}   # 用户索引 {序号: wxid}

    @on_text_message
    async def handle_text(self, bot: WechatAPIClient, message: dict):
        if not self.enable:
            return True

        content = str(message.get("Content", "")).strip()
        command = content.split(" ")
        cmd = command[0].lower() if command else ""

        # 支持的命令列表 (包含简写)
        add_commands = ["添加白名单", "添加群白名单", "加白"]
        remove_commands = ["移除白名单", "移除群白名单", "移白"]
        list_commands = ["白名单列表", "群白名单列表"]

        # 检查命令是否在支持的命令列表中
        if not cmd or (cmd not in add_commands and 
                       cmd not in remove_commands and 
                       cmd not in list_commands):
            return True

        sender_wxid = message.get("SenderWxid", "")
        from_wxid = message.get("FromWxid", "")
        is_group = message.get("IsGroup", False)
        at_users = message.get("Ats", [])

        # 检查权限
        if not sender_wxid or sender_wxid not in self.admins:
            await bot.send_text_message(from_wxid, "-----XY-----\n❌你没有权限使用此命令，需要管理员权限")
            return False
            
        # 只处理群聊消息
        if not is_group:
            await bot.send_text_message(from_wxid, "-----XY-----\n❌该命令只能在群聊中使用")
            return False

        try:
            # 处理添加白名单命令
            if cmd in add_commands:
                # 如果是群聊消息且没有@用户，则添加当前群聊到白名单
                if not at_users and len(command) == 1:
                    await self._add_group_to_whitelist(bot, from_wxid, from_wxid)
                # 如果有@用户，则添加被@的用户到白名单
                elif at_users:
                    for user_wxid in at_users:
                        await self._add_user_to_whitelist(bot, from_wxid, user_wxid)
                # 如果有指定参数，尝试添加指定ID到白名单
                elif len(command) > 1:
                    target_id = command[1]
                    
                    # 检查是否为数字序号
                    if target_id.isdigit():
                        await self._handle_index_operation(bot, from_wxid, int(target_id), "add")
                    # 根据ID格式判断是群聊还是用户
                    elif target_id.endswith("@chatroom"):
                        await self._add_group_to_whitelist(bot, from_wxid, target_id)
                    else:
                        await self._add_user_to_whitelist(bot, from_wxid, target_id)
                else:
                    await bot.send_text_message(from_wxid, self.command_format)
                return False

            # 处理移除白名单命令
            elif cmd in remove_commands:
                # 如果是群聊消息且没有@用户，则移除当前群聊
                if not at_users and len(command) == 1:
                    await self._remove_from_whitelist(bot, from_wxid, from_wxid)
                # 如果有@用户，则移除被@的用户
                elif at_users:
                    for user_wxid in at_users:
                        await self._remove_from_whitelist(bot, from_wxid, user_wxid)
                # 如果有指定参数，尝试移除指定ID
                elif len(command) > 1:
                    target_id = command[1]
                    
                    # 检查是否为数字序号
                    if target_id.isdigit():
                        await self._handle_index_operation(bot, from_wxid, int(target_id), "remove")
                    else:
                        await self._remove_from_whitelist(bot, from_wxid, target_id)
                else:
                    await bot.send_text_message(from_wxid, self.command_format)
                return False

            # 处理查看白名单列表命令
            elif cmd in list_commands:
                await self._show_whitelist(bot, from_wxid)
                return False
        except Exception as e:
            logger.error(f"处理白名单命令出错: {e}", exc_info=True)
            await bot.send_text_message(from_wxid, f"-----XY-----\n❌处理命令时出错: {str(e)}")
            return False
            
        return True
        
    async def _handle_index_operation(self, bot: WechatAPIClient, reply_to: str, index: int, operation: str):
        """通过序号操作白名单
        
        Args:
            bot: 机器人API客户端
            reply_to: 回复到的群聊ID
            index: 序号
            operation: 操作类型，'add'或'remove'
        """
        # 先确保索引已最新
        await self._update_whitelist_index(bot)
        
        # 尝试从群聊索引中查找
        if index in self.groups_index:
            group_id = self.groups_index[index]
            if operation == "add":
                await self._add_group_to_whitelist(bot, reply_to, group_id)
            else:
                await self._remove_from_whitelist(bot, reply_to, group_id)
            return
            
        # 尝试从用户索引中查找
        if index in self.users_index:
            user_id = self.users_index[index]
            if operation == "add":
                await self._add_user_to_whitelist(bot, reply_to, user_id)
            else:
                await self._remove_from_whitelist(bot, reply_to, user_id)
            return
            
        # 未找到对应序号
        await bot.send_text_message(reply_to, f"-----XY-----\n❌未找到序号 {index} 对应的条目，请查看白名单列表获取正确序号")

    async def _update_whitelist_index(self, bot: WechatAPIClient):
        """更新白名单索引信息"""
        # 清空现有索引
        self.groups_index = {}
        self.users_index = {}
        
        # 分类存储群聊和用户
        groups = []
        users = []
        
        for item_id in self.whitelist:
            if item_id.endswith("@chatroom"):
                groups.append(item_id)
            else:
                users.append(item_id)
        
        # 更新群聊索引
        for idx, group_id in enumerate(groups):
            self.groups_index[idx+1] = group_id
        
        # 更新用户索引
        for idx, user_id in enumerate(users):
            self.users_index[idx+1] = user_id

    async def _add_group_to_whitelist(self, bot: WechatAPIClient, reply_to: str, group_id: str):
        """添加群聊到白名单"""
        # 检查是否已在白名单中
        if group_id in self.whitelist:
            await bot.send_text_message(reply_to, f"-----XY-----\n该群 {group_id} 已在白名单中")
            return
        
        # 将群ID添加到白名单
        self.whitelist.append(group_id)
        
        # 保存到main_config.toml
        if self._save_config():
            # 获取群昵称
            try:
                group_name = await self._get_group_name(bot, group_id)
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功添加群 {group_name} ({group_id}) 到白名单")
            except Exception as e:
                logger.error(f"获取群名称失败: {e}")
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功添加群 {group_id} 到白名单")
        else:
            await bot.send_text_message(reply_to, 
                                 f"-----XY-----\n❌添加群 {group_id} 到白名单失败，无法写入配置文件")

    async def _add_user_to_whitelist(self, bot: WechatAPIClient, reply_to: str, user_wxid: str):
        """添加用户到白名单"""
        # 检查是否已在白名单中
        if user_wxid in self.whitelist:
            await bot.send_text_message(reply_to, f"-----XY-----\n该用户 {user_wxid} 已在白名单中")
            return
        
        # 将用户ID添加到白名单
        self.whitelist.append(user_wxid)
        
        # 保存到main_config.toml
        if self._save_config():
            # 获取用户昵称
            try:
                user_name = await self._get_user_name(bot, user_wxid)
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功添加用户 {user_name} ({user_wxid}) 到白名单")
            except Exception as e:
                logger.error(f"获取用户昵称失败: {e}")
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功添加用户 {user_wxid} 到白名单")
        else:
            await bot.send_text_message(reply_to, 
                                 f"-----XY-----\n❌添加用户 {user_wxid} 到白名单失败，无法写入配置文件")

    async def _remove_from_whitelist(self, bot: WechatAPIClient, reply_to: str, target_id: str):
        """从白名单中移除群聊或用户"""
        # 检查是否在白名单中
        if target_id not in self.whitelist:
            await bot.send_text_message(reply_to, f"-----XY-----\n{target_id} 不在白名单中")
            return
        
        # 从白名单中移除
        self.whitelist.remove(target_id)
        
        # 保存到main_config.toml
        if self._save_config():
            # 确定是群聊还是用户
            is_group = target_id.endswith("@chatroom")
            
            try:
                if is_group:
                    name = await self._get_group_name(bot, target_id)
                    type_str = "群"
                else:
                    name = await self._get_user_name(bot, target_id)
                    type_str = "用户"
                
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功将{type_str} {name} ({target_id}) 从白名单中移除")
            except Exception as e:
                logger.error(f"获取名称失败: {e}")
                await bot.send_text_message(reply_to, 
                                     f"-----XY-----\n成功将 {target_id} 从白名单中移除")
        else:
            await bot.send_text_message(reply_to, 
                                 f"-----XY-----\n❌将 {target_id} 从白名单中移除失败，无法写入配置文件")

    async def _show_whitelist(self, bot: WechatAPIClient, reply_to: str):
        """显示白名单列表"""
        if not self.whitelist:
            await bot.send_text_message(reply_to, "-----XY-----\n当前白名单为空")
            return
        
        reply = "-----XY-----\n白名单列表：\n"
        
        # 分类存储群聊和用户
        groups = []
        users = []
        
        for item_id in self.whitelist:
            if item_id.endswith("@chatroom"):
                groups.append(item_id)
            else:
                users.append(item_id)
        
        # 更新索引数据
        self.groups_index = {}
        self.users_index = {}
        
        # 显示群聊白名单
        if groups:
            reply += "\n🏢 群聊白名单：\n"
            for idx, group_id in enumerate(groups):
                # 更新群聊索引
                self.groups_index[idx+1] = group_id
                try:
                    group_name = await self._get_group_name(bot, group_id)
                    reply += f"{idx+1}. {group_id} ({group_name})\n"
                except Exception:
                    reply += f"{idx+1}. {group_id}\n"
        
        # 显示用户白名单
        if users:
            reply += "\n👤 用户白名单：\n"
            for idx, user_id in enumerate(users):
                # 更新用户索引
                self.users_index[idx+1] = user_id
                try:
                    user_name = await self._get_user_name(bot, user_id)
                    reply += f"{idx+1}. {user_id} ({user_name})\n"
                except Exception:
                    reply += f"{idx+1}. {user_id}\n"
        
        # 添加白名单模式提示
        if self.main_config_data.get("ignore-mode") == "Whitelist":
            reply += "\n✅当前已启用白名单模式"
        else:
            reply += "\n⚠️当前未启用白名单模式，请设置ignore-mode=Whitelist生效"
        
        # 添加序号操作提示    
        reply += "\n\n💡 提示：可使用\"添加白名单 序号\"或\"移除白名单 序号\"操作白名单"
            
        await bot.send_text_message(reply_to, reply)

    def _save_config(self) -> bool:
        """保存配置到main_config.toml文件"""
        try:
            # 更新whitelist
            self.main_config_data["whitelist"] = self.whitelist
            self.main_config["XYBot"] = self.main_config_data  # 使用XYBot作为配置键
            
            # 保存配置到文件
            with open("main_config.toml", "w", encoding="utf-8") as f:
                toml.dump(self.main_config, f)
            
            logger.success("成功更新main_config.toml文件")
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False
    
    async def _get_group_name(self, bot: WechatAPIClient, group_id: str) -> str:
        """获取群聊名称"""
        try:
            if not group_id.endswith("@chatroom"):
                return "非群聊ID"
            
            group_info = await bot.get_chatroom_info(group_id)
            if group_info and "nickname" in group_info:
                return group_info["nickname"]
            return "未知群聊"
        except Exception as e:
            logger.error(f"获取群聊信息失败: {e}")
            return "获取失败"
    
    async def _get_user_name(self, bot: WechatAPIClient, user_wxid: str) -> str:
        """获取用户昵称"""
        try:
            if user_wxid.endswith("@chatroom"):
                return "非用户ID"
            
            # 尝试获取用户信息
            user_info = await bot.get_contact(user_wxid)
            if user_info and "nickname" in user_info:
                return user_info["nickname"]
            return "未知用户"
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return "获取失败"
