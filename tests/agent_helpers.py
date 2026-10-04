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


class FakeAds:
    """In-memory stand-in for AdsClient used by executor/property tests. Records every call."""

    MUTATING = {"createAd", "submitAdForReview", "editAd", "increaseAdBudget", "decreaseAdBudget", "deleteAd"}

    def __init__(self, remaining: float = 50.0, create_status: str = "in_review") -> None:
        from ads_api.models import Account

        self.account = Account.model_validate(account(remaining=remaining))
        self.calls: list[tuple[str, dict]] = []
        self.create_status = create_status
        self.by_key: dict[str, object] = {}
        self.next_id = 1000
        self.fail_submit = 0

    @property
    def mutating_calls(self) -> list[tuple[str, dict]]:
        return [c for c in self.calls if c[0] in self.MUTATING]

    async def get_current_account(self):
        self.calls.append(("getCurrentAccount", {}))
        return self.account

    async def create_ad(self, **kwargs):
        from ads_api.models import Ad

        self.calls.append(("createAd", kwargs))
        key = kwargs.get("idempotency_key")
        if key in self.by_key:  # server-side idempotent replay
            return self.by_key[key]
        self.next_id += 1
        ad = Ad.model_validate(
            api_ad(
                self.next_id,
                status=self.create_status,
                remaining=kwargs.get("initial_budget", 0),
                title=kwargs["title"],
                spent_budget=0,
                views=0,
                clicks=0,
                actions=0,
            )
        )
        self.by_key[key] = ad
        return ad

    async def submit_ad_for_review(self, **kwargs):
        self.calls.append(("submitAdForReview", kwargs))
        if self.fail_submit > 0:
            self.fail_submit -= 1
            from ads_api.errors import AdsApiError

            raise AdsApiError("INTERNAL", method="submitAdForReview")
        return None

    async def edit_ad(self, **kwargs):
        self.calls.append(("editAd", kwargs))

    async def increase_ad_budget(self, **kwargs):
        self.calls.append(("increaseAdBudget", kwargs))


class Clock:
    def __init__(self, now: int = 1_790_100_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def build_services(fake=None, clock=None, **config):
    from agent.services import AgentServices
    from agent.store import Store

    fake = fake or FakeAds()
    clock = clock or Clock()
    services = AgentServices.build(
        fake, make_config(**config), store=Store.open(":memory:"), clock=clock, validate_dictionaries=False
    )
    return services, fake, clock
