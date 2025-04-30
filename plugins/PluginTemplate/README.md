# XYBot 插件模板

这是一个符合 XYBot 开发规范的插件模板，可以作为开发新插件的起点。

## 功能

1. 基础消息响应：处理文本和图片消息
2. 关键词触发：当消息中包含指定关键词时触发响应
3. 定时任务：支持定期执行的后台任务
4. 完整生命周期：包含启用/禁用回调处理

## 使用方法

1. 复制整个 `PluginTemplate` 目录，并重命名为你的插件名称
2. 修改 `main.py` 中的类名和功能逻辑
3. 更新 `config.toml` 中的配置选项
4. 修改本 README 文件，描述你的插件功能

### 消息响应

插件默认响应包含触发关键词的文本消息，可以通过配置文件修改关键词。

```
用户：你好，机器人
机器人：你好！我是一个插件模板。
        你发送的消息是: 你好，机器人
```

### 定时任务

插件包含一个每30分钟执行一次的定时任务。可以根据需求修改执行频率和任务内容。

## 配置选项

在 `config.toml` 文件中可以配置以下选项：

```toml
[basic]
# 是否启用插件
enable = false
# 触发关键词
trigger_keyword = "你好"

# 其他配置示例
[other_section]
# 其他配置选项
other_option = "默认值"

# 功能特定配置示例 
[feature]
# 是否启用某功能
enable = true
# 功能参数
parameter = "value"
# 功能列表参数
list_parameter = ["item1", "item2", "item3"]
```

## 开发指南

### 添加新的消息处理器

可以添加各种类型的消息处理器：

```python
@on_voice_message(priority=50)
async def handle_voice(self, bot: WechatAPIClient, message: Dict[str, Any]) -> bool:
    if not self.enable:
        return True
    
    # 处理语音消息
    logger.info(f"收到语音消息")
    
    return True  # 允许后续插件处理
```

### 添加定时任务

可以添加不同类型的定时任务：

```python
# 每天早上8点执行
@schedule('cron', hour=8, minute=0)
async def daily_task(self, bot: WechatAPIClient):
    if not self.enable:
        return
    
    logger.info("执行每日任务")
    # 执行任务逻辑
    
# 在指定日期执行一次
@schedule('date', run_date='2025-01-01 00:00:00')
async def special_task(self, bot: WechatAPIClient):
    if not self.enable:
        return
    
    logger.info("执行特殊任务")
    # 执行任务逻辑
```

## 注意事项

1. 所有消息处理函数都应检查 `self.enable` 确保插件已启用
2. 明确返回 `True` 或 `False` 控制消息是否继续传递给其他插件
3. 使用 `try/except` 捕获异常，避免插件崩溃影响整个机器人
4. 使用 `logger` 记录关键操作和错误信息，便于调试

## 作者

XYBot 开发团队 