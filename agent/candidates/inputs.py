"""Owner-supplied inputs: geo_pool.yaml, external_placements.csv, creatives.yaml. All optional."""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from agent.candidates.base import Creative

logger = logging.getLogger("agent.candidates")


class InputError(ValueError):
    """A present-but-invalid owner file (absence is not an error)."""


@dataclass
class GeoPool:
    groups: dict[str, list[str]] = field(default_factory=dict)
    language_codes: list[str] = field(default_factory=list)
    topic_ids: list[int] = field(default_factory=list)
    base_cpm: float | None = None


def load_geo_pool(path: str | Path) -> GeoPool | None:
    p = Path(path)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise InputError(f"{p}: ожидается словарь")
    groups_raw = data.get("groups") or {}
    if not isinstance(groups_raw, dict):
        raise InputError(f"{p}: groups должен быть словарём «группа: [коды стран]»")
    groups: dict[str, list[str]] = {}
    for name, codes in groups_raw.items():
        if not isinstance(codes, list) or not codes or not all(isinstance(c, str) and len(c) == 2 for c in codes):
            raise InputError(f"{p}: группа {name}: нужен список двухбуквенных кодов стран")
        groups[str(name)] = sorted({c.upper() for c in codes})
    base_cpm = data.get("base_cpm")
    return GeoPool(
        groups=groups,
        language_codes=[str(x) for x in data.get("language_codes") or []],
        topic_ids=[int(x) for x in data.get("topic_ids") or []],
        base_cpm=float(base_cpm) if base_cpm is not None else None,
    )


@dataclass(frozen=True)
class ChannelStats:
    username: str
    price_usd: float
    joins: int
    views: int

    @property
    def cost_per_join_usd(self) -> float:
        return self.price_usd / self.joins if self.joins else float("inf")


LEDGER_COLUMNS = ("channel_username", "date", "price_usd", "joins", "views")


def load_ledger(path: str | Path) -> list[ChannelStats] | None:
    p = Path(path)
    if not p.exists():
        return None
    agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0, 0])
    with p.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = set(LEDGER_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise InputError(f"{p}: нет колонок {', '.join(sorted(missing))}")
        for i, row in enumerate(reader, start=2):
            name = (row["channel_username"] or "").strip().lstrip("@")
            if not name:
                continue
            try:
                a = agg[name.lower()]
                a[0] += float(row["price_usd"] or 0)
                a[1] += int(float(row["joins"] or 0))
                a[2] += int(float(row["views"] or 0))
            except ValueError as exc:
                raise InputError(f"{p}: строка {i}: {exc}") from exc
    return [ChannelStats(name, a[0], int(a[1]), int(a[2])) for name, a in sorted(agg.items())]


def load_creatives(path: str | Path) -> list[Creative] | None:
    p = Path(path)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items = data.get("creatives") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InputError(f"{p}: ожидается список creatives")
    out = []
    for item in items:
        if not isinstance(item, dict) or not item.get("angle") or not item.get("text"):
            raise InputError(f"{p}: у креатива нужны angle и text")
        out.append(Creative(angle=str(item["angle"]), text=str(item["text"]), title=str(item.get("title") or "")))
    return out
