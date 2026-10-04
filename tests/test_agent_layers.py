from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_agent_never_imports_bot():
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "agent").rglob("*.py")
        if any(name == "bot" or name.startswith("bot.") for name in _imports(p))
    ]
    assert offenders == []


def test_ads_api_never_imports_agent_or_bot():
    for p in (ROOT / "ads_api").rglob("*.py"):
        assert not any(n.split(".")[0] in {"agent", "bot", "aiogram"} for n in _imports(p)), p


def test_model_planner_sim_are_pure():
    """Model/planner/simulator never touch the API client, executor or bot."""
    banned = ("ads_api.client", "agent.executor", "bot", "aiogram", "httpx")
    for pkg in ("model", "planner", "sim"):
        for p in (ROOT / "agent" / pkg).rglob("*.py"):
            if p.name == "balance.py":  # phase 1: reads the account (read-only) for the free balance
                continue
            names = _imports(p)
            assert not any(n == b or n.startswith(b + ".") for n in names for b in banned), p


def test_candidates_never_call_the_api_or_executor_directly():
    banned = ("ads_api.client", "agent.executor", "bot", "aiogram")
    for p in (ROOT / "agent" / "candidates").rglob("*.py"):
        names = _imports(p)
        assert not any(n == b or n.startswith(b + ".") for n in names for b in banned), p
