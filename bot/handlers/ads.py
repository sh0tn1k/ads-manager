from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.enums import AdPlacement
from ads_api.errors import AdsApiError, AdsTransportError
from bot.create_ad import (
    PLACEMENT_ALIASES,
    PLACEMENT_LABELS,
    STEP_STATE,
    ask_create_step,
    draft_summary,
    next_step,
    parse_media,
    parse_target,
)
from bot.formatters import ADS_LIST_HEADERS, ad_json, ad_setting_rows, ad_stat_rows, ads_json, ads_table_rows
from bot.handlers.menu import show_error
from bot.keyboards import CB_SKIP, CB_YES, DANGER, LINK, PRIMARY, SUCCESS, ad_card_blocks, confirm_blocks, edit_ad_field_blocks
from bot.rich import button_grid, button_row, copy_row, details, heading, paragraph, pre, quote, table
from bot.states import SKIP_TEXTS, AdIdSG, CreateAdSG, EditAdSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id, send_aside, send_json_file

router = Router(name="ads")

_parse_target = parse_target


@router.message(Command("ads"))
@router.callback_query(F.data == "nav:ads")
@router.callback_query(F.data == "ads:list")
@router.callback_query(F.data == "ads:page")
@router.callback_query(F.data.startswith("ads:page:"))
async def list_ads(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    offset = None
    data = await state.get_data()
    if isinstance(event, CallbackQuery) and event.data == "ads:page":
        offset = data.get("ads_next_offset")
    elif isinstance(event, CallbackQuery) and event.data and event.data.startswith("ads:page:"):
        offset = event.data.split(":", 2)[2] or None
    try:
        page = await ads.get_ads_list(
            account_id=await selected_account_id(state, ads),
            offset=offset,
            limit=8,
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = ads_table_rows(page.ads)
    nav: list[tuple[str, str] | tuple[str, str, str]] = []
    await state.update_data(ads_next_offset=page.next_offset)
    if page.next_offset:
        nav.append(("Далее", "ads:page", PRIMARY))
    nav.append(("Создать", "nav:create_ad", SUCCESS))
    nav.append(("Найти", "ads:find"))
    nav.append(("Правила", "rl:all"))
    nav.append(("JSON", "ads:json", LINK))
    nav.append(("Сохранённые", "nav:saves"))
    nav.append(("A/B", "ab:hub"))
    ad_buttons = [(f"#{ad.ad_id}", f"ads:view:{ad.ad_id}", PRIMARY) for ad in page.ads[:8]]
    await reply_rich(
        event,
        heading("Объявления"),
        quote(f"Всего: {page.total_count}"),
        table(ADS_LIST_HEADERS, rows) if rows else paragraph("Пока пусто."),
        button_row(ad_buttons) if ad_buttons else None,
        *(button_grid(nav, per_row=4) if nav else []),
        back="menu",
    )


JSON_PRE_LIMIT = 3500
JSON_COPY_LIMIT = 256


async def _send_json(event: Event, *, title: str, filename: str, payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(text) <= JSON_PRE_LIMIT:
        extra = []
        if len(text) <= JSON_COPY_LIMIT:
            extra.append(copy_row("Скопировать JSON", text))
            hint = "Отдельное сообщение. Нажмите «Скопировать JSON»."
        else:
            hint = "Отдельное сообщение. Нажмите на блок JSON и скопируйте."
        await send_aside(
            event,
            heading(title),
            paragraph(hint),
            pre(text, language="json"),
            *extra,
        )
        return
    await send_json_file(
        event,
        filename=filename,
        text=text,
        caption=f"{title}. Откройте файл и скопируйте JSON.",
    )


@router.callback_query(F.data == "ads:json")
async def json_all_ads(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    items: list = []
    try:
        async for ad in ads.iter_ads(
            account_id=await selected_account_id(state, ads),
            limit=100,
            return_target=True,
        ):
            items.append(ad)
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await _send_json(
        event,
        title="JSON всех объявлений",
        filename="ads.json",
        payload=ads_json(items),
    )


@router.callback_query(F.data.startswith("ads:json:"))
async def json_one_ad(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    try:
        found = await ads.get_ads_by_id(
            [ad_id],
            account_id=await selected_account_id(state, ads),
            return_target=True,
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    if not found:
        await show_error(event, AdsApiError("AD_NOT_FOUND"))
        return
    ad = found[0]
    await _send_json(
        event,
        title=f"JSON объявления #{ad.ad_id}",
        filename=f"ad-{ad.ad_id}.json",
        payload=ad_json(ad),
    )


@router.callback_query(F.data.startswith("ads:view:"))
async def view_ad_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await _show_ad(event, ads, state, ad_id)


@router.message(Command("ad"))
@router.callback_query(F.data == "ads:find")
async def find_ad_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="view")
    await reply_rich(
        event,
        heading("Карточка объявления"),
        paragraph("Пришлите ad_id."),
        wizard=True,
    )


@router.message(AdIdSG.ad_id)
async def ad_id_entered(message: Message, state: FSMContext, ads: AdsClient) -> None:
    raw = (message.text or "").strip().lstrip("#")
    tokens = raw.split()
    if not tokens or not tokens[0].isdigit():
        await reply_rich(message, quote("Нужно целое число ad_id."), wizard=True)
        return
    ad_id = int(tokens[0])
    data = await state.get_data()
    action = data.get("ad_action", "view")
    if len(tokens) > 1 and tokens[1].lower() in {"resume", "start", "play"}:
        action = "resume"
    if len(tokens) > 1 and tokens[1].lower() in {"pause", "stop"}:
        action = "pause"
    extra = data.get("ad_extra")
    await state.set_state(None)
    if action == "view":
        await _show_ad(message, ads, state, ad_id)
    elif action == "pause":
        await _set_paused(message, ads, state, ad_id, True)
    elif action == "resume":
        await _set_paused(message, ads, state, ad_id, False)
    elif action == "submit":
        await _submit(message, ads, state, ad_id)
    elif action == "delete":
        await _delete(message, ads, state, ad_id)
    elif action == "budget_in":
        await _budget(message, ads, state, ad_id, extra, increase=True)
    elif action == "budget_out":
        await _budget(message, ads, state, ad_id, extra, increase=False)
    elif action == "tx":
        await _ad_tx(message, ads, state, ad_id)
    elif action == "edit":
        await state.set_state(EditAdSG.field)
        await state.update_data(edit_ad_id=ad_id)
        await _ask_edit_field(message, ad_id)
    else:
        await _show_ad(message, ads, state, ad_id)


async def _ask_edit_field(event: Event, ad_id: int) -> None:
    await reply_rich(
        event,
        heading(f"Редактировать #{ad_id}"),
        paragraph("Выберите поле кнопкой или напишите имя поля."),
        *edit_ad_field_blocks(),
        wizard=True,
    )


async def _show_ad(event: Event, ads: AdsClient, state: FSMContext, ad_id: int) -> None:
    try:
        ads_found = await ads.get_ads_by_id(
            [ad_id],
            account_id=await selected_account_id(state, ads),
            return_target=True,
        )
        ad = ads_found[0]
    except (AdsApiError, AdsTransportError, IndexError) as exc:
        await show_error(event, exc if isinstance(exc, Exception) and not isinstance(exc, IndexError) else AdsApiError("AD_NOT_FOUND"))
        return
    decline = None
    if ad.decline_reason:
        decline = details("Причина отклонения", ad.decline_reason.description_html or ad.decline_reason.text)
    await reply_rich(
        event,
        heading(f"Объявление #{ad.ad_id}"),
        table(["Поле", "Значение"], ad_stat_rows(ad), caption="Статистика"),
        table(["Поле", "Значение"], ad_setting_rows(ad), caption="Настройки"),
        decline,
        *ad_card_blocks(ad_id=ad.ad_id, status=str(ad.status) if ad.status else None, is_paused=bool(ad.is_paused)),
    )


@router.callback_query(F.data.startswith("ads:pause:"))
async def pause_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    await _set_paused(event, ads, state, int(event.data.split(":")[2]), True)


@router.callback_query(F.data.startswith("ads:resume:"))
async def resume_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    await _set_paused(event, ads, state, int(event.data.split(":")[2]), False)


@router.callback_query(F.data.startswith("ads:review:"))
async def submit_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    await _submit(event, ads, state, int(event.data.split(":")[2]))


@router.callback_query(F.data.startswith("ads:kill:"))
async def delete_cb(event: CallbackQuery) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await reply_rich(
        event,
        heading(f"Удалить #{ad_id}?"),
        paragraph("Объявление должно быть неактивно не меньше 10 минут. Это нельзя отменить."),
        button_row(
            [
                ("Удалить", f"ads:drop:{ad_id}", DANGER),
                ("Отмена", f"ads:view:{ad_id}"),
            ]
        ),
        back="ads",
    )


@router.callback_query(F.data.startswith("ads:drop:"))
async def delete_do(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    await _delete(event, ads, state, int(event.data.split(":")[2]))


@router.callback_query(F.data.startswith("ads:ledger:"))
async def tx_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    await _ad_tx(event, ads, state, int(event.data.split(":")[2]))


@router.callback_query(F.data.startswith("ads:txpage:"))
async def tx_page_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    parts = event.data.split(":")
    ad_id = int(parts[2])
    raw = parts[3] if len(parts) > 3 else ""
    offset: int | None = int(raw) if raw.lstrip("-").isdigit() else None
    await _ad_tx(event, ads, state, ad_id, offset=offset)


@router.callback_query(F.data.startswith("ads:change:"))
async def edit_from_card(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await state.set_state(EditAdSG.field)
    await state.update_data(edit_ad_id=ad_id)
    await _ask_edit_field(event, ad_id)


async def _set_paused(event, ads, state, ad_id: int, paused: bool) -> None:
    try:
        await ads.edit_ad(
            ad_id=ad_id,
            is_paused=paused,
            account_id=await selected_account_id(state, ads),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await _show_ad(event, ads, state, ad_id)


async def _submit(event, ads, state, ad_id: int) -> None:
    try:
        await ads.submit_ad_for_review(
            ad_id=ad_id, account_id=await selected_account_id(state, ads)
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await _show_ad(event, ads, state, ad_id)


async def _delete(event, ads, state, ad_id: int) -> None:
    try:
        ok = await ads.delete_ad(ad_id=ad_id, account_id=await selected_account_id(state, ads))
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(
        event,
        heading("Удаление"),
        quote("Готово" if ok else "API вернуло false"),
        back="ads",
    )


@router.callback_query(F.data == "ads:ask_pause")
async def pause_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="pause")
    await reply_rich(
        event,
        paragraph("ad_id для паузы. Чтобы возобновить, напишите `id resume` (например `12 resume`)."),
        wizard=True,
    )


@router.callback_query(F.data == "ads:ask_review")
async def submit_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="submit")
    await reply_rich(event, paragraph("ad_id со статусом ready_for_review:"), wizard=True)


@router.callback_query(F.data == "ads:ask_kill")
async def delete_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="delete")
    await reply_rich(
        event,
        paragraph("ad_id. Объявление должно быть неактивно ≥ 10 минут."),
        wizard=True,
    )


@router.callback_query(F.data == "ads:ask_budget")
async def budget_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.extra)
    await state.update_data(ad_action="budget_in")
    await reply_rich(
        event,
        heading("Бюджет объявления"),
        paragraph("Формат: `<ad_id> +<сумма>` или `<ad_id> -<сумма>` (например `12 +10.5`)."),
        wizard=True,
    )


@router.message(AdIdSG.extra)
async def budget_entered(message: Message, state: FSMContext, ads: AdsClient) -> None:
    text = (message.text or "").strip().replace(",", ".")
    data = await state.get_data()
    card_id = data.get("budget_ad_id")
    if card_id:
        try:
            amount = float(text)
        except ValueError:
            await reply_rich(message, quote("Сумма, например +10 или -3."), wizard=True)
            return
        await state.set_state(None)
        await state.update_data(budget_ad_id=None)
        await _budget(message, ads, state, int(card_id), abs(amount), increase=amount >= 0)
        return
    parts = text.split()
    if len(parts) != 2:
        await reply_rich(message, quote("Нужен формат: `12 +10` или `12 -3`."), wizard=True)
        return
    if not parts[0].isdigit():
        await reply_rich(message, quote("Первое слово — ad_id."), wizard=True)
        return
    try:
        amount = float(parts[1])
    except ValueError:
        await reply_rich(message, quote("Сумма должна быть числом."), wizard=True)
        return
    await state.set_state(None)
    await _budget(message, ads, state, int(parts[0]), abs(amount), increase=amount >= 0)


@router.callback_query(F.data.startswith("ads:budget:"))
async def budget_from_card(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await state.set_state(AdIdSG.extra)
    await state.update_data(budget_ad_id=ad_id)
    await reply_rich(
        event,
        heading("Бюджет"),
        paragraph("Напишите сумму: `+10` пополнить или `-3` вывести."),
        wizard=True,
    )


async def _budget(event, ads, state, ad_id: int, amount: float | None, *, increase: bool) -> None:
    if amount is None:
        await reply_rich(event, quote("Не указана сумма."), back="ads")
        return
    try:
        if increase:
            await ads.increase_ad_budget(
                ad_id=ad_id,
                amount=amount,
                account_id=await selected_account_id(state, ads),
                idempotency_key=ads.new_idempotency_key(),
            )
        else:
            await ads.decrease_ad_budget(
                ad_id=ad_id,
                amount=amount,
                account_id=await selected_account_id(state, ads),
                idempotency_key=ads.new_idempotency_key(),
            )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await _show_ad(event, ads, state, ad_id)


@router.callback_query(F.data == "ads:ask_ledger")
async def ad_tx_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="tx")
    await reply_rich(event, paragraph("ad_id для ленты транзакций:"), wizard=True)


async def _ad_tx(event, ads, state, ad_id: int, offset: int | None = None) -> None:
    from bot.formatters import ad_tx_rows

    try:
        listing = await ads.get_ad_transactions_list(
            ad_id=ad_id,
            account_id=await selected_account_id(state, ads),
            offset=offset,
            limit=30,
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = ad_tx_rows(listing.transactions)
    more = None
    if listing.next_offset not in (None, ""):
        more = button_row([("Далее", f"ads:txpage:{ad_id}:{listing.next_offset}", PRIMARY)])
    await reply_rich(
        event,
        heading(f"Транзакции #{ad_id}"),
        quote(f"Всего: {listing.total_count}"),
        table(["Когда", "Peer", "Сумма"], rows) if rows else paragraph("Пусто."),
        more,
        button_row(
            [
                ("К карточке", f"ads:view:{ad_id}", PRIMARY),
                ("К списку", "ads:list"),
                ("Меню", "nav:menu", LINK),
            ]
        ),
    )


@router.callback_query(F.data == "nav:create_ad")
@router.message(Command("create_ad"))
async def start_create_ad(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(CreateAdSG.placement)
    await state.update_data(draft={})
    await reply_rich(
        event,
        heading("Новое объявление"),
        paragraph("Какой таргет: юзеры, каналы, поиск или боты?"),
        button_row(
            [
                ("Юзеры", "cad:pl:video_banner", PRIMARY),
                ("Каналы", "cad:pl:channel_post", PRIMARY),
                ("Поиск", "cad:pl:search_result", PRIMARY),
                ("Боты", "cad:pl:bot_banner", PRIMARY),
            ]
        ),
        wizard=True,
    )


@router.callback_query(F.data.startswith("cad:pl:"))
async def create_placement_cb(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    placement = event.data.split(":")[2]
    await _set_placement(event, state, placement)


@router.message(CreateAdSG.placement)
async def create_placement_text(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    placement = PLACEMENT_ALIASES.get(value.lower(), value)
    if placement not in {item.value for item in AdPlacement}:
        await reply_rich(message, quote("Неизвестный таргет. Юзеры, каналы, поиск или боты."), wizard=True)
        return
    await _set_placement(message, state, placement)


async def _set_placement(event, state: FSMContext, placement: str) -> None:
    draft = (await state.get_data()).get("draft", {})
    draft["placement"] = placement
    await state.update_data(draft=draft)
    await _advance_create(event, state, None)


async def _advance_create(event: Event, state: FSMContext, current: str | None) -> None:
    draft = (await state.get_data()).get("draft", {})
    step = next_step(current, draft)
    if step == "confirm":
        await state.set_state(CreateAdSG.confirm)
        await reply_rich(
            event,
            heading("Подтверждение"),
            table(["Поле", "Значение"], draft_summary(draft)),
            paragraph("Проверьте данные и создайте объявление."),
            *confirm_blocks(),
        )
        return
    await state.set_state(STEP_STATE[step])
    await ask_create_step(event, step=step, draft=draft)


@router.message(CreateAdSG.target_payload)
async def create_target(message: Message, state: FSMContext) -> None:
    draft = (await state.get_data()).get("draft", {})
    try:
        target = parse_target(draft["placement"], message.text or "")
    except Exception as exc:  # noqa: BLE001
        await reply_rich(message, quote(f"Не удалось разобрать таргетинг: {exc}"), wizard=True)
        return
    draft["target"] = target.model_dump(exclude_none=True)
    await state.update_data(draft=draft)
    await _advance_create(message, state, "target_payload")


@router.message(CreateAdSG.title)
async def create_title(message: Message, state: FSMContext) -> None:
    draft = (await state.get_data()).get("draft", {})
    draft["title"] = (message.text or "").strip()
    await state.update_data(draft=draft)
    await _advance_create(message, state, "title")


@router.message(CreateAdSG.text)
@router.callback_query(CreateAdSG.text, F.data == CB_SKIP)
async def create_text(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    text = event_text(event)
    if text not in SKIP_TEXTS:
        draft["text"] = text
    await state.update_data(draft=draft)
    await _advance_create(event, state, "text")


@router.message(CreateAdSG.promote_url)
async def create_url(message: Message, state: FSMContext) -> None:
    draft = (await state.get_data()).get("draft", {})
    draft["promote_url"] = (message.text or "").strip()
    await state.update_data(draft=draft)
    await _advance_create(message, state, "promote_url")


@router.message(CreateAdSG.website)
@router.callback_query(CreateAdSG.website, F.data == CB_SKIP)
async def create_website(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    text = event_text(event)
    if text not in SKIP_TEXTS:
        bits = [item.strip() for item in text.split("|")]
        if bits:
            draft["website_name"] = bits[0]
        if len(bits) > 1:
            draft["button"] = bits[1]
        if len(bits) > 2:
            draft["conversion_event_id"] = bits[2]
    await state.update_data(draft=draft)
    await _advance_create(event, state, "website")


@router.message(CreateAdSG.cpm)
async def create_cpm(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        cpm = float(raw)
    except ValueError:
        await reply_rich(message, quote("Нужно число, например 1.5"), wizard=True)
        return
    draft = (await state.get_data()).get("draft", {})
    draft["cpm"] = cpm
    await state.update_data(draft=draft)
    await _advance_create(message, state, "cpm")


@router.message(CreateAdSG.budget)
@router.callback_query(CreateAdSG.budget, F.data == CB_SKIP)
async def create_budget(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    text = event_text(event).replace(",", ".")
    if text not in SKIP_TEXTS:
        if "/" in text:
            left, right = text.split("/", 1)
            draft["initial_budget"] = float(left.strip())
            draft["daily_budget_limit"] = float(right.strip())
        else:
            draft["initial_budget"] = float(text)
    await state.update_data(draft=draft)
    await _advance_create(event, state, "budget")


@router.message(CreateAdSG.media)
@router.callback_query(CreateAdSG.media, F.data == CB_SKIP)
async def create_media(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    text = event_text(event)
    if text not in SKIP_TEXTS:
        draft.update(parse_media(str(draft.get("placement") or ""), text))
    await state.update_data(draft=draft)
    await _advance_create(event, state, "media")


@router.message(CreateAdSG.userpic)
@router.callback_query(CreateAdSG.userpic, F.data == CB_SKIP)
@router.callback_query(CreateAdSG.userpic, F.data.startswith("cad:up:"))
async def create_userpic(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("cad:up:"):
        draft["show_userpic"] = event.data.endswith(":1")
    await state.update_data(draft=draft)
    await _advance_create(event, state, "userpic")


@router.message(CreateAdSG.freq)
@router.callback_query(CreateAdSG.freq, F.data == CB_SKIP)
@router.callback_query(CreateAdSG.freq, F.data.startswith("cad:fq:"))
async def create_freq(event: Event, state: FSMContext) -> None:
    await ack(event)
    draft = (await state.get_data()).get("draft", {})
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("cad:fq:"):
        draft["impression_frequency"] = int(event.data.split(":")[2])
    else:
        text = event_text(event)
        if text not in SKIP_TEXTS:
            draft["impression_frequency"] = int(text)
    await state.update_data(draft=draft)
    await _advance_create(event, state, "freq")


@router.message(CreateAdSG.confirm)
@router.callback_query(CreateAdSG.confirm, F.data == CB_YES)
async def create_confirm(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    text = event_text(event).lower()
    if text not in {"да", "yes", "ok", "y", "+"}:
        await state.set_state(None)
        await reply_rich(event, paragraph("Создание отменено."), back="ads")
        return
    draft = (await state.get_data()).get("draft", {})
    try:
        ad = await ads.create_ad(
            title=draft["title"],
            promote_url=draft["promote_url"],
            cpm=float(draft["cpm"]),
            target=draft["target"],
            placement=draft.get("placement"),
            account_id=await selected_account_id(state, ads),
            text=draft.get("text"),
            photo_id=draft.get("photo_id"),
            video_id=draft.get("video_id"),
            website_name=draft.get("website_name"),
            button=draft.get("button"),
            conversion_event_id=draft.get("conversion_event_id"),
            initial_budget=draft.get("initial_budget"),
            daily_budget_limit=draft.get("daily_budget_limit"),
            impression_frequency=draft.get("impression_frequency"),
            is_paused=draft.get("is_paused"),
            show_userpic=draft.get("show_userpic"),
            additional_info=draft.get("additional_info"),
            return_target=True,
            idempotency_key=ads.new_idempotency_key(),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await state.set_state(None)
    await _show_ad(event, ads, state, ad.ad_id)


@router.message(Command("edit_ad"))
@router.callback_query(F.data == "ads:ask_change")
async def edit_ad_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(AdIdSG.ad_id)
    await state.update_data(ad_action="edit")
    await reply_rich(event, paragraph("ad_id для редактирования:"), wizard=True)


@router.callback_query(EditAdSG.field, F.data.startswith("ead:f:"))
@router.message(EditAdSG.field)
async def edit_ad_field(event: Event, state: FSMContext) -> None:
    await ack(event)
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("ead:f:"):
        field = event.data.split(":", 2)[2]
    else:
        field = event_text(event)
    await state.update_data(edit_field=field)
    await state.set_state(EditAdSG.value)
    await reply_rich(event, paragraph(f"Новое значение {field}:"), wizard=True)


@router.message(EditAdSG.value)
async def edit_ad_value(message: Message, state: FSMContext, ads: AdsClient) -> None:
    data = await state.get_data()
    field = data.get("edit_field")
    raw = (message.text or "").strip()
    value: object = raw
    if field in {"cpm", "daily_budget_limit"}:
        value = float(raw.replace(",", "."))
    elif field in {"is_paused", "show_userpic"}:
        value = raw.lower() in {"1", "true", "да", "yes"}
    elif field in {"impression_frequency", "activate_date", "deactivate_date"}:
        value = int(raw)
    kwargs = {
        field: value,
        "ad_id": data["edit_ad_id"],
        "account_id": await selected_account_id(state, ads),
    }
    try:
        await ads.edit_ad(**kwargs)
    except (AdsApiError, AdsTransportError, ValueError) as exc:
        await show_error(message, exc if isinstance(exc, (AdsApiError, AdsTransportError)) else AdsApiError(str(exc)))
        return
    await state.set_state(None)
    await _show_ad(message, ads, state, data["edit_ad_id"])
