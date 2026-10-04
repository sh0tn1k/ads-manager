"""Predefined test proposals for phase 2 (there is no optimizer yet).

They use the first allow-listed promote_url and the smallest sensible budget. Marked ``is_test``.
"""

from __future__ import annotations

from agent.config import AgentConfig
from agent.proposals.models import AdSpec, Cost, ProposalKind, ProposalPayload, StopRules


def sample_payloads(config: AgentConfig) -> list[ProposalPayload]:
    url = config.promote_url_allowlist[0] if config.promote_url_allowlist else ""
    budget = 0.1
    if config.first_live_max_budget is not None:
        budget = min(budget, config.first_live_max_budget)
    cost = Cost(initial_budget=budget, max_loss=budget, reserved=budget)
    rules = StopRules(min_views=config.stage1_min_views)
    return [
        ProposalPayload(
            kind=ProposalKind.CREATE_AD,
            stage=1,
            factors={"target_type": "users", "angle": "daily_news"},
            ad=AdSpec(
                title="TEST users GB en sports",
                text="Premier League news, transfers and match stats every day. Join the channel!",
                promote_url=url,
                cpm=0.13,
                placement="video_banner",
                target={"type": "users", "country_codes": ["GB"], "language_codes": ["en"]},
                initial_budget=budget,
            ),
            cost=cost,
            stop_rules=rules,
            rationale_ru="Тестовое предложение для проверки карточек и исполнителя. Прогноза нет.",
            is_test=True,
        ),
        ProposalPayload(
            kind=ProposalKind.CREATE_AD,
            stage=1,
            factors={"target_type": "search", "angle": "club_query"},
            ad=AdSpec(
                title="TEST search premier league",
                promote_url=url,
                cpm=0.13,
                placement="search_result",
                target={"type": "search", "search_queries": ["premier league", "epl news"]},
                initial_budget=budget,
            ),
            cost=cost,
            stop_rules=rules,
            rationale_ru="Тестовое предложение: поисковый таргет. Прогноза нет.",
            is_test=True,
        ),
    ]
