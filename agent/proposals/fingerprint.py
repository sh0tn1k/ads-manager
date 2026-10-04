"""Dedup fingerprint (section 9): sha256(canonical target + creative angle + CPM bucket + placement)."""

from __future__ import annotations

import hashlib
import math

from agent.canonical import canonical_json, sha256_hex
from agent.proposals.models import AdSpec


def cpm_bucket(cpm: float, width: float) -> int:
    return int(math.floor(round(cpm / width, 6)))


def creative_angle(ad: AdSpec, factors: dict[str, str] | None = None) -> str:
    if factors and factors.get("angle"):
        return str(factors["angle"])
    text = (ad.text or "").strip().lower()
    return "text:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fingerprint(ad: AdSpec, factors: dict[str, str] | None = None, *, cpm_bucket_width: float = 0.05) -> str:
    return sha256_hex(
        {
            "target": canonical_json(ad.target),
            "angle": creative_angle(ad, factors),
            "cpm_bucket": cpm_bucket(ad.cpm, cpm_bucket_width),
            "placement": ad.placement,
        }
    )
