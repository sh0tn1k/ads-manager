from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.enums import PixelEventType
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import pixel_event_rows, pixel_secret
from bot.handlers.menu import show_error
from bot.keyboards import DANGER, pixel_event_card_blocks
from bot.rich import button_grid, button_row, copy_row, heading, paragraph, pre, quote, table
from bot.states import PixelEventSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id

router = Router(name="pixels")


def _event_type_blocks():
    items = [
        (t.value, f"px:ty:{t.value}")
        for t in PixelEventType
        if t != PixelEventType.LANDING_VIEW
    ]
    return button_grid(items, per_row=3)


@router.message(Command("pixel"))
@router.callback_query(F.data == "px:show")
async def show_pixel(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        pixel = await ads.get_pixel(account_id=await selected_account_id(state, ads))
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(
        event,
        heading("Пиксель"),
        paragraph(["ID: ", {"type": "code", "text": pixel.pixel_id}]),
        pre(pixel_secret(pixel), language="html"),
        copy_row("Скопировать snippet", pixel.code_snippet),
        back="pixel",
    )


@router.callback_query(F.data == "px:create")
async def create_pixel(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        pixel = await ads.create_pixel(
            account_id=await selected_account_id(state, ads),
            idempotency_key=ads.new_idempotency_key(),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(
        event,
        heading("Пиксель создан"),
        paragraph(["ID: ", {"type": "code", "text": pixel.pixel_id}]),
        pre(pixel.code_snippet, language="html"),
        back="pixel",
    )


@router.callback_query(F.data == "px:events")
async def list_events(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        listing = await ads.get_pixel_events_list(account_id=await selected_account_id(state, ads))
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = [[e.event_id[:10] + "…", e.title, str(e.type), str(e.status)] for e in listing.events[:30]]
    buttons = [(e.title[:16], f"px:view:{e.event_id}") for e in listing.events[:8]]
    await reply_rich(
        event,
        heading("События пикселя"),
        quote(f"Всего: {listing.total_count}"),
        table(["ID", "Название", "Тип", "Статус"], rows) if rows else paragraph("Нет событий."),
        button_row(buttons) if buttons else None,
        back="pixel",
    )


@router.callback_query(F.data.startswith("px:view:"))
async def view_event(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    event_id = event.data.split(":", 2)[2]
    try:
        items = await ads.get_pixel_events_by_id(
            [event_id], account_id=await selected_account_id(state, ads)
        )
        item = items[0]
    except (AdsApiError, AdsTransportError, IndexError) as exc:
        await show_error(event, exc if not isinstance(exc, IndexError) else AdsApiError("NOT_FOUND"))
        return
    snippet = item.code_snippet
    await reply_rich(
        event,
        heading(item.title),
        table(["Поле", "Значение"], pixel_event_rows(item)),
        pre(snippet, language="javascript") if snippet else None,
        *pixel_event_card_blocks(
            event_id=item.event_id,
            auto_created=bool(item.auto_created),
            ads_count=item.ads_count or 0,
        ),
    )


@router.callback_query(F.data == "px:new")
async def create_event_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(PixelEventSG.title)
    await reply_rich(
        event,
        heading("Новое событие"),
        paragraph("Название события:"),
        wizard=True,
    )


@router.message(PixelEventSG.title)
async def create_event_title(message: Message, state: FSMContext) -> None:
    await state.update_data(px_title=(message.text or "").strip())
    await state.set_state(PixelEventSG.event_type)
    await reply_rich(
        message,
        paragraph("Тип события — кнопка или текст:"),
        *_event_type_blocks(),
        wizard=True,
    )


@router.callback_query(PixelEventSG.event_type, F.data.startswith("px:ty:"))
@router.message(PixelEventSG.event_type)
async def create_event_type(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    title = (await state.get_data())["px_title"]
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("px:ty:"):
        event_type = event.data.split(":", 2)[2]
    else:
        event_type = event_text(event)
    try:
        item = await ads.create_pixel_event(
            title=title,
            type=event_type,
            account_id=await selected_account_id(state, ads),
            idempotency_key=ads.new_idempotency_key(),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await state.set_state(None)
    await reply_rich(
        event,
        heading("Событие создано"),
        table(["Поле", "Значение"], pixel_event_rows(item)),
        *pixel_event_card_blocks(
            event_id=item.event_id,
            auto_created=bool(item.auto_created),
            ads_count=item.ads_count or 0,
        ),
    )


@router.callback_query(F.data.startswith("px:ren:"))
async def rename_from_card(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    event_id = event.data.split(":", 2)[2]
    await state.set_state(PixelEventSG.event_id)
    await state.update_data(px_action="edit", px_event_id=event_id)
    await reply_rich(event, heading("Переименовать"), paragraph("Новое название:"), wizard=True)


@router.callback_query(F.data.startswith("px:del:"))
async def delete_event_confirm(event: CallbackQuery) -> None:
    await ack(event)
    event_id = event.data.split(":", 2)[2]
    await reply_rich(
        event,
        heading("Удалить событие?"),
        paragraph("Событие не должно использоваться в объявлениях."),
        button_row(
            [
                ("Удалить", f"px:kill:{event_id}", DANGER),
                ("Отмена", f"px:view:{event_id}"),
            ]
        ),
        back="pixel",
    )


@router.callback_query(F.data.startswith("px:kill:"))
async def delete_event_do(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    event_id = event.data.split(":", 2)[2]
    try:
        ok = await ads.delete_pixel_event(
            event_id=event_id, account_id=await selected_account_id(state, ads)
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(event, heading("Удаление"), quote("Готово" if ok else "false"), back="pixel")


@router.message(PixelEventSG.event_id)
async def pixel_event_rename(message: Message, state: FSMContext, ads: AdsClient) -> None:
    data = await state.get_data()
    event_id = data.get("px_event_id")
    title = (message.text or "").strip()
    if not event_id or not title:
        await reply_rich(message, quote("Нужно название."), wizard=True)
        return
    try:
        item = await ads.edit_pixel_event(
            event_id=event_id,
            title=title,
            account_id=await selected_account_id(state, ads),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Сохранено"),
        table(["Поле", "Значение"], pixel_event_rows(item)),
        *pixel_event_card_blocks(
            event_id=item.event_id,
            auto_created=bool(item.auto_created),
            ads_count=item.ads_count or 0,
        ),
    )
