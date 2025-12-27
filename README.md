# Lincoco Voice Chatbot

语音对话机器人项目

## Git 配置说明

在上传到 Git 仓库之前，您需要先配置 Git 用户信息：

### 1. 配置 Git 用户信息（必需）

在命令行中运行以下命令（请替换为您的实际信息）：

```bash
git config --global user.name "您的姓名"
git config --global user.email "您的邮箱"
```

如果只想为当前项目配置（不使用 `--global`）：

```bash
git config user.name "您的姓名"
git config user.email "您的邮箱"
```

### 2. 创建初始提交

配置完用户信息后，运行：

```bash
git commit -m "Initial commit"
```

### 3. 连接到远程仓库

在 GitHub/Gitee 等平台创建新仓库后，运行：

```bash
# 添加远程仓库地址
git remote add origin <您的仓库地址>

# 推送到远程仓库
git push -u origin master
```

### 注意事项

- `.gitignore` 文件已经配置，会自动忽略以下内容：
  - Python 缓存文件（`__pycache__/`）
  - 日志文件（`logs/`）
  - TTS 音频缓存（`tts_cache/`）
  - 环境变量文件（`.env`）
  - IDE 配置文件
  - 大型模型和音频文件

- 如果项目中有重要的配置文件（如 JSON），请检查 `.gitignore` 文件，确保它们不会被忽略。

