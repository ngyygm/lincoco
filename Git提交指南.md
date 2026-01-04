# Git 提交完整指南

## 目录
- [一、Git提交流程](#一git提交流程)
- [二、常用命令详解](#二常用命令详解)
- [三、提交信息规范](#三提交信息规范)
- [四、常见场景示例](#四常见场景示例)
- [五、解决中文乱码问题](#五解决中文乱码问题)
- [六、进阶技巧](#六进阶技巧)

---

## 一、Git提交流程

### 完整提交流程图
```
工作区 → 暂存区 → 本地仓库 → 远程仓库
```

### 标准流程
```bash
# 1. 查看当前状态
git status

# 2. 添加文件到暂存区
git add <文件名>

# 3. 提交到本地仓库
git commit -m "提交信息"

# 4. 推送到远程仓库
git push origin master
```

### 快速流程（适用于所有修改）
```bash
git add .
git commit -m "提交信息"
git push origin master
```

---

## 二、常用命令详解

### 1. git status - 查看状态
**作用**：查看工作区、暂存区的文件状态

**常用参数**：
- 无参数：显示详细状态
- `-s` 或 `--short`：简短格式显示

**示例**：
```bash
git status
git status -s
```

**输出解读**：
- `未跟踪的文件`：新文件，未被git管理
- `已修改`：已被git管理的文件，有修改
- `已暂存`：已添加到暂存区，等待提交

---

### 2. git add - 添加到暂存区
**作用**：将文件从工作区添加到暂存区

**常用参数**：
- `<文件名>`：添加指定文件
- `.`：添加当前目录所有修改
- `-A` 或 `--all`：添加所有修改（包括删除的文件）
- `-u`：仅添加已跟踪文件的修改

**示例**：
```bash
# 添加单个文件
git add README.md

# 添加当前目录所有修改
git add .

# 添加所有修改（包括删除的文件）
git add -A

# 添加多个文件
git add file1.py file2.py file3.py

# 添加特定类型的文件
git add *.py
```

---

### 3. git commit - 提交到本地仓库
**作用**：将暂存区的内容提交到本地仓库

**常用参数**：
- `-m "信息"`：指定提交信息
- `--amend`：修改最后一次提交
- `--no-edit`：修改提交但不修改提交信息
- `-a`：自动添加已跟踪文件的修改

**示例**：
```bash
# 标准提交
git commit -m "新增用户登录功能"

# 修改最后一次提交信息
git commit --amend -m "修正：新增用户登录功能"

# 修改最后一次提交内容（不修改信息）
git commit --amend --no-edit

# 跳过暂存区直接提交已跟踪文件
git commit -am "修复bug"
```

---

### 4. git push - 推送到远程仓库
**作用**：将本地提交推送到远程仓库

**常用参数**：
- `<远程仓库名> <分支名>`：指定远程仓库和分支
- `-u` 或 `--set-upstream`：建立跟踪关系
- `-f` 或 `--force`：强制推送（慎用）
- `--all`：推送所有分支

**示例**：
```bash
# 标准推送
git push origin master

# 首次推送并建立跟踪关系
git push -u origin master

# 推送到不同分支
git push origin develop

# 强制推送（会覆盖远程）
git push -f origin master

# 推送所有分支
git push --all origin
```

---

### 5. 其他常用命令

#### git diff - 查看差异
```bash
# 查看工作区与暂存区的差异
git diff

# 查看暂存区与本地仓库的差异
git diff --cached

# 查看工作区与本地仓库的差异
git diff HEAD
```

#### git log - 查看提交历史
```bash
# 简单查看
git log

# 美化显示
git log --oneline --graph --all

# 查看最近3次提交
git log -3

# 查看某文件的修改历史
git log README.md
```

#### git reset - 撤销操作
```bash
# 撤销暂存（保留修改）
git reset HEAD <文件>

# 撤销最后一次提交（保留修改）
git reset --soft HEAD^

# 撤销最后一次提交（丢弃修改）
git reset --hard HEAD^
```

---

## 三、提交信息规范

### 提交信息格式
```
<类型>: <简短描述>

<详细描述（可选）>
```

### 常用类型（Type）
- `feat`: 新功能（Feature）
- `fix`: 修复Bug
- `docs`: 文档更新
- `style`: 代码格式修改（不影响功能）
- `refactor`: 重构代码
- `perf`: 性能优化
- `test`: 添加测试
- `chore`: 构建/工具链相关
- `revert`: 回滚提交

### 提交信息示例

#### 好的提交信息
```bash
git commit -m "feat: 新增用户注册功能"
git commit -m "fix: 修复登录时密码校验错误"
git commit -m "docs: 更新API文档"
git commit -m "refactor: 优化数据库查询逻辑"
```

#### 带详细描述的提交
```bash
git commit -m "feat: 新增语音识别功能

- 添加FunASR集成
- 支持实时语音转文字
- 优化识别准确率

关闭 #123"
```

#### 不好的提交信息
```bash
git commit -m "修改"           # 太模糊
git commit -m "update"         # 类型不明确
git commit -m "fix bug"        # 缺少具体说明
git commit -m "2024-01-01"      # 不是描述
```

---

## 四、常见场景示例

### 场景1：首次提交新项目
```bash
# 初始化仓库
git init

# 添加所有文件
git add .

# 首次提交
git commit -m "feat: 初始化Lincoco项目"

# 添加远程仓库
git remote add origin https://github.com/ngyygm/lincoco.git

# 推送到远程（首次推送）
git push -u origin master
```

### 场景2：修改单个文件并提交
```bash
# 查看状态
git status

# 添加修改的文件
git add README.md

# 提交
git commit -m "docs: 更新项目说明文档"

# 推送
git push origin master
```

### 场景3：修改多个文件
```bash
# 查看修改了哪些文件
git status

# 添加所有修改
git add .

# 提交
git commit -m "feat: 新增对话管理器和任务上下文模块"

# 推送
git push origin master
```

### 场景4：删除文件
```bash
# 删除文件
git del file.txt
# 或直接在文件管理器删除

# 添加删除操作
git add -A

# 提交
git commit -m "chore: 删除不需要的测试文件"

# 推送
git push origin master
```

### 场景5：修改最后一次提交
```bash
# 添加忘记提交的文件
git add forgotten_file.py

# 修改最后一次提交（合并到上一次提交）
git commit --amend --no-edit

# 如果需要推送（已经推送到远程）
git push -f origin master
```

### 场景6：查看某次提交的详细信息
```bash
# 查看提交历史
git log

# 查看某次提交的详细信息
git show <commit-id>

# 查看某次提交修改了哪些文件
git show --name-only <commit-id>
```

### 场景7：分支操作
```bash
# 创建新分支
git branch develop

# 切换到新分支
git checkout develop

# 创建并切换（一步完成）
git checkout -b feature-voice

# 合并分支
git checkout master
git merge feature-voice

# 删除分支
git branch -d feature-voice
```

---

## 五、解决中文乱码问题

### 问题原因
Windows环境下，Git默认使用系统编码，导致中文提交信息出现乱码。

### 解决方案

#### 1. 配置Git使用UTF-8编码
```bash
# 设置提交信息编码
git config --global i18n.commitencoding utf-8

# 设置日志输出编码
git config --global i18n.logoutputencoding utf-8

# 设置GUI编码
git config --global gui.encoding utf-8

# 查看当前配置
git config --list | findstr i18n
```

#### 2. 修改已经乱码的提交
```bash
# 查看提交历史
git log

# 修改最后一次提交信息
git commit --amend -m "正确的中文信息"

# 强制推送到远程（慎用）
git push -f origin master
```

#### 3. PowerShell终端设置UTF-8
```powershell
# 设置PowerShell输出编码为UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 查看当前编码
[Console]::OutputEncoding
```

#### 4. Git Bash配置（如果使用Git Bash）
在 `~/.gitconfig` 文件中添加：
```ini
[core]
    quotepath = false
[i18n]
    commitencoding = utf-8
    logoutputencoding = utf-8
```

### 验证配置
```bash
# 测试提交信息
git commit -m "测试中文提交信息"

# 查看提交信息
git log -1 --pretty=format:"%s"
```

---

## 六、进阶技巧

### 1. 使用.gitignore排除不需要提交的文件

在项目根目录创建 `.gitignore` 文件：

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/

# 日志文件
*.log
logs/

# 临时文件
*.tmp
*.swp
*~

# IDE
.vscode/
.idea/
*.suo
*.user

# 操作系统
.DS_Store
Thumbs.db

# 模型文件（大文件）
*.pt
*.pth
*.safetensors
*.bin
```

### 2. 撤销本地修改
```bash
# 撤销单个文件的修改
git checkout -- <文件名>

# 撤销所有修改
git checkout -- .

# 撤销暂存区的修改
git reset HEAD <文件名>
```

### 3. 查看文件历史
```bash
# 查看文件的每次修改
git log -p <文件名>

# 查看文件的修改行数统计
git log --stat <文件名>
```

### 4. 标签管理
```bash
# 创建标签
git tag v1.0.0

# 推送标签
git push origin v1.0.0

# 推送所有标签
git push origin --tags

# 查看标签
git tag
```

### 5. 暂存当前工作
```bash
# 暂存当前修改
git stash

# 查看暂存列表
git stash list

# 恢复暂存
git stash pop

# 恢复指定暂存
git stash pop stash@{0}
```

### 6. 查看远程仓库信息
```bash
# 查看远程仓库
git remote -v

# 添加远程仓库
git remote add origin <URL>

# 修改远程仓库地址
git remote set-url origin <URL>

# 删除远程仓库
git remote remove origin
```

---

## 七、常见问题排查

### 问题1：推送时提示认证失败
```bash
# 解决方案1：使用HTTPS方式
git remote set-url origin https://github.com/用户名/仓库名.git

# 解决方案2：配置SSH密钥（推荐长期使用）
# 1. 生成SSH密钥
ssh-keygen -t rsa -C "your_email@example.com"

# 2. 复制公钥内容（在 ~/.ssh/id_rsa.pub）

# 3. 在GitHub设置中添加SSH密钥
# Settings -> SSH and GPG keys -> New SSH key
```

### 问题2：提交后后悔了
```bash
# 撤销最后一次提交（保留修改）
git reset --soft HEAD^

# 撤销最后一次提交（丢弃修改）
git reset --hard HEAD^

# 如果已经推送，需要强制推送
git push -f origin master
```

### 问题3：合并冲突
```bash
# 1. 查看冲突文件
git status

# 2. 手动编辑解决冲突
# 打开冲突文件，查找 <<<<<<<, =======, >>>>>>> 标记

# 3. 标记冲突已解决
git add <冲突文件>

# 4. 完成合并
git commit
```

### 问题4：文件太大无法推送
```bash
# 解决方案：使用Git LFS（大文件存储）
git lfs install
git lfs track "*.zip"
git add .gitattributes
git commit -m "配置LFS"
git push origin master
```

---

## 八、快速参考卡片

### 常用命令速查表
| 命令 | 作用 | 常用参数 |
|------|------|----------|
| `git status` | 查看状态 | `-s` 简短显示 |
| `git add` | 添加文件 | `.`, `-A`, `-u` |
| `git commit` | 提交 | `-m`, `--amend` |
| `git push` | 推送 | `-u`, `-f`, `--all` |
| `git pull` | 拉取 | `--rebase` |
| `git log` | 查看历史 | `--oneline`, `--graph` |
| `git diff` | 查看差异 | `--cached` |
| `git reset` | 撤销 | `--soft`, `--hard` |

### 提交信息模板
```bash
# 新功能
git commit -m "feat: 描述新功能"

# 修复Bug
git commit -m "fix: 修复xxx问题"

# 文档更新
git commit -m "docs: 更新xxx文档"

# 代码优化
git commit -m "refactor: 重构xxx模块"

# 性能优化
git commit -m "perf: 优化xxx性能"
```

---

## 九、推荐工作流

### 日常开发流程
```bash
# 1. 开始开发前，拉取最新代码
git pull origin master

# 2. 创建新分支（可选）
git checkout -b feature-xxx

# 3. 开发代码...

# 4. 查看修改
git status
git diff

# 5. 添加修改
git add .

# 6. 提交
git commit -m "feat: 描述你的修改"

# 7. 推送
git push origin feature-xxx

# 8. 合并到主分支（通过PR或直接合并）
git checkout master
git merge feature-xxx
git push origin master
```

---

## 附录：相关资源

- [Git官方文档](https://git-scm.com/doc)
- [GitHub文档](https://docs.github.com/)
- [Pro Git中文版](https://git-scm.com/book/zh/v2)
- [GitHub提交信息规范](https://www.conventionalcommits.org/)

---

**最后更新时间**: 2025年1月5日
**维护者**: Lincoco项目组

