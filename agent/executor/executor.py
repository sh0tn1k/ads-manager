from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.planner.balance import FreeBalanceProvider
from agent.proposals.models import Proposal, ProposalKind, ProposalPayload, ProposalStatus
from agent.store import Store
from agent.validator import Dictionaries, validate_payload

logger = logging.getLogger("agent.executor")

DictionariesLoader = Callable[[], Awaitable[Dictionaries | None]]


@dataclass(frozen=True)
class ExecutionResult:
    proposal_id: str
    status: str  # created | failed | pending_retry | refused
    code: str  # ok | dry_run | <error code>
    ad_id: int | None = None
    dry_run: bool = False
    needs_submit: bool = False
    request: dict[str, Any] | None = None


def build_create_request(payload: ProposalPayload, proposal_id: str) -> dict[str, Any]:
    """Arguments for AdsClient.create_ad. Deterministic, so idempotent retries match (docs)."""
    ad = payload.ad
    assert ad is not None
    request: dict[str, Any] = {
        "title": ad.title,
        "text": ad.text,
        "promote_url": ad.promote_url,
        "cpm": ad.cpm,
        "placement": ad.placement,
        "target": ad.target,
        "button": ad.button,
        "photo_id": ad.photo_id,
        "initial_budget": ad.initial_budget,
        "daily_budget_limit": ad.daily_budget_limit,
        "schedule": ad.schedule,
        "impression_frequency": ad.impression_frequency,
        "return_target": True,
        "idempotency_key": proposal_id,  # I4
    }
    return {k: v for k, v in request.items() if v is not None}


class Executor:
    def __init__(
        self,
        ads: AdsClient,
        store: Store,
        config: AgentConfig,
        balance: FreeBalanceProvider,
        *,
        dictionaries: DictionariesLoader | None = None,
        clock: Clock = system_clock,
    ) -> None:
        self.ads = ads
        self.store = store
        self.repo = store.proposals
        self.config = config
        self.balance = balance
        self.dictionaries = dictionaries
        self.clock = clock

    # --- helpers ---

    def _fail(self, proposal: Proposal, code: str, details: dict[str, Any] | None = None) -> ExecutionResult:
        now = self.clock()
        self.repo.transition(
            proposal.proposal_id,
            ProposalStatus.ACCEPTED,
            ProposalStatus.FAILED,
            now,
            event="failed",
            details={"code": code, **(details or {})},
            error=code,
            executed_ts=now,
        )
        logger.warning("proposal %s failed: %s", proposal.proposal_id, code)
        return ExecutionResult(proposal.proposal_id, "failed", code)

    def _live_guard(self, amount: float) -> str | None:
        if self.config.dry_run:
            return None
        if self.config.first_live_max_budget is None:
            return "live_mode_needs_first_live_max_budget"
        if amount > self.config.first_live_max_budget + 1e-9:
            return "over_first_live_max_budget"
        return None

    # --- entry point ---

    async def execute(self, proposal_id: str) -> ExecutionResult:
        proposal = self.repo.get(proposal_id)
        if proposal is None:
            return ExecutionResult(proposal_id, "refused", "not_found")
        if proposal.status == ProposalStatus.CREATED:
            return ExecutionResult(proposal_id, "created", "already_created", ad_id=proposal.created_ad_id, dry_run=proposal.dry_run)
        if proposal.status != ProposalStatus.ACCEPTED:
            # I1: nothing reaches the Ads API unless the proposal is accepted.
            return ExecutionResult(proposal_id, "refused", f"status_{proposal.status.value}")
        if proposal.decided_by is None or proposal.decided_by not in self.config.allowed_user_ids:
            return self._fail(proposal, "user_not_allowed")
        if not self.repo.stored_hash_matches(proposal):
            return self._fail(proposal, "payload_hash_mismatch")
        if proposal.decided_ts is None or proposal.decided_ts >= proposal.expires_ts:
            return self._fail(proposal, "expired_before_accept")
        payload = proposal.payload
        dictionaries = await self.dictionaries() if self.dictionaries else None
        issues = validate_payload(payload, self.config, dictionaries=dictionaries)
        if issues:
            return self._fail(proposal, "validation_failed", {"issues": [str(i) for i in issues]})
        if proposal.created_ad_id is not None:
            return ExecutionResult(proposal_id, "created", "already_created", ad_id=proposal.created_ad_id)

        if payload.kind in (ProposalKind.CREATE_AD, ProposalKind.CLONE_AD):
            return await self._create(proposal, payload)
        if payload.kind == ProposalKind.PAUSE_AD:
            return await self._pause(proposal, payload)
        if payload.kind == ProposalKind.TOPUP_AD:
            return await self._topup(proposal, payload)
        return self._fail(proposal, "unsupported_kind")

    async def _balance_ok(self, proposal: Proposal, amount: float) -> tuple[bool, float]:
        view = await self.balance.current(exclude_proposal_id=proposal.proposal_id)
        return view.free + 1e-9 >= amount, view.free

    async def _create(self, proposal: Proposal, payload: ProposalPayload) -> ExecutionResult:
        assert payload.ad is not None
        amount = payload.ad.initial_budget
        guard = self._live_guard(amount)
        if guard:
            return self._fail(proposal, guard)
        try:
            ok, free = await self._balance_ok(proposal, amount)
        except (AdsApiError, AdsTransportError) as exc:
            self.repo.update_fields(proposal.proposal_id, error=f"balance_check:{getattr(exc, 'error', exc)}")
            return ExecutionResult(proposal.proposal_id, "pending_retry", "balance_check_failed")
        if not ok:
            return self._fail(proposal, "insufficient_balance", {"free": free, "needed": amount})
        request = build_create_request(payload, proposal.proposal_id)
        now = self.clock()
        if self.config.dry_run:
            logger.info("DRY_RUN createAd proposal=%s request=%s", proposal.proposal_id, request)
            self.repo.transition(
                proposal.proposal_id,
                ProposalStatus.ACCEPTED,
                ProposalStatus.CREATED,
                now,
                event="dry_run_create_ad",
                details={"request": request},
                dry_run=1,
                executed_ts=now,
            )
            return ExecutionResult(proposal.proposal_id, "created", "dry_run", dry_run=True, request=request)
        try:
            ad = await self.ads.create_ad(**request)
        except AdsTransportError as exc:
            # Unknown outcome: keep accepted; a retry with the same idempotency key is safe for 24h.
            self.repo.update_fields(proposal.proposal_id, error=f"transport:{exc}")
            self.repo.add_event(proposal.proposal_id, now, "transport_error", {"error": str(exc)})
            return ExecutionResult(proposal.proposal_id, "pending_retry", "transport_error")
        except AdsApiError as exc:
            return self._fail(proposal, exc.error, {"method": exc.method})
        self.store.ads.upsert(
            ad,
            now,
            hypothesis_id=payload.hypothesis_id,
            proposal_id=proposal.proposal_id,
            fingerprint=proposal.fingerprint,
        )
        self.repo.transition(
            proposal.proposal_id,
            ProposalStatus.ACCEPTED,
            ProposalStatus.CREATED,
            now,
            event="created",
            details={"ad_id": ad.ad_id, "status": str(getattr(ad.status, "value", ad.status))},
            created_ad_id=ad.ad_id,
            error=None,
            executed_ts=now,
        )
        needs_submit = False
        if str(getattr(ad.status, "value", ad.status)) == "ready_for_review":
            needs_submit = not await self._submit(proposal.proposal_id, ad.ad_id)
        return ExecutionResult(proposal.proposal_id, "created", "ok", ad_id=ad.ad_id, needs_submit=needs_submit)

    async def _submit(self, proposal_id: str, ad_id: int) -> bool:
        now = self.clock()
        try:
            await self.ads.submit_ad_for_review(ad_id=ad_id, return_target=True)
        except (AdsApiError, AdsTransportError) as exc:
            self.repo.update_fields(proposal_id, needs_submit=1, error=f"submit:{getattr(exc, 'error', exc)}")
            self.repo.add_event(proposal_id, now, "submit_failed", {"error": str(getattr(exc, "error", exc))})
            return False
        self.repo.update_fields(proposal_id, needs_submit=0)
        self.repo.add_event(proposal_id, now, "submitted_for_review", {"ad_id": ad_id})
        return True

    async def _pause(self, proposal: Proposal, payload: ProposalPayload) -> ExecutionResult:
        assert payload.action is not None
        now = self.clock()
        request = {"ad_id": payload.action.ad_id, "is_paused": True}
        if self.config.dry_run:
            logger.info("DRY_RUN editAd(is_paused) proposal=%s request=%s", proposal.proposal_id, request)
            self.repo.transition(proposal.proposal_id, ProposalStatus.ACCEPTED, ProposalStatus.CREATED, now,
                                 event="dry_run_pause_ad", details={"request": request}, dry_run=1, executed_ts=now)
            return ExecutionResult(proposal.proposal_id, "created", "dry_run", dry_run=True, request=request)
        try:
            await self.ads.edit_ad(ad_id=payload.action.ad_id, is_paused=True)
        except AdsTransportError:
            return ExecutionResult(proposal.proposal_id, "pending_retry", "transport_error")
        except AdsApiError as exc:
            return self._fail(proposal, exc.error)
        self.repo.transition(proposal.proposal_id, ProposalStatus.ACCEPTED, ProposalStatus.CREATED, now,
                             event="paused", details=request, executed_ts=now)
        return ExecutionResult(proposal.proposal_id, "created", "ok", ad_id=payload.action.ad_id)

    async def _topup(self, proposal: Proposal, payload: ProposalPayload) -> ExecutionResult:
        assert payload.action is not None and payload.action.amount
        amount = payload.action.amount
        guard = self._live_guard(amount)
        if guard:
            return self._fail(proposal, guard)
        ok, free = await self._balance_ok(proposal, amount)
        if not ok:
            return self._fail(proposal, "insufficient_balance", {"free": free, "needed": amount})
        now = self.clock()
        request = {"ad_id": payload.action.ad_id, "amount": amount, "idempotency_key": proposal.proposal_id}
        if self.config.dry_run:
            logger.info("DRY_RUN increaseAdBudget proposal=%s request=%s", proposal.proposal_id, request)
            self.repo.transition(proposal.proposal_id, ProposalStatus.ACCEPTED, ProposalStatus.CREATED, now,
                                 event="dry_run_topup_ad", details={"request": request}, dry_run=1, executed_ts=now)
            return ExecutionResult(proposal.proposal_id, "created", "dry_run", dry_run=True, request=request)
        try:
            await self.ads.increase_ad_budget(**request)
        except AdsTransportError:
            return ExecutionResult(proposal.proposal_id, "pending_retry", "transport_error")
        except AdsApiError as exc:
            return self._fail(proposal, exc.error)
        self.repo.transition(proposal.proposal_id, ProposalStatus.ACCEPTED, ProposalStatus.CREATED, now,
                             event="topped_up", details=request, executed_ts=now)
        return ExecutionResult(proposal.proposal_id, "created", "ok", ad_id=payload.action.ad_id)

    # --- background retries ---

    async def retry_needs_submit(self) -> int:
        if self.config.dry_run:
            return 0
        done = 0
        for proposal in self.repo.needing_submit():
            if proposal.created_ad_id is not None and await self._submit(proposal.proposal_id, proposal.created_ad_id):
                done += 1
        return done

    async def retry_pending(self, max_age_seconds: int = 23 * 3600) -> int:
        """Re-run accepted proposals whose outcome is unknown, while the idempotency key is valid (24h)."""
        now = self.clock()
        retried = 0
        for proposal in self.repo.by_status(ProposalStatus.ACCEPTED):
            if proposal.decided_ts is None:
                continue
            if now - proposal.decided_ts > max_age_seconds:
                self._fail(proposal, "retry_window_passed")
                continue
            await self.execute(proposal.proposal_id)
            retried += 1
        return retried
