"""LLM cost accounting in llm_calls and the LLM_DAILY_USD_CAP (rolling 24 h)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.config import AgentConfig
from agent.llm.provider import Usage
from agent.store import Store

DAY = 86400


@dataclass(frozen=True)
class Prices:
    input: float
    output: float
    cache_read: float
    cache_write: float
    web_search_per_1k: float | None

    def cost(self, u: Usage) -> float:
        cost = (
            u.input_tokens * self.input
            + u.output_tokens * self.output
            + u.cache_read_tokens * self.cache_read
            + u.cache_write_tokens * self.cache_write
        ) / 1_000_000
        if u.web_search_requests:
            cost += u.web_search_requests * (self.web_search_per_1k or 0.0) / 1000
        return round(cost, 6)


def disabled_reasons(config: AgentConfig) -> list[str]:
    """Why the LLM layer is off. Nothing is guessed: model, key, prices and cap are all owner inputs."""
    reasons = []
    if config.llm_provider != "anthropic":
        reasons.append("LLM_PROVIDER_UNSUPPORTED")
    if not config.llm_model:
        reasons.append("LLM_MODEL")
    if config.llm_provider == "anthropic" and not config.anthropic_api_key:
        reasons.append("ANTHROPIC_API_KEY")
    if config.llm_daily_usd_cap is None:
        reasons.append("LLM_DAILY_USD_CAP")
    for name in ("llm_price_input_per_mtok", "llm_price_output_per_mtok", "llm_price_cache_read_per_mtok",
                 "llm_price_cache_write_per_mtok"):
        if getattr(config, name) is None:
            reasons.append(name.upper())
    return reasons


def prices(config: AgentConfig) -> Prices:
    return Prices(
        input=float(config.llm_price_input_per_mtok or 0),
        output=float(config.llm_price_output_per_mtok or 0),
        cache_read=float(config.llm_price_cache_read_per_mtok or 0),
        cache_write=float(config.llm_price_cache_write_per_mtok or 0),
        web_search_per_1k=config.llm_price_web_search_per_1k,
    )


class LlmCalls:
    def __init__(self, store: Store) -> None:
        self.db = store.db

    def record(self, *, ts: int, purpose: str, model: str | None, usage: Usage, cost: float, ok: bool,
               attempt: int, error: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO llm_calls(ts, purpose, model, input_tokens, output_tokens, cache_read_tokens,
                   cache_write_tokens, web_search_requests, cost_usd, ok, attempt, error)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, purpose, model, usage.input_tokens, usage.output_tokens, usage.cache_read_tokens,
             usage.cache_write_tokens, usage.web_search_requests, cost, int(ok), attempt, (error or None) and error[:500]),
        )

    def spent_since(self, ts: int) -> float:
        return float(self.db.scalar("SELECT COALESCE(SUM(cost_usd),0) FROM llm_calls WHERE ts >= ?", (ts,)) or 0.0)

    def rows(self) -> list[dict]:
        return [dict(r) for r in self.db.fetchall("SELECT * FROM llm_calls ORDER BY id")]
