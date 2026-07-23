# AI Service Adapter - 部署和使用指南

## 项目概述

本项目是一个基于Python和Playwright的Web AI服务转换程序，将DeepSeek和ChatGPT Web端AI服务转换为符合OpenAI标准的API接口。支持多AI服务自动切换和富文本内容（表格、列表、代码块等）的正确渲染。

## 项目结构

```
test_7_17_tuominggezhongxuanrancgban/
├── src/
│   ├── ai_adapter/
│   │   ├── __init__.py              # 模块导出
│   │   ├── base_adapter.py          # 适配器基类（适配器模式）
│   │   ├── deepseek_adapter.py      # DeepSeek适配器
│   │   ├── chatgpt_adapter.py       # ChatGPT适配器
│   │   ├── auto_adapter.py          # 自动切换适配器
│   │   └── types.py                 # Pydantic类型定义
│   ├── api/
│   │   ├── __init__.py
│   │   └── server.py                # FastAPI服务端
│   └── __init__.py
├── edge_profile_deepseek/           # DeepSeek浏览器配置
├── edge_profile_chatgpt/            # ChatGPT浏览器配置
├── .env                             # 环境变量配置（敏感信息）
├── .env.example                     # 环境变量模板
├── .gitignore                       # Git忽略规则
├── start.py                         # 启动脚本
├── requirements.txt                 # 依赖列表
├── DEPLOYMENT.md                    # 部署和使用指南
└── DEVELOPMENT_SUMMARY.md           # 开发经验总结
```

## 系统要求

- **操作系统**: Windows 10/11 (64位)
- **Python版本**: 3.10+
- **浏览器**: Microsoft Edge (已安装)

## 安装步骤

### 1. 克隆项目

```bash
git clone <repository-url>
cd test_7_17_tuominggezhongxuanrancgban
```

### 2. 创建虚拟环境

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 安装Playwright浏览器驱动

```bash
playwright install chromium
```

## 配置说明

### 环境变量配置（推荐）

在项目根目录创建 `.env` 文件（或复制 `.env.example`），配置登录凭证：

```
DEEPSEEK_PHONE=你的手机号
DEEPSEEK_PASSWORD=你的密码
CHATGPT_EMAIL=你的邮箱地址
CHATGPT_PASSWORD=你的密码
```

| 环境变量 | 说明 |
|---------|------|
| `DEEPSEEK_PHONE` | DeepSeek账号手机号 |
| `DEEPSEEK_PASSWORD` | DeepSeek账号密码 |
| `CHATGPT_EMAIL` | ChatGPT账号邮箱 |
| `CHATGPT_PASSWORD` | ChatGPT账号密码 |

> **注意**: `.env` 文件已加入 `.gitignore`，不会被提交到版本控制

### 浏览器配置

在 `src/api/server.py` 中可以配置浏览器选项：

```python
config = AdapterConfig(
    headless=False,              # 是否无头模式运行
    timeout=120,                 # 请求超时时间（秒）
    page_load_timeout=60,        # 页面加载超时时间（秒）
    channel="msedge",            # 使用Edge浏览器
    # 或使用executable_path指定具体路径：
    # executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)
```

### 配置选项说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `headless` | bool | False | 是否无头模式运行浏览器 |
| `timeout` | int | 120 | 请求超时时间（秒） |
| `page_load_timeout` | int | 60 | 页面加载超时时间（秒） |
| `channel` | str | None | 浏览器通道（msedge/chrome） |
| `executable_path` | str | None | 浏览器可执行文件路径 |
| `user_data_dir` | str | None | 用户数据目录（用于保存登录状态） |

## 启动服务

### 开发模式

```bash
python start.py
```

### 生产模式

```bash
uvicorn api.server:app --host 0.0.0.0 --port 8000 --workers 1
```

> **注意**: 由于Playwright浏览器实例是单例的，生产环境建议使用 `--workers 1` 避免并发问题。

## API接口

### 健康检查

```
GET /
GET /health
```

### 列出模型

```
GET /v1/models
```

响应示例：
```json
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-k2.6",
      "object": "model",
      "created": 0,
      "owned_by": "deepseek",
      "name": "K2.6",
      "adapter": "deepseek"
    },
    {
      "id": "deepseek",
      "object": "model",
      "created": 0,
      "owned_by": "deepseek",
      "name": "DeepSeek",
      "adapter": "deepseek"
    },
    {
      "id": "chatgpt-4o",
      "object": "model",
      "created": 0,
      "owned_by": "openai",
      "name": "GPT-4o",
      "adapter": "chatgpt"
    },
    {
      "id": "auto-web",
      "object": "model",
      "created": 0,
      "owned_by": "auto",
      "name": "Auto Web",
      "adapter": "auto",
      "description": "Randomly selects between DeepSeek and ChatGPT"
    }
  ]
}
```

### 非流式聊天补全

```
POST /v1/chat/completions
```

请求体：
```json
{
  "model": "deepseek-k2.6",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.7,
  "max_tokens": 1000,
  "stream": false
}
```

响应示例：
```json
{
  "id": "chatcmpl-1784257487244",
  "object": "chat.completion",
  "created": 1784257487,
  "model": "deepseek-k2.6",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好！我是DeepSeek，很高兴为你服务。"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### 流式聊天补全

```
POST /v1/chat/completions
```

请求体（添加 `stream: true`）：
```json
{
  "model": "deepseek-k2.6",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "stream": true
}
```

响应格式（Server-Sent Events）：
```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":xxx,"model":"deepseek-k2.6","choices":[{"index":0,"delta":{"role":"assistant","content":"你"},"finish_reason":null}]}
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":xxx,"model":"deepseek-k2.6","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}
data: [DONE]
```

### 简化聊天接口

```
POST /api/chat/send
```

请求体：
```json
{
  "message": "你好",
  "model": "deepseek-k2.6"
}
```

响应示例：
```json
{
  "code": 0,
  "response": "你好！我是DeepSeek，很高兴为你服务。"
}
```

## 可用模型

| 模型ID | 适配器 | 说明 |
|--------|--------|------|
| `deepseek-k2.6` | deepseek | DeepSeek K2.6 模型 |
| `deepseek` | deepseek | DeepSeek 默认模型 |
| `chatgpt-4o` | chatgpt | ChatGPT GPT-4o 模型 |
| `gpt-4` | chatgpt | ChatGPT GPT-4 模型 |
| `gpt-5.5` | chatgpt | ChatGPT GPT-5.5 模型 |
| `auto-web` | auto | 自动切换（随机选择DeepSeek或ChatGPT） |

## 使用示例

### 使用curl调用API

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-k2.6",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 使用Python调用API

```python
import requests

url = "http://localhost:8000/v1/chat/completions"
payload = {
    "model": "deepseek-k2.6",
    "messages": [{"role": "user", "content": "你好"}]
}

response = requests.post(url, json=payload)
print(response.json())
```

### 与Chatbox集成

在Chatbox应用中配置API地址：
- **API Base URL**: `http://localhost:8000/v1`
- **API Key**: 任意字符串（当前版本未启用认证）

### 使用auto-web自动切换模型

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-web",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

> **注意**: auto-web模型会随机选择DeepSeek或ChatGPT适配器，当选中的适配器失败时会自动尝试其他适配器。

## 故障排除

### Edge浏览器未找到

问题：服务启动时提示未检测到Edge浏览器

解决方案：
1. 确认Edge浏览器已安装
2. 手动指定浏览器路径：
```python
config = AdapterConfig(
    executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
```

### 登录问题

问题：DeepSeek或ChatGPT网站要求登录

解决方案：
1. 首次启动服务时，浏览器会以有头模式打开
2. 在弹出的浏览器窗口中完成登录
3. 登录状态会自动保存（如果配置了user_data_dir）

### 页面加载超时

问题：`Timeout waiting for page ready`

解决方案：
1. 检查网络连接
2. 增加超时时间：
```python
config = AdapterConfig(
    timeout=180,
    page_load_timeout=90,
)
```

### 资源占用过高

问题：浏览器内存占用过高

解决方案：
1. 使用无头模式：`headless=True`
2. 定期重启服务释放资源

### 环境变量未加载

问题：登录凭证未生效

解决方案：
1. 确认 `.env` 文件位于项目根目录
2. 确认环境变量名称正确（`DEEPSEEK_PHONE`、`DEEPSEEK_PASSWORD`）
3. 重启服务使配置生效

## 安全建议

1. **生产环境**：启用无头模式运行
2. **网络安全**：建议在内部网络部署，或配置防火墙
3. **API认证**：后续版本可添加API Key认证
4. **输入验证**：项目已实现输入验证，但建议在前端也进行验证
5. **日志审计**：监控服务日志，及时发现异常请求
6. **敏感信息保护**：不要将 `.env` 文件提交到版本控制

## 更新日志

### v2.0.0
- 将Kimi适配器重命名为DeepSeek适配器
- 添加 `.env` 环境变量配置支持
- 添加 `python-dotenv` 依赖
- 新增AutoAdapter自动切换功能
- 支持 `auto-web` 模型自动选择适配器
- 删除所有测试文件，清理项目结构
- 添加 `.gitignore` 配置
- 清理浏览器配置中的敏感数据（Login Data、Cookies等）

### v1.0.0
- 完成DeepSeek Web AI服务适配
- 实现OpenAI兼容API接口
- 支持Edge浏览器替代Chromium
- 实现适配器模式架构
- 添加ChatGPT适配器支持
- 支持富文本内容提取与转换
