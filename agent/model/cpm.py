"""Realized CPM model (section 7.2): ratio m / cpm_bid shrunk by (target_type, geo group)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np


@dataclass
class CpmModel:
    """log(m / bid) ~ N(mean_cell, sd^2); cell means shrunk towards the global mean with k pseudo-views."""

    shrink_views: float = 5000.0
    prior_log_ratio: float = 0.0  # realized == bid until data says otherwise
    prior_sd: float = 0.25
    keys: tuple[str, ...] = ("target_type", "geo")
    cells: dict[tuple[str, ...], tuple[float, float]] = field(default_factory=dict)
    global_mean: float = 0.0

    def _key(self, factors: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(factors.get(k, "-") for k in self.keys)

    def fit(self, rows: Sequence) -> "CpmModel":
        agg: dict[tuple[str, ...], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # views, spent, bid*views
        for r in rows:
            if r.views <= 0 or r.spent <= 0 or r.cpm_bid <= 0:
                continue
            a = agg[self._key(r.factors)]
            a[0] += r.views
            a[1] += r.spent
            a[2] += r.cpm_bid * r.views
        tot_v = sum(a[0] for a in agg.values())
        if tot_v:
            g_ratio = (1000 * sum(a[1] for a in agg.values()) / tot_v) / (sum(a[2] for a in agg.values()) / tot_v)
            w = tot_v / (tot_v + self.shrink_views)
            self.global_mean = w * float(np.log(g_ratio)) + (1 - w) * self.prior_log_ratio
        else:
            self.global_mean = self.prior_log_ratio
        self.cells = {}
        for key, (views, spent, bidv) in agg.items():
            ratio = (1000 * spent / views) / (bidv / views)
            w = views / (views + self.shrink_views)
            mean = w * float(np.log(ratio)) + (1 - w) * self.global_mean
            sd = self.prior_sd * np.sqrt(self.shrink_views / (views + self.shrink_views)) + 0.05
            self.cells[key] = (mean, float(sd))
        return self

    def sample(self, factors: Mapping[str, str], bid: float, n: int, rng: np.random.Generator) -> np.ndarray:
        mean, sd = self.cells.get(self._key(factors), (self.global_mean, self.prior_sd))
        return bid * np.exp(rng.normal(mean, sd, size=n))
