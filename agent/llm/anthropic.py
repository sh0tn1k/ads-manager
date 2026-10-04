"""Anthropic Messages API provider over httpx (tool-forced JSON, prompt caching, optional web search)."""

from __future__ import annotations

from typing import Any

import httpx

from agent.llm.provider import LLMProviderError, LLMRequest, LLMResponse, Usage

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
WEB_SEARCH_TOOL = "web_search_20250305"
MAX_CONTINUATIONS = 3


def _usage(data: dict[str, Any]) -> Usage:
    u = data.get("usage") or {}
    server = u.get("server_tool_use") or {}
    return Usage(
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(u.get("cache_creation_input_tokens") or 0),
        web_search_requests=int(server.get("web_search_requests") or 0),
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None, url: str = API_URL,
                 timeout: float = 120.0) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._api_key = api_key
        self._client = client
        self._url = url
        self._timeout = timeout

    def __repr__(self) -> str:  # never print the key
        return "AnthropicProvider(url=%r)" % self._url

    def build_body(self, request: LLMRequest, messages: list[dict[str, Any]], *, force_tool: bool) -> dict[str, Any]:
        tools: list[dict[str, Any]] = []
        if request.allow_web_search:
            tools.append({"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": request.web_search_max_uses})
        tools.append({
            "name": request.tool.name,
            "description": request.tool.description,
            "input_schema": request.tool.input_schema,
            "cache_control": {"type": "ephemeral"},
        })
        # Without web search the output tool is forced; with it the model may search first ("any" still
        # forbids a plain-text answer), and a final forced turn follows if needed.
        tool_choice = {"type": "tool", "name": request.tool.name} if force_tool else {"type": "any"}
        return {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        }

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(self._url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"transport: {type(exc).__name__}") from None
        finally:
            if self._client is None:
                await client.aclose()
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
                detail = f"{err.get('type', '')}: {str(err.get('message', ''))[:200]}"
            except ValueError:
                detail = resp.text[:200]
            raise LLMProviderError(f"http {resp.status_code} {detail}".replace(self._api_key, "***"))
        return resp.json()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = list(request.messages)
        usage = Usage()
        force = not request.allow_web_search
        data: dict[str, Any] = {}
        urls: list[str] = []
        for _ in range(MAX_CONTINUATIONS + 1):
            data = await self._post(self.build_body(request, messages, force_tool=force))
            usage.add(_usage(data))
            content = data.get("content") or []
            for block in content:
                if block.get("type") == "web_search_tool_result" and isinstance(block.get("content"), list):
                    urls += [r.get("url") for r in block["content"] if isinstance(r, dict) and r.get("url")]
            for block in content:
                if block.get("type") == "tool_use" and block.get("name") == request.tool.name:
                    return LLMResponse(
                        tool_input=block.get("input") if isinstance(block.get("input"), dict) else None,
                        usage=usage, model=str(data.get("model") or request.model),
                        stop_reason=data.get("stop_reason"), cited_urls=urls,
                    )
            stop = data.get("stop_reason")
            if stop == "pause_turn":
                messages = messages + [{"role": "assistant", "content": content}]
                continue
            if stop in ("end_turn", "tool_use") and not force:
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Return the final result only by calling the {request.tool.name} tool."},
                ]
                force = True
                continue
            break
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        return LLMResponse(None, usage, str(data.get("model") or request.model), data.get("stop_reason"), text[:500], urls)
