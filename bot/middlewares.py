from __future__ import annotations

import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.utils.chat_action import ChatActionSender

from ads_api.client import AdsClient
from agent.services import AgentServices
from bot.ab import AbStore
from bot.library import LibraryStore
from bot.rules import RuleStore
from bot.ui import current_bot, current_state

logger = logging.getLogger("bot")


class AdsClientMiddleware(BaseMiddleware):
    def __init__(self, ads: AdsClient) -> None:
        self.ads = ads

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        data["ads"] = self.ads
        return await handler(event, data)


class AllowlistMiddleware(BaseMiddleware):
    def __init__(self, allowed_ids: set[int]) -> None:
        self.allowed_ids = allowed_ids

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        if not self.allowed_ids:
            return await handler(event, data)
        user = data.get("event_from_user")
        user_id = getattr(user, "id", None)
        if user_id not in self.allowed_ids:
            if isinstance(event, Message):
                await event.answer("Этот бот закрыт для вашего аккаунта.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет доступа", show_alert=True)
            return None
        return await handler(event, data)


class UiContextMiddleware(BaseMiddleware):
    """Expose bot + FSM to reply_rich so it can edit the current screen."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        bot_token = current_bot.set(data.get("bot"))
        state_token = current_state.set(data.get("state"))
        try:
            return await handler(event, data)
        finally:
            current_bot.reset(bot_token)
            current_state.reset(state_token)


class RuleStoreMiddleware(BaseMiddleware):
    def __init__(self, store: RuleStore) -> None:
        self.store = store

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        data["rules"] = self.store
        return await handler(event, data)


class LibraryStoreMiddleware(BaseMiddleware):
    def __init__(self, store: LibraryStore) -> None:
        self.store = store

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        data["library"] = self.store
        return await handler(event, data)


class AbStoreMiddleware(BaseMiddleware):
    def __init__(self, store: AbStore) -> None:
        self.store = store

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        data["abtests"] = self.store
        return await handler(event, data)


class AgentMiddleware(BaseMiddleware):
    """Expose the proposals agent (store, sync, balance, proposals) to handlers as ``agent``."""

    def __init__(self, services: AgentServices) -> None:
        self.services = services

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        data["agent"] = self.services
        return await handler(event, data)


class TypingMiddleware(BaseMiddleware):
    """Show «печатает…» while handlers talk to the Ads API / build tables."""

    async def __call__(self, handler, event: TelegramObject, data: dict[str, Any]):
        bot = data.get("bot")
        chat = getattr(event, "chat", None)
        if chat is None and isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
        if bot is None or chat is None:
            return await handler(event, data)
        action = "typing"
        if isinstance(event, Message) and event.document:
            action = "upload_document"
        elif isinstance(event, Message) and (event.photo or event.video):
            action = "upload_photo" if event.photo else "upload_video"
        async with ChatActionSender(bot=bot, chat_id=chat.id, action=action):
            return await handler(event, data)
