from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext

from ads_api.client import AdsClient
from bot.formatters import error_needs_user_fix, format_ads_error
from bot.keyboards import CB_BACK, CB_CANCEL
from bot.rich import code, heading, paragraph, quote
from bot.states import BACK_TEXTS, CANCEL_TEXTS
from bot.ui import Event, ack, keep_ui_data, reply_rich, selected_account_id

router = Router(name="menu")


@router.message(CommandStart())
@router.message(Command("menu"))
@router.callback_query(F.data == "nav:menu")
async def cmd_start(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    await state.set_state(None)
    account = await selected_account_id(state, ads)
    await reply_rich(
        event,
        heading("Ads Manager", size=2),
        paragraph(["Кабинет: ", code(account or "текущий токен")]),
        paragraph("Выберите раздел."),
        section="menu",
    )


@router.message(Command("help"))
@router.callback_query(F.data == "nav:help")
async def cmd_help(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Справка"),
        paragraph("Разделы меню покрывают все методы Telegram Ads API: объявления, кабинет, биллинг, аудитории, пиксель, статистика, таргетинг и медиа."),
        paragraph("Автостоп: карточка объявления → Правила. Пауза по показам, кликам, действиям или тратам. Рядом с порогом проверка каждые 1–2 секунды."),
        paragraph("Сохранить: карточка → Сохранить → группа. Потом Сохранённые → Excel: вкладка на группу, в таблице все данные объявления."),
        paragraph("A/B: карточка → A/B → зафиксировать A, затем меняйте объявление. Сравниваются CPC, CPA, CTR и общий процент: плюс — лучше новый, минус — лучше первый."),
        paragraph("Аудитории (ретаргетинг по телефонам) доступны, только если Telegram включил их для кабинета. Иначе раздел покажет пояснение, обычные объявления от этого не зависят."),
        paragraph("В мастере: «Отмена», «Назад», «Пропустить». Команда /cancel тоже сбрасывает шаг."),
        section="menu",
    )


@router.message(Command("cancel"))
@router.message(F.text.in_(CANCEL_TEXTS))
@router.callback_query(F.data == CB_CANCEL)
async def cmd_cancel(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(None)
    data = await state.get_data()
    await state.set_data(keep_ui_data(data))
    await reply_rich(
        event,
        heading("Отменено"),
        paragraph("Мастер сброшен."),
        section="menu",
    )


@router.message(Command("billing"))
@router.callback_query(F.data == "nav:billing")
async def nav_billing(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Биллинг"),
        paragraph("Переводы между кабинетами и лента операций."),
        section="billing",
    )


@router.callback_query(F.data == "nav:pixel")
async def nav_pixel(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Пиксель"),
        paragraph("Тег сайта и события конверсий."),
        section="pixel",
    )


@router.message(Command("stats"))
@router.callback_query(F.data == "nav:stats")
async def nav_stats(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Статистика"),
        paragraph("Показы, клики и расход по аккаунту или объявлению."),
        section="stats",
    )


@router.message(Command("targeting"))
@router.callback_query(F.data == "nav:targeting")
async def nav_targeting(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Таргетинг"),
        paragraph("Словари и поиск каналов и ботов."),
        section="targeting",
    )


@router.message(Command("media"))
@router.callback_query(F.data == "nav:media")
async def nav_media(event: Event) -> None:
    await ack(event)
    await reply_rich(
        event,
        heading("Медиа"),
        paragraph("Загрузка фото и видео для объявления."),
        section="media",
    )


@router.message(StateFilter("*"), F.text.in_(BACK_TEXTS))
@router.callback_query(F.data == CB_BACK)
async def cmd_back_global(event: Event, state: FSMContext) -> None:
    await ack(event)
    current = await state.get_state()
    if current is None:
        await reply_rich(
            event,
            heading("Ads Manager", size=2),
            paragraph("Выберите раздел."),
            section="menu",
        )
        return
    await reply_rich(
        event,
        paragraph("Чтобы выйти из мастера, нажмите «Отмена»."),
        wizard=True,
    )


async def show_error(event: Event, exc: Exception) -> None:
    hint = "Исправьте данные и повторите." if error_needs_user_fix(exc) else "Это ограничение кабинета или ответ API, не ошибка в форме."
    await reply_rich(
        event,
        heading("Ошибка"),
        quote(format_ads_error(exc)),
        paragraph(hint),
        section="menu",
    )
