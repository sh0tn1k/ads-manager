"""LLM-backed candidate inputs (section 10): creatives, research ideas, card explanations.

Every LLM result has already passed the strict schema and guards in ``agent.llm``; on any failure the
cycle continues without it. Numbers never come from the LLM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from agent.candidates.base import Candidate, Creative, short_title
from agent.llm.service import LLMService
from agent.model.factors import FactorRow
from agent.validator.validator import is_telegram_url

PRIOR_SHIFT = 0.3  # section 7.1: LLM/external priors as level shifts with small sigma (~0.3 logits)


def past_variants(rows: Sequence[FactorRow], limit: int = 12) -> list[dict]:
    agg: dict[str, list] = defaultdict(lambda: ["", 0, 0, 0])
    for r in rows:
        text = r.meta.get("text")
        if not text:
            continue
        a = agg[text]
        a[0] = r.factors.get("angle", "")
        a[1] += r.views
        a[2] += r.clicks
        a[3] += r.joins
    items = [
        {"text": t, "views": v, "clicks": c, "joins": j, "ctr": round(c / v, 5) if v else None}
        for t, (_, v, c, j) in agg.items()
    ]
    return sorted(items, key=lambda x: -(x["ctr"] or 0))[:limit]


async def llm_creatives(llm: LLMService, rows: Sequence[FactorRow], promote_url: str) -> tuple[list[Creative], str | None]:
    result = await llm.generate_creatives(
        past=past_variants(rows), promote_urls=list(llm.config.promote_url_allowlist),
        telegram_url=is_telegram_url(promote_url),
    )
    if not result.ok:
        return [], result.reason_ru()
    return [Creative(angle=f"llm_{v.angle}"[:40], text=v.text) for v in result.output.variants], None


async def llm_research(
    llm: LLMService, rows: Sequence[FactorRow], *, channel: str, allowed_geo: list[str] | None, base_cpm: float | None,
    promote_url: str,
) -> tuple[list[Candidate], dict, str | None]:
    """Search-query candidates and geo/topic prior shifts (CTR logit, ±0.3·confidence)."""
    history = [{k: v for k, v in item.items() if k != "text"} | {"angle": None} for item in past_variants(rows)]
    result = await llm.research_hypotheses(channel=channel, history=history, allowed_geo=allowed_geo)
    if not result.ok:
        return [], {}, result.reason_ru()
    out = result.output
    shifts: dict[tuple[str, str], float] = {}
    for g in out.geo_priors:
        shifts[("geo", g.country_code)] = (1 if g.direction == "higher" else -1) * PRIOR_SHIFT * g.confidence
    for t in out.topic_priors:
        shifts[("topic", str(t.topic_id))] = (1 if t.direction == "higher" else -1) * PRIOR_SHIFT * t.confidence
    candidates = []
    if base_cpm is not None:
        for q in sorted(out.search_queries, key=lambda q: -q.confidence)[:5]:
            candidates.append(
                Candidate(
                    source="llm",
                    title=short_title("search", q.query),
                    text=None,
                    placement="search_result",
                    target={"type": "search", "search_queries": [q.query.lower()]},
                    cpm=round(base_cpm, 2),
                    angle="search",
                    promote_url=promote_url,
                    rationale_ru="Поисковый запрос из ресерча (источники ниже). Потенциал измеряется пробным показом.",
                    sources=[str(s) for s in q.sources],
                )
            )
    return candidates, shifts, None


def explanation_facts(candidate: Candidate, factors: dict[str, str]) -> dict:
    """Facts for the explanation task: names only, no numbers (digits are stripped)."""
    def no_digits(value: str) -> str:
        return "".join("#" if ch.isdigit() else ch for ch in value)

    return {
        "source": candidate.source,
        "changed_factors": list(candidate.changed),
        "target_type": factors.get("target_type"),
        "placement": candidate.placement,
        "geo": no_digits(factors.get("geo", "")),
        "angle": no_digits(candidate.angle),
        "template_rationale": no_digits(candidate.rationale_ru),
    }
