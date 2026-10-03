from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.enums import StatsInterval
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import report_table_rows, stats_table_rows
from bot.handlers.menu import show_error
from bot.keyboards import CB_SKIP
from bot.rich import button_row, heading, paragraph, quote, table
from bot.states import SKIP_TEXTS, ReportSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id

router = Router(name="stats")


@router.callback_query(F.data == "st:account")
async def account_stats_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(ReportSG.period)
    await state.update_data(report_kind="account")
    await reply_rich(
        event,
        heading("Статистика аккаунта"),
        paragraph("Интервал: кнопка, `5m` / `1d`, или `from,to,interval` unix."),
        button_row([("5 мин / сутки", "st:int:5m"), ("1 день / 30д", "st:int:1d")]),
        wizard=True,
        skip=True,
    )


@router.callback_query(F.data == "st:ad")
async def ad_stats_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(ReportSG.ad_id)
    await reply_rich(
        event,
        heading("Статистика объявления"),
        paragraph("Формат: `<ad_id> 5m` или `<ad_id> 1d`."),
        wizard=True,
    )


@router.callback_query(F.data == "st:month")
async def report_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(ReportSG.year_month)
    await reply_rich(
        event,
        heading("Месячный отчёт"),
        paragraph("Формат `YYYY-MM`, например 2026-09."),
        wizard=True,
    )


@router.message(ReportSG.year_month)
async def report_run(message: Message, state: FSMContext, ads: AdsClient) -> None:
    text = (message.text or "").strip()
    try:
        year_s, month_s = text.split("-", 1)
        year, month = int(year_s), int(month_s)
    except ValueError:
        await reply_rich(message, quote("Нужен формат YYYY-MM."), wizard=True)
        return
    try:
        items = await ads.get_account_report(
            year=year, month=month, account_id=await selected_account_id(state, ads)
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    rows = report_table_rows(items)
    await reply_rich(
        message,
        heading(f"Отчёт {year}-{month:02d}"),
        table(["ID", "Название", "Показы", "Клики", "Действия", "Spend"], rows)
        if rows
        else paragraph("Нет данных."),
        back="stats",
    )


@router.callback_query(ReportSG.period, F.data.startswith("st:int:"))
@router.callback_query(ReportSG.period, F.data == CB_SKIP)
@router.message(ReportSG.period)
async def account_stats_run(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("st:int:"):
        text = event.data.split(":")[2]
    else:
        text = event_text(event).lower()
    now = int(time.time())
    if text in SKIP_TEXTS or text in {"5m", ""}:
        interval = StatsInterval.FIVE_MINUTES
        frm, to = now - 86400, now
    elif text == "1d":
        interval = StatsInterval.ONE_DAY
        frm, to = now - 86400 * 30, now
    else:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 3:
            await reply_rich(event, quote("Нужно from,to,interval."), wizard=True, skip=True)
            return
        frm, to, interval = int(parts[0]), int(parts[1]), int(parts[2])
    try:
        items = await ads.get_account_stats(
            from_time=int(frm),
            to_time=int(to),
            interval=int(interval),
            account_id=await selected_account_id(state, ads),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await state.set_state(None)
    rows = stats_table_rows(items)
    await reply_rich(
        event,
        heading("Статистика аккаунта"),
        table(["Время", "Показы", "Клики", "Действия", "Spend"], rows) if rows else paragraph("Нет точек."),
        back="stats",
    )


@router.message(ReportSG.ad_id)
async def ad_stats_run(message: Message, state: FSMContext, ads: AdsClient) -> None:
    parts = (message.text or "").split()
    if not parts or not parts[0].isdigit():
        await reply_rich(message, quote("Нужен ad_id."), wizard=True)
        return
    ad_id = int(parts[0])
    flag = parts[1].lower() if len(parts) > 1 else "1d"
    now = int(time.time())
    if flag == "5m":
        interval, frm = StatsInterval.FIVE_MINUTES, now - 86400
    else:
        interval, frm = StatsInterval.ONE_DAY, now - 86400 * 30
    try:
        items = await ads.get_ad_stats(
            ad_id=ad_id,
            from_time=frm,
            to_time=now,
            interval=int(interval),
            account_id=await selected_account_id(state, ads),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    rows = stats_table_rows(items)
    await reply_rich(
        message,
        heading(f"Статистика #{ad_id}"),
        table(["Время", "Показы", "Клики", "Действия", "Spend"], rows) if rows else paragraph("Нет точек."),
        back="stats",
    )
