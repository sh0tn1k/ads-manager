"""Grid generator: geo × topic (× creative) around a base arm, one factor at a time (sections 7.6, 9)."""

from __future__ import annotations

from collections.abc import Sequence

from agent.candidates.base import Candidate, Creative, short_title
from agent.candidates.inputs import GeoPool
from agent.planner.stages import one_factor_design


def grid_candidates(
    geo_pool: GeoPool,
    *,
    creatives: Sequence[Creative],
    base_cpm: float,
    promote_url: str,
) -> list[Candidate]:
    if not geo_pool.groups or not creatives:
        return []
    groups = list(geo_pool.groups)
    by_angle = {c.angle: c for c in creatives}
    base = {"geo": groups[0], "angle": creatives[0].angle, "topic": "any"}
    levels = {
        "geo": groups,
        "topic": ["any"] + [str(t) for t in geo_pool.topic_ids],
        "angle": list(by_angle),
    }
    out = []
    for arm in one_factor_design(base, levels):
        target: dict = {"type": "users", "country_codes": geo_pool.groups[arm["geo"]]}
        if geo_pool.language_codes:
            target["language_codes"] = list(geo_pool.language_codes)
        if arm["topic"] != "any":
            target["topic_ids"] = [int(arm["topic"])]
        creative = by_angle[arm["angle"]]
        changed = tuple(k for k in base if arm[k] != base[k])
        desc = ", ".join(f"{k}={arm[k]}" for k in ("geo", "topic", "angle"))
        out.append(
            Candidate(
                source="grid",
                title=short_title("grid", arm["geo"], arm["topic"], arm["angle"]),
                text=creative.text,
                placement="video_banner",
                target=target,
                cpm=round(base_cpm, 2),
                angle=creative.angle,
                promote_url=promote_url,
                rationale_ru=(
                    "Сетка гео × тема: базовая связка." if not changed
                    else f"Сетка гео × тема: один фактор относительно базовой связки ({desc})."
                ),
                changed=changed,
            )
        )
    return out
