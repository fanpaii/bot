# APIInterface 插件

## 简介

APIInterface是一个XYBotV2微信机器人插件，用于通过简单的命令调用各种API接口，支持多种返回类型，包括文本、图片、视频和JSON数据处理。它提供了星座运势、短剧搜索、小说搜索等功能。

## 功能特点

- 支持多种API调用和返回类型处理
- 支持图片、视频、文本等多媒体消息发送
- 可通过命令动态添加/删除API接口
- 内置星座运势查询功能
- 内置短剧搜索功能
- 内置小说搜索功能
- 命令权限管理
- 白名单过滤

## 安装方法

1. 确保已安装XYBotV2机器人框架
2. 将本插件复制到XYBotV2的`plugins`目录下
3. 安装依赖包：`pip install -r requirements.txt`
4. 重启XYBotV2服务

## 依赖项

- Python 3.11+
- aiohttp
- tomli (Python 3.10及以下) / tomllib (Python 3.11+)
- tomli_w
- PIL (Pillow)
- loguru

## 配置文件

插件包含以下配置文件：

- `config.toml`: 插件基本配置
- `api_config.toml`: API接口配置
- `command_map.toml`: 命令映射配置

## 使用方法

### 基本命令

- `测试图片` - 发送测试图片，验证图片功能
- `API列表` - 列出所有可用API和命令
- `运势占卜` / `运势` - 获取运势占卜图片
- `[星座名]` - 获取指定星座运势，例如：`白羊`、`金牛`等
- `短剧[关键词]` - 搜索短剧，例如：`短剧总裁`
- `显示剩余` - 显示剩余短剧搜索结果
- `小说[关键词]` - 搜索小说，例如：`小说玄幻`

### 管理命令

- `添加API 命令 URL 请求方法 返回类型 描述` - 添加新的API接口
- `删除API 命令` - 删除API接口

## 示例

```
# 查询星座运势
白羊

# 搜索短剧
短剧总裁

# 搜索小说
小说仙侠

# 添加新的API
添加API 笑话 https://api.example.com/joke get text 随机获取笑话
```

## 开发者信息

- 作者：vm
- 版本：1.0.1
- 框架：XYBotV2

## 许可证

MIT License 