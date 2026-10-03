from __future__ import annotations

from datetime import datetime, timezone

from ads_api.errors import AdsApiError, AdsAuthError, AdsRateLimitError, AdsTransportError
from ads_api.models import (
    Account,
    AccountInfo,
    AccountReportItem,
    AccountTransaction,
    Ad,
    AdStatItem,
    AdTransaction,
    Audience,
    Pixel,
    PixelEvent,
    TargetBot,
    TargetChannel,
    TransactionStatus,
)

STATUS_RU = {
    "stopped": "остановлено",
    "ready_for_review": "готово к модерации",
    "in_review": "на модерации",
    "declined": "отклонено",
    "active": "активно",
    "on_hold": "на паузе",
}

PLACEMENT_RU = {
    "channel_post": "каналы",
    "bot_banner": "боты",
    "search_result": "поиск",
    "video_banner": "юзеры",
}

ERROR_RU = {
    "UNAUTHORIZED": "Токен Ads API недействителен. Проверьте TELEGRAM_ADS_ACCESS_TOKEN.",
    "NOT_ENOUGH_BUDGET": "Недостаточно бюджета.",
    "AD_TITLE_REQUIRED": "Нужно название объявления.",
    "CHANNEL_ID_UNKNOWN": "Канал неизвестен этому аккаунту. Укажите @username, чтобы сначала резолвить его.",
    "BOT_ID_UNKNOWN": "Бот неизвестен этому аккаунту. Укажите @username.",
    "ACCOUNT_ID_INVALID": "Неверный account_id. Оставьте TELEGRAM_ADS_ACCOUNT_ID пустым или укажите id кабинета из ads.telegram.org.",
    "MAIN_ACCOUNT_REQUIRED": "Этот метод доступен только из основного кабинета, не из связанного.",
    "RESPONSE_INVALID": "Ads API вернул данные, которые бот не смог разобрать.",
    "IDEMPOTENT_PARAM_MISMATCH": "Повтор с тем же idempotency key, но другими параметрами.",
    "IDEMPOTENT_REQUEST_IN_PROGRESS": "Предыдущий запрос с этим ключом ещё выполняется.",
    "RATE_LIMIT": "Слишком много запросов, подождите секунду.",
    "AD_NOT_FOUND": "Объявление не найдено.",
    "RETARGETING_DISABLED": (
        "Ретаргетинг в этом кабинете выключен. Telegram не отдаёт списки по телефонам, "
        "пока не включит опцию для аккаунта."
    ),
}

ERROR_NO_USER_FIX = frozenset(
    {
        "RESPONSE_INVALID",
        "RETARGETING_DISABLED",
        "UNAUTHORIZED",
        "RATE_LIMIT",
        "MAIN_ACCOUNT_REQUIRED",
        "AD_NOT_FOUND",
    }
)


def money(amount: float | None, currency: str | None = None) -> str:
    if amount is None:
        return "—"
    if abs(amount) >= 100:
        number = f"{amount:,.2f}".replace(",", " ")
    else:
        number = f"{amount:.5f}".rstrip("0").rstrip(".")
    token = str(currency or "").strip()
    if not token or token.lower() in {"none", "null"}:
        return number
    token = token.rsplit(".", 1)[-1].split()[-1]
    if token.upper() in {part.upper() for part in number.split()}:
        return number
    return f"{number} {token}"


def ts(value: int | None) -> str:
    if not value:
        return "—"
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def status_ru(value: str | None) -> str:
    if not value:
        return "—"
    return STATUS_RU.get(value, value)


def placement_ru(value: str | None) -> str:
    if not value:
        return "—"
    return PLACEMENT_RU.get(value, value)


def account_rows(account: Account) -> list[list[str]]:
    return [
        ["ID", account.account_id],
        ["Название", account.title],
        ["Валюта", str(account.currency)],
        ["Потрачено", money(account.spent_budget, str(account.currency))],
        ["Остаток", money(account.remaining_budget, str(account.currency))],
        ["В объявлениях", money(account.ads_budget, str(account.currency))],
    ]


def account_info_rows(info: AccountInfo) -> list[list[str]]:
    return [
        ["ID", info.account_id],
        ["Название", info.title],
        ["Компания", info.full_name],
        ["Email", info.email],
        ["Телефон", info.phone_number],
        ["Страна", info.country],
        ["Город", info.city],
        ["Юр. имя", info.legal_name or "—"],
    ]


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _pct(numerator: float | None, denominator: float | None) -> str:
    value = _ratio(numerator, denominator)
    if value is None:
        return "—"
    return f"{value * 100:.2f}%"


def _per(amount: float | None, count: float | None, currency: str | None) -> str:
    value = _ratio(amount, count)
    if value is None:
        return "—"
    return money(value, currency)


def ad_cpc(ad: Ad) -> str:
    return _per(ad.spent_budget, ad.clicks, str(ad.currency))


def ad_cpa(ad: Ad) -> str:
    return _per(ad.spent_budget, ad.actions, str(ad.currency))


def ad_ctr(ad: Ad) -> str:
    return _pct(ad.clicks, ad.views)


def ad_cr(ad: Ad) -> str:
    return _pct(ad.actions, ad.clicks if ad.clicks else ad.views)


def ad_ecpm(ad: Ad) -> str:
    value = _ratio(ad.spent_budget, ad.views)
    if value is None:
        return "—"
    return money(value * 1000, str(ad.currency))


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "—"
    return "да" if value else "нет"


def _schedule_text(ad: Ad) -> str:
    schedule = ad.schedule
    if schedule is None:
        return "нет (круглосуточно)"
    mask = list(schedule.week_hours_mask or [])
    if not mask or all(int(item) == 0 for item in mask):
        text = "нет (круглосуточно)"
    elif len(mask) >= 7 and all(int(item) == 16_777_215 for item in mask[:7]):
        text = "круглосуточно"
    else:
        days = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
        parts: list[str] = []
        for index, item in enumerate(mask[:7]):
            parts.append(f"{days[index]} {_hours_ranges(int(item))}")
        text = "; ".join(parts)
    extra: list[str] = []
    if schedule.timezone is not None:
        extra.append(f"UTC{schedule.timezone / 3600:+g}")
    if schedule.use_viewer_timezone:
        extra.append("TZ зрителя")
    if extra:
        return f"{text} ({', '.join(extra)})"
    return text


def _hours_ranges(mask: int) -> str:
    hours = [hour for hour in range(24) if mask & (1 << hour)]
    if not hours:
        return "выкл"
    if len(hours) == 24:
        return "00–24"
    ranges: list[str] = []
    start = prev = hours[0]
    for hour in hours[1:]:
        if hour == prev + 1:
            prev = hour
            continue
        ranges.append(f"{start:02d}–{prev + 1:02d}")
        start = prev = hour
    ranges.append(f"{start:02d}–{prev + 1:02d}")
    return ",".join(ranges)


def _join_names(items: object, *, limit: int = 12) -> str:
    if not items:
        return "—"
    seq = list(items)  # type: ignore[arg-type]
    parts: list[str] = []
    for item in seq[:limit]:
        if isinstance(item, str):
            parts.append(item)
            continue
        username = getattr(item, "username", None)
        name = getattr(item, "name", None) or getattr(item, "title", None)
        code = getattr(item, "language_code", None) or getattr(item, "country_code", None)
        if username:
            parts.append(f"@{username}")
        elif name and code:
            parts.append(f"{name} ({code})")
        elif name:
            parts.append(str(name))
        elif code:
            parts.append(str(code))
        else:
            ident = getattr(item, "channel_id", None) or getattr(item, "bot_id", None) or getattr(
                item, "audience_id", None
            )
            parts.append(str(ident if ident is not None else item))
    extra = len(seq) - limit
    if extra > 0:
        parts.append(f"+{extra}")
    return ", ".join(parts)


def target_rows(ad: Ad) -> list[list[str]]:
    target = ad.target
    if target is None:
        return [["Тип таргетинга", "—"]]
    raw_type = str(getattr(target, "type", target)).rsplit(".", 1)[-1]
    kind = {
        "search": "поиск",
        "channels": "каналы",
        "users": "пользователи",
        "bots": "боты",
    }.get(raw_type, raw_type)
    rows = [["Тип таргетинга", kind]]
    if raw_type == "search":
        rows.append(["Ключевые запросы", _join_names(getattr(target, "search_queries", None), limit=50)])
        return rows
    if raw_type == "bots":
        rows.append(["Боты", _join_names(getattr(target, "bots", None))])
        return rows
    rows.append(["Языки", _join_names(getattr(target, "languages", None))])
    rows.append(["Темы", _join_names(getattr(target, "topics", None))])
    rows.append(["Искл. темы", _join_names(getattr(target, "exclude_topics", None))])
    rows.append(["Каналы", _join_names(getattr(target, "channels", None))])
    rows.append(["Искл. каналы", _join_names(getattr(target, "exclude_channels", None))])
    if raw_type == "users":
        rows.append(["Страны", _join_names(getattr(target, "countries", None))])
        rows.append(["Локации", _join_names(getattr(target, "locations", None))])
        rows.append(["Аудитории", _join_names(getattr(target, "audiences", None))])
        rows.append(["Искл. аудитории", _join_names(getattr(target, "exclude_audiences", None))])
        rows.append(["Устройство", str(getattr(target, "device", None) or "—")])
        rows.append(["Темы ∩", _yes_no(getattr(target, "intersect_topics", None))])
        rows.append(["Без политики", _yes_no(getattr(target, "exclude_political_channels", None))])
        rows.append(["Только политика", _yes_no(getattr(target, "political_channels_only", None))])
    return rows


def ad_stat_rows(ad: Ad) -> list[list[str]]:
    cur = str(ad.currency)
    budget = (ad.spent_budget or 0) + (ad.remaining_budget or 0)
    return [
        ["Показы", str(ad.views)],
        ["Клики", str(ad.clicks) if ad.clicks is not None else "—"],
        ["Действия", str(ad.actions)],
        ["Открытия видео", str(ad.opens) if ad.opens is not None else "—"],
        ["CTR", ad_ctr(ad)],
        ["CVR", ad_cr(ad)],
        ["CPM ставка", money(ad.cpm, cur)],
        ["eCPM факт", ad_ecpm(ad)],
        ["CPC", ad_cpc(ad)],
        ["CPA", ad_cpa(ad)],
        ["CPV", _per(ad.spent_budget, ad.opens, cur)],
        ["Потрачено", money(ad.spent_budget, cur)],
        ["Остаток", money(ad.remaining_budget, cur)],
        ["Бюджет", money(budget, cur)],
        ["Дневной расход", money(ad.daily_spent_budget, cur) if ad.daily_spent_budget is not None else "—"],
        ["Дневной лимит", money(ad.daily_budget_limit, cur) if ad.daily_budget_limit else "—"],
    ]


def ad_setting_rows(ad: Ad) -> list[list[str]]:
    freq = (
        str(ad.impression_frequency)
        if ad.impression_frequency is not None
        else "1 раз в день (по умолчанию)"
    )
    photo = ad.photo.photo_id if ad.photo else "—"
    photo_url = ad.photo.photo_url if ad.photo else "—"
    video = ad.video.video_id if ad.video else "—"
    video_url = ad.video.video_url if ad.video else "—"
    site_photo = "—"
    if ad.website_photo:
        site_photo = ad.website_photo.photo_id
    elif ad.website_photo_id:
        site_photo = ad.website_photo_id
    rows = [
        ["ID", str(ad.ad_id)],
        ["Название", ad.title or "—"],
        ["Текст", ad.text or "—"],
        ["Статус", status_ru(str(ad.status) if ad.status else None)],
        ["Пауза", _yes_no(ad.is_paused)],
        ["Таргет", placement_ru(str(ad.placement) if ad.placement else None)],
        ["Валюта", str(ad.currency)],
        ["URL", ad.promote_url or "—"],
        ["Кнопка", str(ad.button) if ad.button else "—"],
        ["Сайт", ad.website_name or "—"],
        ["Фото сайта", site_photo],
        ["Фото", photo],
        ["Фото URL", photo_url],
        ["Видео", video],
        ["Видео URL", video_url],
        ["Частота / день", freq],
        ["Аватар", _yes_no(ad.show_userpic)],
        ["Конверсия", str(ad.action_type) if ad.action_type else "—"],
        ["Pixel event", ad.conversion_event_id or "—"],
        ["Доп. инфо", ad.additional_info or "—"],
        ["Создано", ts(ad.created_date)],
        ["Активация", ts(ad.activate_date)],
        ["Деактивация", ts(ad.deactivate_date)],
        ["Расписание", _schedule_text(ad)],
    ]
    rows.extend(target_rows(ad))
    if ad.decline_reason:
        rows.append(["Отказ", ad.decline_reason.text])
    return rows


def ad_rows(ad: Ad) -> list[list[str]]:
    return ad_stat_rows(ad) + ad_setting_rows(ad)


def ad_computed(ad: Ad) -> dict[str, float | None]:
    views = float(ad.views or 0)
    clicks = float(ad.clicks) if ad.clicks is not None else None
    actions = float(ad.actions or 0)
    spent = float(ad.spent_budget or 0)
    remaining = float(ad.remaining_budget or 0)
    per_view = _ratio(spent, views)
    return {
        "ctr": _ratio(clicks, views),
        "cvr": _ratio(actions, clicks),
        "cpc": _ratio(spent, clicks),
        "cpa": _ratio(spent, actions),
        "ecpm": None if per_view is None else per_view * 1000,
        "budget": spent + remaining,
    }


def ad_json(ad: Ad) -> dict[str, object]:
    payload = ad.model_dump(mode="json")
    payload["computed"] = ad_computed(ad)
    return payload


def ads_json(ads: list[Ad], *, total_count: int | None = None) -> dict[str, object]:
    return {
        "total_count": total_count if total_count is not None else len(ads),
        "ads": [ad_json(ad) for ad in ads],
    }


ADS_LIST_HEADERS = [
    "ID",
    "Название",
    "Статус",
    "Таргет",
    "Показы",
    "Клики",
    "CTR",
    "CPC",
    "Действия",
    "CPA",
    "eCPM",
    "Потрачено",
    "Остаток",
]


def ads_table_rows(ads: list[Ad]) -> list[list[str]]:
    rows: list[list[str]] = []
    for ad in ads:
        rows.append(
            [
                str(ad.ad_id),
                _clip(ad.title, 24),
                status_ru(str(ad.status) if ad.status else None),
                placement_ru(str(ad.placement) if ad.placement else None),
                str(ad.views),
                str(ad.clicks) if ad.clicks is not None else "—",
                ad_ctr(ad),
                ad_cpc(ad),
                str(ad.actions),
                ad_cpa(ad),
                ad_ecpm(ad),
                money(ad.spent_budget, str(ad.currency)),
                money(ad.remaining_budget, str(ad.currency)),
            ]
        )
    return rows


def audience_rows(item: Audience) -> list[list[str]]:
    return [
        ["ID", str(item.audience_id)],
        ["Название", item.title],
        ["Размер", str(item.size)],
        ["В объявлениях", str(item.ads_count)],
        ["Создана", ts(item.created_date)],
        ["Обновлена", ts(item.updated_date)],
    ]


def pixel_event_rows(event: PixelEvent) -> list[list[str]]:
    return [
        ["ID", event.event_id],
        ["Название", event.title],
        ["Тип", str(event.type)],
        ["Статус", str(event.status)],
        ["В объявлениях", str(event.ads_count)],
        ["Авто", "да" if event.auto_created else "нет"],
        ["Последний fire", ts(event.last_triggered_date)],
    ]


def stats_table_rows(items: list[AdStatItem]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items[-20:]:
        rows.append(
            [
                ts(item.from_time)[5:16],
                str(item.views),
                str(item.clicks),
                str(item.actions),
                money(item.spent_budget, str(item.currency)),
            ]
        )
    return rows


def report_table_rows(items: list[AccountReportItem]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items[:40]:
        rows.append(
            [
                str(item.ad_id),
                _clip(item.ad_title, 20),
                str(item.views),
                str(item.clicks),
                str(item.actions),
                money(item.spent_budget, str(item.currency)),
            ]
        )
    return rows


def account_tx_rows(items: list[AccountTransaction]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items[:30]:
        peer = getattr(item.peer, "type", "?")
        rows.append([ts(item.date)[5:16], str(peer), money(item.amount, str(item.currency))])
    return rows


def ad_tx_rows(items: list[AdTransaction]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in items[:30]:
        peer = getattr(item.peer, "type", "?")
        rows.append([ts(item.date)[5:16], str(peer), money(item.amount, str(item.currency))])
    return rows


def transaction_status_rows(item: TransactionStatus) -> list[list[str]]:
    return [
        ["ID", item.transaction_id],
        ["Статус", str(item.status)],
        ["Сумма", money(item.amount, str(item.currency))],
        ["Ошибка", item.error or "—"],
        ["Основной", item.main_account.title if item.main_account else "—"],
        ["Связанный", item.related_account.title if item.related_account else "—"],
    ]


def channel_rows(ch: TargetChannel) -> list[list[str]]:
    return [
        ["ID", str(ch.channel_id)],
        ["Название", ch.title or "—"],
        ["Username", f"@{ch.username}" if ch.username else "—"],
    ]


def bot_rows(bot: TargetBot) -> list[list[str]]:
    return [
        ["ID", str(bot.bot_id)],
        ["Название", bot.title or "—"],
        ["Username", f"@{bot.username}" if bot.username else "—"],
    ]


def pixel_secret(pixel: Pixel) -> str:
    return pixel.code_snippet


def error_needs_user_fix(exc: Exception) -> bool:
    return isinstance(exc, AdsApiError) and exc.error not in ERROR_NO_USER_FIX


def format_ads_error(exc: Exception) -> str:
    if isinstance(exc, AdsAuthError):
        return ERROR_RU.get(exc.error, str(exc))
    if isinstance(exc, AdsRateLimitError):
        extra = f" Повтор через {exc.retry_after:.0f} с." if exc.retry_after else ""
        return ERROR_RU.get("RATE_LIMIT", str(exc)) + extra
    if isinstance(exc, AdsApiError):
        hint = ERROR_RU.get(exc.error)
        extra = f"\nМетод: `{exc.method}`" if exc.method else ""
        field = _parse_error_field(exc)
        if hint:
            return f"{hint}{extra}{field}\nКод: `{exc.error}`"
        return f"Ads API: `{exc.error}`{extra}{field}"
    if isinstance(exc, AdsTransportError):
        return f"Сеть Ads API: {exc}"
    return str(exc)


def _clip(text: str, n: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def _parse_error_field(exc: AdsApiError) -> str:
    payload = exc.payload
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if not isinstance(detail, list) or not detail:
        return ""
    first = detail[0]
    if not isinstance(first, dict):
        return ""
    loc = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    msg = str(first.get("msg") or "").strip()
    if not loc and not msg:
        return ""
    if loc and msg:
        return f"\nПоле: `{loc}` — {msg}"
    return f"\n{msg or loc}"
