from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from bot.formatters import account_tx_rows, transaction_status_rows
from bot.handlers.menu import show_error
from bot.keyboards import PRIMARY
from bot.rich import button_row, heading, paragraph, quote, table
from bot.states import BudgetTransferSG, TxStatusSG
from bot.ui import Event, ack, reply_rich, selected_account_id

router = Router(name="billing")


@router.callback_query(F.data == "bill:in")
async def increase_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(BudgetTransferSG.account_id)
    await state.update_data(tx_dir="in")
    await reply_rich(
        event,
        heading("Пополнить связанный аккаунт"),
        paragraph("Формат: `<account_id> <сумма>`"),
        wizard=True,
    )


@router.callback_query(F.data == "bill:out")
async def decrease_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(BudgetTransferSG.account_id)
    await state.update_data(tx_dir="out")
    await reply_rich(
        event,
        heading("Вывести со связанного"),
        paragraph("Формат: `<account_id> <сумма>`"),
        wizard=True,
    )


@router.message(BudgetTransferSG.account_id)
async def transfer_run(message: Message, state: FSMContext, ads: AdsClient) -> None:
    parts = (message.text or "").replace(",", ".").split()
    if len(parts) != 2:
        await reply_rich(message, quote("Нужен формат: id сумма"), wizard=True)
        return
    account_id, raw_amount = parts
    try:
        amount = float(raw_amount)
    except ValueError:
        await reply_rich(message, quote("Сумма — число."), wizard=True)
        return
    direction = (await state.get_data()).get("tx_dir", "in")
    try:
        if direction == "in":
            status = await ads.increase_account_budget(
                account_id=account_id, amount=amount, idempotency_key=ads.new_idempotency_key()
            )
        else:
            status = await ads.decrease_account_budget(
                account_id=account_id, amount=amount, idempotency_key=ads.new_idempotency_key()
            )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Перевод"),
        table(["Поле", "Значение"], transaction_status_rows(status)),
        back="billing",
    )


@router.callback_query(F.data == "bill:status")
async def tx_status_start(event: Event, state: FSMContext) -> None:
    await ack(event)
    await state.set_state(TxStatusSG.transaction_id)
    await reply_rich(event, paragraph("transaction_id:"), wizard=True)


@router.message(TxStatusSG.transaction_id)
async def tx_status_run(message: Message, state: FSMContext, ads: AdsClient) -> None:
    try:
        status = await ads.get_transaction_status((message.text or "").strip())
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(message, exc)
        return
    await state.set_state(None)
    await reply_rich(
        message,
        heading("Статус перевода"),
        table(["Поле", "Значение"], transaction_status_rows(status)),
        back="billing",
    )


def _callback_offset(data: str | None, prefix: str) -> int | str | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    if not raw:
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    return raw


@router.callback_query(F.data == "bill:ledger")
@router.callback_query(F.data.startswith("bill:more:"))
async def account_ledger(event: Event, ads: AdsClient, state: FSMContext) -> None:
    await ack(event)
    offset = None
    if isinstance(event, CallbackQuery):
        offset = _callback_offset(event.data, "bill:more:")
    try:
        listing = await ads.get_account_transactions_list(
            account_id=await selected_account_id(state, ads),
            offset=offset,
            limit=30,
        )
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    rows = account_tx_rows(listing.transactions)
    more = None
    if listing.next_offset not in (None, ""):
        more = button_row([("Далее", f"bill:more:{listing.next_offset}", PRIMARY)])
    await reply_rich(
        event,
        heading("Лента аккаунта"),
        quote(f"Всего: {listing.total_count}"),
        table(["Когда", "Peer", "Сумма"], rows) if rows else paragraph("Пусто."),
        more,
        back="billing",
    )
