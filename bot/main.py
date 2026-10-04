from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import BotCommand, ErrorEvent

from ads_api.client import AdsClient
from ads_api.config import AdsApiConfig
from ads_api.errors import AdsApiError, AdsTransportError
from agent.config import AgentConfig
from agent.services import AgentServices
from bot.config import BotConfig
from bot.handlers import routers
from bot.handlers.menu import show_error
from bot.ab import AbStore
from bot.library import LibraryStore
from bot.middlewares import (
    AbStoreMiddleware,
    AgentMiddleware,
    AdsClientMiddleware,
    AllowlistMiddleware,
    LibraryStoreMiddleware,
    RuleStoreMiddleware,
    TypingMiddleware,
    UiContextMiddleware,
)
from bot.rich import heading, quote
from bot.rules import RuleStore
from bot.storage import SqliteStorage
from bot.ui import current_bot, reply_rich
from bot.watcher import RuleWatcher

logger = logging.getLogger("bot")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def _set_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Меню"),
            BotCommand(command="help", description="Справка по Ads API"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="ads", description="Список объявлений"),
            BotCommand(command="saves", description="Сохранённые группы"),
            BotCommand(command="ab", description="A/B тесты объявлений"),
            BotCommand(command="rules", description="Правила автостопа"),
            BotCommand(command="create_ad", description="Создать объявление"),
            BotCommand(command="account", description="Текущий аккаунт"),
            BotCommand(command="budget", description="Баланс и резервы агента"),
            BotCommand(command="proposals", description="Предложения агента"),
            BotCommand(command="propose", description="Подобрать новые предложения"),
            BotCommand(command="propose_test", description="Тестовые предложения"),
            BotCommand(command="cancel", description="Отменить мастер"),
        ]
    )


async def _check_ads_account(ads: AdsClient) -> None:
    try:
        current = await ads.get_current_account()
    except Exception:  # noqa: BLE001
        logger.exception("Ads API startup check failed")
        return
    logger.info("Ads current account %s (%s)", current.account_id, current.title)
    default = ads.default_account_id
    if not default or default == current.account_id:
        return
    try:
        matched = await ads.get_accounts_by_id([default])
        if not matched:
            raise AdsApiError("ACCOUNT_ID_INVALID", method="getAccountsById")
    except AdsApiError as exc:
        logger.warning(
            "TELEGRAM_ADS_ACCOUNT_ID rejected (%s); using token account %s",
            exc.error,
            current.account_id,
        )
        ads.default_account_id = None


def _error_chat(event: ErrorEvent):
    update = event.update
    if update.callback_query is not None:
        return update.callback_query
    if update.message is not None:
        return update.message
    return None


async def run() -> None:
    bot_cfg = BotConfig()
    ads_cfg = AdsApiConfig()
    setup_logging(bot_cfg.log_level)

    session = None
    if bot_cfg.bot_api_base_url.rstrip("/") != "https://api.telegram.org":
        session = AiohttpSession(api=TelegramAPIServer.from_base(bot_cfg.bot_api_base_url))

    bot = Bot(
        token=bot_cfg.bot_token,
        session=session,
        default=DefaultBotProperties(),
    )
    storage = SqliteStorage(bot_cfg.fsm_path)
    rule_store = RuleStore(Path(bot_cfg.fsm_path).with_name("rules.sqlite"))
    library_store = LibraryStore(Path(bot_cfg.fsm_path).with_name("library.sqlite"))
    ab_store = AbStore(Path(bot_cfg.fsm_path).with_name("ab.sqlite"))
    dp = Dispatcher(storage=storage)

    ads = AdsClient.from_config(ads_cfg)
    agent = AgentServices.build(ads, AgentConfig())
    dp.update.outer_middleware(AllowlistMiddleware(bot_cfg.allowed_ids))
    dp.update.outer_middleware(AdsClientMiddleware(ads))
    dp.update.outer_middleware(RuleStoreMiddleware(rule_store))
    dp.update.outer_middleware(LibraryStoreMiddleware(library_store))
    dp.update.outer_middleware(AbStoreMiddleware(ab_store))
    dp.update.outer_middleware(AgentMiddleware(agent))
    dp.message.middleware(UiContextMiddleware())
    dp.callback_query.middleware(UiContextMiddleware())
    dp.message.middleware(TypingMiddleware())
    dp.callback_query.middleware(TypingMiddleware())

    for router in routers:
        dp.include_router(router)

    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        logger.exception("handler failed: %s", event.exception)
        target = _error_chat(event)
        if target is None:
            return True
        token = current_bot.set(bot)
        try:
            if isinstance(event.exception, (AdsApiError, AdsTransportError)):
                await show_error(target, event.exception)
            else:
                await reply_rich(
                    target,
                    heading("Ошибка"),
                    quote("Не получилось показать экран. Нажмите /start."),
                    section="menu",
                )
        except Exception:  # noqa: BLE001
            logger.exception("error screen failed")
            chat = getattr(getattr(target, "message", None), "chat", None) or getattr(target, "chat", None)
            if chat is not None:
                try:
                    await bot.send_message(chat.id, "Не получилось показать экран. Нажмите /start.")
                except Exception:  # noqa: BLE001
                    logger.exception("plain error send failed")
        finally:
            current_bot.reset(token)
        return True

    await _set_commands(bot)
    await _check_ads_account(ads)
    stop = asyncio.Event()
    watcher = RuleWatcher(ads, bot, rule_store)
    watch_task = asyncio.create_task(watcher.run(stop), name="autostop-watcher")
    agent_task = asyncio.create_task(agent.proposals.run_background(stop), name="agent-proposals")
    logger.info("Bot starting (Ads API %s)", ads.base_url)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        stop.set()
        watch_task.cancel()
        agent_task.cancel()
        try:
            await watch_task
        except asyncio.CancelledError:
            pass
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        await ads.aclose()
        agent.store.close()
        await bot.session.close()


def main() -> None:
    Path("data").mkdir(exist_ok=True)
    asyncio.run(run())


if __name__ == "__main__":
    main()
