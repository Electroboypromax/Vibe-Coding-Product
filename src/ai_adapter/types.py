from typing import Dict, List, Optional, Union, Literal, Any
from pydantic import BaseModel, field_validator


class ChatMessageContentPart(BaseModel):
    type: str
    text: Optional[str] = None
    image_url: Optional[Dict[str, str]] = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Union[str, List[ChatMessageContentPart]]

    @field_validator("content")
    def content_not_empty(cls, v):
        if not v:
            raise ValueError("Message content cannot be empty")
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("Message content cannot be empty")
        elif isinstance(v, list):
            if not v:
                raise ValueError("Message content cannot be empty")
            for part in v:
                if part.type == "text" and part.text and not part.text.strip():
                    raise ValueError("Message content cannot be empty")
        return v

    def get_text_content(self) -> str:
        """提取文本内容"""
        if isinstance(self.content, str):
            return self.content
        text_parts = []
        for part in self.content:
            if part.type == "text" and part.text:
                text_parts.append(part.text)
        return "\n".join(text_parts)


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Optional[ChatCompletionUsage] = None


class StreamChoiceDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class StreamChoice(BaseModel):
    index: int
    delta: StreamChoiceDelta
    finish_reason: Optional[str] = None


class StreamResponse(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[StreamChoice]


class AdapterConfig(BaseModel):
    browser_type: str = "chromium"
    headless: bool = False
    timeout: int = 120
    page_load_timeout: int = 60
    user_data_dir: Optional[str] = None
    channel: Optional[str] = None
    executable_path: Optional[str] = None