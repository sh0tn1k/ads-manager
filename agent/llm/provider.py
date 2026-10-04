"""Provider-neutral LLM interface (section 10). Implementations: Anthropic (default)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMProviderError(Exception):
    """Transport/API failure. The message never contains credentials."""


@dataclass(frozen=True)
class ToolSpec:
    """The single structured-output tool the model must call. Defined by our code, never by inputs."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    messages: list[dict[str, Any]]
    tool: ToolSpec
    max_tokens: int = 2048
    allow_web_search: bool = False
    web_search_max_uses: int = 5


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    web_search_requests: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.web_search_requests += other.web_search_requests


@dataclass
class LLMResponse:
    tool_input: dict[str, Any] | None
    usage: Usage
    model: str
    stop_reason: str | None = None
    text: str = ""
    cited_urls: list[str] = field(default_factory=list)


class LLMProvider(Protocol):
    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
