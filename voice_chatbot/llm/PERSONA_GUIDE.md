# 人设系统使用指南

## 📖 概述

人设系统用于管理LLM的system prompt，支持从文件加载人设、参数动态替换和运行时切换。通过人设系统，你可以轻松创建和管理不同角色的AI助手。

## 📁 文件结构

人设文件存储在 `llm/personas/` 目录下，支持以下格式：
- **Markdown格式** (`.md`) - 推荐使用
- **文本格式** (`.txt`)

## ✍️ 如何编写人设文件

### 基本格式

人设文件是纯文本文件，直接写入你希望AI助手遵循的指令和角色设定。例如：

```markdown
你叫林可可，是16岁的女高中生，性格活泼开朗，谈吐风趣。
当前时间是 {{current_time}}，今天是 {{current_date}}，{{weekday}}。
你正在和{{user_name}}聊天，回答简短，控制在60字以内，不要用表情。
```

### 编写建议

1. **明确角色定位**：清楚描述AI的身份、年龄、性格等特征
2. **设定对话风格**：说明回答的长度、语气、是否使用表情等
3. **使用变量**：利用特殊变量让AI了解当前时间和用户信息
4. **简洁明了**：避免过长的人设描述，保持重点突出

### 示例人设文件

#### 示例1：活泼高中生（default.md）

```markdown
你叫林可可，是16岁的女高中生，性格活泼开朗，谈吐风趣。
当前时间是 {{current_time}}，今天是 {{current_date}}，{{weekday}}。
你正在和{{user_name}}聊天，回答简短，控制在60字以内，不要用表情。
```

#### 示例2：专业助手（assistant.md）

```markdown
你是一个专业的AI助手，性格温和、专业、有帮助。
当前时间是 {{current_time}}，今天是 {{current_date}}，{{weekday}}。
你正在为{{user_name}}提供帮助，请用专业、清晰的语言回答问题。
```

#### 示例3：幽默朋友

```markdown
你是一个幽默风趣的朋友，喜欢开玩笑，但不会冒犯他人。
今天是{{weekday}}，现在是{{current_time}}，你正在和{{user_name}}聊天。
你的回答要轻松有趣，但也要有实际帮助，控制在100字以内。
```

## 🔧 特殊变量说明

人设系统支持在文本中使用 `{{变量名}}` 格式的特殊变量，这些变量会在每次调用LLM时自动替换为实际值。

### 内置变量

#### 1. `{{current_time}}`
- **含义**：当前时间
- **格式**：`HH:MM:SS`（24小时制）
- **示例**：`22:30:45`
- **使用场景**：让AI知道当前时间，可以做出时间相关的回应

```markdown
现在是{{current_time}}，如果是晚上，你可以提醒用户早点休息。
```

#### 2. `{{current_date}}`
- **含义**：当前日期
- **格式**：`YYYY-MM-DD`
- **示例**：`2025-12-24`
- **使用场景**：让AI知道今天是哪一天，可以做出日期相关的回应

```markdown
今天是{{current_date}}，距离新年还有几天。
```

#### 3. `{{weekday}}`
- **含义**：星期几
- **格式**：中文格式（星期一、星期二、...、星期日）
- **示例**：`星期二`、`星期六`
- **使用场景**：让AI知道今天是星期几，可以做出工作日/周末相关的回应

```markdown
今天是{{weekday}}，如果是周末，你可以建议用户放松一下。
```

#### 4. `{{user_name}}`
- **含义**：用户名
- **格式**：字符串（从配置文件读取）
- **默认值**：`"用户"`
- **配置位置**：`config.py` 中的 `llm.user_name`
- **使用场景**：让AI知道正在和谁对话，可以个性化称呼

```markdown
你正在和{{user_name}}聊天，请用友好的语气称呼对方。
```

### 变量使用规则

1. **格式要求**：变量必须使用双大括号 `{{变量名}}`，变量名只能包含字母、数字和下划线
2. **大小写敏感**：变量名区分大小写，必须完全匹配
3. **实时替换**：变量在每次调用LLM时实时替换，确保时间等参数始终最新
4. **未定义变量**：如果使用了未定义的变量，系统会保留原始文本并记录警告日志

### 变量使用示例

```markdown
# 正确使用
你好，{{user_name}}！现在是{{current_time}}，今天是{{current_date}}，{{weekday}}。

# 错误使用（单大括号）
你好，{user_name}！  # 不会被替换

# 错误使用（变量名错误）
你好，{{userName}}！  # 不会被替换，会保留原样
```

## ⚙️ 配置说明

### 配置文件位置

配置文件位于 `utils/config.py`，在 `llm` 配置节中可以设置以下参数：

```python
"llm": {
    "persona_name": "default",        # 默认人设名称（不含扩展名）
    "persona_dir": "llm/personas",    # 人设文件目录（相对于voice_chatbot目录）
    "user_name": "用户",              # 用户名（用于{{user_name}}变量）
    "system_prompt": "...",           # 系统提示词（作为fallback，向后兼容）
}
```

### 配置参数说明

#### `persona_name`
- **类型**：字符串
- **默认值**：`"default"`
- **说明**：指定默认使用的人设文件名称（不含扩展名）
- **示例**：`"persona_name": "assistant"` 会加载 `llm/personas/assistant.md`

#### `persona_dir`
- **类型**：字符串
- **默认值**：`"llm/personas"`
- **说明**：人设文件目录路径，相对于 `voice_chatbot` 目录
- **注意**：一般不需要修改，除非你想使用其他目录

#### `user_name`
- **类型**：字符串
- **默认值**：`"用户"`
- **说明**：用户名，用于 `{{user_name}}` 变量替换
- **示例**：`"user_name": "小明"` 会在人设文件中将 `{{user_name}}` 替换为 `"小明"`

#### `system_prompt`
- **类型**：字符串
- **默认值**：原有的system_prompt内容
- **说明**：保留作为向后兼容的fallback，当人设文件不存在时使用
- **注意**：如果人设文件存在，此配置不会生效

## 🚀 使用方法

### 1. 创建人设文件

在 `llm/personas/` 目录下创建新的 `.md` 或 `.txt` 文件，例如：

```bash
# 创建新的人设文件
echo "你是一个专业的AI助手..." > llm/personas/my_persona.md
```

### 2. 配置默认人设

在 `config.py` 中设置默认人设：

```python
"llm": {
    "persona_name": "my_persona",  # 使用my_persona.md作为默认人设
}
```

### 3. 运行时切换人设

在代码中动态切换人设：

```python
from voice_chatbot.dialogue.manager import DialogueManager

# 创建对话管理器
manager = DialogueManager(config)

# 切换人设
manager.switch_persona("assistant")

# 查询当前人设
current_persona = manager.get_current_persona_name()
print(f"当前人设: {current_persona}")
```

### 4. 查看所有人设

```python
from voice_chatbot.llm.persona_manager import PersonaManager

manager = PersonaManager(config)
personas = manager.list_personas()
print(f"可用人设: {personas}")
```

## 📝 最佳实践

### 1. 人设文件命名
- 使用有意义的名称，如 `default.md`、`assistant.md`、`friend.md`
- 避免使用特殊字符和空格
- 建议使用小写字母和下划线

### 2. 人设内容编写
- **简洁明了**：避免过长的人设描述，保持重点突出
- **明确指令**：清楚说明AI应该做什么，不应该做什么
- **使用变量**：合理使用特殊变量，让AI了解上下文
- **测试验证**：创建人设后，测试一下效果是否符合预期

### 3. 变量使用
- **适度使用**：不要过度使用变量，保持人设文本的可读性
- **合理组合**：可以将多个变量组合使用，如 `今天是{{current_date}}，{{weekday}}`
- **注意格式**：确保变量格式正确，使用双大括号

### 4. 人设管理
- **版本控制**：将人设文件纳入版本控制，方便管理和回滚
- **文档记录**：为人设文件添加注释，说明用途和特点
- **定期更新**：根据使用效果，定期优化人设内容

## 🔍 故障排查

### 问题1：人设文件未生效

**可能原因**：
- 人设文件路径不正确
- 人设文件名与配置不匹配
- 文件编码不是UTF-8

**解决方法**：
1. 检查 `llm/personas/` 目录下是否存在对应的人设文件
2. 确认配置中的 `persona_name` 与文件名（不含扩展名）一致
3. 确保文件使用UTF-8编码保存

### 问题2：变量未替换

**可能原因**：
- 变量格式错误（使用了单大括号或格式不正确）
- 变量名拼写错误
- 变量未定义

**解决方法**：
1. 检查变量格式是否为 `{{变量名}}`
2. 确认变量名拼写正确（区分大小写）
3. 查看日志，确认是否有警告信息

### 问题3：切换人设失败

**可能原因**：
- 人设文件不存在
- 文件路径错误
- 权限问题

**解决方法**：
1. 使用 `list_personas()` 方法查看可用的人设列表
2. 确认要切换的人设文件存在
3. 检查文件读取权限

## 📚 扩展功能

### 自定义变量处理器

如果需要添加自定义变量，可以通过代码注册：

```python
from voice_chatbot.llm.variable_processor import VariableProcessor
from voice_chatbot.llm.persona_manager import PersonaManager

# 创建变量处理器
processor = VariableProcessor(config)

# 注册自定义变量
def get_weather():
    # 这里可以调用天气API
    return "晴天"

processor.register_processor("weather", get_weather)

# 在人设文件中使用
# 今天是{{weather}}，适合出去走走。
```

### 人设文件热重载（未来功能）

未来可能会支持人设文件的热重载功能，文件修改后自动重新加载，无需重启系统。

## 📖 相关文档

- **项目大纲**：`PROJECT_OUTLINE.md` - 查看人设管理系统的架构设计
- **开发日志**：`DEVELOPMENT_LOG.md` - 查看实现细节和更新记录
- **测试文件**：`test_persona_system.py` - 查看使用示例和测试代码

## 💡 示例集合

### 专业助手
```markdown
你是一个专业的AI助手，性格温和、专业、有帮助。
当前时间是 {{current_time}}，今天是 {{current_date}}，{{weekday}}。
你正在为{{user_name}}提供帮助，请用专业、清晰的语言回答问题。
```

### 幽默朋友
```markdown
你是一个幽默风趣的朋友，喜欢开玩笑，但不会冒犯他人。
今天是{{weekday}}，现在是{{current_time}}，你正在和{{user_name}}聊天。
你的回答要轻松有趣，但也要有实际帮助，控制在100字以内。
```

### 学习伙伴
```markdown
你是一个学习伙伴，擅长帮助{{user_name}}学习和理解知识。
今天是{{current_date}}，{{weekday}}，现在是{{current_time}}。
请用简单易懂的方式解释概念，鼓励用户提问和思考。
```

### 心理咨询师
```markdown
你是一个温和、专业的心理咨询师，擅长倾听和提供建议。
现在是{{current_time}}，今天是{{current_date}}，{{weekday}}。
你正在和{{user_name}}对话，请用同理心和专业态度回应。
```

---

**最后更新**：2025-12-24  
**维护者**：AI Assistant





