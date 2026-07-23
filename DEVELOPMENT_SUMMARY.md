# AI服务适配器项目开发经验总结

## 一、项目概述

本项目实现了一个基于Playwright的AI服务适配器，将Web端AI服务（DeepSeek和ChatGPT）转换为OpenAI兼容的API接口。项目核心目标是提供统一的API接口，支持多AI服务切换，并确保富文本内容（表格、列表、代码块等）的正确渲染。

**核心功能：**
- DeepSeek Web端服务适配
- ChatGPT Web端服务适配
- AutoAdapter自动切换适配器（故障转移）
- OpenAI兼容API接口（流式/非流式）
- 富文本内容提取与Markdown转换
- 浏览器反检测机制

---

## 二、关键技术点

### 1. 适配器模式设计

采用适配器模式实现多AI服务支持，定义统一接口`BaseAIAdapter`，各服务适配器实现具体逻辑：

- **BaseAIAdapter**：抽象基类，定义初始化、发送消息、清理等核心方法
- **DeepseekAdapter**：适配DeepSeek网页端服务
- **ChatGPTAdapter**：适配ChatGPT网页端服务
- **AutoAdapter**：自动切换适配器，支持故障转移

**关键设计：**
- 通过`MODEL_ADAPTER_MAP`实现模型到适配器的动态路由
- 支持延迟初始化，避免服务启动时阻塞
- 线程安全的消息发送（使用`asyncio.Lock`）
- AutoAdapter支持随机选择和自动重试机制

### 2. 反检测技术

为绕过Cloudflare和网站反爬虫机制，采用以下措施：

- 修改浏览器指纹：`navigator.webdriver`、`navigator.plugins`、`navigator.languages`
- 使用真实浏览器通道（msedge）而非纯Headless模式
- 设置真实User-Agent
- 模拟人类操作延迟（`asyncio.sleep`）
- 添加`--disable-blink-features=AutomationControlled`启动参数

### 3. 富文本内容提取与转换

**核心问题**：网页端展示富文本，但直接提取纯文本会丢失格式。

**解决方案：**
- 使用`query_selector`定位包含HTML内容的元素
- 提取`innerHTML`而非`textContent`
- 使用`markdownify`库将HTML转换为Markdown格式
- 通过正则表达式清洗无关内容（引用标记、复制/下载按钮文本等）
- 修复代码块语言标识符位置错误

### 4. 异步编程模型

整个适配器采用异步设计：

- 使用`asyncio`实现并发控制
- `async/await`处理浏览器操作
- 流式响应支持（Server-Sent Events）
- 超时控制和错误重试机制
- FastAPI异步端点支持

### 5. API兼容层

提供OpenAI兼容的API接口：

- `/v1/chat/completions`：支持流式和非流式响应
- `/v1/models`：列出可用模型
- `/health`：健康检查
- `/api/chat/send`：简化的聊天接口

### 6. 环境变量配置

采用`.env`文件管理敏感配置：

- 使用`python-dotenv`加载环境变量
- 支持`DEEPSEEK_PHONE`、`DEEPSEEK_PASSWORD`、`CHATGPT_EMAIL`、`CHATGPT_PASSWORD`
- `.env`文件加入`.gitignore`防止敏感信息泄露
- 提供`.env.example`模板文件

---

## 三、难点及解决方案

### 难点1：登录流程自动化

**问题**：网站登录页面结构复杂，元素选择器不稳定，按钮不可见等问题频繁出现。

**解决方案：**
- 多选择器策略：为每个元素提供多个备选选择器
- 可见性检查：在点击前检查元素是否可见（`offsetParent !== null`）
- JavaScript强制点击：当常规点击失败时使用`el.click()`
- 滚动到元素：确保元素在视口内

### 难点2：Cloudflare反爬虫验证

**问题**：ChatGPT网站使用Cloudflare保护，自动化浏览器容易被检测。

**解决方案：**
- 添加反检测脚本修改浏览器指纹
- 等待Cloudflare验证完成（`wait_for_load_state("networkidle")`）
- 处理验证码复选框点击
- 使用非Headless模式提升通过概率
- 添加`--disable-blink-features=AutomationControlled`启动参数

### 难点3：富文本格式丢失

**问题**：直接提取文本会丢失表格、代码块、列表等结构信息。

**解决方案：**
- 提取HTML内容而非纯文本
- 使用`markdownify`库进行格式转换
- 正则表达式清洗无关内容：
  ```python
  markdown = re.sub(r'\[-?\d+\]\([^)]+\)', '', markdown)  # 去除引用标记
  markdown = re.sub(r'(\w+)\n+```', r'```\1', markdown)    # 修复代码块格式
  ```
- 过滤"复制"、"下载"等无关按钮文本

### 难点4：多适配器兼容性

**问题**：不同AI服务的页面结构差异大，需要统一接口。

**解决方案：**
- 定义`SELECTORS`字典，每个适配器维护自己的选择器
- 基类提供通用方法（`create_completion`、`create_completion_stream`）
- 延迟初始化：按需创建适配器实例
- 独立的浏览器上下文：每个适配器使用独立的浏览器实例

### 难点5：消息发送可靠性

**问题**：输入框定位困难，输入内容可能不被页面识别。

**解决方案：**
- 多种输入方式：`type()`、`evaluate()`设置value并触发input事件
- 输入验证：检查输入后的值是否与预期一致
- 多选择器定位输入框：textarea、contenteditable div等

### 难点6：AutoAdapter故障转移

**问题**：如何实现多个适配器的自动切换和故障转移。

**解决方案：**
- 随机选择适配器：使用`random.shuffle()`打乱适配器顺序
- 异常捕获与重试：当选中的适配器失败时，自动尝试其他适配器
- 对话总结传递：保持不同适配器间的对话状态同步
- 命名请求拦截：处理Chatbox的对话命名请求

---

## 四、技术选型依据

### 1. Playwright vs Selenium

**选择Playwright的原因：**
- 更好的异步支持（原生`async/await`）
- 更强大的元素定位和操作能力
- 内置浏览器自动化能力（无需额外驱动）
- 更好的性能和稳定性
- 活跃的社区和更新频率

### 2. FastAPI vs Flask

**选择FastAPI的原因：**
- 原生异步支持
- 自动生成API文档（Swagger UI）
- 类型提示支持（Pydantic）
- 更好的性能（基于Starlette）
- 现代Python开发体验

### 3. 环境变量配置

**选择`.env`文件的原因：**
- 集中管理敏感配置
- 避免硬编码凭据
- 支持不同环境（开发/测试/生产）
- 易于版本控制（`.env.example`模板）

### 4. 适配器模式

**选择适配器模式的原因：**
- 统一接口，便于扩展新AI服务
- 解耦具体实现与调用方
- 支持运行时动态切换
- 符合开闭原则

---

## 五、配置说明

### 1. 环境变量配置

```
# .env 文件示例
DEEPSEEK_PHONE=your_phone_number
DEEPSEEK_PASSWORD=your_password
CHATGPT_EMAIL=your_email@example.com
CHATGPT_PASSWORD=your_password
```

### 2. 适配器配置

```python
# AdapterConfig配置项
AdapterConfig(
    headless=False,      # 是否使用无头模式
    timeout=120,         # 超时时间（秒）
    page_load_timeout=60,# 页面加载超时（秒）
    channel="msedge",    # 浏览器通道（msedge/chromium/firefox/webkit）
    executable_path=None,# 自定义浏览器路径
    user_data_dir=None,  # 用户数据目录（用于保存登录状态）
)
```

### 3. 模型路由映射

```python
MODEL_ADAPTER_MAP = {
    "deepseek-k2.6": ("deepseek", DeepseekAdapter),
    "deepseek": ("deepseek", DeepseekAdapter),
    "chatgpt": ("chatgpt", ChatGPTAdapter),
    "chatgpt-4o": ("chatgpt", ChatGPTAdapter),
    "gpt-4": ("chatgpt", ChatGPTAdapter),
    "gpt-4o": ("chatgpt", ChatGPTAdapter),
    "gpt-5.5": ("chatgpt", ChatGPTAdapter),
    "auto-web": ("auto", None),
}
```

### 4. API使用示例

#### 非流式请求
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-k2.6",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

#### 流式请求
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-k2.6",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'
```

#### 自动切换
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-web",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

---

## 六、开发经验与教训

### 经验1：环境变量配置迁移

**背景**：最初使用硬编码凭据，存在安全风险。

**解决方案**：引入`python-dotenv`，将凭据迁移到`.env`文件。

**关键点：**
- 在适配器中添加`load_dotenv()`调用
- 创建`.env.example`模板文件
- 更新`.gitignore`排除`.env`文件
- 修改环境变量名称（`KIMI_*` → `DEEPSEEK_*`）

### 经验2：Kimi到DeepSeek重命名

**背景**：项目中实际使用的是DeepSeek服务，但代码和文档中使用了"Kimi"名称。

**解决方案**：进行全面的重命名工作。

**关键点：**
- 重命名文件：`kimi_adapter.py` → `deepseek_adapter.py`
- 重命名类：`KimiAdapter` → `DeepseekAdapter`
- 替换所有字符串：`Kimi` → `Deepseek`，`kimi` → `deepseek`，`KIMI` → `DEEPSEEK`
- 更新模型路由映射中的适配器键
- 更新环境变量名称：`KIMI_PHONE` → `DEEPSEEK_PHONE`
- 更新浏览器配置目录：`edge_profile_kimi` → `edge_profile_deepseek`

### 经验3：AutoAdapter故障转移设计

**背景**：单一适配器可能因服务不可用而失败。

**解决方案**：实现AutoAdapter自动切换机制。

**关键点：**
- 随机选择策略：避免单一适配器过载
- 异常捕获：捕获所有适配器异常并尝试下一个
- 对话状态同步：传递`_conversation_summary`保持一致性
- 命名请求拦截：处理Chatbox的特殊请求

### 经验4：浏览器配置安全清理

**背景**：浏览器配置目录包含敏感数据（登录凭证、Cookie等）。

**解决方案**：清理敏感文件并更新`.gitignore`。

**关键点：**
- 删除`Login Data`、`Cookies`、`History`、`Web Data`等文件
- 更新`.gitignore`排除浏览器配置目录
- 提醒用户不要提交浏览器配置

### 经验5：项目清理与优化

**背景**：项目中存在大量测试文件和调试代码。

**解决方案**：删除无用文件，优化项目结构。

**关键点：**
- 删除所有测试文件（`test_*.py`）
- 删除`tests/`目录和`.pytest_cache/`目录
- 清理`__pycache__`目录
- 更新文档反映最新项目结构

---

## 七、测试结果

### 兼容性测试

| 测试项 | 结果 |
|--------|------|
| DeepSeek适配器（deepseek-k2.6） | ✅ 通过 |
| DeepSeek适配器（deepseek） | ✅ 通过 |
| ChatGPT适配器（chatgpt-4o） | ⚠️ 需手动登录验证 |
| AutoAdapter自动切换 | ✅ 通过 |
| API接口兼容性 | ✅ 通过 |

### 注意事项

ChatGPT自动登录功能受以下因素影响：
- 网站登录页面结构可能变化
- Cloudflare验证可能需要手动处理
- 账号安全设置（如2FA）可能阻止自动登录
- 建议首次使用时采用非Headless模式，手动完成登录后保存用户数据目录

---

## 八、项目结构

```
src/
├── ai_adapter/
│   ├── __init__.py              # 模块导出
│   ├── base_adapter.py          # 适配器基类
│   ├── deepseek_adapter.py      # DeepSeek适配器
│   ├── chatgpt_adapter.py       # ChatGPT适配器
│   ├── auto_adapter.py          # 自动切换适配器
│   └── types.py                 # 数据类型定义
├── api/
│   └── server.py                # API服务入口
└── start.py                     # 启动脚本
```

---

## 九、最佳实践

### 1. 代码组织
- 遵循适配器模式，保持接口一致性
- 使用类型提示（Pydantic）定义数据结构
- 分离配置与代码（使用`.env`文件）
- 添加适当的日志记录

### 2. 安全实践
- 不要将敏感信息硬编码到代码中
- 使用环境变量管理凭据
- 将`.env`文件加入`.gitignore`
- 清理浏览器配置中的敏感数据

### 3. 测试策略
- 使用Playwright进行端到端测试
- 为关键功能编写单元测试
- 测试不同浏览器配置
- 测试网络异常场景

### 4. 部署建议
- 使用虚拟环境隔离依赖
- 配置适当的超时时间
- 使用无头模式运行生产环境
- 监控服务状态和日志

---

## 十、总结

本项目成功实现了多AI服务的统一API适配，解决了富文本渲染一致性问题，建立了可扩展的适配器架构。主要经验：

1. **适配器模式**是实现多服务支持的最佳实践
2. **反检测技术**是Web自动化的关键挑战
3. **HTML转Markdown**是解决富文本格式问题的有效方案
4. **异步编程**提升了服务的并发处理能力
5. **多选择器策略**提高了页面交互的稳定性
6. **环境变量配置**是管理敏感信息的标准做法
7. **AutoAdapter故障转移**提升了服务的可用性

### 未来优化方向

- 添加更多AI服务适配器（如Claude、Gemini等）
- 实现用户数据目录持久化，避免重复登录
- 添加更完善的错误处理和重试机制
- 支持更多消息格式（如图片、文件）
- 添加API Key认证机制
- 实现请求速率限制
- 添加监控和告警功能
