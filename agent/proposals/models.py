"""Proposal payload (section 6) and the proposal state machine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.canonical import canonical_json, sha256_hex


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CREATED = "created"
    FAILED = "failed"
    ACTIVE = "active"
    DECLINED = "declined"
    FINISHED = "finished"


# proposed → accepted | rejected | expired; accepted → created | failed; created → active | declined → finished
TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PROPOSED: frozenset({ProposalStatus.ACCEPTED, ProposalStatus.REJECTED, ProposalStatus.EXPIRED}),
    ProposalStatus.ACCEPTED: frozenset({ProposalStatus.CREATED, ProposalStatus.FAILED}),
    ProposalStatus.CREATED: frozenset({ProposalStatus.ACTIVE, ProposalStatus.DECLINED}),
    ProposalStatus.ACTIVE: frozenset({ProposalStatus.FINISHED}),
    ProposalStatus.DECLINED: frozenset({ProposalStatus.FINISHED}),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.EXPIRED: frozenset(),
    ProposalStatus.FAILED: frozenset(),
    ProposalStatus.FINISHED: frozenset(),
}

# Reservation is released on rejected/expired/failed and becomes the ad budget on created.
RESERVING = frozenset({ProposalStatus.PROPOSED, ProposalStatus.ACCEPTED})


def can_transition(src: str | ProposalStatus, dst: str | ProposalStatus) -> bool:
    return ProposalStatus(dst) in TRANSITIONS[ProposalStatus(src)]


class ProposalKind(StrEnum):
    CREATE_AD = "create_ad"
    PAUSE_AD = "pause_ad"
    TOPUP_AD = "topup_ad"
    CLONE_AD = "clone_ad"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdSpec(_Strict):
    """Arguments of AdsClient.create_ad the agent may set. No account_id, no idempotency_key."""

    title: str
    text: str | None = None
    promote_url: str
    cpm: float
    placement: str
    target: dict[str, Any]
    button: str | None = None
    photo_id: str | None = None
    initial_budget: float
    daily_budget_limit: float | None = None
    schedule: dict[str, Any] | None = None
    impression_frequency: int | None = None


class AdAction(_Strict):
    ad_id: int
    amount: float | None = None


class Interval(_Strict):
    p10: float
    p50: float
    p90: float


class Predicted(_Strict):
    ctr: Interval | None = None
    cvr: Interval | None = None
    cpm_realized: Interval | None = None
    cpa: Interval | None = None
    p_beats_hurdle: float | None = None


class Cost(_Strict):
    initial_budget: float = Field(ge=0)
    max_loss: float = Field(ge=0)
    reserved: float = Field(ge=0)


class StopRules(_Strict):
    min_views: int | None = None
    kill_if_p_below: float | None = None
    promote_if_p_above: float | None = None


class ProposalPayload(_Strict):
    kind: ProposalKind
    hypothesis_id: str | None = None
    stage: Literal[1, 2, 3, "scale"] | None = None
    factors: dict[str, str] = Field(default_factory=dict)
    ad: AdSpec | None = None
    action: AdAction | None = None
    predicted: Predicted | None = None
    cost: Cost
    stop_rules: StopRules | None = None
    rationale_ru: str = ""
    sources: list[str] = Field(default_factory=list)
    model_version: str = ""
    data_cutoff_ts: int | None = None
    is_test: bool = False

    @model_validator(mode="after")
    def _shape(self) -> "ProposalPayload":
        if self.kind in (ProposalKind.CREATE_AD, ProposalKind.CLONE_AD):
            if self.ad is None:
                raise ValueError(f"{self.kind} needs ad")
        elif self.action is None:
            raise ValueError(f"{self.kind} needs action")
        if self.kind == ProposalKind.TOPUP_AD and (self.action is None or not self.action.amount):
            raise ValueError("topup_ad needs action.amount")
        return self


def hashed_part(payload: ProposalPayload) -> dict[str, Any]:
    """I8: what the hash protects — ad + cost (plus kind/action for non-create kinds)."""
    return {
        "kind": payload.kind.value,
        "ad": payload.ad,
        "cost": payload.cost,
        "action": payload.action,
    }


def payload_hash(payload: ProposalPayload) -> str:
    return sha256_hex(hashed_part(payload))


def payload_json(payload: ProposalPayload) -> str:
    return canonical_json(payload)


def reserve_amount(payload: ProposalPayload) -> float:
    if payload.kind in (ProposalKind.CREATE_AD, ProposalKind.CLONE_AD):
        return round(max(payload.cost.reserved, payload.ad.initial_budget if payload.ad else 0.0), 5)
    if payload.kind == ProposalKind.TOPUP_AD:
        return round(max(payload.cost.reserved, payload.action.amount or 0.0), 5)  # type: ignore[union-attr]
    return 0.0


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    created_ts: int
    expires_ts: int
    status: ProposalStatus
    kind: ProposalKind
    hypothesis_id: str | None
    stage: str | None
    payload_json: str
    payload_hash: str
    reserved_amount: float
    fingerprint: str | None
    decided_by: int | None
    decided_ts: int | None
    reject_reason: str | None
    reject_reason_code: str | None
    created_ad_id: int | None
    error: str | None
    needs_submit: bool
    dry_run: bool
    executed_ts: int | None

    @property
    def payload(self) -> ProposalPayload:
        return ProposalPayload.model_validate_json(self.payload_json)

    @classmethod
    def from_row(cls, row: Any) -> "Proposal":
        return cls(
            proposal_id=row["proposal_id"],
            created_ts=row["created_ts"],
            expires_ts=row["expires_ts"],
            status=ProposalStatus(row["status"]),
            kind=ProposalKind(row["kind"]),
            hypothesis_id=row["hypothesis_id"],
            stage=row["stage"],
            payload_json=row["payload_json"],
            payload_hash=row["payload_hash"],
            reserved_amount=row["reserved_amount"],
            fingerprint=row["fingerprint"],
            decided_by=row["decided_by"],
            decided_ts=row["decided_ts"],
            reject_reason=row["reject_reason"],
            reject_reason_code=row["reject_reason_code"],
            created_ad_id=row["created_ad_id"],
            error=row["error"],
            needs_submit=bool(row["needs_submit"]),
            dry_run=bool(row["dry_run"]),
            executed_ts=row["executed_ts"],
        )
