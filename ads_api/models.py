"""Pydantic models for every Telegram Ads API type in the official docs."""

from __future__ import annotations

from collections.abc import Mapping
from io import BufferedReader, BytesIO
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ads_api.enums import (
    AccountTransactionPeerType,
    AdButtonType,
    AdPlacement,
    AdStatus,
    AdTargetType,
    AdTransactionPeerType,
    ConversionActionType,
    Currency,
    DeviceType,
    PixelEventStatus,
    PixelEventType,
    TransactionStatusValue,
)

JsonId = int | str


def _parse_page_offset(value: Any) -> int | str | None:
    """Docs call next_offset a String; transaction lists return an Integer."""
    if value in (None, "", False):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return str(value)


PageOffset = Annotated[int | str | None, BeforeValidator(_parse_page_offset)]


class AdsModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Account(AdsModel):
    account_id: str
    title: str
    currency: Currency | str
    spent_budget: float
    remaining_budget: float
    ads_budget: float


class AccountInfo(AdsModel):
    account_id: str
    title: str
    full_name: str
    email: str
    phone_number: str
    country: str
    city: str
    legal_name: str | None = None


class RelatedAccountsList(AdsModel):
    total_count: int
    accounts: list[Account] = Field(default_factory=list)


class TransactionStatus(AdsModel):
    transaction_id: str
    status: TransactionStatusValue | str
    currency: Currency | str
    amount: float
    main_account: Account | None = None
    related_account: Account | None = None
    error: str | None = None


class AccountTransactionPeerExternal(AdsModel):
    type: Literal[AccountTransactionPeerType.EXTERNAL] = AccountTransactionPeerType.EXTERNAL
    comment: str | None = None


class AccountTransactionPeerMainAccount(AdsModel):
    type: Literal[AccountTransactionPeerType.MAIN_ACCOUNT] = AccountTransactionPeerType.MAIN_ACCOUNT


class AccountTransactionPeerRelatedAccount(AdsModel):
    type: Literal[AccountTransactionPeerType.RELATED_ACCOUNT] = (
        AccountTransactionPeerType.RELATED_ACCOUNT
    )
    account_id: str
    account_title: str


class AccountTransactionPeerAd(AdsModel):
    type: Literal[AccountTransactionPeerType.AD] = AccountTransactionPeerType.AD
    ad_id: int
    ad_title: str
    ad_is_deleted: bool | None = None


AccountTransactionPeer = Annotated[
    Union[
        AccountTransactionPeerExternal,
        AccountTransactionPeerMainAccount,
        AccountTransactionPeerRelatedAccount,
        AccountTransactionPeerAd,
    ],
    Field(discriminator="type"),
]


class AccountTransaction(AdsModel):
    peer: AccountTransactionPeer
    date: int
    currency: Currency | str
    amount: float


class AccountTransactionsList(AdsModel):
    total_count: int
    transactions: list[AccountTransaction] = Field(default_factory=list)
    next_offset: PageOffset = None


class AccountReportItem(AdsModel):
    ad_id: int
    ad_title: str
    ad_is_deleted: bool | None = None
    views: int
    opens: int | None = None
    clicks: int
    actions: int = 0
    currency: Currency | str
    spent_budget: float
    joins: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_joins(cls, data: Any) -> Any:
        return _alias_joins_to_actions(data)


class AdPhoto(AdsModel):
    photo_id: str
    photo_url: str


class AdVideo(AdsModel):
    video_id: str
    video_url: str


class WebsitePhoto(AdsModel):
    photo_id: str
    photo_url: str


class InputAdSchedule(AdsModel):
    week_hours_mask: list[int]
    timezone: int | None = None
    use_viewer_timezone: bool | None = None


class AdSchedule(AdsModel):
    week_hours_mask: list[int]
    timezone: int | None = None
    use_viewer_timezone: bool | None = None


class DeclineReason(AdsModel):
    text: str
    description_html: str | None = None


class InputAdTargetChannels(AdsModel):
    type: Literal[AdTargetType.CHANNELS] = AdTargetType.CHANNELS
    language_codes: list[str] | None = None
    topic_ids: list[int] | None = None
    exclude_topic_ids: list[int] | None = None
    channel_ids: list[JsonId] | None = None
    exclude_channel_ids: list[JsonId] | None = None


class InputAdTargetUsers(AdsModel):
    type: Literal[AdTargetType.USERS] = AdTargetType.USERS
    country_codes: list[str] | None = None
    location_ids: list[int] | None = None
    language_codes: list[str] | None = None
    topic_ids: list[int] | None = None
    intersect_topics: bool | None = None
    exclude_topic_ids: list[int] | None = None
    channel_ids: list[JsonId] | None = None
    exclude_channel_ids: list[JsonId] | None = None
    audience_ids: list[int] | None = None
    exclude_audience_ids: list[int] | None = None
    device: DeviceType | str | None = None
    exclude_political_channels: bool | None = None
    political_channels_only: bool | None = None


class InputAdTargetBots(AdsModel):
    type: Literal[AdTargetType.BOTS] = AdTargetType.BOTS
    bot_ids: list[JsonId] | None = None


class InputAdTargetSearch(AdsModel):
    type: Literal[AdTargetType.SEARCH] = AdTargetType.SEARCH
    search_queries: list[str]


InputAdTarget = Annotated[
    Union[InputAdTargetChannels, InputAdTargetUsers, InputAdTargetBots, InputAdTargetSearch],
    Field(discriminator="type"),
]


class TargetLanguage(AdsModel):
    language_code: str
    name: str


class TargetTopic(AdsModel):
    topic_id: int
    name: str


class TargetCountry(AdsModel):
    country_code: str
    name: str


class TargetLocation(AdsModel):
    location_id: int
    name: str
    country_code: str | None = None
    region: str | None = None


class TargetLocationList(AdsModel):
    total_count: int
    locations: list[TargetLocation] = Field(default_factory=list)
    next_offset: PageOffset = None


class TargetChannel(AdsModel):
    channel_id: int
    title: str | None = None
    username: str | None = None
    photo_url: str | None = None


class TargetBot(AdsModel):
    bot_id: int
    title: str | None = None
    username: str | None = None
    photo_url: str | None = None


class Audience(AdsModel):
    audience_id: int
    title: str
    size: int
    ads_count: int
    created_date: int
    updated_date: int


class AudiencesList(AdsModel):
    total_count: int
    audiences: list[Audience] = Field(default_factory=list)


class AdTargetChannels(AdsModel):
    type: Literal[AdTargetType.CHANNELS] = AdTargetType.CHANNELS
    languages: list[TargetLanguage] | None = None
    topics: list[TargetTopic] | None = None
    exclude_topics: list[TargetTopic] | None = None
    channels: list[TargetChannel] | None = None
    exclude_channels: list[TargetChannel] | None = None


class AdTargetUsers(AdsModel):
    type: Literal[AdTargetType.USERS] = AdTargetType.USERS
    countries: list[TargetCountry] | None = None
    locations: list[TargetLocation] | None = None
    languages: list[TargetLanguage] | None = None
    topics: list[TargetTopic] | None = None
    intersect_topics: bool | None = None
    exclude_topics: list[TargetTopic] | None = None
    channels: list[TargetChannel] | None = None
    exclude_channels: list[TargetChannel] | None = None
    audiences: list[Audience] | None = None
    exclude_audiences: list[Audience] | None = None
    device: DeviceType | str | None = None
    exclude_political_channels: bool | None = None
    political_channels_only: bool | None = None


class AdTargetBots(AdsModel):
    type: Literal[AdTargetType.BOTS] = AdTargetType.BOTS
    bots: list[TargetBot] | None = None


class AdTargetSearch(AdsModel):
    type: Literal[AdTargetType.SEARCH] = AdTargetType.SEARCH
    search_queries: list[str] = Field(default_factory=list)


AdTarget = Annotated[
    Union[AdTargetChannels, AdTargetUsers, AdTargetBots, AdTargetSearch],
    Field(discriminator="type"),
]


class Ad(AdsModel):
    ad_id: int
    title: str
    currency: Currency | str
    text: str | None = None
    photo: AdPhoto | None = None
    video: AdVideo | None = None
    promote_url: str
    cpm: float
    placement: AdPlacement | str | None = None
    target: AdTarget | None = None
    impression_frequency: int | None = None
    website_name: str | None = None
    website_photo: WebsitePhoto | None = None
    website_photo_id: str | None = None
    website_photo_url: str | None = None
    button: AdButtonType | str | None = None
    conversion_event_id: str | None = None
    additional_info: str | None = None
    show_userpic: bool | None = None
    spent_budget: float = 0
    remaining_budget: float = 0
    daily_spent_budget: float | None = None
    daily_budget_limit: float | None = None
    views: int = 0
    opens: int | None = None
    clicks: int | None = None
    actions: int = 0
    action_type: ConversionActionType | str | None = None
    created_date: int | None = None
    is_paused: bool | None = None
    activate_date: int | None = None
    deactivate_date: int | None = None
    schedule: AdSchedule | None = None
    status: AdStatus | str | None = None
    decline_reason: DeclineReason | None = None
    joins: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_joins(cls, data: Any) -> Any:
        return _alias_joins_to_actions(data)


class AdList(AdsModel):
    total_count: int
    ads: list[Ad] = Field(default_factory=list)
    next_offset: PageOffset = None


class AdTransactionPeerAccount(AdsModel):
    type: Literal[AdTransactionPeerType.ACCOUNT] = AdTransactionPeerType.ACCOUNT


class AdTransactionPeerViews(AdsModel):
    type: Literal[AdTransactionPeerType.VIEWS] = AdTransactionPeerType.VIEWS
    views: int


AdTransactionPeer = Annotated[
    Union[AdTransactionPeerAccount, AdTransactionPeerViews],
    Field(discriminator="type"),
]


class AdTransaction(AdsModel):
    peer: AdTransactionPeer
    date: int
    currency: Currency | str
    amount: float


class AdTransactionsList(AdsModel):
    total_count: int
    transactions: list[AdTransaction] = Field(default_factory=list)
    next_offset: PageOffset = None


class AdStatItem(AdsModel):
    from_time: int
    to_time: int
    views: int
    opens: int | None = None
    clicks: int
    actions: int = 0
    currency: Currency | str
    spent_budget: float
    joins: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_joins(cls, data: Any) -> Any:
        return _alias_joins_to_actions(data)


class Pixel(AdsModel):
    pixel_id: str
    code_snippet: str


class PixelEvent(AdsModel):
    event_id: str
    title: str
    type: PixelEventType | str
    status: PixelEventStatus | str
    ads_count: int
    created_date: int
    last_triggered_date: int | None = None
    auto_created: bool | None = None
    code_snippet: str | None = None


class PixelEventsList(AdsModel):
    total_count: int
    events: list[PixelEvent] = Field(default_factory=list)


class UploadFile:
    """multipart InputFile for uploadAdPhoto / uploadAdVideo / uploadWebsitePhoto."""

    def __init__(
        self,
        content: bytes | BinaryIO | Path | str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> None:
        self.content = content
        self.filename = filename
        self.content_type = content_type

    def as_multipart(self) -> tuple[str, bytes, str]:
        filename = self.filename or "upload.bin"
        raw: bytes
        source = self.content
        if isinstance(source, (bytes, bytearray)):
            raw = bytes(source)
        elif isinstance(source, (str, Path)):
            path = Path(source)
            filename = self.filename or path.name
            raw = path.read_bytes()
        elif isinstance(source, (BytesIO, BufferedReader)):
            raw = source.read()
            if not self.filename and getattr(source, "name", None):
                filename = Path(str(source.name)).name
        else:
            raw = source.read()  # type: ignore[union-attr]
        ctype = self.content_type or _guess_content_type(filename)
        return filename, raw, ctype


def dump_param(value: Any) -> Any:
    """JSON-ready Ads API parameter (nested models become dicts/lists)."""
    if value is None:
        return None
    if isinstance(value, AdsModel):
        return value.model_dump(exclude_none=True, mode="json")
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True, mode="json")
    if isinstance(value, Mapping):
        return {k: dump_param(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [dump_param(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "value") and not isinstance(value, (bytes, str, int, float, bool)):
        return getattr(value, "value")
    return value


def _alias_joins_to_actions(data: Any) -> Any:
    if isinstance(data, dict) and "actions" not in data and "joins" in data:
        data = dict(data)
        data["actions"] = data["joins"]
    return data


def _guess_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".mov"):
        return "video/quicktime"
    if lower.endswith(".webm"):
        return "video/webm"
    return "application/octet-stream"
