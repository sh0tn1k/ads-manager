"""Factor extraction (section 9 catalogue) and the factor table built from synced ads."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from agent.canonical import enum_value

CORE_FACTORS = ("target_type", "placement", "geo", "topic", "language", "device", "angle", "cpm_bucket")


def _codes(target: dict[str, Any], input_key: str, output_key: str, code_key: str) -> list[str]:
    """Accept both InputAdTarget (codes) and AdTarget (objects) shapes."""
    values = target.get(input_key)
    if values is None and target.get(output_key) is not None:
        values = [item.get(code_key) if isinstance(item, dict) else item for item in target[output_key]]
    return sorted(str(v) for v in values or [] if v is not None)


def target_levels(target: dict[str, Any] | None) -> dict[str, str]:
    if not target:
        return {}
    ttype = enum_value(target.get("type")) or "unknown"
    levels = {"target_type": ttype}
    countries = _codes(target, "country_codes", "countries", "country_code")
    languages = _codes(target, "language_codes", "languages", "language_code")
    topics = _codes(target, "topic_ids", "topics", "topic_id")
    if ttype == "users":
        levels["geo"] = "+".join(countries) or "any"
    if ttype in ("users", "channels"):
        levels["language"] = "+".join(languages) or "any"
        levels["topic"] = "+".join(topics) or "any"
    if ttype == "users" and target.get("device"):
        levels["device"] = str(enum_value(target.get("device")))
    if ttype == "search":
        queries = sorted(str(q).lower() for q in target.get("search_queries") or [])
        levels["queries"] = "|".join(queries)[:120] or "none"
    if ttype == "channels":
        chans = _codes(target, "channel_ids", "channels", "username")
        if chans:
            levels["channels"] = "+".join(chans)[:120]
    return levels


def cpm_bucket_level(cpm: float, width: float = 0.05) -> str:
    low = math.floor(round(cpm / width, 6)) * width
    return f"{low:.2f}"


def ad_factors(
    *,
    target: dict[str, Any] | None,
    placement: str | None,
    cpm: float,
    text: str | None = None,
    overrides: dict[str, str] | None = None,
    cpm_bucket_width: float = 0.05,
) -> dict[str, str]:
    factors = target_levels(target)
    if placement:
        factors["placement"] = str(enum_value(placement))
    factors["cpm_bucket"] = cpm_bucket_level(cpm, cpm_bucket_width)
    if text:
        factors["angle"] = _angle_from_text(text)
    if overrides:
        factors.update({k: str(v) for k, v in overrides.items()})
    return factors


def _angle_from_text(text: str) -> str:
    import hashlib

    return "text:" + hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]


@dataclass
class FactorRow:
    """One aggregated binomial row per ad (section 7.1)."""

    factors: dict[str, str]
    views: int
    clicks: int
    joins: int
    spent: float
    cpm_bid: float
    ad_id: int | None = None
    tracks_joins: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def realized_cpm(self) -> float | None:
        return 1000.0 * self.spent / self.views if self.views else None


def factor_rows_from_store(store: Any, *, cpm_bucket_width: float = 0.05) -> list[FactorRow]:
    """Build rows from synced ads (cumulative counters in raw_json) and stored proposal factors."""
    rows: list[FactorRow] = []
    proposal_factors: dict[str, dict[str, str]] = {}
    for prow in store.db.fetchall("SELECT proposal_id, payload_json FROM proposals WHERE created_ad_id IS NOT NULL"):
        try:
            proposal_factors[prow["proposal_id"]] = json.loads(prow["payload_json"]).get("factors") or {}
        except ValueError:
            continue
    for row in store.ads.rows():
        raw = json.loads(row["raw_json"])
        target = json.loads(row["target_json"]) if row["target_json"] else None
        overrides = proposal_factors.get(row["proposal_id"] or "", {})
        factors = ad_factors(
            target=target,
            placement=row["placement"],
            cpm=float(row["cpm"]),
            text=row["text"],
            overrides=overrides,
            cpm_bucket_width=cpm_bucket_width,
        )
        rows.append(
            FactorRow(
                factors=factors,
                views=int(raw.get("views") or 0),
                clicks=int(raw.get("clicks") or 0),
                joins=int(raw.get("actions") or 0),
                spent=float(raw.get("spent_budget") or 0.0),
                cpm_bid=float(row["cpm"]),
                ad_id=int(row["ad_id"]),
                tracks_joins=(row["action_type"] == "join"),
                meta={"status": row["status"], "text": row["text"], "target": target, "placement": row["placement"],
                      "promote_url": row["promote_url"], "created_date": row["created_date"]},
            )
        )
    return rows
