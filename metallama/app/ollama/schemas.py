from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


class SubserverConfig(BaseModel):
    name: str
    url: str
    size: int = 0
    family: str = "unknown"
    parameter_size: str = "unknown"
    context_length: int = 4096
    upstream_model_id: Optional[str] = None
    upstream_meta: dict[str, Any] = Field(default_factory=dict)
    reachable: bool = False


class AppConfig(BaseModel):
    subservers: list[SubserverConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ollama request/response schemas
# ---------------------------------------------------------------------------

class OllamaChatMessage(BaseModel):
    role: str
    content: str


class OllamaChatRequest(BaseModel):
    model: str
    messages: list[OllamaChatMessage]
    stream: bool = True
    options: Optional[dict[str, Any]] = None


class OllamaGenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = True
    options: Optional[dict[str, Any]] = None


class OllamaShowRequest(BaseModel):
    model: Optional[str] = None
    name: Optional[str] = None

    @model_validator(mode="after")
    def validate_model_or_name(self) -> "OllamaShowRequest":
        if not self.model and not self.name:
            raise ValueError("Either 'model' or 'name' must be provided")
        return self

    @property
    def model_name(self) -> str:
        return self.model or self.name or ""


# ---------------------------------------------------------------------------
# OpenAI passthrough schemas (minimal — bodies forwarded as-is)
# ---------------------------------------------------------------------------

class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    stream: bool = False

    class Config:
        extra = "allow"


class OpenAICompletionRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False

    class Config:
        extra = "allow"


class OpenAIEmbeddingRequest(BaseModel):
    model: str
    input: Any

    class Config:
        extra = "allow"
