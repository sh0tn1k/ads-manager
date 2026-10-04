"""Scoring and greedy selection (section 7.3): score = P(CPA < c*) · V / C_stage."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from agent.candidates.base import Candidate, changed_factors
from agent.model.cpa import ArmPrediction, predict_arm
from agent.model.cpm import CpmModel
from agent.model.factors import FactorRow
from agent.model.posterior import PosteriorModel


@dataclass
class Scored:
    candidate: Candidate
    factors: dict[str, str]
    prediction: ArmPrediction
    score: float

    @property
    def p(self) -> float:
        return float(self.prediction.p_beats_hurdle or 0.0)


def score_candidates(
    candidates: Sequence[Candidate],
    rows: Sequence[FactorRow],
    *,
    model_kwargs: dict,
    hurdle: float,
    stage_cost: float,
    volume_views: float,
    samples: int,
    cpm_bucket_width: float,
    rng: np.random.Generator,
    extra_ctr_shifts: dict | None = None,
) -> list[Scored]:
    ctr_shifts: dict = dict(extra_ctr_shifts or {})
    cvr_shifts: dict = {}
    for c in candidates:
        ctr_shifts.update(c.ctr_shifts)
        cvr_shifts.update(c.cvr_shifts)
    model = PosteriorModel.from_config(**model_kwargs, ctr_shifts=ctr_shifts, cvr_shifts=cvr_shifts).fit(rows)
    cpm_model = CpmModel().fit(rows)
    out = []
    for c in candidates:
        factors = c.factors(cpm_bucket_width)
        pred = predict_arm(model, cpm_model, factors, bid=c.cpm, hurdle=hurdle, samples=samples, rng=rng)
        p = float(pred.p_beats_hurdle or 0.0)
        out.append(Scored(c, factors, pred, p * volume_views / stage_cost))
    return out


def select(
    scored: Sequence[Scored],
    *,
    tau_min: float,
    budget_cap: float,
    cost: float,
    max_count: int,
    references: dict[str, dict[str, str]],
) -> tuple[list[Scored], int]:
    """Filter by tau_min, rank by score, pick greedily within budget/count.

    Diversification: at most one changed factor vs the reference (control) arm of the same target type.
    A target type without a reference adopts its best-scoring candidate as the reference.
    Returns (selected, number below tau_min).
    """
    passing = [s for s in scored if s.p >= tau_min]
    below = len(scored) - len(passing)
    refs = dict(references)
    chosen: list[Scored] = []
    spent = 0.0
    for s in sorted(passing, key=lambda s: (-s.score, s.candidate.title)):
        if len(chosen) >= max_count or spent + cost > budget_cap + 1e-9:
            break
        ttype = s.factors.get("target_type", "?")
        ref = refs.setdefault(ttype, s.factors)
        if len(changed_factors(s.factors, ref)) > 1:
            continue
        chosen.append(s)
        spent += cost
    return chosen, below
