"""History generator: one-factor neighbours of the best past ads (section 9)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from agent.candidates.base import Candidate, Creative, input_target, short_title
from agent.candidates.inputs import GeoPool
from agent.model.factors import FactorRow

FACTOR_RU = {"cpm": "CPM", "angle": "креатив", "geo": "гео", "topic": "тема"}


def rank_history(rows: Sequence[FactorRow]) -> list[FactorRow]:
    """Best first: observed CPA for ads with joins, then CPC; ads without clicks are not 'best'."""
    def key(r: FactorRow) -> tuple[int, float]:
        if r.tracks_joins and r.joins > 0:
            return (0, r.spent / r.joins)
        if r.clicks > 0:
            return (1, r.spent / r.clicks)
        return (2, math.inf)
    usable = [r for r in rows if r.meta.get("target") and (r.clicks > 0 or r.joins > 0)]
    return sorted(usable, key=key)


def base_candidate(row: FactorRow, promote_url: str, source: str = "control") -> Candidate | None:
    target = input_target(row.meta.get("target"))
    placement = row.meta.get("placement")
    if not target or not placement:
        return None
    text = row.meta.get("text")
    return Candidate(
        source=source,
        title=short_title("control", f"#{row.ad_id}"),
        text=text,
        placement=str(placement),
        target=target,
        cpm=round(row.cpm_bid, 2),
        angle=row.factors.get("angle", "none"),
        promote_url=promote_url,
        rationale_ru=f"Контроль: повтор лучшего известного объявления #{row.ad_id} в том же периоде, что и тесты.",
        base_ad_id=row.ad_id,
    )


def history_candidates(
    rows: Sequence[FactorRow],
    *,
    promote_url: str,
    creatives: Sequence[Creative],
    geo_pool: GeoPool | None,
    cpm_step: float,
    top_n: int,
) -> list[Candidate]:
    out: list[Candidate] = []
    for row in rank_history(rows)[:top_n]:
        base = base_candidate(row, promote_url, source="history")
        if base is None:
            continue
        ttype = base.target["type"]

        def variant(factor: str, old: str, new: str, **changes) -> Candidate:
            fields = {**base.__dict__, **changes}
            fields.update(
                source="history",
                changed=(factor,),
                title=short_title("hist", f"#{row.ad_id}", factor, new),
                rationale_ru=f"Мутация лучшего объявления #{row.ad_id}: {FACTOR_RU[factor]} {old} → {new}.",
                ctr_shifts={},
                cvr_shifts={},
            )
            return Candidate(**fields)

        for delta in (-cpm_step, cpm_step):
            cpm = round(base.cpm + delta, 2)
            if cpm > 0:
                out.append(variant("cpm", f"{base.cpm:.2f}", f"{cpm:.2f}", cpm=cpm))
        if ttype != "search":
            for creative in creatives:
                if creative.angle != base.angle and creative.text != base.text:
                    out.append(variant("angle", base.angle, creative.angle, text=creative.text, angle=creative.angle))
        if geo_pool and ttype == "users":
            current = base.target.get("country_codes") or []
            for name, codes in geo_pool.groups.items():
                if codes != current:
                    out.append(variant("geo", "+".join(current) or "any", name, target={**base.target, "country_codes": codes}))
        if geo_pool and ttype in ("users", "channels") and not base.target.get("channel_ids"):
            current_topics = base.target.get("topic_ids") or []
            for topic in geo_pool.topic_ids:
                if [topic] != current_topics:
                    target = {**base.target, "topic_ids": [topic]}
                    if ttype == "channels" and len(target.get("language_codes") or []) != 1:
                        continue
                    out.append(variant("topic", "+".join(map(str, current_topics)) or "any", str(topic), target=target))
    return out
