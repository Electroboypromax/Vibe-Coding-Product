import logging
import random
import time
from typing import Optional, AsyncGenerator, Dict, List
from .base_adapter import BaseAIAdapter
from .types import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    AdapterConfig,
    ChatMessage,
    ChatCompletionChoice,
    ChatCompletionUsage,
)

logger = logging.getLogger(__name__)


class AutoAdapter(BaseAIAdapter):
    """
    自动选择适配器

    随机选择 DeepSeek 或 ChatGPT 适配器发送消息，
    当选中的适配器失败时，自动尝试其他可用适配器。

    特性：
    1. 随机选择适配器
    2. 发送失败时自动重试其他适配器
    3. 保持与其他适配器相同的接口
    4. 支持对话总结传递
    """

    def __init__(self, config: AdapterConfig):
        super().__init__(config)
        self.sub_adapters: Dict[str, BaseAIAdapter] = {}
        self.available_adapters: List[str] = []
        self._conversation_summary: str = ""
        self._last_used_adapter: Optional[str] = None

    async def initialize(self) -> None:
        """
        初始化自动适配器

        此方法不启动浏览器，适配器的初始化由外部管理。
        """
        logger.info("AutoAdapter initialized")

    async def cleanup(self) -> None:
        """
        清理所有子适配器资源
        """
        logger.info("Cleaning up AutoAdapter...")
        for key, adapter in self.sub_adapters.items():
            try:
                await adapter.cleanup()
                logger.info(f"Cleaned up {key} adapter")
            except Exception as e:
                logger.error(f"Error cleaning up {key} adapter: {e}")
        self.sub_adapters = {}
        self.available_adapters = []

    def register_adapter(self, key: str, adapter: BaseAIAdapter) -> None:
        """
        注册子适配器

        Args:
            key: 适配器键（如 "deepseek", "chatgpt"）
            adapter: 适配器实例
        """
        self.sub_adapters[key] = adapter
        if key not in self.available_adapters:
            self.available_adapters.append(key)
        logger.info(f"Registered adapter: {key} ({type(adapter).__name__})")

    def unregister_adapter(self, key: str) -> None:
        """
        注销子适配器

        Args:
            key: 适配器键
        """
        if key in self.sub_adapters:
            del self.sub_adapters[key]
        if key in self.available_adapters:
            self.available_adapters.remove(key)
        if self._last_used_adapter == key:
            self._last_used_adapter = None
        logger.info(f"Unregistered adapter: {key}")

    def _is_naming_request(self, message: str) -> bool:
        """
        判断是否是Chatbox的对话命名请求

        Args:
            message: 消息文本

        Returns:
            如果是命名请求返回True，否则返回False
        """
        if not message:
            return False

        message_lower = message.lower()
        naming_keywords = [
            "give this conversation a name",
            "name this conversation",
            "请给这个对话起个名字",
            "命名",
            "conversation name",
        ]
        for keyword in naming_keywords:
            if keyword.lower() in message_lower:
                return True
        return False

    def _generate_conversation_name(self, messages: list[ChatMessage]) -> str:
        """
        根据对话历史生成简短的对话名称

        优先使用从子适配器获取的对话总结，如果没有则使用用户的原始提问。

        Args:
            messages: 消息列表

        Returns:
            对话名称（10字以内）
        """
        latest_summary = ""
        default_titles = ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT", "DeepSeek"]
        
        if self._last_used_adapter and self._last_used_adapter in self.sub_adapters:
            adapter = self.sub_adapters[self._last_used_adapter]
            if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                latest_summary = adapter._conversation_summary.strip()
                logger.info(f"AutoAdapter got latest summary from {self._last_used_adapter}: '{latest_summary}'")

        if not latest_summary and self._conversation_summary:
            latest_summary = self._conversation_summary.strip()
            logger.info(f"AutoAdapter using cached summary: '{latest_summary}'")

        if latest_summary and latest_summary not in default_titles:
            latest_summary = latest_summary.replace(" - DeepSeek", "").replace("- DeepSeek", "")
            latest_summary = latest_summary.replace(" - ChatGPT", "").replace("- ChatGPT", "")
            logger.info(f"AutoAdapter using extracted conversation summary: '{latest_summary}'")
            return latest_summary[:10]

        for msg in messages:
            if msg.role == "user":
                content = msg.get_text_content().strip()
                if content and not self._is_naming_request(content):
                    return content[:10]
        return "对话"

    async def send_message(self, messages: list[ChatMessage]) -> str:
        """
        发送消息并获取AI回复

        随机选择适配器发送消息，失败时自动尝试其他适配器。
        支持对话总结传递。

        Args:
            messages: 消息列表，包含对话历史

        Returns:
            AI回复的文本内容

        Raises:
            RuntimeError: 如果所有适配器都失败
        """
        if not self.available_adapters:
            raise RuntimeError("No adapters available for AutoAdapter")

        user_message = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_message = msg.get_text_content()
                break
        if not user_message:
            user_message = messages[-1].get_text_content() if messages else ""

        user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
        if user_msg_count == 1:
            self._conversation_summary = ""
            for key, adapter in self.sub_adapters.items():
                if hasattr(adapter, '_conversation_summary'):
                    adapter._conversation_summary = ""
            logger.info(f"AutoAdapter detected new conversation, cleared cached summary for all adapters")

        if self._is_naming_request(user_message):
            default_titles = ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT", "DeepSeek"]
            
            timeout = 20
            start_time = time.time()
            logger.info(f"AutoAdapter intercepted naming request, waiting for summary (timeout: {timeout}s)...")
            
            while time.time() - start_time < timeout:
                if self._last_used_adapter and self._last_used_adapter in self.sub_adapters:
                    adapter = self.sub_adapters[self._last_used_adapter]
                    if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                        summary = adapter._conversation_summary.strip()
                        if summary and summary not in default_titles:
                            self._conversation_summary = summary
                            logger.info(f"AutoAdapter got summary after {time.time() - start_time:.1f}s: '{summary}'")
                            break
                await asyncio.sleep(0.5)
            
            name = self._generate_conversation_name(messages)
            logger.info(f"AutoAdapter intercepted naming request, returning: '{name}'")
            return name

        tried_adapters = []
        remaining_adapters = self.available_adapters.copy()
        random.shuffle(remaining_adapters)

        while remaining_adapters:
            selected_key = remaining_adapters.pop(0)
            tried_adapters.append(selected_key)
            adapter = self.sub_adapters[selected_key]

            logger.info(f"AutoAdapter trying {selected_key} ({type(adapter).__name__})")

            try:
                result = await adapter.send_message(messages)
                self._last_used_adapter = selected_key
                if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                    self._conversation_summary = adapter._conversation_summary
                    logger.info(f"AutoAdapter updated summary from {selected_key}: '{self._conversation_summary}'")
                logger.info(f"AutoAdapter succeeded with {selected_key}")
                return result
            except Exception as e:
                logger.warning(f"AutoAdapter failed with {selected_key}: {e}")
                logger.info(f"AutoAdapter will try next available adapter")

        raise RuntimeError(
            f"All adapters failed. Tried: {tried_adapters}. Last error: {e}"
        )

    async def send_message_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncGenerator[str, None]:
        """
        发送消息并以流式方式获取AI回复

        随机选择适配器发送消息，失败时自动尝试其他适配器。
        支持对话总结传递。

        Args:
            messages: 消息列表，包含对话历史

        Yields:
            流式回复的文本片段

        Raises:
            RuntimeError: 如果所有适配器都失败
        """
        if not self.available_adapters:
            raise RuntimeError("No adapters available for AutoAdapter")

        user_message = ""
        for msg in reversed(messages):
            if msg.role == "user":
                user_message = msg.get_text_content()
                break
        if not user_message:
            user_message = messages[-1].get_text_content() if messages else ""

        user_msg_count = sum(1 for msg in messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
        if user_msg_count == 1:
            self._conversation_summary = ""
            for key, adapter in self.sub_adapters.items():
                if hasattr(adapter, '_conversation_summary'):
                    adapter._conversation_summary = ""
            logger.info(f"AutoAdapter detected new conversation, cleared cached summary for all adapters")

        if self._is_naming_request(user_message):
            default_titles = ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT", "DeepSeek"]
            
            timeout = 20
            start_time = time.time()
            logger.info(f"AutoAdapter intercepted naming request (stream), waiting for summary (timeout: {timeout}s)...")
            
            while time.time() - start_time < timeout:
                if self._last_used_adapter and self._last_used_adapter in self.sub_adapters:
                    adapter = self.sub_adapters[self._last_used_adapter]
                    if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                        summary = adapter._conversation_summary.strip()
                        if summary and summary not in default_titles:
                            self._conversation_summary = summary
                            logger.info(f"AutoAdapter got summary after {time.time() - start_time:.1f}s: '{summary}'")
                            break
                await asyncio.sleep(0.5)
            
            name = self._generate_conversation_name(messages)
            logger.info(f"AutoAdapter intercepted naming request (stream), returning: '{name}'")
            yield name
            return

        tried_adapters = []
        remaining_adapters = self.available_adapters.copy()
        random.shuffle(remaining_adapters)

        while remaining_adapters:
            selected_key = remaining_adapters.pop(0)
            tried_adapters.append(selected_key)
            adapter = self.sub_adapters[selected_key]

            logger.info(f"AutoAdapter trying {selected_key} ({type(adapter).__name__})")

            try:
                async for chunk in adapter.send_message_stream(messages):
                    yield chunk
                self._last_used_adapter = selected_key
                if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                    self._conversation_summary = adapter._conversation_summary
                    logger.info(f"AutoAdapter updated summary from {selected_key}: '{self._conversation_summary}'")
                logger.info(f"AutoAdapter succeeded with {selected_key}")
                return
            except Exception as e:
                logger.warning(f"AutoAdapter failed with {selected_key}: {e}")
                logger.info(f"AutoAdapter will try next available adapter")

        raise RuntimeError(
            f"All adapters failed. Tried: {tried_adapters}. Last error: {e}"
        )

    async def create_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        """
        创建聊天补全响应（非流式）

        随机选择适配器处理请求，失败时自动尝试其他适配器。
        支持对话总结传递和命名请求拦截。

        Args:
            request: 聊天补全请求

        Returns:
            聊天补全响应

        Raises:
            RuntimeError: 如果所有适配器都失败
        """
        if not self.available_adapters:
            raise RuntimeError("No adapters available for AutoAdapter")

        user_message = ""
        for msg in reversed(request.messages):
            if msg.role == "user":
                user_message = msg.get_text_content()
                break
        if not user_message:
            user_message = request.messages[-1].get_text_content() if request.messages else ""

        user_msg_count = sum(1 for msg in request.messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
        if user_msg_count == 1:
            self._conversation_summary = ""
            for key, adapter in self.sub_adapters.items():
                if hasattr(adapter, '_conversation_summary'):
                    adapter._conversation_summary = ""
            logger.info(f"AutoAdapter detected new conversation, cleared cached summary for all adapters")

        if self._is_naming_request(user_message):
            default_titles = ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT", "DeepSeek"]
            
            timeout = 20
            start_time = time.time()
            logger.info(f"AutoAdapter intercepted naming request, waiting for summary (timeout: {timeout}s)...")
            
            while time.time() - start_time < timeout:
                if self._last_used_adapter and self._last_used_adapter in self.sub_adapters:
                    adapter = self.sub_adapters[self._last_used_adapter]
                    if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                        summary = adapter._conversation_summary.strip()
                        if summary and summary not in default_titles:
                            self._conversation_summary = summary
                            logger.info(f"AutoAdapter got summary after {time.time() - start_time:.1f}s: '{summary}'")
                            break
                await asyncio.sleep(0.5)
            
            name = self._generate_conversation_name(request.messages)
            logger.info(f"AutoAdapter intercepted naming request, returning: '{name}'")
            
            message = ChatMessage(role="assistant", content=name)
            
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
            return response

        tried_adapters = []
        remaining_adapters = self.available_adapters.copy()
        random.shuffle(remaining_adapters)

        while remaining_adapters:
            selected_key = remaining_adapters.pop(0)
            tried_adapters.append(selected_key)
            adapter = self.sub_adapters[selected_key]

            logger.info(f"AutoAdapter trying {selected_key} ({type(adapter).__name__})")

            try:
                result = await adapter.create_completion(request)
                self._last_used_adapter = selected_key
                if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                    self._conversation_summary = adapter._conversation_summary
                    logger.info(f"AutoAdapter updated summary from {selected_key}: '{self._conversation_summary}'")
                logger.info(f"AutoAdapter succeeded with {selected_key}")
                return result
            except Exception as e:
                logger.warning(f"AutoAdapter failed with {selected_key}: {e}")
                logger.info(f"AutoAdapter will try next available adapter")

        raise RuntimeError(
            f"All adapters failed. Tried: {tried_adapters}. Last error: {e}"
        )

    async def create_completion_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncGenerator[dict, None]:
        """
        创建聊天补全响应（流式）

        随机选择适配器处理请求，失败时自动尝试其他适配器。
        支持对话总结传递和命名请求拦截。

        Args:
            request: 聊天补全请求

        Yields:
            流式响应的字典片段

        Raises:
            RuntimeError: 如果所有适配器都失败
        """
        if not self.available_adapters:
            raise RuntimeError("No adapters available for AutoAdapter")

        user_message = ""
        for msg in reversed(request.messages):
            if msg.role == "user":
                user_message = msg.get_text_content()
                break
        if not user_message:
            user_message = request.messages[-1].get_text_content() if request.messages else ""

        user_msg_count = sum(1 for msg in request.messages if msg.role == "user" and not self._is_naming_request(msg.get_text_content()))
        if user_msg_count == 1:
            self._conversation_summary = ""
            for key, adapter in self.sub_adapters.items():
                if hasattr(adapter, '_conversation_summary'):
                    adapter._conversation_summary = ""
            logger.info(f"AutoAdapter detected new conversation, cleared cached summary for all adapters")

        if self._is_naming_request(user_message):
            default_titles = ["对话", "新对话", "New chat", "New Chat", "Untitled", "历史聊天记录", "ChatGPT", "DeepSeek"]
            
            timeout = 20
            start_time = time.time()
            logger.info(f"AutoAdapter intercepted naming request (stream), waiting for summary (timeout: {timeout}s)...")
            
            while time.time() - start_time < timeout:
                if self._last_used_adapter and self._last_used_adapter in self.sub_adapters:
                    adapter = self.sub_adapters[self._last_used_adapter]
                    if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                        summary = adapter._conversation_summary.strip()
                        if summary and summary not in default_titles:
                            self._conversation_summary = summary
                            logger.info(f"AutoAdapter got summary after {time.time() - start_time:.1f}s: '{summary}'")
                            break
                await asyncio.sleep(0.5)
            
            name = self._generate_conversation_name(request.messages)
            response_id = f"chatcmpl-{int(time.time() * 1000)}"
            logger.info(f"AutoAdapter intercepted naming request (stream), returning: '{name}'")
            yield {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", "content": name}, "finish_reason": None}
                ],
            }
            yield {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": request.model,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            }
            return

        tried_adapters = []
        remaining_adapters = self.available_adapters.copy()
        random.shuffle(remaining_adapters)

        while remaining_adapters:
            selected_key = remaining_adapters.pop(0)
            tried_adapters.append(selected_key)
            adapter = self.sub_adapters[selected_key]

            logger.info(f"AutoAdapter trying {selected_key} ({type(adapter).__name__})")

            try:
                async for chunk in adapter.create_completion_stream(request):
                    yield chunk
                self._last_used_adapter = selected_key
                if hasattr(adapter, '_conversation_summary') and adapter._conversation_summary:
                    self._conversation_summary = adapter._conversation_summary
                    logger.info(f"AutoAdapter updated summary from {selected_key} (stream): '{self._conversation_summary}'")
                logger.info(f"AutoAdapter succeeded with {selected_key}")
                return
            except Exception as e:
                logger.warning(f"AutoAdapter failed with {selected_key}: {e}")
                logger.info(f"AutoAdapter will try next available adapter")

        raise RuntimeError(
            f"All adapters failed. Tried: {tried_adapters}. Last error: {e}"
        )
