"""Dedup (section 9): skip live, recently finished, pending and recently rejected combinations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from agent.candidates.base import Candidate, input_target
from agent.proposals.fingerprint import fingerprint
from agent.proposals.models import AdSpec
from agent.store import Store
from agent.store.repositories import LIVE_AD_STATUSES

PENDING = ("proposed", "accepted")
RECENT = ("created", "active", "declined", "finished", "rejected")


def candidate_spec(c: Candidate, budget: float = 0.01) -> AdSpec:
    return AdSpec(
        title=c.title, text=c.text, promote_url=c.promote_url, cpm=c.cpm, placement=c.placement,
        target=c.target, initial_budget=budget,
    )


def fingerprint_keys(spec: AdSpec, angle: str | None, width: float) -> set[str]:
    """Both the semantic-angle and the text-hash fingerprint, so a labelled creative still matches its raw text."""
    keys = {fingerprint(spec, None, cpm_bucket_width=width)}
    if angle:
        keys.add(fingerprint(spec, {"angle": angle}, cpm_bucket_width=width))
    return keys


def blocked_fingerprints(store: Store, *, now: int, dedup_days: int, width: float) -> set[str]:
    since = now - dedup_days * 86400
    blocked = set(store.proposals.fingerprints_since(0, PENDING))
    blocked |= store.proposals.fingerprints_since(since, RECENT)
    marks = ",".join("?" for _ in LIVE_AD_STATUSES)
    last_live = {
        r["ad_id"]: r["ts"]
        for r in store.db.fetchall(
            f"SELECT ad_id, MAX(ts) AS ts FROM ad_snapshots WHERE status IN ({marks}) GROUP BY ad_id", LIVE_AD_STATUSES
        )
    }
    for row in store.ads.rows():
        live = row["status"] in LIVE_AD_STATUSES
        # "Recently finished": last seen live inside the window (never seen live: created inside the window).
        recent = max(last_live.get(row["ad_id"], 0), row["created_date"] or 0) >= since
        if not (live or recent):
            continue
        if row["fingerprint"]:
            blocked.add(row["fingerprint"])
        target = input_target(_json(row["target_json"]))
        if not target or not row["placement"]:
            continue
        spec = AdSpec(
            title=row["title"] or "-", text=row["text"], promote_url=row["promote_url"] or "",
            cpm=float(row["cpm"]), placement=row["placement"], target=target, initial_budget=0.01,
        )
        blocked |= fingerprint_keys(spec, None, width)
    return blocked


def _json(value: str | None):
    import json

    return json.loads(value) if value else None


def dedup(
    candidates: Sequence[Candidate],
    blocked: Iterable[str],
    *,
    width: float,
    exempt_sources: tuple[str, ...] = (),
) -> tuple[list[Candidate], int]:
    """Return unique, not-blocked candidates and the number dropped."""
    blocked = set(blocked)
    seen: set[str] = set()
    out = []
    for c in candidates:
        keys = fingerprint_keys(candidate_spec(c), c.angle, width)
        if keys & seen:
            continue
        if c.source not in exempt_sources and keys & blocked:
            continue
        seen |= keys
        out.append(c)
    return out, len(candidates) - len(out)
