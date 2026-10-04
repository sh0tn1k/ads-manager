"""Backtest runner and metrics (section 13)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from agent.sim.env import SimConfig, SimEnv
from agent.sim.policies import POLICIES


@dataclass
class SimReport:
    budgets: list[float]
    reps: int
    mean_regret: dict[str, list[float]] = field(default_factory=dict)
    p_within20: dict[str, list[float]] = field(default_factory=dict)
    p_top3: dict[str, list[float]] = field(default_factory=dict)
    spend_to_within20: dict[str, float] = field(default_factory=dict)
    never_within20: dict[str, float] = field(default_factory=dict)
    coverage: dict[str, float] = field(default_factory=dict)

    def budget_to_reach(self, policy: str, target_regret: float) -> float:
        """Smallest budget (log-interpolated) where mean regret <= target; inf if never."""
        regrets = self.mean_regret[policy]
        for i, (b, r) in enumerate(zip(self.budgets, regrets)):
            if r <= target_regret:
                if i == 0:
                    return b
                b0, r0 = self.budgets[i - 1], regrets[i - 1]
                if r0 == r:
                    return b
                t = (r0 - target_regret) / (r0 - r)
                return float(math.exp(math.log(b0) + t * (math.log(b) - math.log(b0))))
        return math.inf

    def savings_vs_grid(self, policy: str = "proposed") -> list[tuple[float, float, float]]:
        """For each grid budget B: (B, budget the policy needs for grid's regret at B, ratio)."""
        out = []
        for b, r in zip(self.budgets, self.mean_regret["grid"]):
            need = self.budget_to_reach(policy, r)
            out.append((b, need, b / need if need and math.isfinite(need) else 0.0))
        return out


def run_experiment(
    config: SimConfig,
    *,
    budgets: Sequence[float],
    reps: int,
    model_kwargs: dict,
    policies: Sequence[str] = ("random", "grid", "halving", "proposed", "proposed_4_2"),
    samples: int = 1000,
    seed: int = 0,
) -> SimReport:
    report = SimReport(budgets=list(budgets), reps=reps)
    regrets = {p: np.zeros((reps, len(budgets))) for p in policies}
    top3 = {p: np.zeros((reps, len(budgets))) for p in policies}
    coverage: dict[str, list[bool]] = {"cpa": [], "ctr": []}
    for r in range(reps):
        env = SimEnv(config, seed=seed * 100_003 + r)
        best3 = env.top(3)
        for bi, budget in enumerate(budgets):
            for pi, name in enumerate(policies):
                rng = np.random.default_rng([seed, r, bi, pi])
                kwargs = {"hurdle": config.hurdle, "model_kwargs": model_kwargs, "samples": samples}
                result = POLICIES[name](env, float(budget), rng, **kwargs)
                regrets[name][r, bi] = env.regret(result.chosen)
                top3[name][r, bi] = result.chosen in best3
                if name != "proposed":
                    continue
                for k, v in result.coverage.items():
                    coverage[k].extend(v)
    for name in policies:
        report.mean_regret[name] = regrets[name].mean(axis=0).tolist()
        within = regrets[name] <= 0.2
        report.p_within20[name] = within.mean(axis=0).tolist()
        report.p_top3[name] = top3[name].mean(axis=0).tolist()
        first = [budgets[int(np.argmax(row))] if row.any() else math.inf for row in within]
        finite = [f for f in first if math.isfinite(f)]
        report.spend_to_within20[name] = float(np.median(finite)) if finite else math.inf
        report.never_within20[name] = float(np.mean([not math.isfinite(f) for f in first]))
    report.coverage = {k: float(np.mean(v)) for k, v in coverage.items() if v}
    return report
