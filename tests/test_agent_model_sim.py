"""Phase 3: posterior model, CPM model, planner, stage policy and simulator (explicit fixture values)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.special import expit

from ads_api.models import Ad
from agent.model.cpa import predict_arm
from agent.model.cpm import CpmModel
from agent.model.factors import FactorRow, ad_factors, factor_rows_from_store
from agent.model.posterior import BinomialLogitModel, PosteriorModel
from agent.planner.budget import BudgetPlanner, exploration_justified
from agent.planner.stages import StagePolicy, one_factor_design
from agent.sim.env import SimConfig, SimEnv
from agent.sim.policies import POLICIES
from agent.sim.runner import SimReport, run_experiment
from agent.store import Store
from tests.agent_helpers import api_ad, make_config

# Test fixture values (not production config).
PRIOR = dict(prior_ctr=0.0125, prior_ctr_strength=1000.0, prior_cvr=0.067, prior_cvr_strength=50.0, temper=0.6)
SIM = SimConfig(base_ctr=0.0125, base_cvr=0.067, base_cpm=0.13, hurdle=0.15)
PLANNER_INPUTS = dict(hurdle_cpa_ton=0.2, explore_share=0.2, max_loss_per_hypothesis=5.0, max_active_experiments=3)


def _synthetic_rows(rng, true_ctr, views=20000):
    rows = []
    for geo, p in true_ctr.items():
        for _ in range(3):
            clicks = int(rng.binomial(views, p))
            rows.append(FactorRow({"geo": geo}, views, clicks, int(clicks * 0.07), views * 0.13 / 1000, 0.13))
    return rows


def test_posterior_recovers_level_ordering_and_rates():
    rng = np.random.default_rng(1)
    truth = {"GB": 0.02, "DE": 0.01, "FR": 0.005}
    model = PosteriorModel.from_config(**PRIOR).fit(_synthetic_rows(rng, truth))
    points = {g: model.ctr.point({"geo": g}) for g in truth}
    assert points["GB"] > points["DE"] > points["FR"]
    for g, p in truth.items():
        assert abs(points[g] - p) / p < 0.15


def test_prior_dominates_without_data():
    model = BinomialLogitModel(prior_rate=0.0125, prior_strength=1000.0).fit([], [], [])
    assert model.point({}) == pytest.approx(0.0125, rel=1e-3)
    draws = model.sample({"geo": "XX"}, 4000, np.random.default_rng(0))
    assert 0.008 < np.median(draws) < 0.02


def test_unknown_level_falls_back_to_prior_and_is_wider():
    rng = np.random.default_rng(2)
    model = PosteriorModel.from_config(**PRIOR).fit(_synthetic_rows(rng, {"GB": 0.015}))
    seen = model.ctr.sample_logits({"geo": "GB"}, 4000, rng)
    unseen = model.ctr.sample_logits({"geo": "BR"}, 4000, rng)
    assert unseen.std() > 2 * seen.std()


def test_level_shift_prior_moves_unseen_level():
    model = BinomialLogitModel(0.0125, 1000.0, level_shifts={("geo", "IN"): -1.0}).fit([], [], [])
    assert model.point({"geo": "IN"}) == pytest.approx(float(expit(np.log(0.0125 / 0.9875) - 1.0)), rel=1e-3)


def test_temper_widens_intervals():
    rng = np.random.default_rng(3)
    rows = _synthetic_rows(rng, {"GB": 0.015, "DE": 0.01})
    sds = {}
    for temper in (1.0, 0.5):
        m = BinomialLogitModel(0.0125, 1000.0, temper=temper)
        m.fit([r.factors for r in rows], [r.views for r in rows], [r.clicks for r in rows])
        sds[temper] = m.sample_logits({"geo": "GB"}, 4000, np.random.default_rng(0)).std()
    assert sds[0.5] > sds[1.0] * 1.2


def test_cpm_model_shrinks_toward_bid_and_learns_ratio():
    empty = CpmModel().fit([])
    draws = empty.sample({"geo": "GB"}, 0.2, 2000, np.random.default_rng(0))
    assert np.median(draws) == pytest.approx(0.2, rel=0.05)
    rows = [FactorRow({"target_type": "users", "geo": "GB"}, 100000, 1000, 70, 100000 * 0.1 / 1000, 0.2)]
    learned = CpmModel().fit(rows).sample({"target_type": "users", "geo": "GB"}, 0.2, 2000, np.random.default_rng(0))
    assert np.median(learned) < 0.15


def test_predict_arm_cpa_and_probability():
    model = PosteriorModel.from_config(**PRIOR).fit([])
    cpm = CpmModel().fit([])
    rng = np.random.default_rng(0)
    pred = predict_arm(model, cpm, {"geo": "GB"}, bid=0.13, hurdle=0.15, samples=4000, rng=rng)
    # 0.13 / (1000 * 0.0125 * 0.067) ≈ 0.155
    assert 0.08 < pred.cpa_median < 0.3
    assert 0.0 < pred.p_beats_hurdle < 1.0
    p = pred.predicted()
    assert p.cpa.p10 <= p.cpa.p50 <= p.cpa.p90
    assert predict_arm(model, cpm, {}, bid=0.13, hurdle=None, samples=100, rng=rng).p_beats_hurdle is None


def test_planner_refuses_without_owner_inputs_and_names_them():
    plan = BudgetPlanner(make_config()).plan(free_balance=100.0, active_experiments=0)
    assert not plan.ok
    assert set(plan.reasons) >= {"HURDLE_CPA_TON", "EXPLORE_SHARE", "MAX_LOSS_PER_HYPOTHESIS", "MAX_ACTIVE_EXPERIMENTS"}
    assert "не задан порог HURDLE_CPA_TON" in plan.reason_ru()


def test_planner_caps_and_slots():
    planner = BudgetPlanner(make_config(**PLANNER_INPUTS))
    plan = planner.plan(free_balance=50.0, active_experiments=1)
    assert plan.ok and plan.explore_cap == 10.0 and plan.slots == 2
    assert plan.stage_budgets == {"1": 0.7, "2": 3.0, "3": 5.0}  # stage 3 capped by MAX_LOSS_PER_HYPOTHESIS
    assert planner.plan(free_balance=2.0, active_experiments=0).reasons == ["no_budget"]
    full = planner.plan(free_balance=50.0, active_experiments=3)
    assert "no_slots" in full.reasons and "лимит активных" in full.reason_ru()


def test_exploration_justified_formula():
    assert exploration_justified(cost=10, scale_spend=100, found_cpa=0.1, hurdle=0.2)
    assert not exploration_justified(cost=60, scale_spend=100, found_cpa=0.1, hurdle=0.2)


def test_stage_policy_decisions_and_halving():
    policy = StagePolicy(make_config())
    assert policy.decide(stage=1, views=100, p_beats_hurdle=0.0).action == "continue"
    assert policy.decide(stage=1, views=6000, p_beats_hurdle=0.01).action == "kill"
    assert policy.decide(stage=2, views=0, p_beats_hurdle=0.95).action == "promote"
    assert policy.decide(stage=2, views=0, p_beats_hurdle=0.5).action == "continue"
    assert policy.survivors(list(range(10))) == [0, 1, 2, 3]
    assert policy.survivors([]) == []
    assert policy.next_stage(1) == 2 and policy.next_stage(3) == "scale"
    assert policy.is_cold_start(3) and not policy.is_cold_start(12)


def test_one_factor_design_changes_exactly_one_factor():
    base = {"geo": "GB", "angle": "a"}
    arms = one_factor_design(base, {"geo": ["GB", "DE", "FR"], "angle": ["b"]})
    assert arms[0] == base and len(arms) == 4
    for arm in arms[1:]:
        assert sum(arm[k] != base[k] for k in base) == 1


def test_factor_rows_from_store_uses_cumulative_counters():
    store = Store.open(":memory:")
    store.ads.upsert(Ad.model_validate(api_ad(1)), 1)
    rows = factor_rows_from_store(store)
    assert len(rows) == 1
    r = rows[0]
    assert (r.views, r.clicks, r.joins, r.spent) == (800, 10, 1, 0.5)
    assert r.factors["geo"] == "GB" and r.factors["target_type"] == "users" and r.factors["cpm_bucket"] == "0.10"
    assert r.factors["angle"].startswith("text:") and len(r.factors["angle"]) == 5 + 16
    assert r.realized_cpm == pytest.approx(0.625)


def test_ad_factors_overrides_win():
    f = ad_factors(target=None, placement="channels", cpm=0.3, text="x", overrides={"angle": "fomo"})
    assert f == {"placement": "channels", "cpm_bucket": "0.30", "angle": "fomo"}


def test_sim_env_deterministic_and_regret():
    a, b = SimEnv(SIM, seed=5), SimEnv(SIM, seed=5)
    assert np.array_equal(a.true_cpa, b.true_cpa)
    assert a.regret(a.best) == 0.0 and all(a.regret(c) >= 0 for c in range(a.n_cells))
    o1 = a.run(0, 1.0, np.random.default_rng(1))
    o2 = b.run(0, 1.0, np.random.default_rng(1))
    assert o1 == o2 and o1.views == int(1000 / a.cpm[0])


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_every_policy_runs_within_budget(name):
    env = SimEnv(SIM, seed=0)
    model_kwargs = dict(PRIOR)
    res = POLICIES[name](env, 10.0, np.random.default_rng(0), hurdle=SIM.hurdle, model_kwargs=model_kwargs, samples=200)
    assert 0 <= res.chosen < env.n_cells
    assert sum(o.budget for o in res.observations) == pytest.approx(10.0)


def test_run_experiment_is_deterministic_and_reports_metrics():
    kwargs = dict(budgets=[5.0, 20.0], reps=2, model_kwargs=dict(PRIOR), samples=200, seed=7,
                  policies=("grid", "halving", "proposed"))
    r1 = run_experiment(SIM, **kwargs)
    r2 = run_experiment(SIM, **kwargs)
    assert r1.mean_regret == r2.mean_regret
    assert set(r1.p_within20) == {"grid", "halving", "proposed"}
    assert 0.0 <= r1.coverage["cpa"] <= 1.0
    assert len(r1.savings_vs_grid()) == 2


def test_budget_to_reach_interpolates_and_handles_never():
    rep = SimReport(budgets=[1.0, 4.0], reps=1, mean_regret={"grid": [0.5, 0.1], "p": [0.3, 0.2]})
    assert rep.budget_to_reach("p", 0.4) == 1.0
    assert rep.budget_to_reach("p", 0.25) == pytest.approx(2.0)
    assert rep.budget_to_reach("p", 0.1) == float("inf")
    assert rep.savings_vs_grid("p")[1][2] == 0.0
