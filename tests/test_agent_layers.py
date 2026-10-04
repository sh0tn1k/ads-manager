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
