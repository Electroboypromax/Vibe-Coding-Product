import logging
import asyncio
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from ai_adapter.types import ChatCompletionRequest, ChatCompletionResponse, AdapterConfig
from ai_adapter.base_adapter import BaseAIAdapter
from ai_adapter.deepseek_adapter import DeepseekAdapter
from ai_adapter.chatgpt_adapter import ChatGPTAdapter
from ai_adapter.auto_adapter import AutoAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

adapters: dict[str, BaseAIAdapter] = {}
adapter_init_locks: dict[str, asyncio.Lock] = {}

MODEL_ADAPTER_MAP = {
    "Deepseek-k2.6": ("Deepseek", DeepseekAdapter),
    "Deepseek": ("Deepseek", DeepseekAdapter),
    "deepseek": ("Deepseek", DeepseekAdapter),
    "chatgpt": ("chatgpt", ChatGPTAdapter),
    "chatgpt-4o": ("chatgpt", ChatGPTAdapter),
    "chatgpt-5.5": ("chatgpt", ChatGPTAdapter),
    "chatgpt-5": ("chatgpt", ChatGPTAdapter),
    "gpt-4": ("chatgpt", ChatGPTAdapter),
    "gpt-4o": ("chatgpt", ChatGPTAdapter),
    "gpt-5": ("chatgpt", ChatGPTAdapter),
    "gpt-5.5": ("chatgpt", ChatGPTAdapter),
    "gpt-55": ("chatgpt", ChatGPTAdapter),
    "auto-web": ("auto", None),
}


def get_adapter_key(model: str) -> str:
    """
    根据模型名称获取适配器键

    Args:
        model: 模型名称

    Returns:
        适配器键（Deepseek、chatgpt 或 auto）
    """
    for model_prefix, (adapter_key, _) in MODEL_ADAPTER_MAP.items():
        if model.lower().startswith(model_prefix.lower()):
            return adapter_key
    return "Deepseek"


async def initialize_adapter(model: str) -> BaseAIAdapter:
    """
    初始化指定模型的适配器（延迟初始化）

    在第一次请求时调用，避免启动时阻塞。
    使用锁机制防止并发请求导致创建多个浏览器窗口。

    对于 auto-web 模型，会随机选择 deepseek 或 chatgpt。

    Args:
        model: 模型名称

    Returns:
        初始化后的适配器实例
    """
    adapter_key = get_adapter_key(model)

    if adapter_key == "auto":
        return await initialize_auto_adapter()

    if adapter_key in adapters:
        return adapters[adapter_key]

    if adapter_key not in adapter_init_locks:
        adapter_init_locks[adapter_key] = asyncio.Lock()

    async with adapter_init_locks[adapter_key]:
        if adapter_key in adapters:
            return adapters[adapter_key]

        try:
            config = AdapterConfig(
                headless=False,
                timeout=120,
                page_load_timeout=60,
                channel="msedge",
            )

            _, AdapterClass = MODEL_ADAPTER_MAP.get(model.lower(), ("Deepseek", DeepseekAdapter))
            adapter = AdapterClass(config)
            await adapter.initialize()

            adapters[adapter_key] = adapter
            logger.info(f"{AdapterClass.__name__} initialized successfully for model: {model}")
            return adapter

        except Exception as e:
            logger.error(f"Failed to initialize adapter for model {model}: {e}")
            if adapter_key in adapters:
                del adapters[adapter_key]
            raise


async def initialize_auto_adapter() -> BaseAIAdapter:
    """
    初始化 auto-web 模型的适配器

    创建一个 AutoAdapter 实例，注册所有可用的子适配器（Deepseek 和 chatgpt），
    当选中的适配器失败时，会自动尝试其他可用适配器。

    Returns:
        AutoAdapter 实例
    """
    available_keys = ["Deepseek", "chatgpt"]
    config = AdapterConfig(
        headless=False,
        timeout=120,
        page_load_timeout=60,
        channel="msedge",
    )

    if "auto" in adapters:
        return adapters["auto"]

    if "auto" not in adapter_init_locks:
        adapter_init_locks["auto"] = asyncio.Lock()

    async with adapter_init_locks["auto"]:
        if "auto" in adapters:
            return adapters["auto"]

        auto_adapter = AutoAdapter(config)
        await auto_adapter.initialize()

        for adapter_key in available_keys:
            if adapter_key not in adapters:
                try:
                    if adapter_key not in adapter_init_locks:
                        adapter_init_locks[adapter_key] = asyncio.Lock()
                    
                    async with adapter_init_locks[adapter_key]:
                        if adapter_key not in adapters:
                            if adapter_key == "Deepseek":
                                sub_adapter = DeepseekAdapter(config)
                            else:
                                sub_adapter = ChatGPTAdapter(config)
                            
                            await sub_adapter.initialize()
                            adapters[adapter_key] = sub_adapter
                            logger.info(f"{type(sub_adapter).__name__} initialized for auto-web")
                except Exception as e:
                    logger.warning(f"Failed to initialize {adapter_key} adapter for auto-web: {e}")
                    continue

            if adapter_key in adapters:
                auto_adapter.register_adapter(adapter_key, adapters[adapter_key])

    if not auto_adapter.available_adapters:
        raise RuntimeError("No adapters available for auto-web model")

    adapters["auto"] = auto_adapter
    logger.info(f"AutoAdapter initialized with adapters: {auto_adapter.available_adapters}")
    
    return auto_adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    服务启动时初始化Deepseek适配器（用于DeepSeek），
    确保浏览器窗口在服务启动时就弹出，方便用户提前登录。
    在应用关闭时清理所有适配器资源。
    """
    global adapters
    logger.info("Starting AI service adapter...")

    adapters = {}

    try:
        config = AdapterConfig(
            headless=False,
            timeout=120,
            page_load_timeout=60,
            channel="msedge",
        )
        
        logger.info("Initializing Deepseek adapter (DeepSeek) on startup...")
        Deepseek_adapter = DeepseekAdapter(config)
        await Deepseek_adapter.initialize()
        adapters["Deepseek"] = Deepseek_adapter
        logger.info("Deepseek adapter initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Deepseek adapter on startup: {e}")
        logger.info("Deepseek adapter will be initialized on first request")

    yield

    logger.info("Shutting down AI service adapters...")
    for adapter_key, adapter in adapters.items():
        try:
            await adapter.cleanup()
            logger.info(f"Cleaned up {adapter_key} adapter")
        except Exception as e:
            logger.error(f"Error cleaning up {adapter_key} adapter: {e}")
    adapters = {}


app = FastAPI(
    title="AI Service Adapter API",
    description="将Web端AI服务转换为OpenAI兼容的API接口，支持DeepSeek和ChatGPT",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.options("/{full_path:path}")
async def options_handler(full_path: str):
    """
    全局OPTIONS请求处理程序，解决CORS预检问题
    """
    from fastapi.responses import Response
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key",
            "Access-Control-Allow-Credentials": "true",
        },
    )


@app.get("/")
async def root():
    """
    健康检查端点

    返回服务状态信息。
    """
    return {
        "status": "healthy",
        "service": "AI Service Adapter",
        "version": "2.0.0",
        "adapters": list(adapters.keys()) if adapters else ["Deepseek", "chatgpt"],
    }


@app.get("/health")
async def health_check():
    """
    健康检查端点

    检查所有适配器状态和服务可用性。
    """
    results = {}
    for adapter_key, adapter in adapters.items():
        if adapter and adapter.page:
            try:
                await adapter.page.wait_for_selector(
                    adapter.SELECTORS["input_box"],
                    timeout=5000,
                )
                results[adapter_key] = {"connected": True}
            except Exception as e:
                logger.warning(f"Health check failed for {adapter_key}: {e}")
                results[adapter_key] = {"connected": False, "error": str(e)}
        else:
            results[adapter_key] = {"connected": False, "message": "Adapter not initialized"}

    if not adapters:
        return {
            "status": "starting",
            "adapters": {"Deepseek": {"message": "Not initialized yet"}, "chatgpt": {"message": "Not initialized yet"}},
            "message": "Adapters will initialize on first request"
        }

    all_connected = all(r.get("connected") for r in results.values())
    status = "healthy" if all_connected else "degraded"

    return {"status": status, "adapters": results}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, raw_request: Request):
    """
    聊天补全端点（支持流式和非流式）

    接收OpenAI格式的请求，根据model字段选择对应的适配器获取回复，
    返回OpenAI格式的响应。

    Args:
        request: OpenAI格式的聊天请求

    Returns:
        OpenAI格式的聊天响应
    """
    try:
        adapter = await initialize_adapter(request.model)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to initialize adapter: {str(e)}")

    if request.messages:
        user_msg = request.messages[-1].get_text_content() if request.messages[-1] else ""
        logger.info(f"Received chat completion request: model={request.model}, adapter={type(adapter).__name__}, messages={len(request.messages)}, stream={request.stream}, user_message={user_msg[:50]}")

    if request.stream:
        logger.info("Streaming requested")
        async def stream_generator():
            try:
                async for chunk in adapter.create_completion_stream(request):
                    import json
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Error streaming: {e}")
                import json
                yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Transfer-Encoding": "chunked",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        response = await adapter.create_completion(request)
        logger.info(f"Response generated successfully: {len(response.choices[0].message.content)} characters")
        
        import json
        response_dict = {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": [
                {
                    "index": choice.index,
                    "message": {
                        "role": choice.message.role,
                        "content": choice.message.content
                    },
                    "finish_reason": choice.finish_reason
                }
                for choice in response.choices
            ],
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0
            }
        }
        logger.debug(f"Response dict: {response_dict}")
        return response_dict

    except Exception as e:
        logger.error(f"Error processing chat completion: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/completions/stream")
async def chat_completions_stream(request: ChatCompletionRequest, raw_request: Request):
    """
    聊天补全端点（流式）

    接收OpenAI格式的请求，根据model字段选择对应的适配器获取流式回复，
    返回Server-Sent Events格式的响应。

    Args:
        request: OpenAI格式的聊天请求

    Returns:
        流式响应
    """
    try:
        adapter = await initialize_adapter(request.model)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to initialize adapter: {str(e)}")

    logger.info(f"Received streaming chat completion request: model={request.model}, adapter={type(adapter).__name__}")

    async def stream_generator():
        try:
            async for chunk in adapter.create_completion_stream(request):
                import json

                yield f"data: {json.dumps(chunk)}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.error(f"Error streaming chat completion: {e}")
            import json

            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Transfer-Encoding": "chunked",
        },
    )


@app.get("/v1/models")
async def list_models():
    """
    列出可用模型端点

    返回当前适配器支持的所有模型列表。
    """
    return {
        "object": "list",
        "data": [
            {
                "id": "Deepseek-k2.6",
                "object": "model",
                "created": 0,
                "owned_by": "Deepseek",
                "name": "K2.6",
                "adapter": "Deepseek",
            },
            {
                "id": "deepseek",
                "object": "model",
                "created": 0,
                "owned_by": "deepseek",
                "name": "DeepSeek",
                "adapter": "Deepseek",
            },
            {
                "id": "chatgpt-4o",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "name": "GPT-4o",
                "adapter": "chatgpt",
            },
            {
                "id": "gpt-4",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "name": "GPT-4",
                "adapter": "chatgpt",
            },
            {
                "id": "gpt-5.5",
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "name": "GPT-5.5",
                "adapter": "chatgpt",
            },
            {
                "id": "auto-web",
                "object": "model",
                "created": 0,
                "owned_by": "auto",
                "name": "Auto Web",
                "adapter": "auto",
                "description": "Randomly selects between DeepSeek and ChatGPT",
            },
        ],
    }


@app.post("/api/chat/send")
async def send_message(request: Request):
    """
    简化的聊天发送端点

    提供简化的API接口，适用于chatbox应用集成。

    Args:
        request: 包含message字段的JSON请求，可选model字段指定模型

    Returns:
        包含code和response字段的响应
    """
    try:
        body = await request.json()
        message = body.get("message", "")
        model = body.get("model", "Deepseek-k2.6")

        if not message:
            raise HTTPException(status_code=400, detail="Message is required")

        adapter = await initialize_adapter(model)

        from ai_adapter.types import ChatMessage

        messages = [ChatMessage(role="user", content=message)]
        response_text = await adapter.send_message(messages)

        return {"code": 0, "response": response_text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return {"code": -1, "response": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
