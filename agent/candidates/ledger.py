"""Ledger generator: best channels from external_placements as `channels` targets (section 9)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from agent.candidates.base import Candidate, Creative, short_title
from agent.candidates.inputs import ChannelStats


def ledger_prior_shift(channel: ChannelStats, median_cpj: float, clip: float = 1.0) -> float:
    """Logit shift for CVR: cheaper joins than the ledger median → positive shift. Relative only (USD ≠ TON)."""
    if not channel.joins or not math.isfinite(channel.cost_per_join_usd) or channel.cost_per_join_usd <= 0:
        return 0.0
    return max(-clip, min(clip, math.log(median_cpj / channel.cost_per_join_usd)))


def ledger_candidates(
    channels: Sequence[ChannelStats],
    *,
    creative: Creative | None,
    base_cpm: float,
    promote_url: str,
    top_n: int,
    min_joins: int,
) -> list[Candidate]:
    usable = [c for c in channels if c.joins >= min_joins and c.price_usd > 0]
    if not usable or creative is None:
        return []
    median_cpj = statistics.median(c.cost_per_join_usd for c in usable)
    out = []
    for ch in sorted(usable, key=lambda c: c.cost_per_join_usd)[:top_n]:
        username = f"@{ch.username}"
        out.append(
            Candidate(
                source="ledger",
                title=short_title("ledger", username),
                text=creative.text,
                placement="channel_post",
                target={"type": "channels", "channel_ids": [username]},
                cpm=round(base_cpm, 2),
                angle=creative.angle,
                promote_url=promote_url,
                rationale_ru=f"Канал {username} из внешних размещений: стоимость вступления ниже медианы таблицы.",
                cvr_shifts={("channels", username): ledger_prior_shift(ch, median_cpj)},
            )
        )
    return out
