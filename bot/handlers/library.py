from __future__ import annotations

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import _clip, ad_json, money, status_ru, ts
from bot.handlers.menu import show_error
from bot.keyboards import DANGER, LINK, PRIMARY, SUCCESS
from bot.library import LibraryStore, SavedAd, SavedGroup
from bot.rich import button_grid, button_row, heading, paragraph, quote, table
from bot.states import SaveGroupSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id, send_file

router = Router(name="library")


def _peer(event: Event) -> tuple[int, int]:
    user = event.from_user
    user_id = user.id if user else 0
    if isinstance(event, Message):
        return event.chat.id, user_id
    chat = getattr(event.message, "chat", None)
    return (chat.id if chat else 0), user_id


def _group_rows(groups: list[SavedGroup]) -> list[list[str]]:
    return [
        [str(group.id), group.title, str(group.ads_count), ts(group.created_at)]
        for group in groups
    ]


def _ad_rows(items: list[SavedAd]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items:
        ad = item.payload.get("ad") if isinstance(item.payload.get("ad"), dict) else {}
        status = status_ru(str(ad.get("status") or "")) if ad.get("status") else "—"
        views = ad.get("views", "—")
        spent = money(ad.get("spent_budget"), str(ad.get("currency") or ""))
        rows.append(
            [
                str(item.ad_id),
                _clip(item.ad_title or str(ad.get("title") or "—"), 28),
                status,
                str(views),
                spent,
                ts(item.saved_at),
            ]
        )
    return rows


async def _load_ad_payload(event: Event, ads: AdsClient, state: FSMContext, ad_id: int) -> dict | None:
    try:
        found = await ads.get_ads_by_id(
            [ad_id],
            account_id=await selected_account_id(state, ads),
            return_target=True,
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return None
    if not found:
        await show_error(event, AdsApiError("AD_NOT_FOUND"))
        return None
    return {
        "saved_at": int(time.time()),
        "account_id": await selected_account_id(state, ads),
        "ad": ad_json(found[0]),
    }


async def show_library(event: Event, library: LibraryStore) -> None:
    _, user_id = _peer(event)
    groups = await library.list_groups(user_id)
    total = sum(group.ads_count for group in groups)
    buttons = [(group.title[:18], f"sv:g:{group.id}", PRIMARY) for group in groups[:12]]
    nav: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Новая группа", "sv:new:0", SUCCESS),
    ]
    if groups:
        nav.append(("Excel", "sv:xls", PRIMARY))
    nav.append(("Объявления", "ads:list"))
    await reply_rich(
        event,
        heading("Сохранённые"),
        quote(f"Групп: {len(groups)} · объявлений: {total}"),
        paragraph("Снимок всех настроек и статистики. Потом можно выгрузить Excel — вкладка на группу."),
        table(["ID", "Группа", "Объявлений", "Создана"], _group_rows(groups))
        if groups
        else paragraph("Пока пусто. Откройте объявление → Сохранить."),
        button_row(buttons) if buttons else None,
        button_row(nav),
        back="menu",
    )


async def _show_group(event: Event, library: LibraryStore, group_id: int) -> None:
    _, user_id = _peer(event)
    group = await library.get_group(group_id, user_id)
    if group is None:
        await reply_rich(event, quote("Группа не найдена."), back="saves")
        return
    items = await library.list_ads(group_id, user_id)
    open_btns = [(f"#{item.ad_id}", f"ads:view:{item.ad_id}", PRIMARY) for item in items[:8]]
    drop_btns = [(f"Убрать #{item.ad_id}", f"sv:rm:{group.id}:{item.ad_id}", DANGER) for item in items[:8]]
    await reply_rich(
        event,
        heading(group.title),
        quote(f"Объявлений: {len(items)}"),
        table(
            ["ID", "Название", "Статус", "Показы", "Потрачено", "Снимок"],
            _ad_rows(items),
        )
        if items
        else paragraph("В группе пока нет объявлений."),
        button_row(open_btns) if open_btns else None,
        *(button_grid(drop_btns, per_row=2) if drop_btns else []),
        button_row(
            [
                ("Excel", "sv:xls", PRIMARY),
                ("Удалить группу", f"sv:kg:{group.id}", DANGER),
                ("К группам", "sv:hub", LINK),
            ]
        ),
        back="saves",
    )


async def _show_picker(event: Event, library: LibraryStore, ad_id: int) -> None:
    _, user_id = _peer(event)
    groups = await library.list_groups(user_id)
    already = await library.ad_group_ids(user_id, ad_id)
    rows = [
        [
            group.title,
            str(group.ads_count),
            "да" if group.id in already else "—",
        ]
        for group in groups
    ]
    put = [
        (group.title[:16], f"sv:put:{group.id}:{ad_id}", PRIMARY if group.id not in already else LINK)
        for group in groups[:12]
    ]
    await reply_rich(
        event,
        heading(f"Сохранить #{ad_id}"),
        paragraph("Выберите группу или создайте новую. Сохраняются все параметры, настройки и статистика на этот момент."),
        table(["Группа", "Объявлений", "Уже здесь"], rows) if rows else paragraph("Групп ещё нет — создайте первую."),
        *(button_grid(put, per_row=2) if put else []),
        button_row(
            [
                ("Новая группа", f"sv:new:{ad_id}", SUCCESS),
                ("К карточке", f"ads:view:{ad_id}"),
            ]
        ),
        back="ads",
    )


async def _put_ad(
    event: Event,
    ads: AdsClient,
    state: FSMContext,
    library: LibraryStore,
    *,
    group_id: int,
    ad_id: int,
) -> None:
    payload = await _load_ad_payload(event, ads, state, ad_id)
    if payload is None:
        return
    _, user_id = _peer(event)
    try:
        saved = await library.save_ad(group_id=group_id, user_id=user_id, ad_id=ad_id, payload=payload)
    except PermissionError:
        await reply_rich(event, quote("Группа не найдена."), back="saves")
        return
    group = await library.get_group(group_id, user_id)
    title = group.title if group else "группа"
    await reply_rich(
        event,
        heading("Сохранено"),
        quote(f"#{saved.ad_id} → {title}"),
        paragraph("Снимок можно обновить, сохранив объявление в эту группу ещё раз."),
        button_row(
            [
                ("Группа", f"sv:g:{group_id}", PRIMARY),
                ("К карточке", f"ads:view:{ad_id}"),
                ("Excel", "sv:xls", SUCCESS),
            ]
        ),
        back="saves",
    )


@router.message(Command("saves"))
@router.callback_query(F.data == "nav:saves")
@router.callback_query(F.data == "sv:hub")
async def library_hub(event: Event, library: LibraryStore, state: FSMContext) -> None:
    await ack(event)
    if isinstance(event, Message):
        await state.set_state(None)
    await show_library(event, library)


@router.callback_query(F.data.startswith("sv:ad:"))
async def pick_group(event: CallbackQuery, library: LibraryStore) -> None:
    await ack(event)
    await _show_picker(event, library, int(event.data.split(":")[2]))


@router.callback_query(F.data.startswith("sv:put:"))
async def put_ad(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, library: LibraryStore
) -> None:
    await ack(event)
    _, _, group_s, ad_s = event.data.split(":", 3)
    await _put_ad(
        event,
        ads,
        state,
        library,
        group_id=int(group_s),
        ad_id=int(ad_s),
    )


@router.callback_query(F.data.startswith("sv:new:"))
async def new_group(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await state.set_state(SaveGroupSG.title)
    await state.update_data(save_ad_id=ad_id)
    await reply_rich(
        event,
        heading("Новая группа"),
        paragraph("Напишите название группы, например «Группа 1» или «Поиск апрель»."),
        wizard=True,
    )


@router.message(SaveGroupSG.title)
async def create_group_name(
    message: Message, ads: AdsClient, state: FSMContext, library: LibraryStore
) -> None:
    title = event_text(message)
    data = await state.get_data()
    ad_id = int(data.get("save_ad_id") or 0)
    try:
        chat_id, user_id = _peer(message)
        group = await library.create_group(user_id=user_id, chat_id=chat_id, title=title)
    except ValueError:
        await reply_rich(message, quote("Название не должно быть пустым."), wizard=True)
        return
    await state.set_state(None)
    if ad_id:
        await _put_ad(message, ads, state, library, group_id=group.id, ad_id=ad_id)
        return
    await show_library(message, library)


@router.callback_query(F.data.startswith("sv:g:"))
async def view_group(event: CallbackQuery, library: LibraryStore) -> None:
    await ack(event)
    await _show_group(event, library, int(event.data.split(":")[2]))


@router.callback_query(F.data.startswith("sv:rm:"))
async def remove_ad(event: CallbackQuery, library: LibraryStore) -> None:
    await ack(event)
    _, _, group_s, ad_s = event.data.split(":", 3)
    _, user_id = _peer(event)
    await library.delete_ad(int(group_s), user_id, int(ad_s))
    await _show_group(event, library, int(group_s))


@router.callback_query(F.data.startswith("sv:kg:"))
async def confirm_delete_group(event: CallbackQuery) -> None:
    await ack(event)
    group_id = int(event.data.split(":")[2])
    await reply_rich(
        event,
        heading("Удалить группу?"),
        paragraph("Снимки объявлений в этой группе удалятся. Живые объявления в Ads не трогаем."),
        button_row(
            [
                ("Удалить", f"sv:dg:{group_id}", DANGER),
                ("Отмена", f"sv:g:{group_id}"),
            ]
        ),
        back="saves",
    )


@router.callback_query(F.data.startswith("sv:dg:"))
async def delete_group(event: CallbackQuery, library: LibraryStore) -> None:
    await ack(event)
    _, user_id = _peer(event)
    await library.delete_group(int(event.data.split(":")[2]), user_id)
    await show_library(event, library)


@router.callback_query(F.data == "sv:xls")
async def export_excel(event: Event, library: LibraryStore) -> None:
    await ack(event)
    _, user_id = _peer(event)
    groups = await library.list_groups(user_id)
    if not groups:
        await reply_rich(
            event,
            heading("Excel"),
            paragraph("Сначала сохраните объявление в группу."),
            back="saves",
        )
        return
    data = await library.export_user(user_id)
    day = time.strftime("%Y-%m-%d")
    await send_file(
        event,
        filename=f"ads-groups-{day}.xlsx",
        data=data,
        caption="Excel по группам: каждая вкладка — таблица объявлений этой группы.",
    )
