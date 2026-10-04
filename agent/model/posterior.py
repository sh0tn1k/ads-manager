"""Bayesian logistic regression on aggregated binomial rows with a Laplace posterior (section 7.1, v1).

logit p_a = mu + sum_f u_{f, level(a, f)},  u ~ N(shift_{f,l}, sigma_f^2),  mu ~ N(logit p0, sigma_mu^2)

* The prior centre/strength is encoded as one pseudo-row (``prior_strength`` trials at rate ``p0``)
  on the intercept, i.e. "CTR 1.25% with the weight of ~1000 views".
* Overdispersion between ads: the binomial likelihood is tempered by ``temper`` (0.5–0.7). This is the
  documented choice over beta-binomial: one scalar, closed-form gradients, conservative intervals.
* Laplace: Newton iterations to the MAP, covariance = inverse Hessian. Thompson samples come from the
  Gaussian posterior; levels never seen in training are drawn from their prior.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.special import expit, logit

DEFAULT_SIGMA = 0.5


@dataclass
class BinomialLogitModel:
    prior_rate: float
    prior_strength: float
    temper: float = 0.6
    sigma_mu: float = 1.5
    factor_sigmas: Mapping[str, float] = field(default_factory=dict)
    level_shifts: Mapping[tuple[str, str], float] = field(default_factory=dict)
    shift_sigma: float = 0.3
    max_iter: int = 50

    # fitted state
    columns: list[tuple[str, str]] = field(default_factory=list, init=False)
    mean: np.ndarray | None = field(default=None, init=False)
    cov: np.ndarray | None = field(default=None, init=False)

    def sigma_for(self, factor: str, level: str) -> float:
        if (factor, level) in self.level_shifts:
            return self.shift_sigma
        return float(self.factor_sigmas.get(factor, DEFAULT_SIGMA))

    def prior_mean_for(self, factor: str, level: str) -> float:
        return float(self.level_shifts.get((factor, level), 0.0))

    def _design(self, factor_sets: Sequence[Mapping[str, str]]) -> np.ndarray:
        index = {col: i + 1 for i, col in enumerate(self.columns)}
        x = np.zeros((len(factor_sets), len(self.columns) + 1))
        x[:, 0] = 1.0
        for r, factors in enumerate(factor_sets):
            for f, level in factors.items():
                j = index.get((f, level))
                if j is not None:
                    x[r, j] = 1.0
        return x

    def fit(self, factor_sets: Sequence[Mapping[str, str]], trials: Iterable[float], successes: Iterable[float]) -> "BinomialLogitModel":
        n = np.asarray(list(trials), dtype=float)
        k = np.asarray(list(successes), dtype=float)
        cols = sorted({(f, lvl) for fs in factor_sets for f, lvl in fs.items()})
        self.columns = cols
        x = self._design(factor_sets)
        # Prior pseudo-row on the intercept (not tempered).
        x_all = np.vstack([x, np.eye(1, x.shape[1])])
        n_all = np.concatenate([n * self.temper, [self.prior_strength]])
        k_all = np.concatenate([k * self.temper, [self.prior_strength * self.prior_rate]])
        mu0 = float(logit(self.prior_rate))
        prior_mean = np.array([mu0] + [self.prior_mean_for(f, lvl) for f, lvl in cols])
        prior_prec = np.array([1.0 / self.sigma_mu**2] + [1.0 / self.sigma_for(f, lvl) ** 2 for f, lvl in cols])
        beta = prior_mean.copy()
        for _ in range(self.max_iter):
            p = expit(x_all @ beta)
            grad = x_all.T @ (k_all - n_all * p) - prior_prec * (beta - prior_mean)
            w = n_all * p * (1 - p)
            hess = (x_all.T * w) @ x_all + np.diag(prior_prec)
            step = np.linalg.solve(hess, grad)
            beta = beta + step
            if np.max(np.abs(step)) < 1e-8:
                break
        p = expit(x_all @ beta)
        w = n_all * p * (1 - p)
        hess = (x_all.T * w) @ x_all + np.diag(prior_prec)
        self.mean = beta
        self.cov = np.linalg.inv(hess)
        return self

    def sample_logits(self, factors: Mapping[str, str], n: int, rng: np.random.Generator) -> np.ndarray:
        if self.mean is None or self.cov is None:
            self.fit([], [], [])
        assert self.mean is not None and self.cov is not None
        index = {col: i + 1 for i, col in enumerate(self.columns)}
        idx = [0] + [index[(f, lvl)] for f, lvl in factors.items() if (f, lvl) in index]
        sub_mean = self.mean[idx]
        sub_cov = self.cov[np.ix_(idx, idx)]
        draws = rng.multivariate_normal(sub_mean, sub_cov, size=n, method="cholesky").sum(axis=1)
        for f, lvl in factors.items():
            if (f, lvl) not in index:
                draws = draws + rng.normal(self.prior_mean_for(f, lvl), self.sigma_for(f, lvl), size=n)
        return draws

    def sample(self, factors: Mapping[str, str], n: int, rng: np.random.Generator) -> np.ndarray:
        return expit(self.sample_logits(factors, n, rng))

    def point(self, factors: Mapping[str, str]) -> float:
        if self.mean is None:
            self.fit([], [], [])
        assert self.mean is not None
        x = self._design([factors])[0]
        extra = sum(self.prior_mean_for(f, lvl) for f, lvl in factors.items() if (f, lvl) not in set(self.columns))
        return float(expit(x @ self.mean + extra))


@dataclass
class PosteriorModel:
    """Two-stage model: CTR (clicks/views) and CVR (joins/clicks)."""

    ctr: BinomialLogitModel
    cvr: BinomialLogitModel

    @classmethod
    def from_config(
        cls,
        *,
        prior_ctr: float,
        prior_ctr_strength: float,
        prior_cvr: float,
        prior_cvr_strength: float,
        temper: float,
        factor_sigmas: Mapping[str, float] | None = None,
        ctr_shifts: Mapping[tuple[str, str], float] | None = None,
        cvr_shifts: Mapping[tuple[str, str], float] | None = None,
        shift_sigma: float = 0.3,
    ) -> "PosteriorModel":
        sig = dict(factor_sigmas or {})
        return cls(
            ctr=BinomialLogitModel(prior_ctr, prior_ctr_strength, temper, factor_sigmas=sig,
                                   level_shifts=dict(ctr_shifts or {}), shift_sigma=shift_sigma),
            cvr=BinomialLogitModel(prior_cvr, prior_cvr_strength, temper, factor_sigmas=sig,
                                   level_shifts=dict(cvr_shifts or {}), shift_sigma=shift_sigma),
        )

    def fit(self, rows: Sequence) -> "PosteriorModel":
        """rows: objects with ``factors, views, clicks, joins, tracks_joins``."""
        rows = [r for r in rows if r.views > 0]
        self.ctr.fit([r.factors for r in rows], [r.views for r in rows], [min(r.clicks, r.views) for r in rows])
        cvr_rows = [r for r in rows if getattr(r, "tracks_joins", True) and r.clicks > 0]
        self.cvr.fit([r.factors for r in cvr_rows], [r.clicks for r in cvr_rows], [min(r.joins, r.clicks) for r in cvr_rows])
        return self

    def sample(self, arm: Mapping[str, str], n: int, rng: np.random.Generator | None = None) -> tuple[np.ndarray, np.ndarray]:
        rng = rng or np.random.default_rng()
        return self.ctr.sample(arm, n, rng), self.cvr.sample(arm, n, rng)
