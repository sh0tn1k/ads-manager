"""ProposalService: the bot-facing API for proposals (create, accept, reject, expire)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from agent.clock import Clock, system_clock
from agent.config import AgentConfig
from agent.executor import ExecutionResult, Executor
from agent.planner.balance import FreeBalanceProvider
from agent.proposals.fingerprint import fingerprint
from agent.proposals.models import Proposal, ProposalPayload, ProposalStatus
from agent.proposals.repository import ReservationExceeded
from agent.proposals.samples import sample_payloads
from agent.store import Store
from agent.validator import validate_payload

logger = logging.getLogger("agent.proposals")

REJECT_REASONS = ("creative", "geo", "price", "other")


@dataclass
class CreateResult:
    proposal: Proposal | None
    errors: list[str] = field(default_factory=list)


@dataclass
class DecisionResult:
    code: str  # accepted/ok codes from executor, or forbidden / not_found / expired / already_<status> / ...
    proposal: Proposal | None = None
    execution: ExecutionResult | None = None


class ProposalService:
    def __init__(
        self,
        store: Store,
        config: AgentConfig,
        balance: FreeBalanceProvider,
        executor: Executor,
        clock: Clock = system_clock,
    ) -> None:
        self.store = store
        self.repo = store.proposals
        self.config = config
        self.balance = balance
        self.executor = executor
        self.clock = clock

    # --- queries ---

    def get(self, proposal_id: str) -> Proposal | None:
        return self.repo.get(proposal_id)

    def pending(self) -> list[Proposal]:
        return self.repo.by_status(ProposalStatus.PROPOSED)

    def is_allowed(self, user_id: int | None) -> bool:
        # I1: an empty allow-list means nobody may decide proposals.
        return user_id is not None and user_id in self.config.allowed_user_ids

    # --- create ---

    async def create(self, payload: ProposalPayload, *, proposal_id: str | None = None) -> CreateResult:
        issues = validate_payload(payload, self.config)
        if issues:
            return CreateResult(None, [str(i) for i in issues])
        if self.repo.count_pending() >= self.config.max_pending_proposals:
            return CreateResult(None, ["max_pending_proposals"])
        view = await self.balance.current()
        free_before_reserves = view.free + view.reserved
        pid = proposal_id or str(uuid.uuid4())
        fp = fingerprint(payload.ad, payload.factors, cpm_bucket_width=self.config.fingerprint_cpm_bucket) if payload.ad else None
        try:
            proposal = self.repo.insert_reserving(
                proposal_id=pid,
                payload=payload,
                now=self.clock(),
                ttl_seconds=self.config.proposal_ttl_seconds,
                fingerprint=fp,
                free_before_reserves=free_before_reserves,
            )
        except ReservationExceeded as exc:
            return CreateResult(None, [f"insufficient_free_balance: need {exc.needed}, free {exc.available}"])
        return CreateResult(proposal)

    async def inject_test_proposals(self, user_id: int | None) -> list[CreateResult]:
        """Create the predefined test proposals (phase 2: there is no optimizer yet)."""
        if not self.is_allowed(user_id):
            return [CreateResult(None, ["forbidden"])]
        return [await self.create(payload) for payload in sample_payloads(self.config)]

    # --- decisions ---

    async def accept(self, proposal_id: str, user_id: int | None) -> DecisionResult:
        if not self.is_allowed(user_id):
            return DecisionResult("forbidden", self.repo.get(proposal_id))
        now = self.clock()
        assert user_id is not None
        if not self.repo.try_accept(proposal_id, user_id, now):
            proposal = self.repo.get(proposal_id)
            if proposal is None:
                return DecisionResult("not_found")
            if proposal.status == ProposalStatus.PROPOSED and proposal.expires_ts <= now:
                self.repo.expire_due(now)
                return DecisionResult("expired", self.repo.get(proposal_id))
            return DecisionResult(f"already_{proposal.status.value}", proposal)
        proposal = self.repo.get(proposal_id)
        assert proposal is not None
        if not self.repo.stored_hash_matches(proposal):
            self.repo.transition(
                proposal_id, ProposalStatus.ACCEPTED, ProposalStatus.FAILED, now,
                event="failed", details={"code": "payload_hash_mismatch"}, error="payload_hash_mismatch",
            )
            return DecisionResult("payload_hash_mismatch", self.repo.get(proposal_id))
        result = await self.executor.execute(proposal_id)
        return DecisionResult(result.code, self.repo.get(proposal_id), result)

    def reject(self, proposal_id: str, user_id: int | None, reason: str | None = None) -> DecisionResult:
        if not self.is_allowed(user_id):
            return DecisionResult("forbidden", self.repo.get(proposal_id))
        now = self.clock()
        proposal = self.repo.get(proposal_id)
        if proposal is None:
            return DecisionResult("not_found")
        ok = self.repo.transition(
            proposal_id, ProposalStatus.PROPOSED, ProposalStatus.REJECTED, now,
            event="rejected", details={"user_id": user_id}, decided_by=user_id, decided_ts=now, reject_reason=reason,
        )
        if not ok:
            return DecisionResult(f"already_{proposal.status.value}", self.repo.get(proposal_id))
        return DecisionResult("rejected", self.repo.get(proposal_id))

    def set_reject_reason(self, proposal_id: str, user_id: int | None, code: str) -> DecisionResult:
        if not self.is_allowed(user_id):
            return DecisionResult("forbidden")
        if code not in REJECT_REASONS:
            return DecisionResult("bad_reason")
        proposal = self.repo.get(proposal_id)
        if proposal is None or proposal.status != ProposalStatus.REJECTED:
            return DecisionResult("not_rejected", proposal)
        self.repo.update_fields(proposal_id, reject_reason_code=code)
        self.repo.add_event(proposal_id, self.clock(), "reject_reason", {"code": code})
        return DecisionResult("reason_saved", self.repo.get(proposal_id))

    # --- background ---

    def expire_due(self) -> list[str]:
        return self.repo.expire_due(self.clock())

    async def tick(self) -> None:
        expired = self.expire_due()
        if expired:
            logger.info("expired proposals: %s", expired)
        await self.executor.retry_pending()
        await self.executor.retry_needs_submit()

    async def run_background(self, stop: asyncio.Event) -> None:
        """Expiry task (section 11) + idempotent retries (needs_submit, unknown outcomes)."""
        while not stop.is_set():
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("proposal background tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.expiry_check_seconds)
            except asyncio.TimeoutError:
                pass
