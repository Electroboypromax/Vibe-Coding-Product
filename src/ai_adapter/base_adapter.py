import logging
from abc import ABC, abstractmethod
from typing import Optional, AsyncGenerator, TYPE_CHECKING
from .types import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    AdapterConfig,
    ChatMessage,
    ChatCompletionChoice,
    ChatCompletionUsage,
)
import asyncio

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from playwright.async_api import (
        AsyncPlaywright,
        Browser,
        BrowserContext,
        Page,
    )


class BaseAIAdapter(ABC):
    """
    AI服务适配器基类，定义统一的接口规范

    采用适配器模式，所有Web AI服务都需要实现此基类的接口，
    确保新增平台时仅需修改对应适配器模块，无需变更核心逻辑。
    """

    def __init__(self, config: AdapterConfig):
        self.config = config
        self.playwright: Optional[AsyncPlaywright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._lock = asyncio.Lock()

    @abstractmethod
    async def initialize(self) -> None:
        """
        初始化浏览器和页面，建立与AI服务的连接

        子类需要实现具体的初始化逻辑，包括：
        1. 启动浏览器
        2. 创建上下文
        3. 导航到目标URL
        4. 处理登录状态（如有）
        """
        pass

    @abstractmethod
    async def cleanup(self) -> None:
        """
        清理资源，关闭浏览器和页面

        子类需要实现具体的清理逻辑，确保资源正确释放。
        """
        pass

    @abstractmethod
    async def send_message(self, messages: list[ChatMessage]) -> str:
        """
        发送消息并获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Returns:
            AI回复的文本内容
        """
        pass

    @abstractmethod
    async def send_message_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        """
        发送消息并以流式方式获取AI回复

        Args:
            messages: 消息列表，包含对话历史

        Yields:
            流式回复的文本片段
        """
        pass

    async def create_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """
        创建完整的聊天补全响应

        Args:
            request: OpenAI格式的聊天请求

        Returns:
            OpenAI格式的聊天响应
        """
        import time

        response_text = await self.send_message(request.messages)

        logger.debug(f"Response text: {response_text[:100]}...")

        message = ChatMessage(role="assistant", content=response_text)
        
        choice = ChatCompletionChoice(
            index=0,
            message=message,
            finish_reason="stop",
        )
        
        usage = ChatCompletionUsage(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

        response = ChatCompletionResponse(
            id=f"chatcmpl-{int(time.time() * 1000)}",
            created=int(time.time()),
            model=request.model,
            choices=[choice],
            usage=usage,
        )
        
        logger.debug(f"Response created: {response}")
        
        return response

    async def create_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncGenerator[dict, None]:
        """
        创建流式聊天补全响应

        Args:
            request: OpenAI格式的聊天请求

        Yields:
            OpenAI格式的流式响应片段
        """
        import time

        response_id = f"chatcmpl-{int(time.time() * 1000)}"
        created = int(time.time())

        first_chunk = True
        async for chunk in self.send_message_stream(request.messages):
            delta = {"content": chunk}
            if first_chunk:
                delta["role"] = "assistant"
                first_chunk = False

            yield {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": None}
                ],
            }

        yield {
            "id": response_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "stop"}
            ],
        }

    async def acquire_lock(self):
        """获取并发锁，确保同一时刻只有一个请求"""
        await self._lock.acquire()

    def release_lock(self):
        """释放并发锁"""
        if self._lock.locked():
            self._lock.release()