"""Synthetic Telegram Ads cells with known true CTR, CVR and CPM.

Truth: logit CTR = logit(base_ctr) + geo + angle + interaction (normal effects),
logit CVR likewise, CPM = base_cpm * lognormal(geo). Running a cell with budget b buys
floor(1000 b / cpm) views; clicks ~ Binomial(views, ctr); joins ~ Binomial(clicks, cvr).
All centres/spreads are explicit SimConfig fields supplied by the caller (tests, report script).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import expit, logit


@dataclass(frozen=True)
class SimConfig:
    base_ctr: float
    base_cvr: float
    base_cpm: float
    hurdle: float
    n_geo: int = 6
    n_angle: int = 4
    sd_geo_ctr: float = 0.4
    sd_angle_ctr: float = 0.3
    sd_geo_cvr: float = 0.5
    sd_angle_cvr: float = 0.2
    sd_interaction: float = 0.1
    sd_cpm_geo: float = 0.3


@dataclass
class Observation:
    cell: int
    budget: float
    views: int
    clicks: int
    joins: int


class SimEnv:
    def __init__(self, config: SimConfig, seed: int) -> None:
        self.config = config
        rng = np.random.default_rng(seed)
        g, a = config.n_geo, config.n_angle
        geo_ctr = rng.normal(0, config.sd_geo_ctr, g)
        ang_ctr = rng.normal(0, config.sd_angle_ctr, a)
        geo_cvr = rng.normal(0, config.sd_geo_cvr, g)
        ang_cvr = rng.normal(0, config.sd_angle_cvr, a)
        geo_cpm = rng.normal(0, config.sd_cpm_geo, g)
        self.cells: list[dict[str, str]] = []
        ctr, cvr, cpm = [], [], []
        for i in range(g):
            for j in range(a):
                self.cells.append({"geo": f"g{i}", "angle": f"a{j}"})
                ctr.append(expit(logit(config.base_ctr) + geo_ctr[i] + ang_ctr[j] + rng.normal(0, config.sd_interaction)))
                cvr.append(expit(logit(config.base_cvr) + geo_cvr[i] + ang_cvr[j] + rng.normal(0, config.sd_interaction)))
                cpm.append(config.base_cpm * np.exp(geo_cpm[i]))
        self.ctr = np.array(ctr)
        self.cvr = np.array(cvr)
        self.cpm = np.array(cpm)
        self.true_cpa = self.cpm / (1000 * self.ctr * self.cvr)
        self.best = int(np.argmin(self.true_cpa))

    @property
    def n_cells(self) -> int:
        return len(self.cells)

    def run(self, cell: int, budget: float, rng: np.random.Generator) -> Observation:
        views = int(1000 * budget / self.cpm[cell])
        clicks = int(rng.binomial(views, self.ctr[cell])) if views else 0
        joins = int(rng.binomial(clicks, self.cvr[cell])) if clicks else 0
        return Observation(cell, budget, views, clicks, joins)

    def regret(self, cell: int) -> float:
        """Relative simple regret: CPA(chosen) / CPA(best) - 1."""
        return float(self.true_cpa[cell] / self.true_cpa[self.best] - 1)

    def top(self, k: int) -> set[int]:
        return set(np.argsort(self.true_cpa)[:k].tolist())
