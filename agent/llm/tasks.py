"""The four LLM tasks (section 10): prompts, schemas, semantic checks. Web search only in research."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agent.llm.guard import UNTRUSTED_NOTE, creative_text_issues, search_query_issues
from agent.llm.schemas import CreativesOutput, ExplanationOutput, PolicyCheckOutput, ResearchOutput


@dataclass(frozen=True)
class TaskSpec:
    purpose: str
    schema: type[BaseModel]
    tool_name: str
    tool_description: str
    system: str
    web_search: bool = False


RESEARCH = TaskSpec(
    purpose="research_hypotheses",
    schema=ResearchOutput,
    tool_name="submit_research",
    tool_description="Submit research hypotheses for Telegram Ads targeting. Every item needs source URLs.",
    system=(
        "You help plan Telegram Ads experiments for a football (Premier League) Telegram channel. "
        "Propose search queries (clubs, players, events), geo and topic priors (direction only) and an event "
        "calendar (derbies, transfer deadlines, FPL gameweeks). Use web search when useful. Every item must cite "
        "URLs you actually saw in search results and carry a confidence in [0,1]. Do not estimate search volumes, "
        "prices or CPA. " + UNTRUSTED_NOTE
    ),
    web_search=True,
)

CREATIVES = TaskSpec(
    purpose="generate_creatives",
    schema=CreativesOutput,
    tool_name="submit_creatives",
    tool_description="Submit ad text variants along the axes angle, length, CTA and button.",
    system=(
        "You write Telegram Ads texts (max 160 characters) for a football Telegram channel. Mutate the best past "
        "variants and drop ideas similar to the worst. Vary angle, length and call to action. Never include links, "
        "@mentions of other channels, prices or promises you cannot keep. Do not predict click-through rates. "
        + UNTRUSTED_NOTE
    ),
)

POLICY = TaskSpec(
    purpose="policy_check_and_fix",
    schema=PolicyCheckOutput,
    tool_name="submit_policy_check",
    tool_description="Report whether the ad text complies with Telegram Ads rules and propose a fixed text if not.",
    system=(
        "You check Telegram Ads texts against the platform ad policies (no misleading claims, no prohibited content, "
        "no links in text, correct grammar and capitalisation). If a moderation decline reason is given, fix the "
        "text to address it while keeping the meaning. " + UNTRUSTED_NOTE
    ),
)

EXPLAIN = TaskSpec(
    purpose="explain_proposal",
    schema=ExplanationOutput,
    tool_name="submit_explanation",
    tool_description="Submit a short Russian explanation of why this experiment is proposed.",
    system=(
        "Write a short explanation in Russian (2–3 sentences) of why this Telegram Ads experiment is proposed, "
        "based only on the given facts. Do not write any numbers or digits: the card shows numbers from the "
        "statistical model separately. " + UNTRUSTED_NOTE
    ),
)


def research_check(allowed_geo: Iterable[str] | None, cited_urls: list[str]) -> Callable[[Any], list[str]]:
    geo = {g.upper() for g in allowed_geo} if allowed_geo is not None else None
    cited = {u.rstrip("/") for u in cited_urls}

    def check(out: ResearchOutput) -> list[str]:
        issues = []
        for q in out.search_queries:
            issues += search_query_issues(q.query)
        if geo is not None:
            issues += [f"geo_not_in_pool:{g.country_code}" for g in out.geo_priors if g.country_code not in geo]
        items = [*out.search_queries, *out.geo_priors, *out.topic_priors, *out.events]
        for item in items:
            for src in item.sources:
                if str(src).rstrip("/") not in cited:
                    issues.append("source_not_from_search")
                    break
        return sorted(set(issues))

    return check


def creatives_check(promote_urls: Iterable[str], telegram_url: bool) -> Callable[[Any], list[str]]:
    urls = list(promote_urls)

    def check(out: CreativesOutput) -> list[str]:
        issues = []
        for v in out.variants:
            issues += creative_text_issues(v.text, urls)
            if telegram_url and v.button is not None:
                issues.append("button_not_applicable_for_telegram_url")
        return sorted(set(issues))

    return check


def policy_check(promote_urls: Iterable[str]) -> Callable[[Any], list[str]]:
    urls = list(promote_urls)

    def check(out: PolicyCheckOutput) -> list[str]:
        if out.fixed_text is None:
            return [] if out.compliant else ["fixed_text_missing"]
        return creative_text_issues(out.fixed_text, urls)

    return check


def explain_check(out: ExplanationOutput) -> list[str]:
    return creative_text_issues(out.rationale_ru, [])
