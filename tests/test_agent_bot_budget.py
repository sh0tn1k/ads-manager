from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import respx
from aiogram.types import Chat, Message, User

from ads_api.client import AdsClient
from agent.services import AgentServices
from agent.store import Store
from bot.handlers.budget import cmd_budget
from bot.ui import _screens, current_bot, current_state
from tests.agent_helpers import BASE, account, make_config, ok


def _message(text: str) -> Message:
    return Message.model_construct(
        message_id=1,
        date=0,
        chat=Chat.model_construct(id=100, type="private"),
        from_user=User.model_construct(id=200, is_bot=False, first_name="A"),
        text=text,
    )


def _texts(payload) -> str:
    out = []
    for block in payload.blocks:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            out.append(text)
        for row in getattr(block, "cells", None) or []:
            out.extend(str(c.text) for c in row)
        for btn in getattr(block, "buttons", None) or []:
            out.append(btn.text)
    return "\n".join(out)


@pytest.mark.asyncio
async def test_budget_command_sends_new_panel_with_free_balance():
    _screens.clear()
    bot = AsyncMock()
    bot.send_rich_message = AsyncMock(return_value=type("M", (), {"message_id": 7})())
    t_bot = current_bot.set(bot)
    t_state = current_state.set(None)
    try:
        async with respx.mock(base_url=BASE) as mock:
            mock.post("/getCurrentAccount").mock(return_value=ok(account(remaining=20.0)))
            async with AdsClient("tok") as client:
                services = AgentServices.build(client, make_config(), store=Store.open(":memory:"))
                await cmd_budget(_message("/budget"), services)
        assert bot.send_rich_message.await_count == 1
        text = _texts(bot.send_rich_message.await_args.kwargs["rich_message"])
        assert "Свободно для предложений" in text
        assert "20 TON" in text
        assert "ADS_DRY_RUN=1" in text
        assert "Синхронизировать" in text
    finally:
        current_bot.reset(t_bot)
        current_state.reset(t_state)
        _screens.clear()
