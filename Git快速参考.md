# Git 快速参考卡

## 🚀 最常用命令（90%的场景）

### 标准提交流程
```bash
git status                    # 查看修改了什么
git add .                     # 添加所有修改
git commit -m "feat: 描述"    # 提交
git push origin master        # 推送到远程
```

### 快速提交（跳过查看状态）
```bash
git add . && git commit -m "feat: 描述" && git push origin master
```

---

## 📝 提交信息类型速查

| 类型 | 含义 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat: 新增用户登录功能` |
| `fix` | 修复Bug | `fix: 修复登录时密码校验错误` |
| `docs` | 文档 | `docs: 更新API文档` |
| `refactor` | 重构 | `refactor: 优化数据库查询` |
| `style` | 格式 | `style: 调整代码缩进` |
| `perf` | 性能 | `perf: 优化查询速度` |
| `test` | 测试 | `test: 添加单元测试` |
| `chore` | 构建/工具 | `chore: 升级依赖包` |

---

## 🔧 常用操作

### 查看相关
```bash
git status              # 查看状态
git log                 # 查看提交历史
git log --oneline       # 简短显示历史
git diff                # 查看工作区修改
git diff --cached       # 查看暂存区修改
```

### 添加文件
```bash
git add 文件名          # 添加指定文件
git add .               # 添加所有修改
git add -A              # 添加所有（包括删除）
```

### 提交相关
```bash
git commit -m "信息"    # 提交
git commit --amend      # 修改最后一次提交
git commit -am "信息"   # 跳过暂存区提交
```

### 推送相关
```bash
git push origin master  # 推送到远程
git push -u origin dev  # 首次推送并建立跟踪
git push -f origin master  # 强制推送（慎用）
```

### 撤销操作
```bash
git checkout -- 文件    # 撤销文件修改
git reset HEAD 文件     # 撤销暂存
git reset --soft HEAD^  # 撤销最后一次提交（保留修改）
git reset --hard HEAD^  # 撤销最后一次提交（丢弃修改）
```

---

## 🎯 常见场景解决方案

### 场景1：修改了文件但后悔了
```bash
git checkout -- 文件名
```

### 场景2：已经git add但想撤销
```bash
git reset HEAD 文件名
```

### 场景3：已经commit但想修改
```bash
# 方式1：添加遗漏的文件到上一次提交
git add 遗漏的文件
git commit --amend --no-edit

# 方式2：修改上一次提交信息
git commit --amend -m "新的提交信息"
```

### 场景4：已经push到远程了
```bash
# 如果修改了本地提交，需要强制推送
git push -f origin master
```

### 场景5：拉取远程最新代码
```bash
git pull origin master
```

### 场景6：合并冲突
```bash
# 1. 手动编辑冲突文件
# 2. 标记冲突已解决
git add 冲突文件
# 3. 完成提交
git commit
```

### 场景7：推送时DNS解析失败（无法连接GitHub）
**问题现象**：
```
fatal: unable to access 'https://github.com/xxx/xxx.git/':
Failed to connect to github.com port 443
```

**原因分析**：DNS解析问题，常见于中国大陆访问GitHub

**解决方案1：修改hosts文件（推荐，最直接）**
```bash
# 1. 查询GitHub的IP地址
nslookup github.com 8.8.8.8

# 2. 使用管理员权限打开PowerShell，添加hosts记录
echo <GitHub的IP> github.com >> C:\Windows\System32\drivers\etc\hosts

# 示例：
echo 20.205.243.166 github.com >> C:\Windows\System32\drivers\etc\hosts

# 3. 再次推送
git push origin master
```

**解决方案2：使用SSH协议（长期使用更稳定）**
```bash
# 1. 切换远程仓库为SSH地址
git remote set-url origin git@github.com:ngyygm/lincoco.git

# 2. 配置SSH密钥（首次使用需要）
ssh-keygen -t rsa -C "your_email@example.com"

# 3. 复制公钥到GitHub（~/.ssh/id_rsa.pub内容）
# Settings -> SSH and GPG keys -> New SSH key

# 4. 推送
git push origin master
```

**解决方案3：配置公共DNS服务器**
```powershell
# 使用Google DNS或阿里DNS
Set-DnsClientServerAddress -InterfaceAlias "以太网" -ServerAddresses "8.8.8.8","114.114.114.114"
```

---

## 📋 分支操作

```bash
git branch              # 查看本地分支
git branch -r           # 查看远程分支
git branch dev          # 创建分支
git checkout dev        # 切换分支
git checkout -b feature  # 创建并切换
git checkout master     # 切换回主分支
git merge dev           # 合并dev到当前分支
git branch -d dev       # 删除本地分支
```

---

## ⚙️ 配置中文编码

如果遇到中文乱码，运行以下命令：

```bash
git config --global i18n.commitencoding utf-8
git config --global i18n.logoutputencoding utf-8
git config --global core.quotepath false
```

### 验证配置
```bash
git config --list | findstr i18n
```

---

## 🚨 注意事项

1. **提交信息使用英文**（避免中文乱码）
   ```bash
   # ❌ 不推荐
   git commit -m "修改bug"
   
   # ✅ 推荐
   git commit -m "fix: resolve login issue"
   ```

2. **强制推送前要确认**
   ```bash
   # ❌ 危险操作
   git push -f origin master
   
   # ✅ 确认后再执行
   # 只在确定要覆盖远程代码时使用
   ```

3. **使用.gitignore排除不必要文件**
   ```bash
   # Python项目常用排除
   __pycache__/
   *.py[cod]
   *.log
   venv/
   ```

---

## 📞 快速帮助

```bash
git help 命令           # 查看命令帮助
git help commit         # 例如查看commit帮助
```

---

## 💡 小技巧

1. **设置别名**（简化命令）
   ```bash
   git config --global alias.st status
   git config --global alias.co checkout
   git config --global alias.br branch
   git config --global alias.ci commit
   
   # 使用: git st 代替 git status
   ```

2. **查看最后一次提交**
   ```bash
   git show
   ```

3. **查看文件修改历史**
   ```bash
   git log -p 文件名
   ```

4. **比较两次提交的差异**
   ```bash
   git diff 提交ID1 提交ID2
   ```

---

**记住：90%的时候只需要用 `git status`, `git add`, `git commit`, `git push` 这4个命令！**

有问题？查看详细文档：`Git提交指南.md`

