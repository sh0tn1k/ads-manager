"""Proposal cards: /proposals, /propose_test and ``prop:<action>:<proposal_id>`` callbacks.

The payload never travels in callback data: it is loaded from the agent store by id.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery

from ads_api.errors import AdsApiError, AdsTransportError
from agent.proposals.models import Interval, Proposal, ProposalStatus
from agent.proposals.service import DecisionResult
from agent.services import AgentServices
from bot.formatters import money
from bot.handlers.menu import show_error
from bot.keyboards import DANGER, LINK, PRIMARY, SUCCESS
from bot.rich import bullet_list, button_row, heading, paragraph, quote, table
from bot.ui import Event, ack, reply_rich

router = Router(name="proposals")

CALLBACK_RE = re.compile(r"^prop:(view|accept|reject|rc-creative|rc-geo|rc-price|rc-other):([0-9a-f-]{36})$")

STATUS_RU = {
    "proposed": "ожидает решения",
    "accepted": "принято, исполняется",
    "rejected": "отклонено",
    "expired": "истекло",
    "created": "исполнено",
    "failed": "ошибка",
    "active": "объявление активно",
    "declined": "отклонено модерацией",
    "finished": "завершено",
}

CODE_RU = {
    "ok": "Объявление создано.",
    "dry_run": "Тестовый режим (ADS_DRY_RUN=1): запрос в Ads API записан в журнал, объявление не создано.",
    "already_created": "Уже исполнено ранее, повтор не создаёт второе объявление.",
    "forbidden": "Нет прав принимать решения по предложениям. Добавьте свой id в BOT_ALLOWED_USER_IDS.",
    "not_found": "Предложение не найдено.",
    "expired": "Срок предложения истёк, резерв освобождён.",
    "payload_hash_mismatch": "Содержимое предложения изменилось после создания. Исполнение отменено.",
    "insufficient_balance": "Недостаточно свободного баланса. Пополните кабинет вручную — агент этого не делает.",
    "validation_failed": "Предложение не прошло проверку перед исполнением.",
    "user_not_allowed": "Пользователь не в списке разрешённых.",
    "live_mode_needs_first_live_max_budget": "Боевой режим требует FIRST_LIVE_MAX_BUDGET. Исполнение отменено.",
    "over_first_live_max_budget": "Бюджет больше FIRST_LIVE_MAX_BUDGET. Исполнение отменено.",
    "transport_error": "Сеть Ads API недоступна. Повтор будет выполнен автоматически с тем же ключом.",
    "balance_check_failed": "Не удалось проверить баланс. Повтор будет выполнен автоматически.",
    "rejected": "Предложение отклонено, резерв освобождён. Можно указать причину.",
    "reason_saved": "Причина сохранена.",
}

REASON_LABELS = (("Креатив", "creative"), ("Гео", "geo"), ("Дорого", "price"), ("Другое", "other"))


def code_text(code: str) -> str:
    if code.startswith("already_"):
        status = code.removeprefix("already_")
        return CODE_RU.get(code, f"Решение уже принято: {STATUS_RU.get(status, status)}.")
    return CODE_RU.get(code, f"Ads API: `{code}`")


def _ts(value: int | None) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%d.%m %H:%M UTC")


def _interval(value: Interval | None, *, percent: bool = False, cur: str = "") -> str:
    if value is None:
        return "—"
    if percent:
        return f"{value.p50 * 100:.2f}% ({value.p10 * 100:.2f}–{value.p90 * 100:.2f}%)"
    return f"{money(value.p50, cur)} ({money(value.p10, cur)}–{money(value.p90, cur)})"


def target_rows(target: dict) -> list[list[str]]:
    labels = {
        "type": "Тип",
        "country_codes": "Страны",
        "language_codes": "Языки",
        "topic_ids": "Темы",
        "exclude_topic_ids": "Искл. темы",
        "channel_ids": "Каналы",
        "exclude_channel_ids": "Искл. каналы",
        "search_queries": "Запросы",
        "device": "Устройство",
        "exclude_political_channels": "Без политики",
    }
    rows = []
    for key, value in target.items():
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "да" if value else "нет"
        rows.append([labels.get(key, key), str(value)])
    return rows


def proposal_card(proposal: Proposal, cur: str = "TON") -> list:
    payload = proposal.payload
    blocks: list = [heading("Тестовое предложение" if payload.is_test else "Предложение")]
    rows = [
        ["Статус", STATUS_RU.get(proposal.status.value, proposal.status.value)],
        ["Действие", payload.kind.value],
        ["Стадия", str(payload.stage or "—")],
        ["Истекает", _ts(proposal.expires_ts)],
    ]
    if payload.ad is not None:
        ad = payload.ad
        rows += [
            ["Размещение", ad.placement],
            ["Текст", ad.text or "— (поиск)"],
            ["Кнопка", ad.button or "—"],
            ["CPM", money(ad.cpm, cur)],
            ["Бюджет", money(ad.initial_budget, cur)],
            ["Макс. потеря", money(payload.cost.max_loss, cur)],
            ["Расписание", "задано" if ad.schedule else "всегда"],
            ["Ссылка", ad.promote_url],
        ]
    if payload.action is not None:
        rows.append(["Объявление", str(payload.action.ad_id)])
        if payload.action.amount:
            rows.append(["Сумма", money(payload.action.amount, cur)])
    blocks.append(table(["Поле", "Значение"], rows))
    if payload.ad is not None:
        blocks.append(table(["Таргет", "Значение"], target_rows(payload.ad.target)))
    pred = payload.predicted
    if pred is not None:
        blocks.append(
            table(
                ["Прогноз", "p50 (p10–p90)"],
                [
                    ["CTR", _interval(pred.ctr, percent=True)],
                    ["CVR", _interval(pred.cvr, percent=True)],
                    ["CPA", _interval(pred.cpa, cur=cur)],
                    ["P(CPA < порог)", "—" if pred.p_beats_hurdle is None else f"{pred.p_beats_hurdle * 100:.0f}%"],
                ],
            )
        )
    else:
        blocks.append(paragraph("Прогноза нет."))
    if payload.stop_rules is not None:
        sr = payload.stop_rules
        blocks.append(
            paragraph(
                f"Правила остановки: минимум показов {sr.min_views or '—'}, пауза при P < "
                f"{sr.kill_if_p_below if sr.kill_if_p_below is not None else '—'}, "
                f"повышение при P > {sr.promote_if_p_above if sr.promote_if_p_above is not None else '—'}."
            )
        )
    if payload.rationale_ru:
        blocks.append(quote(payload.rationale_ru))
    if payload.sources:
        blocks.append(bullet_list(payload.sources[:8]))
    if proposal.error:
        blocks.append(paragraph(f"Ошибка: {proposal.error}"))
    if proposal.created_ad_id:
        blocks.append(paragraph(f"Создано объявление #{proposal.created_ad_id}."))
    pid = proposal.proposal_id
    if proposal.status == ProposalStatus.PROPOSED:
        blocks.append(button_row([("Принять", f"prop:accept:{pid}", SUCCESS), ("Отклонить", f"prop:reject:{pid}", DANGER)]))
    elif proposal.status == ProposalStatus.REJECTED and not proposal.reject_reason_code:
        blocks.append(button_row([(label, f"prop:rc-{code}:{pid}") for label, code in REASON_LABELS]))
    blocks.append(button_row([("К списку", "nav:proposals", PRIMARY), ("Меню", "nav:menu", LINK)]))
    return blocks


def list_blocks(proposals: list[Proposal], cur: str = "TON") -> list:
    blocks: list = [heading("Предложения")]
    if not proposals:
        blocks.append(paragraph("Ожидающих предложений нет."))
    else:
        rows = []
        for i, p in enumerate(proposals, 1):
            ad = p.payload.ad
            what = ad.title if ad else p.kind.value
            rows.append([str(i), what, money(p.reserved_amount, cur), _ts(p.expires_ts)])
        blocks.append(table(["#", "Связка", "Резерв", "Истекает"], rows))
        blocks.append(button_row([(f"Открыть {i}", f"prop:view:{p.proposal_id}", PRIMARY) for i, p in enumerate(proposals, 1)][:8]))
    blocks.append(button_row([("Подобрать предложения", "agent:propose", SUCCESS), ("Тестовое предложение", "agent:test")]))
    blocks.append(button_row([("Бюджет", "agent:budget"), ("Меню", "nav:menu", LINK)]))
    return blocks


@router.message(Command("proposals"))
@router.callback_query(F.data == "nav:proposals")
async def cmd_proposals(event: Event, agent: AgentServices) -> None:
    await ack(event)
    agent.proposals.expire_due()
    await reply_rich(event, *list_blocks(agent.proposals.pending(), agent.config.currency))


@router.message(Command("propose_test"))
@router.callback_query(F.data == "agent:test")
async def cmd_propose_test(event: Event, agent: AgentServices) -> None:
    await ack(event)
    user_id = getattr(event.from_user, "id", None)
    try:
        results = await agent.proposals.inject_test_proposals(user_id)
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    errors = [e for r in results for e in r.errors]
    created = [r.proposal for r in results if r.proposal is not None]
    blocks = list_blocks(agent.proposals.pending(), agent.config.currency)
    summary = f"Создано тестовых предложений: {len(created)}."
    if errors:
        summary += " Не создано: " + "; ".join(code_text(e) if e in CODE_RU else e for e in errors)
    await reply_rich(event, blocks[0], quote(summary), *blocks[1:])


@router.message(Command("propose"))
@router.callback_query(F.data == "agent:propose")
async def cmd_propose(event: Event, agent: AgentServices) -> None:
    """Run the proposal cycle: at most MAX_PENDING_PROPOSALS proposals or "предложений нет" with a reason."""
    await ack(event, "Подбираю…")
    user_id = getattr(event.from_user, "id", None)
    try:
        result = await agent.cycle.run(user_id)
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    summary = result.message_ru()
    if result.errors:
        summary += " Ошибки: " + "; ".join(result.errors[:5])
    blocks = list_blocks(agent.proposals.pending(), agent.config.currency)
    await reply_rich(event, blocks[0], quote(summary), *blocks[1:])


async def _render_decision(event: Event, agent: AgentServices, result: DecisionResult) -> None:
    text = code_text(result.code)
    if result.proposal is None:
        await reply_rich(event, heading("Предложение"), quote(text), button_row([("К списку", "nav:proposals", PRIMARY)]))
        return
    card = proposal_card(result.proposal, agent.config.currency)
    await reply_rich(event, card[0], quote(text), *card[1:])


@router.callback_query(F.data.startswith("prop:"))
async def on_proposal_callback(event: CallbackQuery, agent: AgentServices) -> None:
    match = CALLBACK_RE.match(event.data or "")
    if match is None:
        await ack(event, "Неизвестная кнопка")
        return
    action, pid = match.groups()
    user_id = getattr(event.from_user, "id", None)
    if action == "view":
        await ack(event)
        proposal = agent.proposals.get(pid)
        if proposal is None:
            await _render_decision(event, agent, DecisionResult("not_found"))
            return
        await reply_rich(event, *proposal_card(proposal, agent.config.currency))
        return
    if not agent.proposals.is_allowed(user_id):
        await ack(event, "Нет доступа")
        await _render_decision(event, agent, DecisionResult("forbidden", agent.proposals.get(pid)))
        return
    if action == "accept":
        await ack(event, "Исполняю…")
        try:
            result = await agent.proposals.accept(pid, user_id)
        except (AdsApiError, AdsTransportError) as exc:
            await show_error(event, exc)
            return
    elif action == "reject":
        await ack(event)
        result = agent.proposals.reject(pid, user_id)
    else:
        await ack(event)
        result = agent.proposals.set_reject_reason(pid, user_id, action.removeprefix("rc-"))
    await _render_decision(event, agent, result)
