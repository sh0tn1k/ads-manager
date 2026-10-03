from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import bot_rows, channel_rows
from bot.handlers.menu import show_error
from bot.rich import heading, paragraph, quote, table
from bot.states import TargetingSG
from bot.ui import Event, ack, reply_rich

router = Router(name="targeting")


@router.callback_query(F.data == "tg:lang")
async def languages(event: Event, ads: AdsClient) -> None:
    await ack(event)
    try:
        items = await ads.get_target_languages_list()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = [[i.language_code, i.name] for i in items[:80]]
    await reply_rich(
        event,
        heading("Языки"),
        table(["Код", "Название"], rows),
        back="targeting",
    )


@router.callback_query(F.data == "tg:topics")
async def topics(event: Event, ads: AdsClient) -> None:
    await ack(event)
    try:
        items = await ads.get_target_topics_list()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = [[str(i.topic_id), i.name] for i in items[:80]]
    await reply_rich(
        event,
        heading("Темы"),
        table(["ID", "Название"], rows),
        back="targeting",
    )


@router.callback_query(F.data == "tg:countries")
async def countries(event: Event, ads: AdsClient) -> None:
    await ack(event)
    try:
        items = await ads.get_target_countries_list()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = [[i.country_code, i.name] for i in items[:80]]
    await reply_rich(
        event,
        heading("Страны"),
        table(["ISO", "Название"], rows),
        back="targeting",
    )


@router.callback_query(F.data == "tg:loc")
async def locations_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(TargetingSG.query)
    await state.update_data(tg_action="locations")
    await reply_rich(
        event,
        heading("Поиск локаций"),
        paragraph("Формат: `RU Москва` (ISO + запрос)."),
        wizard=True,
    )


@router.callback_query(F.data == "tg:ch")
async def channel_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(TargetingSG.query)
    await state.update_data(tg_action="channel")
    await reply_rich(
        event,
        heading("Резолв канала"),
        paragraph("Username `@durov` (рекомендуется) или числовой id, если уже резолвили."),
        wizard=True,
    )


@router.callback_query(F.data == "tg:bot")
async def bot_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(TargetingSG.query)
    await state.update_data(tg_action="bot")
    await reply_rich(
        event,
        heading("Резолв бота"),
        paragraph("Username `@BotFather` или числовой id."),
        wizard=True,
    )


@router.message(TargetingSG.query)
async def targeting_query(message: Message, state: FSMContext, ads: AdsClient) -> None:
    action = (await state.get_data()).get("tg_action")
    text = (message.text or "").strip()
    try:
        if action == "locations":
            if text.lower().startswith("ids "):
                ids = [int(x) for x in text[4:].replace(";", ",").split(",") if x.strip().isdigit()]
                found = await ads.get_target_locations_by_id(ids)
                rows = [[str(i.location_id), i.name, i.region or ""] for i in found]
                await reply_rich(
                    message,
                    heading("Локации по id"),
                    table(["ID", "Название", "Регион"], rows) if rows else paragraph("Ничего."),
                    back="targeting",
                )
                await state.set_state(None)
                return
            country, _, query = text.partition(" ")
            listing = await ads.get_target_locations_list(country_code=country.upper(), query=query.strip() or country)
            rows = [
                [str(i.location_id), i.name, i.region or ""]
                for i in listing.locations[:40]
            ]
            await reply_rich(
                message,
                heading("Локации"),
                quote(f"Всего: {listing.total_count}"),
                table(["ID", "Название", "Регион"], rows) if rows else paragraph("Ничего."),
                back="targeting",
            )
        elif action == "channel":
            ch = await ads.get_target_channel(text)
            await reply_rich(
                message,
                heading("Канал"),
                table(["Поле", "Значение"], channel_rows(ch)),
                back="targeting",
            )
        else:
            bot = await ads.get_target_bot(text)
            await reply_rich(
                message,
                heading("Бот"),
                table(["Поле", "Значение"], bot_rows(bot)),
                back="targeting",
            )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
