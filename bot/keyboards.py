"""In-message button rows only. No ReplyKeyboardMarkup and no InlineKeyboardMarkup."""

from __future__ import annotations

from bot.rich import InputRichBlockButtons, button_grid, button_row

CB_SKIP = "wiz:skip"
CB_CANCEL = "wiz:cancel"
CB_BACK = "wiz:back"
CB_YES = "cad:ok"
CB_NO = "cad:no"

PRIMARY = "primary"
SUCCESS = "success"
DANGER = "danger"
LINK = "link"

EDIT_ACCOUNT_FIELDS = (
    "title",
    "full_name",
    "email",
    "phone_number",
    "country",
    "city",
    "legal_name",
)

EDIT_AD_FIELDS = (
    "title",
    "text",
    "promote_url",
    "cpm",
    "is_paused",
    "daily_budget_limit",
    "website_name",
    "button",
    "impression_frequency",
    "show_userpic",
    "photo_id",
    "video_id",
    "additional_info",
    "activate_date",
    "deactivate_date",
)


def menu_row() -> InputRichBlockButtons:
    return button_row([("Меню", "nav:menu", LINK)])


def nav_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Объявления", "nav:ads", PRIMARY),
                ("Создать", "nav:create_ad", SUCCESS),
                ("Аккаунт", "nav:account"),
                ("Биллинг", "nav:billing"),
            ]
        ),
        button_row(
            [
                ("Аудитории", "nav:audiences"),
                ("Пиксель", "nav:pixel"),
                ("Статистика", "nav:stats"),
                ("Таргетинг", "nav:targeting"),
            ]
        ),
        button_row(
            [
                ("Медиа", "nav:media"),
                ("Сохранённые", "nav:saves"),
                ("A/B", "nav:ab"),
                ("Помощь", "nav:help", LINK),
            ]
        ),
        button_row(
            [
                ("Предложения", "nav:proposals", PRIMARY),
                ("Бюджет агента", "agent:budget"),
            ]
        ),
    ]


def wizard_blocks(*, skip: bool = False) -> list[InputRichBlockButtons]:
    items: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Отмена", CB_CANCEL, DANGER),
        ("Назад", CB_BACK),
    ]
    if skip:
        items.append(("Пропустить", CB_SKIP, LINK))
    items.append(("Меню", "nav:menu", LINK))
    return [button_row(items)]


def ads_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Список", "ads:list", PRIMARY),
                ("Создать", "nav:create_ad", SUCCESS),
                ("Найти", "ads:find"),
                ("Сохранённые", "nav:saves"),
                ("A/B", "ab:hub"),
            ]
        ),
        menu_row(),
    ]


def account_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Текущий", "acc:cur", PRIMARY),
                ("Реквизиты", "acc:info"),
                ("Связанные", "acc:rel"),
                ("Сменить", "acc:pick"),
            ]
        ),
        button_row(
            [
                ("Создать", "acc:create", SUCCESS),
                ("Изменить", "acc:edit"),
                ("Меню", "nav:menu", LINK),
            ]
        ),
    ]


def billing_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Пополнить", "bill:in", SUCCESS),
                ("Вывести", "bill:out", DANGER),
                ("Статус", "bill:status"),
                ("Лента", "bill:ledger"),
            ]
        ),
        menu_row(),
    ]


def audiences_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Список", "au:list", PRIMARY),
                ("Создать", "au:create", SUCCESS),
            ]
        ),
        menu_row(),
    ]


def pixel_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Показать", "px:show", PRIMARY),
                ("Создать", "px:create", SUCCESS),
                ("События", "px:events"),
                ("Новое событие", "px:new"),
            ]
        ),
        menu_row(),
    ]


def stats_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Аккаунт", "st:account", PRIMARY),
                ("Объявление", "st:ad"),
                ("Месяц", "st:month"),
            ]
        ),
        menu_row(),
    ]


def targeting_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Языки", "tg:lang"),
                ("Темы", "tg:topics"),
                ("Страны", "tg:countries"),
            ]
        ),
        button_row(
            [
                ("Локации", "tg:loc"),
                ("Канал", "tg:ch"),
                ("Бот", "tg:bot"),
            ]
        ),
        menu_row(),
    ]


def media_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Фото объявления", "md:photo", PRIMARY),
                ("Видео", "md:video"),
                ("Фото сайта", "md:site"),
            ]
        ),
        menu_row(),
    ]


def back_blocks(name: str) -> list[InputRichBlockButtons]:
    """Compact footer for cards and results — not the full section hub."""
    homes = {
        "menu": None,
        "ads": ("К списку", "ads:list"),
        "account": ("Аккаунт", "nav:account"),
        "billing": ("Биллинг", "nav:billing"),
        "audiences": ("К списку", "au:list"),
        "pixel": ("Пиксель", "nav:pixel"),
        "stats": ("Статистика", "nav:stats"),
        "targeting": ("Таргетинг", "nav:targeting"),
        "media": ("Медиа", "nav:media"),
        "saves": ("Сохранённые", "sv:hub"),
        "ab": ("A/B", "ab:hub"),
    }
    home = homes.get(name)
    items: list[tuple[str, str] | tuple[str, str, str]] = []
    if home:
        items.append((home[0], home[1], PRIMARY))
    items.append(("Меню", "nav:menu", LINK))
    return [button_row(items)]


def _status_key(status: str | None) -> str:
    return str(status or "").rsplit(".", 1)[-1]


def ad_card_blocks(*, ad_id: int, status: str | None, is_paused: bool) -> list[InputRichBlockButtons]:
    """Only actions that make sense for this ad status."""
    status = _status_key(status)
    actions: list[tuple[str, str] | tuple[str, str, str]] = []
    if status == "active" and not is_paused:
        actions.append(("Пауза", f"ads:pause:{ad_id}"))
    if status == "on_hold" or (is_paused and status == "active"):
        actions.append(("Старт", f"ads:resume:{ad_id}", SUCCESS))
    if status == "ready_for_review":
        actions.append(("На модерацию", f"ads:review:{ad_id}", PRIMARY))
    if status not in {"in_review", "stopped"}:
        actions.append(("Править", f"ads:change:{ad_id}"))
    if status not in {"stopped", "declined"}:
        actions.append(("Бюджет", f"ads:budget:{ad_id}"))
    actions.append(("Лента", f"ads:ledger:{ad_id}"))
    actions.append(("Правила", f"ads:rules:{ad_id}"))
    actions.append(("JSON", f"ads:json:{ad_id}", LINK))
    actions.append(("Сохранить", f"sv:ad:{ad_id}", SUCCESS))
    actions.append(("A/B", f"ab:ad:{ad_id}"))
    if status in {"stopped", "declined", "ready_for_review", "on_hold"}:
        actions.append(("Удалить", f"ads:kill:{ad_id}", DANGER))
    rows = button_grid(actions, per_row=4) if actions else []
    rows.extend(back_blocks("ads"))
    return rows


def audience_card_blocks(*, audience_id: int, ads_count: int = 0) -> list[InputRichBlockButtons]:
    actions: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Изменить", f"au:e:{audience_id}"),
    ]
    if ads_count <= 0:
        actions.append(("Удалить", f"au:del:{audience_id}", DANGER))
    return [button_row(actions), *back_blocks("audiences")]


def pixel_event_card_blocks(
    *, event_id: str, auto_created: bool = False, ads_count: int = 0
) -> list[InputRichBlockButtons]:
    actions: list[tuple[str, str] | tuple[str, str, str]] = [
        ("Переименовать", f"px:ren:{event_id}"),
    ]
    if not auto_created and ads_count <= 0:
        actions.append(("Удалить", f"px:del:{event_id}", DANGER))
    return [button_row(actions), *back_blocks("pixel")]


def confirm_blocks() -> list[InputRichBlockButtons]:
    return [
        button_row(
            [
                ("Создать", CB_YES, SUCCESS),
                ("Отмена", CB_CANCEL, DANGER),
            ]
        )
    ]


def edit_account_field_blocks() -> list[InputRichBlockButtons]:
    return button_grid([(field, f"eacc:{field}") for field in EDIT_ACCOUNT_FIELDS], per_row=4)


def edit_ad_field_blocks() -> list[InputRichBlockButtons]:
    return button_grid([(field, f"ead:f:{field}") for field in EDIT_AD_FIELDS], per_row=4)


SECTION_BLOCKS = {
    "menu": nav_blocks,
    "ads": ads_blocks,
    "account": account_blocks,
    "billing": billing_blocks,
    "audiences": audiences_blocks,
    "pixel": pixel_blocks,
    "stats": stats_blocks,
    "targeting": targeting_blocks,
    "media": media_blocks,
}


def section_blocks(name: str) -> list[InputRichBlockButtons]:
    builder = SECTION_BLOCKS.get(name)
    if builder is None:
        return []
    return builder()
