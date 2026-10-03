"""Placement-aware create-ad wizard. Not part of the Ads API client."""

from __future__ import annotations

from ads_api.models import (
    InputAdTargetBots,
    InputAdTargetChannels,
    InputAdTargetSearch,
    InputAdTargetUsers,
)
from bot.keyboards import PRIMARY, SUCCESS
from bot.rich import button_row, heading, paragraph
from bot.states import CreateAdSG
from bot.ui import Event, reply_rich

STEP_ORDER = (
    "target_payload",
    "title",
    "text",
    "promote_url",
    "website",
    "cpm",
    "budget",
    "media",
    "userpic",
    "freq",
    "confirm",
)

STEP_STATE = {
    "target_payload": CreateAdSG.target_payload,
    "title": CreateAdSG.title,
    "text": CreateAdSG.text,
    "promote_url": CreateAdSG.promote_url,
    "website": CreateAdSG.website,
    "cpm": CreateAdSG.cpm,
    "budget": CreateAdSG.budget,
    "media": CreateAdSG.media,
    "userpic": CreateAdSG.userpic,
    "freq": CreateAdSG.freq,
    "confirm": CreateAdSG.confirm,
}

TARGET_HINTS = {
    "channel_post": "Таргетинг каналов: языки через запятую (`en,ru`) или @каналы. Можно `topics:1,2`.",
    "bot_banner": "Таргетинг ботов: username через запятую, например `@mybot,@otherbot`.",
    "search_result": "Таргетинг поиска: до 10 ключевых фраз через запятую.",
    "video_banner": "Таргетинг юзеров: страны ISO `RU,US`. Опционально языки `lang:ru,en` и устройство `device:ios`.",
}

PLACEMENT_LABELS = {
    "channel_post": "Каналы",
    "bot_banner": "Боты",
    "search_result": "Поиск",
    "video_banner": "Юзеры",
}

PLACEMENT_ALIASES = {
    "каналы": "channel_post",
    "канал": "channel_post",
    "боты": "bot_banner",
    "бот": "bot_banner",
    "поиск": "search_result",
    "юзеры": "video_banner",
    "юзер": "video_banner",
    "пользователи": "video_banner",
    "users": "video_banner",
    "видео": "video_banner",
    "видео-баннер": "video_banner",
}


def is_telegram_url(url: str) -> bool:
    raw = url.lower().strip()
    return any(token in raw for token in ("t.me/", "telegram.me/", "telegram.dog/"))


def step_enabled(placement: str, step: str, draft: dict | None = None) -> bool:
    draft = draft or {}
    if step == "text":
        return placement != "search_result"
    if step == "website":
        url = str(draft.get("promote_url") or "")
        return bool(url) and not is_telegram_url(url)
    if step == "media":
        return placement in {"channel_post", "video_banner"}
    if step == "userpic":
        return placement in {"channel_post", "bot_banner"}
    return True


def steps_for(placement: str, draft: dict | None = None) -> list[str]:
    payload = dict(draft or {})
    payload["placement"] = placement
    return [step for step in STEP_ORDER if step_enabled(placement, step, payload)]


def next_step(current: str | None, draft: dict) -> str:
    placement = str(draft.get("placement") or "")
    start = 0
    if current in STEP_ORDER:
        start = STEP_ORDER.index(current) + 1
    for step in STEP_ORDER[start:]:
        if step_enabled(placement, step, draft):
            return step
    return "confirm"


def parse_target(placement: str, text: str):
    parts = [item.strip() for item in text.split(",") if item.strip()]
    if placement == "search_result":
        return InputAdTargetSearch(search_queries=parts)
    if placement == "bot_banner":
        return InputAdTargetBots(bot_ids=parts)
    if placement == "channel_post":
        if any(item.startswith("@") or item.startswith("topics:") for item in parts) or (
            parts and not parts[0].isalpha()
        ):
            topic_ids: list[int] = []
            channel_ids: list[str] = []
            for part in parts:
                if part.startswith("topics:"):
                    topic_ids.extend(
                        int(item) for item in part.split(":", 1)[1].split(",") if item.strip().isdigit()
                    )
                else:
                    channel_ids.append(part)
            if topic_ids:
                return InputAdTargetChannels(topic_ids=topic_ids, channel_ids=channel_ids or None)
            return InputAdTargetChannels(channel_ids=channel_ids)
        return InputAdTargetChannels(language_codes=parts)
    countries: list[str] = []
    langs: list[str] = []
    device = None
    for part in parts:
        if part.startswith("lang:"):
            langs.extend(item.strip() for item in part.split(":", 1)[1].split(",") if item.strip())
        elif part.startswith("device:"):
            device = part.split(":", 1)[1].strip()
        else:
            countries.append(part.upper())
    return InputAdTargetUsers(
        country_codes=countries or None,
        language_codes=langs or None,
        device=device,
    )


def parse_media(placement: str, text: str) -> dict[str, str]:
    raw = text.strip()
    if raw.startswith("video:") or raw.startswith("v:"):
        return {"video_id": raw.split(":", 1)[1].strip()}
    if raw.startswith("photo:"):
        return {"photo_id": raw.split(":", 1)[1].strip()}
    return {"photo_id": raw}


def draft_summary(draft: dict) -> list[list[str]]:
    placement = str(draft.get("placement") or "")
    rows = [
        ["Таргет", PLACEMENT_LABELS.get(placement, placement)],
        ["Название", str(draft.get("title") or "—")],
        ["Ссылка", str(draft.get("promote_url") or "—")],
        ["CPM", str(draft.get("cpm") or "—")],
        ["Бюджет", str(draft.get("initial_budget") or 0)],
    ]
    if draft.get("text"):
        rows.insert(2, ["Текст", str(draft["text"])])
    if draft.get("website_name"):
        rows.append(["Сайт", str(draft["website_name"])])
    if draft.get("button"):
        rows.append(["Кнопка", str(draft["button"])])
    if draft.get("photo_id"):
        rows.append(["Фото", str(draft["photo_id"])])
    if draft.get("video_id"):
        rows.append(["Видео", str(draft["video_id"])])
    if "show_userpic" in draft:
        rows.append(["Аватар", "да" if draft.get("show_userpic") else "нет"])
    if draft.get("impression_frequency"):
        rows.append(["Частота / день", str(draft["impression_frequency"])])
    if draft.get("daily_budget_limit"):
        rows.append(["Дневной лимит", str(draft["daily_budget_limit"])])
    return rows


async def ask_create_step(event: Event, *, step: str, draft: dict) -> None:
    placement = str(draft.get("placement") or "")
    label = PLACEMENT_LABELS.get(placement, placement)
    if step == "target_payload":
        await reply_rich(
            event,
            heading(label),
            paragraph(TARGET_HINTS[placement]),
            wizard=True,
        )
        return
    if step == "title":
        await reply_rich(
            event,
            paragraph("Внутреннее название объявления (видите только вы в кабинете, 1–128 байт):"),
            wizard=True,
        )
        return
    if step == "text":
        await reply_rich(
            event,
            paragraph("Текст объявления, который увидит аудитория (1–160 символов):"),
            wizard=True,
        )
        return
    if step == "promote_url":
        await reply_rich(
            event,
            paragraph("Ссылка (URL), которую откроет кнопка: канал, пост, бот или сайт."),
            wizard=True,
        )
        return
    if step == "website":
        await reply_rich(
            event,
            paragraph(
                "Внешний сайт: название (обязательно), через `|` можно кнопку и pixel event. "
                "Например `My Shop|buy|ev1`. Кнопка: open, buy, learn_more…"
            ),
            wizard=True,
            skip=True,
        )
        return
    if step == "cpm":
        await reply_rich(event, paragraph("CPM — цена за 1000 показов в валюте кабинета:"), wizard=True)
        return
    if step == "budget":
        await reply_rich(
            event,
            paragraph("Стартовый бюджет: `10` или `10 / 2` (бюджет / дневной лимит). Пропуск = 0."),
            wizard=True,
            skip=True,
        )
        return
    if step == "media":
        await reply_rich(
            event,
            paragraph("ID фото из раздела «Медиа». Для видео напишите `video:ID`. Можно пропустить."),
            wizard=True,
            skip=True,
        )
        return
    if step == "userpic":
        await reply_rich(
            event,
            paragraph("Показать аватар канала или бота в объявлении? Обычно это +30% к нужному CPM."),
            button_row([("Да, аватар", "cad:up:1", SUCCESS), ("Нет", "cad:up:0")]),
            wizard=True,
            skip=True,
        )
        return
    if step == "freq":
        await reply_rich(
            event,
            paragraph("Сколько раз в день показывать одному человеку (1–4). По умолчанию 1."),
            button_row(
                [
                    ("1", "cad:fq:1", PRIMARY),
                    ("2", "cad:fq:2"),
                    ("3", "cad:fq:3"),
                    ("4", "cad:fq:4"),
                ]
            ),
            wizard=True,
            skip=True,
        )
        return
    raise ValueError(f"unknown create step {step}")
