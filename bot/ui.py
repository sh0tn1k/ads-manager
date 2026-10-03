from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from aiogram import Bot
from aiogram.exceptions import ClientDecodeError, TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from ads_api.client import AdsClient
from bot.keyboards import CB_NO, CB_SKIP, CB_YES, back_blocks, section_blocks, wizard_blocks
from bot.rich import RichBlock, rich

logger = logging.getLogger("bot")

Event = Message | CallbackQuery

current_bot: ContextVar[Bot | None] = ContextVar("ui_bot", default=None)
current_state: ContextVar[FSMContext | None] = ContextVar("ui_state", default=None)

UI_MESSAGE_KEY = "ui_message_id"
UI_CHAT_KEY = "ui_chat_id"
UI_KEYS = (UI_MESSAGE_KEY, UI_CHAT_KEY, "account_id")

_screens: dict[tuple[int, int], int] = {}


async def selected_account_id(state: FSMContext, ads: AdsClient) -> str | None:
    data = await state.get_data()
    return data.get("account_id") or ads.default_account_id


def event_text(event: Event) -> str:
    if isinstance(event, CallbackQuery):
        data = event.data or ""
        if data == CB_SKIP:
            return "Пропустить"
        if data == CB_YES:
            return "да"
        if data == CB_NO:
            return "нет"
        return data
    return (event.text or "").strip()


def screen_peer(event: Event) -> tuple[int, int] | None:
    user = getattr(event, "from_user", None)
    if user is None:
        return None
    if isinstance(event, Message):
        if event.chat is None:
            return None
        return event.chat.id, user.id
    message = event.message
    chat = getattr(message, "chat", None) if message is not None else None
    if chat is None:
        return None
    return chat.id, user.id


def resolve_screen_id(event: Event, stored_id: int | None) -> int | None:
    """Only buttons edit in place. Commands always send a fresh message."""
    if isinstance(event, CallbackQuery) and event.message is not None:
        if isinstance(event.message, InaccessibleMessage):
            return stored_id
        return event.message.message_id
    return None


def _is_not_modified(exc: TelegramAPIError) -> bool:
    return "not modified" in str(exc).lower()


def _decode_ok(exc: ClientDecodeError) -> bool:
    return isinstance(exc.data, dict) and exc.data.get("ok") is True


def _message_id_from_decode(exc: ClientDecodeError) -> int | None:
    data = exc.data
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("message_id"), int):
        return result["message_id"]
    return None


async def _load_stored_id(peer: tuple[int, int]) -> int | None:
    state = current_state.get()
    if state is not None:
        data = await state.get_data()
        stored = data.get(UI_MESSAGE_KEY)
        if isinstance(stored, int):
            return stored
    return _screens.get(peer)


async def _save_screen(peer: tuple[int, int], chat_id: int, message_id: int) -> None:
    _screens[peer] = message_id
    state = current_state.get()
    if state is not None:
        await state.update_data(**{UI_MESSAGE_KEY: message_id, UI_CHAT_KEY: chat_id})


def keep_ui_data(data: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    kept = {key: data[key] for key in UI_KEYS if key in data}
    if extra:
        kept.update(extra)
    return kept


def _plain_text(payload: Any) -> str:
    lines: list[str] = []
    for block in getattr(payload, "blocks", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            lines.append(text.strip())
        elif isinstance(text, list):
            chunk = "".join(part if isinstance(part, str) else str(getattr(part, "text", "")) for part in text)
            if chunk.strip():
                lines.append(chunk.strip())
    return "\n".join(lines) or "Нажмите /start"


async def _edit_screen(bot: Bot, chat_id: int, message_id: int, payload: Any) -> bool:
    try:
        await bot(
            EditMessageText(
                chat_id=chat_id,
                message_id=message_id,
                rich_message=payload,
                parse_mode=None,
                link_preview_options=None,
                disable_web_page_preview=None,
            )
        )
        return True
    except ClientDecodeError as exc:
        if _decode_ok(exc):
            logger.warning("edit screen %s decode error after ok=true: %s", message_id, exc.original)
            return True
        logger.warning("edit screen %s decode error: %s", message_id, exc)
        return False
    except TelegramAPIError as exc:
        if _is_not_modified(exc):
            return True
        logger.warning("edit screen %s failed: %s", message_id, exc)
        return False


async def _send_plain(bot: Bot, chat_id: int, text: str) -> int | None:
    sent = await bot.send_message(chat_id=chat_id, text=text)
    return sent.message_id


async def _send_screen(bot: Bot, chat_id: int, payload: Any) -> int | None:
    try:
        sent = await bot.send_rich_message(chat_id=chat_id, rich_message=payload)
        return sent.message_id
    except ClientDecodeError as exc:
        if _decode_ok(exc):
            message_id = _message_id_from_decode(exc)
            if message_id is not None:
                logger.warning("send screen decode error after ok=true (id=%s): %s", message_id, exc.original)
                return message_id
            logger.warning("send screen decode error after ok=true without id: %s", exc.original)
            return None
        logger.exception("sendRichMessage decode error")
    except TelegramAPIError:
        logger.exception("sendRichMessage failed")
    try:
        return await _send_plain(bot, chat_id, _plain_text(payload))
    except TelegramAPIError:
        logger.exception("plain send_message failed")
        return None


async def reply_rich(
    event: Event,
    *blocks: RichBlock | None,
    section: str | None = None,
    back: str | None = None,
    wizard: bool = False,
    skip: bool = False,
) -> None:
    extras: list[RichBlock] = []
    if wizard:
        extras.extend(wizard_blocks(skip=skip))
    elif section:
        extras.extend(section_blocks(section))
    elif back:
        extras.extend(back_blocks(back))
    payload = rich(*blocks, *extras)

    bot = current_bot.get()
    peer = screen_peer(event)
    message = event if isinstance(event, Message) else event.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if bot is None or peer is None or chat_id is None:
        if message is not None and hasattr(message, "answer_rich") and not isinstance(
            message, InaccessibleMessage
        ):
            await message.answer_rich(payload)
        return

    stored_id = await _load_stored_id(peer)
    target_id = resolve_screen_id(event, stored_id)
    if target_id is not None:
        if await _edit_screen(bot, chat_id, target_id, payload):
            await _save_screen(peer, chat_id, target_id)
            return
        logger.info("edit of %s failed, sending a new panel", target_id)

    logger.info("send panel chat=%s", chat_id)
    sent_id = await _send_screen(bot, chat_id, payload)
    if sent_id is not None:
        await _save_screen(peer, chat_id, sent_id)
        return
    logger.error("failed to show UI in chat %s", chat_id)


async def send_aside(event: Event, *blocks: RichBlock | None) -> int | None:
    """New message that does not replace the current panel."""
    payload = rich(*blocks)
    bot = current_bot.get()
    message = event if isinstance(event, Message) else event.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if bot is None or chat_id is None:
        if message is not None and hasattr(message, "answer_rich") and not isinstance(
            message, InaccessibleMessage
        ):
            sent = await message.answer_rich(payload)
            return getattr(sent, "message_id", None)
        return None
    return await _send_screen(bot, chat_id, payload)


async def send_file(event: Event, *, filename: str, data: bytes, caption: str) -> None:
    from aiogram.types import BufferedInputFile

    bot = current_bot.get()
    message = event if isinstance(event, Message) else event.message
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if bot is None or chat_id is None:
        return
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(data, filename=filename),
            caption=caption[:1024],
        )
    except TelegramAPIError:
        logger.exception("send document failed")


async def send_json_file(event: Event, *, filename: str, text: str, caption: str) -> None:
    await send_file(event, filename=filename, data=text.encode("utf-8"), caption=caption)


async def ack(event: Event, text: str | None = None) -> None:
    if isinstance(event, CallbackQuery):
        await event.answer(text)
