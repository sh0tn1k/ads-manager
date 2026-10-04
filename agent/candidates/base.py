"""Candidate model and helpers shared by the generators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.canonical import enum_value
from agent.model.factors import ad_factors

# Factors compared for the "at most one changed factor vs the control" rule (section 7.3).
DESIGN_FACTORS = ("placement", "geo", "topic", "language", "device", "angle", "cpm_bucket", "queries", "channels")


@dataclass(frozen=True)
class Creative:
    angle: str
    text: str
    title: str = ""


@dataclass
class Candidate:
    source: str  # history | grid | ledger | control
    title: str
    text: str | None
    placement: str
    target: dict[str, Any]
    cpm: float
    angle: str
    promote_url: str
    rationale_ru: str
    changed: tuple[str, ...] = ()
    base_ad_id: int | None = None
    ctr_shifts: dict[tuple[str, str], float] = field(default_factory=dict)
    cvr_shifts: dict[tuple[str, str], float] = field(default_factory=dict)

    def factors(self, cpm_bucket_width: float) -> dict[str, str]:
        return ad_factors(
            target=self.target,
            placement=self.placement,
            cpm=self.cpm,
            text=self.text,
            overrides={"angle": self.angle},
            cpm_bucket_width=cpm_bucket_width,
        )


def changed_factors(a: dict[str, str], b: dict[str, str]) -> list[str]:
    return [f for f in DESIGN_FACTORS if a.get(f) != b.get(f)]


def input_target(target: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert a synced AdTarget (objects) to the InputAdTarget shape createAd accepts."""
    if not target:
        return None
    ttype = enum_value(target.get("type"))
    out: dict[str, Any] = {"type": ttype}

    def codes(input_key: str, output_key: str, code_key: str) -> list[Any]:
        if target.get(input_key) is not None:
            return list(target[input_key])
        return [i.get(code_key) if isinstance(i, dict) else i for i in target.get(output_key) or []]

    countries = codes("country_codes", "countries", "country_code")
    languages = codes("language_codes", "languages", "language_code")
    topics = codes("topic_ids", "topics", "topic_id")
    channels = [c if str(c).startswith("@") else f"@{c}" for c in codes("channel_ids", "channels", "username") if c]
    if ttype == "users":
        if countries:
            out["country_codes"] = sorted(countries)
        if target.get("device"):
            out["device"] = enum_value(target["device"])
        if target.get("exclude_political_channels"):
            out["exclude_political_channels"] = True
    if ttype in ("users", "channels"):
        if languages:
            out["language_codes"] = sorted(languages)
        if topics:
            out["topic_ids"] = sorted(int(t) for t in topics)
        if channels:
            out["channel_ids"] = channels
    if ttype == "search":
        out["search_queries"] = list(target.get("search_queries") or [])
    return out


def short_title(*parts: str, limit: int = 120) -> str:
    title = " ".join(p for p in parts if p).strip()
    while len(title.encode("utf-8")) > limit:
        title = title[:-1]
    return title or "agent"
