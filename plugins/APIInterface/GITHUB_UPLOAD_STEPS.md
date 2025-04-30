# 上传到GitHub的步骤

## 1. 创建GitHub仓库

1. 登录GitHub账号
2. 点击右上角"+"按钮，选择"New repository"
3. 填写仓库名称，例如"XYBotV2-APIInterface"
4. 添加描述：XYBotV2 微信机器人API接口插件
5. 选择公开（Public）或私有（Private）
6. 不要初始化仓库（不要勾选"Add a README file"）
7. 点击"Create repository"

## 2. 初始化本地Git仓库并推送

打开命令行，进入插件目录，执行以下命令：

```bash
# 初始化Git仓库
git init

# 添加所有文件到暂存区
git add .

# 提交更改
git commit -m "初始提交APIInterface插件"

# 添加远程仓库（替换为你的GitHub仓库URL）
git remote add origin https://github.com/你的用户名/XYBotV2-APIInterface.git

# 推送到GitHub
git push -u origin master
```

## 3. 验证上传

1. 刷新GitHub仓库页面
2. 确认所有文件都已上传成功
3. 检查README.md是否正确显示

## 4. 后续维护

每次更新插件后，可以使用以下命令推送更新：

```bash
git add .
git commit -m "更新说明"
git push
``` 