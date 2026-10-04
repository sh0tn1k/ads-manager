"""/budget: account balance, live ad budgets, proposal reservations and free balance."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command

from ads_api.errors import AdsApiError, AdsTransportError
from agent.planner.balance import BalanceView
from agent.services import AgentServices
from bot.formatters import money
from bot.handlers.menu import show_error
from bot.keyboards import LINK, PRIMARY
from bot.rich import button_row, heading, paragraph, quote, table
from bot.ui import Event, ack, reply_rich

router = Router(name="budget")

SEMANTICS_RU = {
    "includes_ads": "остаток кабинета включает бюджеты объявлений (консервативно)",
    "excludes_ads": "остаток кабинета уже без бюджетов объявлений",
}


def budget_rows(view: BalanceView) -> list[list[str]]:
    cur = view.currency
    return [
        ["Остаток кабинета", money(view.account_remaining, cur)],
        ["В объявлениях (ads_budget)", money(view.account_ads_budget, cur)],
        ["Остатки живых объявлений", money(view.live_ads_remaining, cur)],
        ["Резервы предложений", money(view.reserved, cur)],
        ["Свободно для предложений", money(view.free, cur)],
        ["Потрачено всего", money(view.account_spent, cur)],
    ]


def budget_blocks(view: BalanceView, *, dry_run: bool, synced: str | None = None):
    mode = "тестовый (ADS_DRY_RUN=1): объявления не создаются" if dry_run else "боевой"
    return [
        heading("Бюджет агента"),
        table(["Поле", "Значение"], budget_rows(view)),
        paragraph(f"Формула: {SEMANTICS_RU.get(view.semantics, view.semantics)}."),
        quote("Семантика полей баланса не подтверждена — см. docs/agent-recon.md, раздел 2."),
        paragraph(f"Режим: {mode}."),
        paragraph(synced) if synced else None,
        button_row([("Синхронизировать", "agent:sync", PRIMARY), ("Меню", "nav:menu", LINK)]),
    ]


@router.message(Command("budget"))
@router.callback_query(F.data == "agent:budget")
async def cmd_budget(event: Event, agent: AgentServices) -> None:
    await ack(event)
    try:
        view = await agent.balance.current()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    await reply_rich(event, *budget_blocks(view, dry_run=agent.config.dry_run))


@router.callback_query(F.data == "agent:sync")
async def cb_sync(event: Event, agent: AgentServices) -> None:
    await ack(event, "Синхронизация…")
    try:
        result = await agent.sync.full()
        view = await agent.balance.current()
    except (AdsApiError, AdsTransportError) as exc:
        await show_error(event, exc)
        return
    note = (
        f"Синхронизировано: объявлений {result.ads_seen}, новых снимков {result.ad_snapshots_added}, "
        f"строк статистики {result.stats_rows}."
    )
    await reply_rich(event, *budget_blocks(view, dry_run=agent.config.dry_run, synced=note))
