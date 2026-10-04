"""Predictive CPA = m / (1000 * ctr * cvr) by Monte Carlo (sections 7, 7.3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from agent.model.cpm import CpmModel
from agent.model.posterior import PosteriorModel
from agent.proposals.models import Interval, Predicted


def _interval(values: np.ndarray) -> Interval:
    p10, p50, p90 = np.percentile(values, [10, 50, 90])
    return Interval(p10=float(p10), p50=float(p50), p90=float(p90))


@dataclass
class ArmPrediction:
    ctr: np.ndarray
    cvr: np.ndarray
    cpm: np.ndarray
    cpa: np.ndarray
    p_beats_hurdle: float | None

    def predicted(self) -> Predicted:
        return Predicted(
            ctr=_interval(self.ctr),
            cvr=_interval(self.cvr),
            cpm_realized=_interval(self.cpm),
            cpa=_interval(self.cpa),
            p_beats_hurdle=self.p_beats_hurdle,
        )

    @property
    def cpa_median(self) -> float:
        return float(np.median(self.cpa))


def predict_arm(
    model: PosteriorModel,
    cpm_model: CpmModel,
    arm: Mapping[str, str],
    *,
    bid: float,
    hurdle: float | None,
    samples: int,
    rng: np.random.Generator,
) -> ArmPrediction:
    ctr, cvr = model.sample(arm, samples, rng)
    cpm = cpm_model.sample(arm, bid, samples, rng)
    cpa = cpm / (1000.0 * ctr * cvr)
    p = float(np.mean(cpa < hurdle)) if hurdle is not None else None
    return ArmPrediction(ctr=ctr, cvr=cvr, cpm=cpm, cpa=cpa, p_beats_hurdle=p)
