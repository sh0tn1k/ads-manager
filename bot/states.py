from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CreateAccountSG(StatesGroup):
    title = State()
    full_name = State()
    email = State()
    phone = State()
    country = State()
    city = State()
    legal_name = State()


class EditAccountSG(StatesGroup):
    field = State()
    value = State()


class BudgetTransferSG(StatesGroup):
    direction = State()
    account_id = State()
    amount = State()


class TxStatusSG(StatesGroup):
    transaction_id = State()


class CreateAdSG(StatesGroup):
    placement = State()
    target_payload = State()
    title = State()
    text = State()
    promote_url = State()
    website = State()
    cpm = State()
    budget = State()
    media = State()
    userpic = State()
    freq = State()
    confirm = State()


class EditAdSG(StatesGroup):
    ad_id = State()
    field = State()
    value = State()


class AdIdSG(StatesGroup):
    """Flows that only need an ad id (pause, submit, stats, delete, budget)."""

    action = State()
    ad_id = State()
    extra = State()


class UploadMediaSG(StatesGroup):
    kind = State()
    file = State()


class AudienceSG(StatesGroup):
    action = State()
    title = State()
    phones = State()
    audience_id = State()


class PixelEventSG(StatesGroup):
    action = State()
    title = State()
    event_type = State()
    event_id = State()


class TargetingSG(StatesGroup):
    action = State()
    query = State()


class ReportSG(StatesGroup):
    kind = State()
    ad_id = State()
    year_month = State()
    period = State()


class SelectAccountSG(StatesGroup):
    account_id = State()


class RuleCreateSG(StatesGroup):
    threshold = State()


class SaveGroupSG(StatesGroup):
    title = State()


class AbNoteSG(StatesGroup):
    note = State()


CANCEL_TEXTS = {"❌ Отмена", "Отмена", "/cancel", "cancel"}
BACK_TEXTS = {"⬅️ Назад", "Назад", "/back", "back"}
SKIP_TEXTS = {"Пропустить", "/skip", "-", "—"}
