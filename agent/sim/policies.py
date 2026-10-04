"""Policies compared in the backtest: random, full grid, successive halving, proposed."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from agent.model.cpa import predict_arm
from agent.model.cpm import CpmModel
from agent.model.factors import FactorRow
from agent.model.posterior import PosteriorModel
from agent.sim.env import Observation, SimEnv


@dataclass
class PolicyResult:
    chosen: int
    spent: float
    observations: list[Observation] = field(default_factory=list)
    coverage: dict[str, list[bool]] = field(default_factory=dict)


def _totals(obs: list[Observation]) -> dict[int, list[float]]:
    agg: dict[int, list[float]] = defaultdict(lambda: [0.0, 0, 0, 0])
    for o in obs:
        a = agg[o.cell]
        a[0] += o.budget
        a[1] += o.views
        a[2] += o.clicks
        a[3] += o.joins
    return agg


def _observed_cpa(agg: dict[int, list[float]], cells: list[int] | None = None) -> int:
    cells = list(agg) if cells is None else cells
    def key(c: int) -> tuple[float, float]:
        spent, views, clicks, joins = agg[c]
        cpa = spent / joins if joins else math.inf
        cpc = spent / clicks if clicks else math.inf
        return (cpa, cpc)
    return min(cells, key=key)


def random_policy(env: SimEnv, budget: float, rng: np.random.Generator, *, chunk: float = 8.0, **_: object) -> PolicyResult:
    n = max(1, min(env.n_cells, int(budget // chunk)))
    cells = rng.choice(env.n_cells, size=n, replace=False).tolist()
    obs = [env.run(c, budget / n, rng) for c in cells]
    return PolicyResult(_observed_cpa(_totals(obs)), budget, obs)


def grid_policy(env: SimEnv, budget: float, rng: np.random.Generator, **_: object) -> PolicyResult:
    per = budget / env.n_cells
    obs = [env.run(c, per, rng) for c in range(env.n_cells)]
    return PolicyResult(_observed_cpa(_totals(obs)), budget, obs)


def halving_policy(env: SimEnv, budget: float, rng: np.random.Generator, *, eta: int = 3, **_: object) -> PolicyResult:
    survivors = list(range(env.n_cells))
    rounds = max(1, math.ceil(math.log(env.n_cells, eta)))
    obs: list[Observation] = []
    for r in range(rounds):
        per = budget / rounds / len(survivors)
        obs += [env.run(c, per, rng) for c in survivors]
        agg = _totals(obs)
        if r == rounds - 1:
            break

        def score(c: int) -> float:
            spent, views, clicks, joins = agg[c]
            if r == 0:  # screening by CPC (section 7.4: enough events)
                return spent / clicks if clicks else math.inf
            return spent / joins if joins else math.inf

        survivors = sorted(survivors, key=score)[: max(1, math.ceil(len(survivors) / eta))]
    return PolicyResult(_observed_cpa(_totals(obs), survivors), budget, obs)


def _rows(env: SimEnv, obs: list[Observation]) -> list[FactorRow]:
    return [
        FactorRow(env.cells[c], int(v), int(cl), int(j), float(s), float(env.cpm[c]))
        for c, (s, v, cl, j) in _totals(obs).items()
    ]


def proposed_policy(
    env: SimEnv,
    budget: float,
    rng: np.random.Generator,
    *,
    hurdle: float,
    model_kwargs: dict,
    samples: int = 1000,
    fractions: tuple[float, float, float] = (0.3, 0.35, 0.35),
    stage2_arms: int | None = None,
    stage3_arms: int | None = None,
    eta: int = 3,
    **_: object,
) -> PolicyResult:
    """Model + stages + planner: cold-start design → P(CPA<c*) screening → validation of the leaders.

    Stage sizes default to successive halving with eta (ceil(K/eta), then ceil(that/eta)).
    """
    if stage2_arms is None:
        stage2_arms = max(1, math.ceil(env.n_cells / eta))
    if stage3_arms is None:
        stage3_arms = max(1, math.ceil(stage2_arms / eta))
    geos = sorted({c["geo"] for c in env.cells})
    angles = sorted({c["angle"] for c in env.cells})
    index = {(c["geo"], c["angle"]): i for i, c in enumerate(env.cells)}
    design = [index[(geos[i % len(geos)], angles[i % len(angles)])] for i in range(max(len(geos), len(angles)))]
    obs = [env.run(c, budget * fractions[0] / len(design), rng) for c in design]

    def fit() -> tuple[PosteriorModel, CpmModel]:
        rows = _rows(env, obs)
        model = PosteriorModel.from_config(**model_kwargs).fit(rows)
        cpm = CpmModel(keys=("geo",)).fit(rows)
        return model, cpm

    def predictions(cells: list[int]) -> dict[int, object]:
        model, cpm = fit()
        return {
            c: predict_arm(model, cpm, env.cells[c], bid=float(env.cpm[c]), hurdle=hurdle, samples=samples, rng=rng)
            for c in cells
        }

    preds = predictions(list(range(env.n_cells)))
    stage2 = sorted(preds, key=lambda c: -preds[c].p_beats_hurdle)[:stage2_arms]
    obs += [env.run(c, budget * fractions[1] / len(stage2), rng) for c in stage2]
    preds = predictions(stage2)
    stage3 = sorted(stage2, key=lambda c: preds[c].cpa_median)[:stage3_arms]
    obs += [env.run(c, budget * fractions[2] / len(stage3), rng) for c in stage3]
    tested = sorted(_totals(obs))
    preds = predictions(tested)
    chosen = min(stage3, key=lambda c: preds[c].cpa_median)
    coverage: dict[str, list[bool]] = {"cpa": [], "ctr": []}
    for c in tested:
        p = preds[c]
        lo, hi = np.percentile(p.cpa, [10, 90])
        coverage["cpa"].append(bool(lo <= env.true_cpa[c] <= hi))
        lo, hi = np.percentile(p.ctr, [10, 90])
        coverage["ctr"].append(bool(lo <= env.ctr[c] <= hi))
    return PolicyResult(chosen, budget, obs, coverage)


def proposed_fixed_policy(env: SimEnv, budget: float, rng: np.random.Generator, **kwargs) -> PolicyResult:
    """First variant that was tried: 4 arms at stage 2, 2 at stage 3 (kept for the honest record)."""
    kwargs.pop("stage2_arms", None)
    kwargs.pop("stage3_arms", None)
    return proposed_policy(env, budget, rng, stage2_arms=4, stage3_arms=2, **kwargs)


POLICIES: dict[str, Callable[..., PolicyResult]] = {
    "random": random_policy,
    "grid": grid_policy,
    "halving": halving_policy,
    "proposed": proposed_policy,
    "proposed_4_2": proposed_fixed_policy,
}
