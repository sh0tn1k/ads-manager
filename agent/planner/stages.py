"""Stage policy (sections 7.4–7.6): successive halving, stop/promote rules, cold start."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeVar

from agent.config import AgentConfig

T = TypeVar("T")


@dataclass(frozen=True)
class StageDecision:
    action: str  # continue | kill | promote
    reason: str


class StagePolicy:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def min_views(self, stage: int | str) -> int:
        return self.config.stage1_min_views if str(stage) == "1" else 0

    def decide(self, *, stage: int | str, views: int, p_beats_hurdle: float) -> StageDecision:
        c = self.config
        if views < self.min_views(stage):
            return StageDecision("continue", "min_views_not_reached")
        if p_beats_hurdle < c.kill_if_p_below:
            return StageDecision("kill", f"P(CPA<c*)={p_beats_hurdle:.2f} < {c.kill_if_p_below}")
        if p_beats_hurdle > c.promote_if_p_above:
            return StageDecision("promote", f"P(CPA<c*)={p_beats_hurdle:.2f} > {c.promote_if_p_above}")
        return StageDecision("continue", "inconclusive")

    def survivors(self, ranked: Sequence[T]) -> list[T]:
        """Keep the top 1/eta of a list already sorted best-first."""
        if not ranked:
            return []
        keep = max(1, math.ceil(len(ranked) / self.config.halving_eta))
        return list(ranked[:keep])

    def next_stage(self, stage: int | str) -> int | str:
        return {"1": 2, "2": 3, "3": "scale"}.get(str(stage), "scale")

    def is_cold_start(self, finished_cells: int) -> bool:
        return finished_cells < self.config.cold_start_n0


def one_factor_design(base: dict[str, str], levels: dict[str, Sequence[str]]) -> list[dict[str, str]]:
    """Section 7.6: base arm plus one-factor-at-a-time variants."""
    arms = [dict(base)]
    for factor, values in levels.items():
        for value in values:
            if base.get(factor) == value:
                continue
            arm = dict(base)
            arm[factor] = value
            arms.append(arm)
    return arms
