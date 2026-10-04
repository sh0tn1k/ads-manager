"""BudgetPlanner (sections 7.9, 8): exploration cap, slots and per-stage budgets."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.config import AgentConfig

REASON_RU = {
    "HURDLE_CPA_TON": "не задан порог HURDLE_CPA_TON",
    "EXPLORE_SHARE": "не задана доля исследования EXPLORE_SHARE",
    "MAX_LOSS_PER_HYPOTHESIS": "не задан лимит MAX_LOSS_PER_HYPOTHESIS",
    "MAX_ACTIVE_EXPERIMENTS": "не задан лимит MAX_ACTIVE_EXPERIMENTS",
    "no_budget": "нет свободного бюджета на исследование",
    "no_slots": "достигнут лимит активных экспериментов",
}


@dataclass
class BudgetPlan:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    free_balance: float = 0.0
    explore_cap: float = 0.0
    slots: int = 0
    stage_budgets: dict[str, float] = field(default_factory=dict)

    def reason_ru(self) -> str:
        return "; ".join(REASON_RU.get(r, r) for r in self.reasons)


class BudgetPlanner:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    def stage_budget(self, stage: int | str) -> float:
        c = self.config
        budget = {1: c.stage1_budget, 2: c.stage2_budget, 3: c.stage3_budget}.get(int(stage) if str(stage).isdigit() else 0)
        if budget is None:
            raise ValueError(f"no fixed budget for stage {stage}")
        if c.max_loss_per_hypothesis is not None:
            budget = min(budget, c.max_loss_per_hypothesis)
        return round(budget, 2)

    def plan(self, free_balance: float, active_experiments: int) -> BudgetPlan:
        missing = self.config.missing_planner_inputs()
        if missing:
            return BudgetPlan(False, missing, free_balance=free_balance)
        c = self.config
        assert c.explore_share is not None and c.max_active_experiments is not None
        explore_cap = round(max(0.0, free_balance) * c.explore_share, 2)
        slots = max(0, c.max_active_experiments - active_experiments)
        stages = {str(s): self.stage_budget(s) for s in (1, 2, 3)}
        reasons = []
        if slots == 0:
            reasons.append("no_slots")
        if explore_cap < stages["1"]:
            reasons.append("no_budget")
        return BudgetPlan(
            ok=not reasons,
            reasons=reasons,
            free_balance=free_balance,
            explore_cap=explore_cap,
            slots=slots,
            stage_budgets=stages,
        )


def exploration_justified(cost: float, scale_spend: float, found_cpa: float, hurdle: float) -> bool:
    """Section 7.9: E < S * (1 - c_found / c*)."""
    return cost < scale_spend * (1 - found_cpa / hurdle)
