"""Shared fixtures/builders for agent tests (explicit test values, not production config)."""

from __future__ import annotations

import httpx

from agent.config import AgentConfig

BASE = "https://promoteapi.telegram.org"
PROMOTE_URL = "https://t.me/premierleaguedaily"


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def err(code: str, status: int = 400):
    return httpx.Response(status, json={"ok": False, "error": code})


def make_config(**overrides) -> AgentConfig:
    values = {"db_path": ":memory:", "allowed_user_ids_raw": "200", "promote_url_allowlist_raw": PROMOTE_URL}
    values.update(overrides)
    return AgentConfig(_env_file=None, **values)


def api_ad(ad_id: int, *, status: str = "active", remaining: float = 1.0, **extra) -> dict:
    data = {
        "ad_id": ad_id,
        "title": f"Ad {ad_id}",
        "currency": "TON",
        "text": "Premier League news every day",
        "promote_url": PROMOTE_URL,
        "cpm": 0.13,
        "placement": "video_banner",
        "target": {
            "type": "users",
            "countries": [{"country_code": "GB", "name": "United Kingdom"}],
            "languages": [{"language_code": "en", "name": "English"}],
            "topics": [{"topic_id": 7, "name": "Sports"}],
        },
        "spent_budget": 0.5,
        "remaining_budget": remaining,
        "views": 800,
        "clicks": 10,
        "actions": 1,
        "action_type": "join",
        "status": status,
        "is_paused": False,
        "created_date": 1_790_000_000,
    }
    data.update(extra)
    return data


def account(remaining: float = 50.0, ads_budget: float = 5.0) -> dict:
    return {
        "account_id": "acc_1",
        "title": "Main",
        "currency": "TON",
        "spent_budget": 3.0,
        "remaining_budget": remaining,
        "ads_budget": ads_budget,
    }
