from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.ab import (
    AbStore,
    AbTest,
    compare_payloads,
    creative_changes,
    format_delta,
    format_value,
    verdict,
)
from bot.formatters import ad_json, ts
from bot.handlers.menu import show_error
from bot.keyboards import DANGER, LINK, PRIMARY, SUCCESS
from bot.rich import button_row, heading, paragraph, quote, table
from bot.states import SKIP_TEXTS, AbNoteSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id

router = Router(name="ab")


def _peer(event: Event) -> tuple[int, int]:
    user = event.from_user
    user_id = user.id if user else 0
    if isinstance(event, Message):
        return event.chat.id, user_id
    chat = getattr(event.message, "chat", None)
    return (chat.id if chat else 0), user_id


async def _snapshot(event: Event, ads: AdsClient, state: FSMContext, ad_id: int) -> dict | None:
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
        "account_id": await selected_account_id(state, ads),
        "ad": ad_json(found[0]),
    }


def _currency(payload: dict) -> str | None:
    ad = payload.get("ad") if isinstance(payload.get("ad"), dict) else payload
    if isinstance(ad, dict) and ad.get("currency"):
        return str(ad["currency"])
    return None


def _status_ru(status: str) -> str:
    return "идёт" if status == "running" else "завершён"


async def show_ab_hub(event: Event, abtests: AbStore) -> None:
    _, user_id = _peer(event)
    items = await abtests.list_for_user(user_id)
    rows = [
        [
            str(item.id),
            f"#{item.ad_id}",
            _status_ru(item.status),
            item.note or "—",
            ts(item.started_at),
        ]
        for item in items[:20]
    ]
    buttons = [(f"#{item.id}", f"ab:v:{item.id}", PRIMARY) for item in items[:8]]
    await reply_rich(
        event,
        heading("A/B тесты"),
        paragraph(
            "Фиксируете вариант A на текущих цифрах, меняете объявление (фото, текст, ставка) "
            "и сравниваете только новую открутку. Плюс общего процента — лучше B, минус — лучше A."
        ),
        table(["ID", "Ad", "Статус", "Что меняли", "Старт"], rows) if rows else paragraph("Пока нет тестов. Откройте объявление → A/B."),
        button_row(buttons) if buttons else None,
        button_row([("Объявления", "ads:list", PRIMARY), ("Меню", "nav:menu", LINK)]),
        back="menu",
    )


async def _show_start(event: Event, ads: AdsClient, state: FSMContext, ad_id: int) -> None:
    payload = await _snapshot(event, ads, state, ad_id)
    if payload is None:
        return
    ad = payload["ad"]
    currency = _currency(payload)
    rows = [
        ["Показы", str(ad.get("views") or 0)],
        ["Клики", str(ad.get("clicks") if ad.get("clicks") is not None else "—")],
        ["Действия", str(ad.get("actions") or 0)],
        ["Потрачено", format_value("spent", float(ad.get("spent_budget") or 0), currency)],
        ["CTR", format_value("ctr", (ad.get("computed") or {}).get("ctr"), currency)],
        ["CPC", format_value("cpc", (ad.get("computed") or {}).get("cpc"), currency)],
        ["CPA", format_value("cpa", (ad.get("computed") or {}).get("cpa"), currency)],
        ["eCPM", format_value("ecpm", (ad.get("computed") or {}).get("ecpm"), currency)],
    ]
    await reply_rich(
        event,
        heading(f"A/B #{ad_id}"),
        paragraph(
            "Сейчас зафиксируем эти цифры как вариант A. Потом меняйте объявление — "
            "например добавьте картинку. Новые показы, клики и расход станут вариантом B."
        ),
        table(["Поле", "Вариант A сейчас"], rows),
        button_row(
            [
                ("Зафиксировать A", f"ab:go:{ad_id}", SUCCESS),
                ("С комментарием", f"ab:note:{ad_id}"),
            ]
        ),
        button_row([("К карточке", f"ads:view:{ad_id}", PRIMARY)]),
        back="ads",
    )


async def _show_test(
    event: Event,
    ads: AdsClient,
    state: FSMContext,
    abtests: AbStore,
    test: AbTest,
) -> None:
    current = test.followup
    if test.status == "running":
        live = await _snapshot(event, ads, state, test.ad_id)
        if live is None:
            return
        current = live
    if current is None:
        current = test.baseline
    result = compare_payloads(test.baseline, current)
    currency = _currency(current) or _currency(test.baseline)
    table_rows = [
        [
            row.label,
            format_value(row.key, row.a, currency),
            format_value(row.key, row.b, currency),
            "—" if row.volume else format_delta(row.delta),
        ]
        for row in result.rows
    ]
    changes = creative_changes(test.baseline, current)
    bits = [f"#{test.ad_id}", _status_ru(test.status)]
    if test.note:
        bits.append(test.note)
    if changes:
        bits.append(", ".join(changes))
    actions: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Обновить", f"ab:v:{test.id}", PRIMARY),
        ("К карточке", f"ads:view:{test.ad_id}"),
    ]
    if test.status == "running":
        actions.insert(1, ("Править объявление", f"ads:change:{test.ad_id}"))
        actions.append(("Завершить", f"ab:end:{test.id}", SUCCESS))
    else:
        actions.append(("Удалить", f"ab:del:{test.id}", DANGER))
    await reply_rich(
        event,
        heading(f"A/B #{test.id}"),
        quote(" · ".join(bits)),
        paragraph(verdict(result.overall, b_views=result.b.views)),
        paragraph("Δ %: плюс — лучше B (новый), минус — лучше A (первый). CPC, CPA и eCPM лучше когда меньше."),
        table(["Метрика", "A", "B после", "Δ %"], table_rows),
        button_row(actions[:4]),
        button_row(actions[4:]) if len(actions) > 4 else None,
        button_row([("Все тесты", "ab:hub", LINK), ("Меню", "nav:menu", LINK)]),
        back="ads",
    )


@router.message(Command("ab"))
@router.callback_query(F.data == "nav:ab")
@router.callback_query(F.data == "ab:hub")
async def ab_hub(event: Event, abtests: AbStore, state: FSMContext) -> None:
    await ack(event)
    if isinstance(event, Message):
        await state.set_state(None)
    await show_ab_hub(event, abtests)


@router.callback_query(F.data.startswith("ab:ad:"))
async def ab_for_ad(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, abtests: AbStore
) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    _, user_id = _peer(event)
    running = await abtests.get_running(user_id, ad_id)
    if running:
        await _show_test(event, ads, state, abtests, running)
        return
    await _show_start(event, ads, state, ad_id)


@router.callback_query(F.data.startswith("ab:go:"))
async def ab_start(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, abtests: AbStore
) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    data = await state.get_data()
    note = str(data.get("ab_note") or "")
    await state.set_state(None)
    await state.update_data(ab_note="")
    payload = await _snapshot(event, ads, state, ad_id)
    if payload is None:
        return
    chat_id, user_id = _peer(event)
    test = await abtests.start(
        user_id=user_id,
        chat_id=chat_id,
        account_id=await selected_account_id(state, ads),
        ad_id=ad_id,
        note=note,
        baseline=payload,
    )
    await _show_test(event, ads, state, abtests, test)


@router.callback_query(F.data.startswith("ab:note:"))
async def ab_note_ask(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await state.set_state(AbNoteSG.note)
    await state.update_data(ab_ad_id=ad_id, ab_note="")
    await reply_rich(
        event,
        heading(f"A/B #{ad_id}"),
        paragraph("Что меняете? Например: «добавил картинку». Можно пропустить."),
        wizard=True,
        skip=True,
    )


@router.message(AbNoteSG.note)
@router.callback_query(AbNoteSG.note, F.data == "wiz:skip")
async def ab_note_save(
    event: Event, ads: AdsClient, state: FSMContext, abtests: AbStore
) -> None:
    await ack(event)
    data = await state.get_data()
    ad_id = int(data.get("ab_ad_id") or 0)
    text = event_text(event)
    note = "" if text in SKIP_TEXTS or (isinstance(event, CallbackQuery) and event.data == "wiz:skip") else text
    await state.set_state(None)
    await state.update_data(ab_note=note, ab_ad_id=0)
    if not ad_id:
        await show_ab_hub(event, abtests)
        return
    payload = await _snapshot(event, ads, state, ad_id)
    if payload is None:
        return
    chat_id, user_id = _peer(event)
    test = await abtests.start(
        user_id=user_id,
        chat_id=chat_id,
        account_id=await selected_account_id(state, ads),
        ad_id=ad_id,
        note=note,
        baseline=payload,
    )
    await _show_test(event, ads, state, abtests, test)


@router.callback_query(F.data.startswith("ab:v:"))
async def ab_view(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, abtests: AbStore
) -> None:
    await ack(event)
    _, user_id = _peer(event)
    test = await abtests.get(int(event.data.split(":")[2]), user_id)
    if test is None:
        await reply_rich(event, quote("Тест не найден."), back="ads")
        return
    await _show_test(event, ads, state, abtests, test)


@router.callback_query(F.data.startswith("ab:end:"))
async def ab_finish(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, abtests: AbStore
) -> None:
    await ack(event)
    _, user_id = _peer(event)
    test = await abtests.get(int(event.data.split(":")[2]), user_id)
    if test is None:
        await reply_rich(event, quote("Тест не найден."), back="ads")
        return
    payload = await _snapshot(event, ads, state, test.ad_id)
    if payload is None:
        return
    finished = await abtests.finish(test.id, user_id, payload)
    await _show_test(event, ads, state, abtests, finished or test)


@router.callback_query(F.data.startswith("ab:del:"))
async def ab_delete(event: CallbackQuery, abtests: AbStore) -> None:
    await ack(event)
    _, user_id = _peer(event)
    await abtests.delete(int(event.data.split(":")[2]), user_id)
    await show_ab_hub(event, abtests)
