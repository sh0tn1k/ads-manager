from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User

from agent.proposals.models import ProposalStatus
from bot.handlers.proposals import CALLBACK_RE, cmd_proposals, cmd_propose_test, on_proposal_callback, proposal_card
from bot.rich import rich
from bot.ui import _screens, current_bot, current_state
from tests.agent_helpers import build_services
from tests.test_agent_bot_budget import _texts


def _cb(data: str, user_id: int = 200) -> CallbackQuery:
    msg = Message.model_construct(
        message_id=50, date=0, chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=1, is_bot=True, first_name="Bot"),
    )
    cb = CallbackQuery.model_construct(
        id="1", data=data, from_user=User.model_construct(id=user_id, is_bot=False, first_name="A"),
        chat_instance="x", message=msg,
    )
    object.__setattr__(cb, "answer", AsyncMock())
    return cb


def _msg(text: str, user_id: int = 200) -> Message:
    return Message.model_construct(
        message_id=1, date=0, chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=user_id, is_bot=False, first_name="A"), text=text,
    )


@pytest.fixture
def ui():
    _screens.clear()
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=type("M", (), {"message_id": 9})())
    t_bot = current_bot.set(bot)
    t_state = current_state.set(None)
    yield bot
    current_bot.reset(t_bot)
    current_state.reset(t_state)
    _screens.clear()


def _last_text(bot) -> str:
    if bot.send_rich_message.await_count and (not bot.await_count):
        return _texts(bot.send_rich_message.await_args.kwargs["rich_message"])
    method = bot.await_args.args[0]
    return _texts(method.rich_message)


@pytest.mark.asyncio
async def test_propose_test_then_accept_via_callback_dry_run(ui):
    services, fake, _ = build_services()
    await cmd_propose_test(_msg("/propose_test"), services)
    text = _texts(ui.send_rich_message.await_args.kwargs["rich_message"])
    assert "Создано тестовых предложений: 2" in text
    pid = services.proposals.pending()[0].proposal_id
    await on_proposal_callback(_cb(f"prop:view:{pid}"), services)
    card = _texts(ui.await_args.args[0].rich_message)
    assert "Принять" in card and "Отклонить" in card
    await on_proposal_callback(_cb(f"prop:accept:{pid}"), services)
    assert "ADS_DRY_RUN=1" in _texts(ui.await_args.args[0].rich_message)
    await on_proposal_callback(_cb(f"prop:accept:{pid}"), services)  # double click
    assert "Уже исполнено" in _texts(ui.await_args.args[0].rich_message) or "уже" in _texts(ui.await_args.args[0].rich_message).lower()
    assert services.store.proposals.get(pid).status == ProposalStatus.CREATED
    assert fake.mutating_calls == []


@pytest.mark.asyncio
async def test_foreign_user_callback_is_refused(ui):
    services, fake, _ = build_services()
    await services.proposals.inject_test_proposals(200)
    pid = services.proposals.pending()[0].proposal_id
    cb = _cb(f"prop:accept:{pid}", user_id=999)
    await on_proposal_callback(cb, services)
    cb.answer.assert_awaited()
    assert "Нет доступа" in str(cb.answer.await_args)
    assert services.store.proposals.get(pid).status == ProposalStatus.PROPOSED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    ["prop:accept:not-a-uuid", "prop:delete:00000000-0000-0000-0000-000000000000", "prop:accept:00000000-0000-0000-0000-000000000000:{\"ad\":1}"],
)
async def test_forged_callbacks(ui, data):
    services, fake, _ = build_services()
    cb = _cb(data)
    await on_proposal_callback(cb, services)
    assert "Неизвестная кнопка" in str(cb.answer.await_args)
    assert fake.mutating_calls == []


@pytest.mark.asyncio
async def test_reject_then_reason_buttons(ui):
    services, _, _ = build_services()
    await services.proposals.inject_test_proposals(200)
    pid = services.proposals.pending()[0].proposal_id
    await on_proposal_callback(_cb(f"prop:reject:{pid}"), services)
    text = _texts(ui.await_args.args[0].rich_message)
    assert "Креатив" in text and "Дорого" in text
    await on_proposal_callback(_cb(f"prop:rc-price:{pid}"), services)
    assert services.store.proposals.get(pid).reject_reason_code == "price"


@pytest.mark.asyncio
async def test_proposals_list_and_card_callback_sizes(ui):
    services, _, _ = build_services()
    await services.proposals.inject_test_proposals(200)
    await cmd_proposals(_msg("/proposals"), services)
    text = _texts(ui.send_rich_message.await_args.kwargs["rich_message"])
    assert "Открыть 1" in text
    for p in services.proposals.pending():
        rich(*proposal_card(p))  # builds without exceeding 64-byte callback data
        assert CALLBACK_RE.match(f"prop:rc-creative:{p.proposal_id}")


@pytest.mark.asyncio
async def test_propose_without_owner_inputs_says_why(ui, tmp_path):
    from bot.handlers.proposals import cmd_propose

    services, fake, _ = build_services(geo_pool_path=str(tmp_path / "none.yaml"))
    await cmd_propose(_msg("/propose"), services)
    text = _texts(ui.send_rich_message.await_args.kwargs["rich_message"])
    assert "Предложений нет" in text and "не задан порог HURDLE_CPA_TON" in text
    assert services.proposals.pending() == [] and fake.mutating_calls == []


@pytest.mark.asyncio
async def test_propose_creates_cards_from_geo_pool(ui, tmp_path):
    from bot.handlers.proposals import cmd_propose

    geo = tmp_path / "geo.yaml"
    geo.write_text("groups:\n  uk: [GB]\n  de: [DE]\nbase_cpm: 0.12\n")
    creatives = tmp_path / "cr.yaml"
    creatives.write_text("creatives:\n  - angle: news\n    text: Daily Premier League news\n")
    services, fake, _ = build_services(
        hurdle_cpa_ton=0.2, explore_share=0.2, max_loss_per_hypothesis=5.0, max_active_experiments=5, mc_samples=300,
        geo_pool_path=str(geo), creatives_path=str(creatives), external_placements_path=str(tmp_path / "x.csv"),
    )
    await cmd_propose(_msg("/propose"), services)
    text = _texts(ui.send_rich_message.await_args.kwargs["rich_message"])
    assert "Новых предложений" in text
    assert 1 <= len(services.proposals.pending()) <= services.config.max_pending_proposals
    assert fake.mutating_calls == []
