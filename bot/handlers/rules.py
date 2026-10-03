from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ads_api.client import AdsClient
from ads_api.errors import AdsApiError, AdsTransportError
from ads_api.models import Ad
from bot.keyboards import DANGER, LINK, PRIMARY, SUCCESS
from bot.rich import button_grid, button_row, heading, paragraph, quote, table
from bot.rules import (
    METRIC_CODE,
    METRIC_RU,
    METRICS,
    PRESETS,
    STATUS_RU,
    AdRule,
    RuleStore,
    format_metric,
    metric_value,
    rule_title,
)
from bot.states import RuleCreateSG
from bot.ui import Event, ack, reply_rich, selected_account_id

router = Router(name="rules")


def _peer(event: Event) -> tuple[int, int]:
    user = event.from_user
    user_id = user.id if user else 0
    if isinstance(event, Message):
        return event.chat.id, user_id
    chat = getattr(event.message, "chat", None)
    return (chat.id if chat else 0), user_id


def _ad_nav(ad_id: int):
    return button_row(
        [
            ("К карточке", f"ads:view:{ad_id}", PRIMARY),
            ("К списку", "ads:list"),
            ("Меню", "nav:menu", LINK),
        ]
    )


def _rule_rows(rules: list[AdRule], ads: dict[int, Ad] | None = None) -> list[list[str]]:
    rows: list[list[str]] = []
    for rule in rules:
        current = "—"
        ad = ads.get(rule.ad_id) if ads else None
        if ad is not None:
            cur = metric_value(ad, rule.metric)
            currency = str(ad.currency) if ad.currency else None
            current = (
                f"{format_metric(rule.metric, cur, currency)} / "
                f"{format_metric(rule.metric, rule.threshold, currency)}"
            )
        else:
            current = f"≥ {format_metric(rule.metric, rule.threshold)}"
        rows.append(
            [
                str(rule.id),
                f"#{rule.ad_id}",
                rule_title(rule),
                current,
                STATUS_RU.get(rule.status, rule.status),
            ]
        )
    return rows


async def _fetch_ads(ads: AdsClient, state: FSMContext, ad_ids: list[int]) -> dict[int, Ad]:
    unique = list(dict.fromkeys(ad_ids))
    if not unique:
        return {}
    try:
        items = await ads.get_ads_by_id(
            unique,
            account_id=await selected_account_id(state, ads),
            return_target=False,
        )
    except (AdsApiError, AdsTransportError):
        return {}
    return {item.ad_id: item for item in items}


@router.message(Command("rules"))
@router.callback_query(F.data == "rl:all")
async def list_all_rules(event: Event, ads: AdsClient, state: FSMContext, rules: RuleStore) -> None:
    await ack(event)
    items = await rules.list_all()
    fetched = await _fetch_ads(ads, state, [r.ad_id for r in items])
    buttons = [(f"#{r.id}", f"rl:v:{r.id}", PRIMARY) for r in items[:8]]
    await reply_rich(
        event,
        heading("Правила автостопа"),
        paragraph("Пауза по показам, кликам, действиям или тратам. Рядом с порогом проверка каждые 1–2 с."),
        table(["ID", "Ad", "Правило", "Сейчас", "Статус"], _rule_rows(items, fetched))
        if items
        else paragraph("Пока нет правил. Откройте объявление → Правила."),
        button_row(buttons) if buttons else None,
        button_row([("Объявления", "ads:list", PRIMARY), ("Меню", "nav:menu", LINK)]),
    )


@router.callback_query(F.data.startswith("ads:rules:"))
async def list_ad_rules(event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await _show_ad_rules(event, ads, state, rules, ad_id)


async def _show_ad_rules(
    event: Event, ads: AdsClient, state: FSMContext, store: RuleStore, ad_id: int
) -> None:
    items = await store.list_by_ad(ad_id)
    fetched = await _fetch_ads(ads, state, [ad_id])
    buttons = [(f"#{r.id}", f"rl:v:{r.id}", PRIMARY) for r in items[:8]]
    await reply_rich(
        event,
        heading(f"Правила #{ad_id}"),
        paragraph("Автостоп ставит объявление на паузу, когда метрика доходит до порога."),
        table(["ID", "Ad", "Правило", "Сейчас", "Статус"], _rule_rows(items, fetched))
        if items
        else paragraph("Для этого объявления правил нет."),
        button_row(buttons) if buttons else None,
        button_row([("Добавить", f"rl:new:{ad_id}", SUCCESS)]),
        _ad_nav(ad_id),
    )


@router.callback_query(F.data.startswith("rl:new:"))
async def new_rule(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    ad_id = int(event.data.split(":")[2])
    await state.set_state(None)
    await state.update_data(rule_ad_id=ad_id)
    await reply_rich(
        event,
        heading(f"Автостоп #{ad_id}"),
        paragraph("По какой метрике остановить объявление?"),
        button_row(
            [
                ("Показы", f"rl:m:{ad_id}:v", PRIMARY),
                ("Клики", f"rl:m:{ad_id}:c"),
                ("Действия", f"rl:m:{ad_id}:a"),
            ]
        ),
        button_row(
            [
                ("Траты", f"rl:m:{ad_id}:s"),
                ("Дневной расход", f"rl:m:{ad_id}:d"),
            ]
        ),
        wizard=True,
    )


@router.callback_query(F.data.startswith("rl:m:"))
async def pick_metric(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    _, _, ad_s, code = event.data.split(":", 3)
    ad_id = int(ad_s)
    metric = METRIC_CODE.get(code)
    if metric is None:
        await reply_rich(event, quote("Неизвестная метрика."), back="ads")
        return
    await state.update_data(rule_ad_id=ad_id, rule_metric=metric)
    presets = PRESETS[metric]
    items: list[tuple[str, str] | tuple[str, str, str]] = []
    for value in presets:
        label = format_metric(metric, value)
        items.append((label, f"rl:p:{ad_id}:{code}:{value}"))
    items.append(("Своё число", f"rl:x:{ad_id}:{code}", LINK))
    await reply_rich(
        event,
        heading(METRIC_RU[metric].capitalize()),
        paragraph("Порог. Для показов 1000 пауза сработает на 1000 или чуть выше — шаг проверки около порога 1–2 с."),
        *button_grid(items, per_row=4),
        wizard=True,
    )


@router.callback_query(F.data.startswith("rl:x:"))
async def custom_threshold(event: CallbackQuery, state: FSMContext) -> None:
    await ack(event)
    _, _, ad_s, code = event.data.split(":", 3)
    metric = METRIC_CODE[code]
    await state.set_state(RuleCreateSG.threshold)
    await state.update_data(rule_ad_id=int(ad_s), rule_metric=metric)
    unit = "TON (или валюта кабинета)" if metric in {"spent", "daily_spent"} else "целое число"
    await reply_rich(
        event,
        heading(METRIC_RU[metric].capitalize()),
        paragraph(f"Напишите порог — {unit}."),
        wizard=True,
    )


@router.message(RuleCreateSG.threshold)
async def custom_threshold_value(
    message: Message, state: FSMContext, ads: AdsClient, rules: RuleStore
) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        threshold = float(raw)
    except ValueError:
        await reply_rich(message, quote("Нужно число, например 1000."), wizard=True)
        return
    data = await state.get_data()
    await _save_rule(
        message,
        ads,
        state,
        rules,
        ad_id=int(data["rule_ad_id"]),
        metric=str(data["rule_metric"]),
        threshold=threshold,
    )


@router.callback_query(F.data.startswith("rl:p:"))
async def preset_threshold(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore
) -> None:
    await ack(event)
    parts = event.data.split(":", 4)
    ad_id = int(parts[2])
    metric = METRIC_CODE[parts[3]]
    threshold = float(parts[4])
    await _save_rule(event, ads, state, rules, ad_id=ad_id, metric=metric, threshold=threshold)


async def _save_rule(
    event: Event,
    ads: AdsClient,
    state: FSMContext,
    store: RuleStore,
    *,
    ad_id: int,
    metric: str,
    threshold: float,
) -> None:
    if metric not in METRICS or threshold <= 0:
        await reply_rich(event, quote("Порог должен быть больше нуля."), wizard=True)
        return
    chat_id, user_id = _peer(event)
    account_id = await selected_account_id(state, ads)
    rule = await store.add(
        ad_id=ad_id,
        account_id=account_id,
        chat_id=chat_id,
        user_id=user_id,
        metric=metric,
        threshold=threshold,
    )
    await state.set_state(None)
    await reply_rich(
        event,
        heading("Правило создано"),
        quote(f"#{rule.id}: {rule_title(rule)}. Объявление #{ad_id} уйдёт на паузу при достижении порога."),
        button_row(
            [
                ("Правила", f"ads:rules:{ad_id}", PRIMARY),
                ("К карточке", f"ads:view:{ad_id}"),
                ("Меню", "nav:menu", LINK),
            ]
        ),
    )


@router.callback_query(F.data.startswith("rl:v:"))
async def view_rule(event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore) -> None:
    await ack(event)
    rule = await rules.get(int(event.data.split(":")[2]))
    if rule is None:
        await reply_rich(event, quote("Правило не найдено."), back="ads")
        return
    await _show_rule(event, ads, state, rule)


async def _show_rule(event: Event, ads: AdsClient, state: FSMContext, rule: AdRule) -> None:
    fetched = await _fetch_ads(ads, state, [rule.ad_id])
    ad = fetched.get(rule.ad_id)
    currency = str(ad.currency) if ad and ad.currency else None
    current = format_metric(rule.metric, metric_value(ad, rule.metric), currency) if ad else "—"
    rows = [
        ["ID", str(rule.id)],
        ["Объявление", str(rule.ad_id)],
        ["Метрика", METRIC_RU.get(rule.metric, rule.metric)],
        ["Порог", format_metric(rule.metric, rule.threshold, currency)],
        ["Сейчас", current],
        ["Статус", STATUS_RU.get(rule.status, rule.status)],
        ["Действие", "пауза"],
    ]
    if rule.fired_value is not None:
        rows.append(["Сработало на", format_metric(rule.metric, rule.fired_value, currency)])
    actions: list[tuple[str, str] | tuple[str, str, str]] = []
    if rule.status == "active":
        actions.append(("Выключить", f"rl:off:{rule.id}"))
    else:
        actions.append(("Включить", f"rl:on:{rule.id}", SUCCESS))
    actions.append(("Удалить", f"rl:del:{rule.id}", DANGER))
    await reply_rich(
        event,
        heading(f"Правило #{rule.id}"),
        table(["Поле", "Значение"], rows),
        button_row(actions),
        button_row(
            [
                ("К правилам", f"ads:rules:{rule.ad_id}", PRIMARY),
                ("К карточке", f"ads:view:{rule.ad_id}"),
                ("Меню", "nav:menu", LINK),
            ]
        ),
    )


@router.callback_query(F.data.startswith("rl:off:"))
async def disable_rule(event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore) -> None:
    await ack(event)
    rule_id = int(event.data.split(":")[2])
    await rules.set_status(rule_id, "disabled")
    rule = await rules.get(rule_id)
    if rule is None:
        await reply_rich(event, quote("Правило не найдено."), back="ads")
        return
    await _show_rule(event, ads, state, rule)


@router.callback_query(F.data.startswith("rl:on:"))
async def enable_rule(event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore) -> None:
    await ack(event)
    rule_id = int(event.data.split(":")[2])
    await rules.set_status(rule_id, "active")
    rule = await rules.get(rule_id)
    if rule is None:
        await reply_rich(event, quote("Правило не найдено."), back="ads")
        return
    await _show_rule(event, ads, state, rule)


@router.callback_query(F.data.startswith("rl:del:"))
async def delete_rule_confirm(event: CallbackQuery, rules: RuleStore) -> None:
    await ack(event)
    rule = await rules.get(int(event.data.split(":")[2]))
    if rule is None:
        await reply_rich(event, quote("Правило не найдено."), back="ads")
        return
    await reply_rich(
        event,
        heading(f"Удалить правило #{rule.id}?"),
        quote(rule_title(rule)),
        button_row(
            [
                ("Удалить", f"rl:kill:{rule.id}", DANGER),
                ("Отмена", f"rl:v:{rule.id}"),
            ]
        ),
    )


@router.callback_query(F.data.startswith("rl:kill:"))
async def delete_rule_do(
    event: CallbackQuery, ads: AdsClient, state: FSMContext, rules: RuleStore
) -> None:
    await ack(event)
    rule = await rules.get(int(event.data.split(":")[2]))
    if rule is None:
        await reply_rich(event, quote("Уже удалено."), back="ads")
        return
    await rules.delete(rule.id)
    await _show_ad_rules(event, ads, state, rules, rule.ad_id)
