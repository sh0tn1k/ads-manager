from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import audience_rows
from bot.handlers.menu import show_error
from bot.keyboards import CB_SKIP, DANGER, SUCCESS, audience_card_blocks
from bot.rich import button_row, heading, paragraph, quote, table
from bot.states import SKIP_TEXTS, AudienceSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id

router = Router(name="audiences")


def _retargeting_off(exc: Exception) -> bool:
    return isinstance(exc, AdsApiError) and exc.error == "RETARGETING_DISABLED"


async def _show_retargeting_off(event: Event, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.set_state(None)
    await reply_rich(
        event,
        heading("Аудитории"),
        paragraph(
            "Ретаргетинг в этом кабинете выключен. Telegram не отдаёт и не даёт создавать "
            "списки по номерам телефонов, пока не включит опцию для аккаунта."
        ),
        paragraph(
            "Обычные объявления — юзеры, каналы, поиск, боты — работают без этого. "
            "Включить ретаргетинг можно только в ads.telegram.org или через поддержку Telegram Ads."
        ),
        back="menu",
    )


async def _fail(event: Event, exc: Exception, state: FSMContext | None = None) -> None:
    if _retargeting_off(exc):
        await _show_retargeting_off(event, state)
        return
    await show_error(event, exc)


@router.message(Command("audiences"))
@router.callback_query(F.data == "nav:audiences")
@router.callback_query(F.data == "au:list")
async def list_audiences(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        listing = await ads.get_audiences_list(account_id=await selected_account_id(state, ads), limit=50)
    except (AdsApiError, AdsTransportError) as exc:
        await _fail(event, exc, state)
        return
    rows = [[str(a.audience_id), a.title, str(a.size), str(a.ads_count)] for a in listing.audiences]
    buttons = [(f"#{a.audience_id}", f"au:v:{a.audience_id}", "primary") for a in listing.audiences[:8]]
    await reply_rich(
        event,
        heading("Аудитории"),
        quote(f"Всего: {listing.total_count}"),
        table(["ID", "Название", "Размер", "Ads"], rows) if rows else paragraph("Нет аудиторий."),
        button_row(buttons) if buttons else None,
        button_row([("Создать", "au:create", SUCCESS)]),
        back="menu",
    )


@router.callback_query(F.data.startswith("au:v:"))
async def view_audience_cb(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    audience_id = int(event.data.split(":")[2])
    try:
        items = await ads.get_audiences_by_id(
            [audience_id], account_id=await selected_account_id(state, ads)
        )
        item = items[0]
    except (AdsApiError, AdsTransportError, IndexError) as exc:
        await _fail(
            event,
            exc if not isinstance(exc, IndexError) else AdsApiError("NOT_FOUND"),
            state,
        )
        return
    await reply_rich(
        event,
        heading(f"Аудитория #{item.audience_id}"),
        table(["Поле", "Значение"], audience_rows(item)),
        *audience_card_blocks(audience_id=item.audience_id, ads_count=item.ads_count or 0),
    )


@router.callback_query(F.data == "au:create")
async def create_aud_start(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    try:
        await ads.get_audiences_list(
            account_id=await selected_account_id(state, ads), limit=1
        )
    except (AdsApiError, AdsTransportError) as exc:
        await _fail(event, exc, state)
        return
    await state.set_state(AudienceSG.title)
    await state.update_data(aud_action="create")
    await reply_rich(event, heading("Новая аудитория"), paragraph("Название (1–64):"), wizard=True)


@router.message(AudienceSG.title)
async def create_aud_title(message: Message, state: FSMContext) -> None:
    await state.update_data(aud_title=(message.text or "").strip())
    await state.set_state(AudienceSG.phones)
    await reply_rich(
        message,
        paragraph("Телефоны или SHA256, по одному на строку (до 10000). Можно пропустить."),
        wizard=True,
        skip=True,
    )


@router.message(AudienceSG.phones)
@router.callback_query(AudienceSG.phones, F.data == CB_SKIP)
async def create_aud_phones(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    data = await state.get_data()
    text = event_text(event)
    phones = None if text.strip() in SKIP_TEXTS else [p.strip() for p in text.splitlines() if p.strip()]
    try:
        item = await ads.create_audience(
            title=data["aud_title"],
            add_phones=phones,
            account_id=await selected_account_id(state, ads),
            idempotency_key=ads.new_idempotency_key(),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await _fail(event, exc, state)
        return
    await state.set_state(None)
    await reply_rich(
        event,
        heading("Аудитория создана"),
        table(["Поле", "Значение"], audience_rows(item)),
        *audience_card_blocks(audience_id=item.audience_id, ads_count=item.ads_count or 0),
    )


@router.callback_query(F.data.startswith("au:e:"))
async def edit_from_card(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    audience_id = int(event.data.split(":")[2])
    await state.set_state(AudienceSG.audience_id)
    await state.update_data(aud_action="edit_card", edit_audience_id=audience_id)
    await reply_rich(
        event,
        heading(f"Изменить #{audience_id}"),
        paragraph("Новое название одной строкой. Со следующей строки можно добавить телефоны. `reset` в первой строке очищает список."),
        wizard=True,
    )


@router.callback_query(F.data.startswith("au:del:"))
async def delete_from_card(event: CallbackQuery) -> None:
    await ack(event)
    audience_id = int(event.data.split(":")[2])
    await reply_rich(
        event,
        heading(f"Удалить #{audience_id}?"),
        paragraph("Нельзя удалить аудиторию, которая используется в объявлениях."),
        button_row(
            [
                ("Удалить", f"au:kill:{audience_id}", DANGER),
                ("Отмена", f"au:v:{audience_id}"),
            ]
        ),
        back="audiences",
    )


@router.callback_query(F.data.startswith("au:kill:"))
async def delete_audience_do(event: CallbackQuery, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    audience_id = int(event.data.split(":")[2])
    try:
        ok = await ads.delete_audience(
            audience_id=audience_id, account_id=await selected_account_id(state, ads)
        )
    except (AdsApiError, AdsTransportError) as exc:
        await _fail(event, exc, state)
        return
    await reply_rich(event, heading("Удаление"), quote("Готово" if ok else "false"), back="audiences")


@router.message(AudienceSG.audience_id)
async def edit_aud_value(message: Message, state: FSMContext, ads: AdsClient) -> None:
    data = await state.get_data()
    audience_id = data.get("edit_audience_id")
    text = (message.text or "").strip()
    if not audience_id:
        await reply_rich(message, quote("Сначала откройте аудиторию из списка."), back="audiences")
        await state.set_state(None)
        return
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    tokens = first.lower().split()
    reset = "reset" in tokens
    title = None
    if first and not reset:
        title = first.split("=", 1)[1] if first.startswith("title=") else first
    phones = [p.strip() for p in lines[1:] if p.strip()] or None
    try:
        item = await ads.edit_audience(
            audience_id=int(audience_id),
            title=title,
            add_phones=phones,
            reset_phones=True if reset else None,
            account_id=await selected_account_id(state, ads),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await _fail(message, exc, state)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Сохранено"),
        table(["Поле", "Значение"], audience_rows(item)),
        *audience_card_blocks(audience_id=item.audience_id, ads_count=item.ads_count or 0),
    )
