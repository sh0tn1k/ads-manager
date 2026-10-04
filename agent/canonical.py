"""Deterministic JSON + hashing helpers shared by the store, proposals and dedup."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def to_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(to_plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw).rsplit(".", 1)[-1]
