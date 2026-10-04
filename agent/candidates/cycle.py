"""Proposal cycle (sections 7.3, 7.8, 12): generate → dedup → score → select → create proposals.

Returns at most MAX_PENDING_PROPOSALS proposals, or "предложений нет" with a reason.
Missing owner inputs are never guessed: the cycle stops and names them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from agent.candidates.base import Candidate, Creative
from agent.candidates.dedup import blocked_fingerprints, dedup
from agent.candidates.grid import grid_candidates
from agent.candidates.history import base_candidate, history_candidates, rank_history
from agent.candidates.inputs import InputError, load_creatives, load_geo_pool, load_ledger
from agent.candidates.ledger import ledger_candidates
from agent.candidates.llm import explanation_facts, llm_creatives, llm_research
from agent.candidates.scoring import Scored, score_candidates, select
from agent.canonical import sha256_hex
from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.llm.service import REASON_RU as LLM_REASON_RU
from agent.llm.service import LLMService
from agent.model.factors import factor_rows_from_store
from agent.planner.balance import FreeBalanceProvider
from agent.planner.budget import REASON_RU as PLANNER_REASON_RU
from agent.planner.budget import BudgetPlanner
from agent.proposals.models import AdSpec, Cost, Proposal, ProposalKind, ProposalPayload, StopRules
from agent.proposals.service import ProposalService
from agent.store import Store
from agent.store.repositories import LIVE_AD_STATUSES

logger = logging.getLogger("agent.candidates")

MODEL_VERSION = "posterior-v1"

REASON_RU = {
    **PLANNER_REASON_RU,
    "HURDLE_CPA_TON": "не задан порог HURDLE_CPA_TON",
    "forbidden": "нет прав запускать цикл (BOT_ALLOWED_USER_IDS)",
    "promote_url_allowlist_empty": "не задан AGENT_PROMOTE_URL_ALLOWLIST",
    "pending_full": "уже есть максимум ожидающих предложений (MAX_PENDING_PROPOSALS)",
    "input_error": "ошибка во входном файле",
    "no_candidates": "нет кандидатов",
    "all_duplicates": "все кандидаты уже запускались, ожидают решения или недавно отклонены",
    "below_threshold": "ничто не проходит порог P(CPA < порог) ≥ τ_min",
    "not_selected": "ни один кандидат не уместился в бюджет и ограничения диверсификации",
    "create_failed": "предложения не прошли проверку при создании",
}

NOTE_RU = {
    "no_history": "истории объявлений с кликами нет",
    "no_geo_pool": "нет config/geo_pool.yaml — сетка гео × тема не строится",
    "no_ledger": "нет data/external_placements.csv — каналы из внешних размещений не предлагаются",
    "no_creatives": "нет креативов (config/creatives.yaml и тексты прошлых объявлений)",
    "no_base_cpm": "не задан базовый CPM (история, base_cpm в geo_pool.yaml или AGENT_BASE_CPM)",
}


NOTE_PREFIX_RU = {
    "llm_disabled": "LLM выключен",
    "llm_creatives_failed": "LLM-креативы не получены",
    "llm_research_failed": "LLM-ресерч не получен",
    "llm_explain_failed": "LLM-пояснение не получено, использован шаблон",
}


def _note_ru(note: str) -> str:
    key, sep, rest = note.partition(": ")
    if sep and key in NOTE_PREFIX_RU:
        return f"{NOTE_PREFIX_RU[key]} ({rest})"
    return NOTE_RU.get(note, note)


@dataclass
class CycleResult:
    created: list[Proposal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.created)

    def message_ru(self) -> str:
        if self.created:
            text = f"Новых предложений: {len(self.created)}."
        else:
            reasons = "; ".join(REASON_RU.get(r, r) for r in self.reasons) or "причина неизвестна"
            text = f"Предложений нет: {reasons}."
        if self.notes:
            text += " Замечания: " + "; ".join(_note_ru(n) for n in self.notes) + "."
        return text


class ProposalCycle:
    def __init__(
        self,
        store: Store,
        config: AgentConfig,
        balance: FreeBalanceProvider,
        proposals: ProposalService,
        clock: Clock = system_clock,
        llm: LLMService | None = None,
    ) -> None:
        self.llm = llm
        self.store = store
        self.config = config
        self.balance = balance
        self.proposals = proposals
        self.clock = clock
        self.planner = BudgetPlanner(config)

    def active_experiments(self) -> int:
        marks = ",".join("?" for _ in LIVE_AD_STATUSES)
        live_agent_ads = int(
            self.store.db.scalar(
                f"SELECT COUNT(*) FROM ads WHERE proposal_id IS NOT NULL AND status IN ({marks})", LIVE_AD_STATUSES
            )
        )
        reserving = int(self.store.db.scalar("SELECT COUNT(*) FROM proposals WHERE status IN ('proposed','accepted')"))
        return live_agent_ads + reserving

    async def run(self, user_id: int | None) -> CycleResult:
        c = self.config
        if not self.proposals.is_allowed(user_id):
            return CycleResult(reasons=["forbidden"])
        self.proposals.expire_due()
        missing = c.missing_planner_inputs()
        if missing:
            return CycleResult(reasons=missing)
        if not c.promote_url_allowlist:
            return CycleResult(reasons=["promote_url_allowlist_empty"])
        room = c.max_pending_proposals - self.store.proposals.count_pending()
        if room <= 0:
            return CycleResult(reasons=["pending_full"])
        view = await self.balance.current()
        plan = self.planner.plan(view.free, self.active_experiments())
        if not plan.ok:
            return CycleResult(reasons=plan.reasons)
        result = CycleResult()
        try:
            candidates, control, prior_shifts = await self._generate(result)
        except InputError as exc:
            return CycleResult(reasons=["input_error"], errors=[str(exc)])
        result.stats["generated"] = len(candidates)
        if not candidates:
            result.reasons.append("no_candidates")
            return result
        now = self.clock()
        blocked = blocked_fingerprints(self.store, now=now, dedup_days=c.dedup_days, width=c.fingerprint_cpm_bucket)
        fresh, dropped = dedup(candidates, blocked, width=c.fingerprint_cpm_bucket)
        result.stats["duplicates"] = dropped
        if not fresh:
            result.reasons.append("all_duplicates")
            return result
        budget = self._stage1_budget()
        rows = self._rows()
        rng = np.random.default_rng(now)
        pool = fresh + ([control] if control is not None else [])
        scored = score_candidates(
            pool, rows, model_kwargs=self._model_kwargs(), hurdle=float(c.hurdle_cpa_ton), stage_cost=budget,
            volume_views=c.default_volume_views, samples=c.mc_samples, cpm_bucket_width=c.fingerprint_cpm_bucket, rng=rng,
            extra_ctr_shifts=prior_shifts,
        )
        control_scored = scored[-1] if control is not None else None
        tests = scored[:-1] if control is not None else scored
        max_count = min(room, plan.slots)
        references = {}
        if control_scored is not None:
            references[control_scored.factors.get("target_type", "?")] = control_scored.factors
        # Section 7.8: a control ad (best known arm, same period) when there are >= 2 tests and budget allows.
        # Its slot and cost are reserved inside the batch.
        control_ok = (
            c.add_control and control_scored is not None and max_count >= 3
            and 3 * budget <= plan.explore_cap + 1e-9 and self._control_allowed(control, blocked)
        )
        chosen, below = [], 0
        if control_ok:
            chosen, below = select(
                tests, tau_min=c.tau_min, budget_cap=plan.explore_cap - budget, cost=budget,
                max_count=max_count - 1, references=references,
            )
            if len(chosen) >= 2:
                chosen.append(control_scored)
            else:
                control_ok = False
        if not control_ok:
            chosen, below = select(
                tests, tau_min=c.tau_min, budget_cap=plan.explore_cap, cost=budget, max_count=max_count,
                references=references,
            )
        result.stats["below_threshold"] = below
        if not chosen:
            result.reasons.append("below_threshold" if below == len(tests) else "not_selected")
            return result
        for s in chosen:
            rationale = await self._explain(s, result)
            created = await self.proposals.create(self._payload(s, budget, now, rationale))
            if created.proposal is not None:
                result.created.append(created.proposal)
            else:
                result.errors.extend(created.errors)
        if not result.created:
            result.reasons.append("create_failed")
        return result

    # --- helpers ---

    def _rows(self):
        return factor_rows_from_store(self.store, cpm_bucket_width=self.config.fingerprint_cpm_bucket)

    def _model_kwargs(self) -> dict:
        c = self.config
        return dict(
            prior_ctr=c.prior_ctr, prior_ctr_strength=c.prior_ctr_strength,
            prior_cvr=c.prior_cvr, prior_cvr_strength=c.prior_cvr_strength,
            temper=c.likelihood_temper, shift_sigma=c.external_prior_sigma,
        )

    def _stage1_budget(self) -> float:
        budget = self.planner.stage_budget(1)
        if not self.config.dry_run and self.config.first_live_max_budget is not None:
            budget = min(budget, self.config.first_live_max_budget)
        return round(budget, 2)

    def _control_allowed(self, control: Candidate, blocked: set[str]) -> bool:
        """The control may repeat a recently finished ad, but not a live/pending one."""
        if control.base_ad_id is not None:
            row = self.store.ads.row(control.base_ad_id)
            if row and row["status"] in LIVE_AD_STATUSES:
                return False
        pending = self.store.proposals.fingerprints_since(0, ("proposed", "accepted"))
        _, dropped = dedup([control], pending, width=self.config.fingerprint_cpm_bucket)
        return dropped == 0

    def _llm_on(self, result: CycleResult) -> bool:
        if self.llm is None:
            return False
        reasons = self.llm.disabled_reasons()
        if reasons:
            note = "llm_disabled: " + "; ".join(LLM_REASON_RU.get(r, r) for r in reasons)
            if note not in result.notes:
                result.notes.append(note)
            return False
        return True

    async def _explain(self, s: Scored, result: CycleResult) -> str:
        if not self._llm_on(result):
            return s.candidate.rationale_ru
        assert self.llm is not None
        res = await self.llm.explain_proposal(facts=explanation_facts(s.candidate, s.factors))
        if not res.ok:
            result.notes.append("llm_explain_failed: " + res.reason_ru())
            return s.candidate.rationale_ru
        return res.output.rationale_ru

    async def _generate(self, result: CycleResult) -> tuple[list[Candidate], Candidate | None, dict]:
        c = self.config
        url = c.promote_url_allowlist[0]
        rows = self._rows()
        ranked = rank_history(rows)
        geo_pool = load_geo_pool(c.geo_pool_path)
        ledger = load_ledger(c.external_placements_path)
        creatives = load_creatives(c.creatives_path) or []
        if not ranked:
            result.notes.append("no_history")
        if geo_pool is None:
            result.notes.append("no_geo_pool")
        if ledger is None:
            result.notes.append("no_ledger")
        # Past texts are creatives too (best first), after the owner's list.
        seen = {cr.text for cr in creatives}
        for r in ranked:
            text = r.meta.get("text")
            if text and text not in seen and r.factors.get("angle"):
                creatives.append(Creative(angle=r.factors["angle"], text=text))
                seen.add(text)
        base_cpm = round(ranked[0].cpm_bid, 2) if ranked else (
            geo_pool.base_cpm if geo_pool and geo_pool.base_cpm is not None else c.base_cpm
        )
        out: list[Candidate] = []
        prior_shifts: dict = {}
        if self._llm_on(result):
            assert self.llm is not None
            extra, err = await llm_creatives(self.llm, rows, url)
            if err:
                result.notes.append("llm_creatives_failed: " + err)
            creatives += [cr for cr in extra if cr.text not in seen]
            allowed_geo = sorted({code for codes in geo_pool.groups.values() for code in codes}) if geo_pool else None
            research, prior_shifts, err = await llm_research(
                self.llm, rows, channel=c.channel_description or url, allowed_geo=allowed_geo, base_cpm=base_cpm,
                promote_url=url,
            )
            if err:
                result.notes.append("llm_research_failed: " + err)
            out += research
        if not creatives:
            result.notes.append("no_creatives")
        out += history_candidates(
            rows, promote_url=url, creatives=creatives, geo_pool=geo_pool, cpm_step=max(c.cpm_step, c.fingerprint_cpm_bucket), top_n=c.history_top_n,
        )
        if base_cpm is None:
            if geo_pool or ledger:
                result.notes.append("no_base_cpm")
        else:
            if geo_pool:
                out += grid_candidates(geo_pool, creatives=creatives, base_cpm=base_cpm, promote_url=url)
            if ledger:
                out += ledger_candidates(
                    ledger, creative=creatives[0] if creatives else None, base_cpm=base_cpm, promote_url=url,
                    top_n=c.ledger_top_n, min_joins=c.ledger_min_joins,
                )
        control = base_candidate(ranked[0], url) if ranked else None
        return out, control, prior_shifts

    def _payload(self, s: Scored, budget: float, now: int, rationale: str | None = None) -> ProposalPayload:
        cand = s.candidate
        c = self.config
        ad = AdSpec(
            title=cand.title, text=cand.text, promote_url=cand.promote_url, cpm=cand.cpm,
            placement=cand.placement, target=cand.target, initial_budget=budget,
        )
        return ProposalPayload(
            kind=ProposalKind.CREATE_AD,
            stage=1,
            hypothesis_id=f"{cand.source}-{sha256_hex(s.factors)[:12]}",
            factors=dict(s.factors),
            ad=ad,
            predicted=s.prediction.predicted(),
            cost=Cost(initial_budget=budget, max_loss=budget, reserved=budget),
            stop_rules=StopRules(
                min_views=c.stage1_min_views, kill_if_p_below=c.kill_if_p_below, promote_if_p_above=c.promote_if_p_above,
            ),
            rationale_ru=rationale or cand.rationale_ru,
            sources=list(cand.sources),
            model_version=MODEL_VERSION,
            data_cutoff_ts=now,
        )
