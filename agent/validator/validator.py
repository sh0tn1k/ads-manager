from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from agent.config import AgentConfig
from agent.proposals.models import AdSpec, ProposalKind, ProposalPayload
from agent.validator import rules


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


@dataclass
class Dictionaries:
    """Target dictionaries from getTargetCountriesList / getTargetLanguagesList / getTargetTopicsList."""

    countries: set[str] = field(default_factory=set)
    languages: set[str] = field(default_factory=set)
    topics: set[int] = field(default_factory=set)


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    return f"{scheme}://{host}{path}" + (f"?{parts.query}" if parts.query else "")


def is_telegram_url(url: str) -> bool:
    host = urlsplit(url.strip()).netloc.lower()
    return host in {"t.me", "telegram.me", "telegram.dog", "www.t.me"}


def _decimals_ok(value: float, decimals: int) -> bool:
    return abs(round(value, decimals) - value) < 1e-9


def _list(target: dict[str, Any], key: str) -> list[Any]:
    value = target.get(key)
    return list(value) if isinstance(value, (list, tuple)) else []


def _check_target(ad: AdSpec, dictionaries: Dictionaries | None, issues: list[ValidationIssue]) -> None:
    target = ad.target
    ttype = str(target.get("type", ""))
    if ttype not in rules.SUPPORTED_TARGET_TYPES:
        issues.append(ValidationIssue("target_type_unsupported", ttype))
        return
    if (ad.placement, ttype) not in rules.ALLOWED_PLACEMENT_TARGETS:
        issues.append(ValidationIssue("placement_target_mismatch", f"{ad.placement} x {ttype}"))
    allowed_fields = {"users": rules.USERS_FIELDS, "channels": rules.CHANNELS_FIELDS, "search": rules.SEARCH_FIELDS}[ttype]
    extra = set(target) - allowed_fields
    if extra:
        issues.append(ValidationIssue("target_unknown_fields", ",".join(sorted(extra))))
    topics = _list(target, "topic_ids") + _list(target, "exclude_topic_ids")
    channels = _list(target, "channel_ids") + _list(target, "exclude_channel_ids")
    languages = _list(target, "language_codes")
    if len(topics) > rules.TOPICS_TOTAL_MAX:
        issues.append(ValidationIssue("too_many_topics", str(len(topics))))
    if len(channels) > rules.CHANNELS_TOTAL_MAX:
        issues.append(ValidationIssue("too_many_channels", str(len(channels))))
    if len(languages) > rules.LANGUAGES_MAX:
        issues.append(ValidationIssue("too_many_languages", str(len(languages))))
    for ch in channels:
        # Numeric ids only work after resolving by username on this account; require @username.
        if not (isinstance(ch, str) and ch.startswith("@") and len(ch) > 1):
            issues.append(ValidationIssue("channel_must_be_username", str(ch)))
    if ttype == "search":
        queries = _list(target, "search_queries")
        if not queries or len(queries) > rules.SEARCH_QUERIES_MAX:
            issues.append(ValidationIssue("search_queries_count", str(len(queries))))
        if any(not isinstance(q, str) or not q.strip() for q in queries):
            issues.append(ValidationIssue("search_query_empty"))
    if ttype == "channels":
        if _list(target, "channel_ids") and (_list(target, "topic_ids") or languages):
            issues.append(ValidationIssue("channels_ids_exclusive", "channel_ids requires empty topic_ids/language_codes"))
        if _list(target, "topic_ids") and len(languages) != 1:
            issues.append(ValidationIssue("channels_topics_need_one_language"))
    if ttype == "users":
        countries = _list(target, "country_codes")
        locations = _list(target, "location_ids")
        if len(countries) > rules.COUNTRIES_MAX:
            issues.append(ValidationIssue("too_many_countries", str(len(countries))))
        if len(locations) > rules.LOCATIONS_MAX:
            issues.append(ValidationIssue("too_many_locations", str(len(locations))))
        if locations and len(countries) != 1:
            issues.append(ValidationIssue("locations_need_one_country"))
        if len(_list(target, "audience_ids")) > rules.AUDIENCES_MAX:
            issues.append(ValidationIssue("too_many_audiences"))
        if len(_list(target, "audience_ids")) + len(_list(target, "exclude_audience_ids")) > rules.AUDIENCES_TOTAL_MAX:
            issues.append(ValidationIssue("too_many_audiences"))
        device = target.get("device")
        if device is not None and device not in rules.DEVICES:
            issues.append(ValidationIssue("device_invalid", str(device)))
        if target.get("exclude_political_channels") and target.get("political_channels_only"):
            issues.append(ValidationIssue("political_flags_conflict"))
        if dictionaries is not None:
            for code in countries:
                if dictionaries.countries and code not in dictionaries.countries:
                    issues.append(ValidationIssue("country_unknown", str(code)))
    if dictionaries is not None:
        for code in languages:
            if dictionaries.languages and code not in dictionaries.languages:
                issues.append(ValidationIssue("language_unknown", str(code)))
        for tid in topics:
            if dictionaries.topics and tid not in dictionaries.topics:
                issues.append(ValidationIssue("topic_unknown", str(tid)))


def _check_ad(ad: AdSpec, payload: ProposalPayload, config: AgentConfig, issues: list[ValidationIssue]) -> None:
    allow = {normalize_url(u) for u in config.promote_url_allowlist}
    if not ad.promote_url or normalize_url(ad.promote_url) not in allow:
        issues.append(ValidationIssue("promote_url_not_allowed", ad.promote_url))
    if len(ad.promote_url) > rules.PROMOTE_URL_MAX:
        issues.append(ValidationIssue("promote_url_too_long"))
    title_bytes = len(ad.title.encode("utf-8"))
    if not 1 <= title_bytes <= rules.TITLE_MAX_BYTES:
        issues.append(ValidationIssue("title_length", str(title_bytes)))
    ttype = str(ad.target.get("type", ""))
    if ttype != "search":
        if not ad.text or not ad.text.strip():
            issues.append(ValidationIssue("text_required"))
    if ad.text is not None and len(ad.text) > rules.TEXT_MAX_CHARS:
        issues.append(ValidationIssue("text_too_long", str(len(ad.text))))
    if ad.button is not None:
        if ad.button not in rules.BUTTONS:
            issues.append(ValidationIssue("button_invalid", ad.button))
        elif is_telegram_url(ad.promote_url):
            issues.append(ValidationIssue("button_not_applicable_for_telegram_url", ad.button))
    if ad.photo_id is not None and ttype not in {"users", "channels"}:
        issues.append(ValidationIssue("photo_not_applicable", ttype))
    decimals = rules.MONEY_DECIMALS.get(config.currency, 2)
    if ad.cpm <= 0 or not _decimals_ok(ad.cpm, decimals):
        issues.append(ValidationIssue("cpm_invalid", str(ad.cpm)))
    if ad.initial_budget <= 0 or not _decimals_ok(ad.initial_budget, decimals):
        issues.append(ValidationIssue("initial_budget_invalid", str(ad.initial_budget)))
    if ad.daily_budget_limit is not None and (ad.daily_budget_limit < 0 or not _decimals_ok(ad.daily_budget_limit, decimals)):
        issues.append(ValidationIssue("daily_budget_limit_invalid"))
    if ad.impression_frequency is not None:
        lo, hi = rules.IMPRESSION_FREQUENCY_RANGE
        if not lo <= ad.impression_frequency <= hi:
            issues.append(ValidationIssue("impression_frequency_invalid", str(ad.impression_frequency)))
    if ad.schedule is not None:
        mask = ad.schedule.get("week_hours_mask")
        if not (isinstance(mask, list) and len(mask) == 7 and all(isinstance(m, int) and 0 <= m <= rules.WEEK_HOURS_MASK_MAX for m in mask)):
            issues.append(ValidationIssue("schedule_invalid"))
        if set(ad.schedule) - {"week_hours_mask", "timezone", "use_viewer_timezone"}:
            issues.append(ValidationIssue("schedule_invalid", "unknown fields"))
    cost = payload.cost
    if abs(cost.initial_budget - ad.initial_budget) > 1e-9:
        issues.append(ValidationIssue("cost_mismatch", "cost.initial_budget != ad.initial_budget"))
    if cost.max_loss + 1e-9 < ad.initial_budget:
        issues.append(ValidationIssue("cost_mismatch", "max_loss < initial_budget (max loss is the whole budget)"))
    if cost.reserved + 1e-9 < ad.initial_budget:
        issues.append(ValidationIssue("cost_mismatch", "reserved < initial_budget"))
    if config.max_loss_per_hypothesis is not None and ad.initial_budget > config.max_loss_per_hypothesis + 1e-9:
        issues.append(ValidationIssue("over_max_loss_per_hypothesis", f"{ad.initial_budget} > {config.max_loss_per_hypothesis}"))


def validate_payload(
    payload: ProposalPayload | dict[str, Any],
    config: AgentConfig,
    *,
    dictionaries: Dictionaries | None = None,
) -> list[ValidationIssue]:
    """Return a list of issues; empty list = valid."""
    if not isinstance(payload, ProposalPayload):
        try:
            payload = ProposalPayload.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            return [ValidationIssue("schema_invalid", str(exc)[:300])]
    issues: list[ValidationIssue] = []
    if payload.kind in (ProposalKind.CREATE_AD, ProposalKind.CLONE_AD):
        assert payload.ad is not None
        _check_ad(payload.ad, payload, config, issues)
        _check_target(payload.ad, dictionaries, issues)
    elif payload.kind == ProposalKind.TOPUP_AD:
        amount = payload.action.amount if payload.action else None
        decimals = rules.MONEY_DECIMALS.get(config.currency, 2)
        if not amount or amount <= 0 or not _decimals_ok(amount, decimals):
            issues.append(ValidationIssue("amount_invalid", str(amount)))
        if config.max_loss_per_hypothesis is not None and amount and amount > config.max_loss_per_hypothesis + 1e-9:
            issues.append(ValidationIssue("over_max_loss_per_hypothesis"))
    if any(not s.startswith(("http://", "https://")) for s in payload.sources):
        issues.append(ValidationIssue("source_not_url"))
    return issues
