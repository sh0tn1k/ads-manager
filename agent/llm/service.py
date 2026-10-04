"""LLMService: runs a task with schema validation, ≤ LLM_MAX_RETRIES retries, cost logging and the daily cap.

Invalid output never leaves this module: callers get ``TaskResult.ok=False`` and fall back to non-LLM logic.
The service has no access to the Ads API client or the executor and never sends credentials to the model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.llm import tasks
from agent.llm.costs import DAY, LlmCalls, disabled_reasons, prices
from agent.llm.guard import wrap_untrusted
from agent.llm.provider import LLMProvider, LLMProviderError, LLMRequest, ToolSpec, Usage
from agent.store import Store

logger = logging.getLogger("agent.llm")

T = TypeVar("T", bound=BaseModel)

REASON_RU = {
    "LLM_MODEL": "не задана модель LLM_MODEL",
    "ANTHROPIC_API_KEY": "не задан ANTHROPIC_API_KEY",
    "LLM_DAILY_USD_CAP": "не задан суточный лимит LLM_DAILY_USD_CAP",
    "LLM_PRICE_INPUT_PER_MTOK": "не задана цена LLM_PRICE_INPUT_PER_MTOK",
    "LLM_PRICE_OUTPUT_PER_MTOK": "не задана цена LLM_PRICE_OUTPUT_PER_MTOK",
    "LLM_PRICE_CACHE_READ_PER_MTOK": "не задана цена LLM_PRICE_CACHE_READ_PER_MTOK",
    "LLM_PRICE_CACHE_WRITE_PER_MTOK": "не задана цена LLM_PRICE_CACHE_WRITE_PER_MTOK",
    "LLM_PRICE_WEB_SEARCH_PER_1K": "не задана цена веб-поиска LLM_PRICE_WEB_SEARCH_PER_1K",
    "LLM_PROVIDER_UNSUPPORTED": "неподдерживаемый LLM_PROVIDER",
    "llm_daily_cap_reached": "достигнут суточный лимит LLM_DAILY_USD_CAP",
    "invalid_output": "LLM вернула невалидный ответ",
}


@dataclass
class TaskResult(Generic[T]):
    ok: bool
    output: T | None = None
    reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    attempts: int = 0
    cited_urls: list[str] = field(default_factory=list)

    def reason_ru(self) -> str:
        return "; ".join(REASON_RU.get(r, r) for r in self.reasons)


def _validation_summary(exc: ValidationError) -> list[str]:
    # Locations and error types only: never echo (possibly injected) input values back to the model.
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()[:10]]


class LLMService:
    def __init__(self, store: Store, config: AgentConfig, provider: LLMProvider | None, clock: Clock = system_clock) -> None:
        self.store = store
        self.config = config
        self.provider = provider
        self.clock = clock
        self.calls = LlmCalls(store)

    @classmethod
    def from_config(cls, store: Store, config: AgentConfig, clock: Clock = system_clock) -> "LLMService":
        provider = None
        if config.llm_provider == "anthropic" and config.anthropic_api_key:
            from agent.llm.anthropic import AnthropicProvider

            provider = AnthropicProvider(config.anthropic_api_key)
        return cls(store, config, provider, clock)

    def disabled_reasons(self, *, web_search: bool = False) -> list[str]:
        reasons = disabled_reasons(self.config)
        if self.provider is None and not reasons:
            reasons.append("ANTHROPIC_API_KEY")
        if web_search and self.config.llm_price_web_search_per_1k is None:
            reasons.append("LLM_PRICE_WEB_SEARCH_PER_1K")
        return reasons

    @property
    def enabled(self) -> bool:
        return not self.disabled_reasons()

    def spent_today(self) -> float:
        return self.calls.spent_since(self.clock() - DAY)

    def _worst_case_cost(self, web_search: bool) -> float:
        p = prices(self.config)
        cost = self.config.llm_max_tokens * p.output / 1_000_000
        if web_search:
            cost += self.config.llm_web_search_max_uses * (p.web_search_per_1k or 0) / 1000
        return cost

    async def run(
        self,
        spec: tasks.TaskSpec,
        content: str,
        check: Callable[[Any], list[str]] | None = None,
        check_factory: Callable[[list[str]], Callable[[Any], list[str]]] | None = None,
    ) -> TaskResult:
        reasons = self.disabled_reasons(web_search=spec.web_search)
        if reasons:
            return TaskResult(False, reasons=reasons)
        assert self.provider is not None and self.config.llm_model and self.config.llm_daily_usd_cap is not None
        tool = ToolSpec(spec.tool_name, spec.tool_description, spec.schema.model_json_schema())
        p = prices(self.config)
        result = TaskResult(False)
        feedback = ""
        for attempt in range(1, self.config.llm_max_retries + 2):
            if self.spent_today() + self._worst_case_cost(spec.web_search) > self.config.llm_daily_usd_cap + 1e-12:
                result.reasons = ["llm_daily_cap_reached"]
                return result
            request = LLMRequest(
                model=self.config.llm_model,
                system=spec.system,
                messages=[{"role": "user", "content": content + feedback}],
                tool=tool,
                max_tokens=self.config.llm_max_tokens,
                allow_web_search=spec.web_search,
                web_search_max_uses=self.config.llm_web_search_max_uses,
            )
            result.attempts = attempt
            try:
                response = await self.provider.complete(request)
            except LLMProviderError as exc:
                self.calls.record(ts=self.clock(), purpose=spec.purpose, model=self.config.llm_model, usage=Usage(),
                                  cost=0.0, ok=False, attempt=attempt, error=str(exc))
                result.errors.append(str(exc))
                continue
            cost = p.cost(response.usage)
            result.cost_usd = round(result.cost_usd + cost, 6)
            errors: list[str] = []
            output = None
            if response.tool_input is None:
                errors = ["no_tool_call"]
            else:
                try:
                    output = spec.schema.model_validate(response.tool_input)
                except ValidationError as exc:
                    errors = _validation_summary(exc)
                if output is not None:
                    checker = check_factory(response.cited_urls) if check_factory else check
                    errors = checker(output) if checker else []
            ok = not errors
            self.calls.record(ts=self.clock(), purpose=spec.purpose, model=response.model, usage=response.usage,
                              cost=cost, ok=ok, attempt=attempt, error="; ".join(errors) or None)
            if ok:
                result.ok, result.output, result.cited_urls = True, output, response.cited_urls
                return result
            result.errors += errors
            feedback = (
                "\n\nYour previous tool call was rejected by the validator: "
                + json.dumps(errors[:10]) + ". Call the tool again with output that satisfies the schema and rules."
            )
        result.reasons = ["invalid_output"]
        return result

    # --- the four tasks ---

    async def research_hypotheses(self, *, channel: str, history: list[dict], allowed_geo: list[str] | None,
                                  web_notes: str = "") -> TaskResult:
        content = "\n\n".join([
            "Channel description:", wrap_untrusted("channel", channel),
            "Past ads summary (JSON):", wrap_untrusted("history", json.dumps(history, ensure_ascii=False)[:8000]),
            "Allowed country codes (owner geo pool): " + (", ".join(allowed_geo) if allowed_geo else "any"),
            *(["Notes from pages:", wrap_untrusted("web", web_notes)] if web_notes else []),
        ])
        return await self.run(tasks.RESEARCH, content,
                              check_factory=lambda urls: tasks.research_check(allowed_geo, urls))

    async def generate_creatives(self, *, past: list[dict], promote_urls: list[str], telegram_url: bool) -> TaskResult:
        content = "Past variants with real metrics (JSON):\n" + wrap_untrusted(
            "past_ads", json.dumps(past, ensure_ascii=False)[:8000]
        )
        return await self.run(tasks.CREATIVES, content, check=tasks.creatives_check(promote_urls, telegram_url))

    async def policy_check_and_fix(self, *, text: str, decline_reason: str | None, promote_urls: list[str]) -> TaskResult:
        content = "Ad text:\n" + wrap_untrusted("ad_text", text)
        if decline_reason:
            content += "\n\nModeration decline reason:\n" + wrap_untrusted("decline_reason", decline_reason)
        return await self.run(tasks.POLICY, content, check=tasks.policy_check(promote_urls))

    async def explain_proposal(self, *, facts: dict) -> TaskResult:
        content = "Facts about the proposed experiment (JSON, no numbers):\n" + wrap_untrusted(
            "facts", json.dumps(facts, ensure_ascii=False)
        )
        return await self.run(tasks.EXPLAIN, content, check=tasks.explain_check)
