from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.proposals.models import (
    AdSpec,
    Cost,
    ProposalKind,
    ProposalPayload,
    ProposalStatus,
    TRANSITIONS,
    can_transition,
    payload_hash,
)
from agent.validator import validate_payload
from tests.agent_helpers import PROMOTE_URL, make_config


def payload(**ad_over) -> ProposalPayload:
    ad = dict(
        title="T",
        text="Premier League daily",
        promote_url=PROMOTE_URL,
        cpm=0.13,
        placement="video_banner",
        target={"type": "users", "country_codes": ["GB"]},
        initial_budget=0.5,
    )
    ad.update(ad_over)
    budget = ad["initial_budget"]
    return ProposalPayload(kind=ProposalKind.CREATE_AD, ad=AdSpec(**ad), cost=Cost(initial_budget=budget, max_loss=budget, reserved=budget))


def codes(p, **cfg) -> set[str]:
    return {i.code for i in validate_payload(p, make_config(**cfg))}


def test_valid_payload_passes():
    assert codes(payload()) == set()


@pytest.mark.parametrize(
    "over, code",
    [
        ({"text": "x" * 161}, "text_too_long"),
        ({"text": None}, "text_required"),
        ({"button": "subscribe"}, "button_not_applicable_for_telegram_url"),
        ({"placement": "channel_post"}, "placement_target_mismatch"),
        ({"target": {"type": "bots", "bot_ids": ["@x"]}, "placement": "bot_banner"}, "target_type_unsupported"),
        ({"target": {"type": "users", "channel_ids": [12345]}}, "channel_must_be_username"),
        ({"target": {"type": "users", "country_codes": list("ABCDEFGHI")}}, "too_many_countries"),
        ({"cpm": 0.135}, "cpm_invalid"),
        ({"impression_frequency": 5}, "impression_frequency_invalid"),
        ({"title": "й" * 65}, "title_length"),
        ({"target": {"type": "search", "search_queries": [f"q{i}" for i in range(11)]}, "placement": "search_result"}, "search_queries_count"),
        ({"target": {"type": "users", "account_id": "x"}}, "target_unknown_fields"),
        ({"schedule": {"week_hours_mask": [1, 2]}}, "schedule_invalid"),
    ],
)
def test_validator_rejects(over, code):
    assert code in codes(payload(**over))


def test_search_without_text_is_fine():
    p = payload(text=None, placement="search_result", target={"type": "search", "search_queries": ["epl"]})
    assert codes(p) == set()


def test_max_loss_per_hypothesis_cap():
    assert "over_max_loss_per_hypothesis" in codes(payload(initial_budget=2.0), max_loss_per_hypothesis=1.0)


def test_payload_schema_forbids_extra_fields():
    raw = payload().model_dump(mode="json")
    raw["ad"]["account_id"] = "foreign"
    assert {i.code for i in validate_payload(raw, make_config())} == {"schema_invalid"}


@settings(max_examples=200, deadline=None)
@given(
    st.one_of(
        st.text(min_size=0, max_size=40).map(lambda s: "https://t.me/" + s),
        st.from_regex(r"https?://[a-z]{1,10}\.[a-z]{2,4}/[a-z]{0,20}", fullmatch=True),
    )
)
def test_promote_url_allow_list_property(url):
    allowed = url.rstrip("/").lower().replace("http://", "https://") == PROMOTE_URL
    result = "promote_url_not_allowed" in codes(payload(promote_url=url))
    assert result is not allowed or url.lower().startswith("http://")


@pytest.mark.parametrize(
    "url, ok",
    [
        (PROMOTE_URL, True),
        (PROMOTE_URL + "/", True),
        ("https://T.ME/premierleaguedaily", True),
        ("https://t.me/premierleaguedaily.evil", False),
        ("https://t.me/premierleaguedaily/123/../x", False),
        ("https://evil.com/?u=https://t.me/premierleaguedaily", False),
        ("https://t.me/other", False),
        ("", False),
    ],
)
def test_promote_url_cases(url, ok):
    assert ("promote_url_not_allowed" not in codes(payload(promote_url=url))) is ok


def test_state_machine_matches_plan():
    S = ProposalStatus
    assert TRANSITIONS[S.PROPOSED] == {S.ACCEPTED, S.REJECTED, S.EXPIRED}
    assert TRANSITIONS[S.ACCEPTED] == {S.CREATED, S.FAILED}
    assert TRANSITIONS[S.CREATED] == {S.ACTIVE, S.DECLINED}
    assert can_transition(S.ACTIVE, S.FINISHED) and can_transition(S.DECLINED, S.FINISHED)
    for terminal in (S.REJECTED, S.EXPIRED, S.FAILED, S.FINISHED):
        assert TRANSITIONS[terminal] == frozenset()
    assert not can_transition(S.EXPIRED, S.ACCEPTED)


def test_payload_hash_covers_ad_and_cost_only_changes():
    p = payload()
    same = p.model_copy(update={"rationale_ru": "другое пояснение"})
    assert payload_hash(p) == payload_hash(same)
    assert payload_hash(p) != payload_hash(payload(initial_budget=0.6))
    assert payload_hash(p) != payload_hash(payload(promote_url="https://t.me/other"))
