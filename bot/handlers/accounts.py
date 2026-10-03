from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import account_info_rows, account_rows
from bot.handlers.menu import show_error
from bot.keyboards import CB_SKIP, EDIT_ACCOUNT_FIELDS, edit_account_field_blocks
from bot.rich import copy_row, heading, paragraph, quote, table
from bot.states import CreateAccountSG, EditAccountSG, SKIP_TEXTS, SelectAccountSG
from bot.ui import Event, ack, event_text, reply_rich, selected_account_id

router = Router(name="accounts")


@router.message(Command("account"))
@router.callback_query(F.data == "nav:account")
@router.callback_query(F.data == "acc:cur")
async def show_current(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        selected = await selected_account_id(state, ads)
        if selected:
            accounts = await ads.get_accounts_by_id([selected])
            account = accounts[0] if accounts else await ads.get_current_account()
        else:
            account = await ads.get_current_account()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(
        event,
        heading("Аккаунт"),
        table(["Поле", "Значение"], account_rows(account)),
        section="account",
    )


@router.callback_query(F.data == "acc:info")
async def show_info(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    try:
        info = await ads.get_account_info(account_id=await selected_account_id(state, ads))
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(
        event,
        heading("Реквизиты"),
        table(["Поле", "Значение"], account_info_rows(info)),
        back="account",
    )


@router.callback_query(F.data == "acc:rel")
async def show_related(event: Event, ads: AdsClient) -> None:
    await ack(event)
    try:
        listing = await ads.get_related_accounts_list()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = [
        [
            item.account_id[:12] + "…",
            item.title,
            f"{item.remaining_budget:.2f} {item.currency}",
        ]
        for item in listing.accounts[:30]
    ]
    await reply_rich(
        event,
        heading("Связанные аккаунты"),
        quote(f"Всего: {listing.total_count}"),
        table(["ID", "Название", "Остаток"], rows) if rows else paragraph("Нет связанных аккаунтов."),
        back="account",
    )


@router.callback_query(F.data == "acc:pick")
async def pick_account_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(SelectAccountSG.account_id)
    await reply_rich(
        event,
        heading("Выбор аккаунта"),
        paragraph("Пришлите account_id или нажмите «Пропустить», чтобы вернуться к аккаунту токена."),
        wizard=True,
        skip=True,
    )


@router.message(SelectAccountSG.account_id)
@router.callback_query(SelectAccountSG.account_id, F.data == CB_SKIP)
async def pick_account_value(event: Event, state: FSMContext) -> None:
    await ack(event)
    text = event_text(event)
    if text in SKIP_TEXTS or text == "-":
        data = await state.get_data()
        data.pop("account_id", None)
        await state.set_data(data)
        await state.set_state(None)
        await reply_rich(event, paragraph("Используется аккаунт токена."), back="account")
        return
    await state.update_data(account_id=text)
    await state.set_state(None)
    await reply_rich(
        event,
        paragraph(["Рабочий аккаунт: ", {"type": "code", "text": text}]),
        back="account",
    )


@router.callback_query(F.data == "acc:create")
async def create_account_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(CreateAccountSG.title)
    await state.update_data(new_account={})
    await reply_rich(
        event,
        heading("Новый связанный аккаунт"),
        paragraph("Название (1–128 символов). Только с главного кабинета."),
        wizard=True,
    )


@router.message(CreateAccountSG.title)
async def create_account_title(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_title=(message.text or "").strip())
    await state.set_state(CreateAccountSG.full_name)
    await reply_rich(message, paragraph("Полное имя компании (1–256)."), wizard=True)


@router.message(CreateAccountSG.full_name)
async def create_account_full_name(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_full_name=(message.text or "").strip())
    await state.set_state(CreateAccountSG.email)
    await reply_rich(message, paragraph("Email компании (1–64)."), wizard=True)


@router.message(CreateAccountSG.email)
async def create_account_email(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_email=(message.text or "").strip())
    await state.set_state(CreateAccountSG.phone)
    await reply_rich(message, paragraph("Телефон компании."), wizard=True)


@router.message(CreateAccountSG.phone)
async def create_account_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_phone=(message.text or "").strip())
    await state.set_state(CreateAccountSG.country)
    await reply_rich(message, paragraph("Страна (1–128)."), wizard=True)


@router.message(CreateAccountSG.country)
async def create_account_country(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_country=(message.text or "").strip())
    await state.set_state(CreateAccountSG.city)
    await reply_rich(message, paragraph("Город (1–128)."), wizard=True)


@router.message(CreateAccountSG.city)
async def create_account_city(message: Message, state: FSMContext) -> None:
    await state.update_data(new_account_city=(message.text or "").strip())
    await state.set_state(CreateAccountSG.legal_name)
    await reply_rich(
        message,
        paragraph("Юридическое имя (можно пропустить)."),
        wizard=True,
        skip=True,
    )


@router.message(CreateAccountSG.legal_name)
@router.callback_query(CreateAccountSG.legal_name, F.data == CB_SKIP)
async def create_account_finish(event: Event, state: FSMContext, ads: AdsClient) -> None:
    await ack(event)
    legal = event_text(event)
    if legal in SKIP_TEXTS:
        legal = ""
    data = await state.get_data()
    try:
        info = await ads.create_account(
            title=data["new_account_title"],
            full_name=data["new_account_full_name"],
            email=data["new_account_email"],
            phone_number=data["new_account_phone"],
            country=data["new_account_country"],
            city=data["new_account_city"],
            legal_name=legal or None,
            idempotency_key=ads.new_idempotency_key(),
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await state.set_state(None)
    await reply_rich(
        event,
        heading("Аккаунт создан"),
        table(["Поле", "Значение"], account_info_rows(info)),
        copy_row("Скопировать ID", info.account_id),
        back="account",
    )


@router.callback_query(F.data == "acc:edit")
async def edit_account_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(EditAccountSG.field)
    await reply_rich(
        event,
        heading("Изменить реквизиты"),
        paragraph("Выберите поле кнопкой или напишите имя."),
        *edit_account_field_blocks(),
        wizard=True,
    )


@router.callback_query(EditAccountSG.field, F.data.startswith("eacc:"))
@router.message(EditAccountSG.field)
async def edit_account_field(event: Event, state: FSMContext) -> None:
    await ack(event)
    if isinstance(event, CallbackQuery) and event.data and event.data.startswith("eacc:"):
        field = event.data.split(":", 1)[1]
    else:
        field = event_text(event)
    allowed = set(EDIT_ACCOUNT_FIELDS)
    if field not in allowed:
        await reply_rich(
            event,
            quote("Неизвестное поле. Выберите кнопку или одно из: " + ", ".join(sorted(allowed))),
            *edit_account_field_blocks(),
            wizard=True,
        )
        return
    await state.update_data(edit_field=field)
    await state.set_state(EditAccountSG.value)
    await reply_rich(event, paragraph(f"Новое значение для {field}:"), wizard=True)


@router.message(EditAccountSG.value)
async def edit_account_value(message: Message, state: FSMContext, ads: AdsClient) -> None:
    data = await state.get_data()
    field = data.get("edit_field")
    value = (message.text or "").strip()
    kwargs = {field: value, "account_id": await selected_account_id(state, ads)}
    try:
        info = await ads.edit_account_info(**kwargs)
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Сохранено"),
        table(["Поле", "Значение"], account_info_rows(info)),
        back="account",
    )
