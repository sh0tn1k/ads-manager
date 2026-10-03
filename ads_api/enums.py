from __future__ import annotations

from enum import IntEnum, StrEnum


class Currency(StrEnum):
    EUR = "EUR"
    TON = "TON"
    XTR = "XTR"


class AdPlacement(StrEnum):
    CHANNEL_POST = "channel_post"
    BOT_BANNER = "bot_banner"
    SEARCH_RESULT = "search_result"
    VIDEO_BANNER = "video_banner"


class AdStatus(StrEnum):
    STOPPED = "stopped"
    READY_FOR_REVIEW = "ready_for_review"
    IN_REVIEW = "in_review"
    DECLINED = "declined"
    ACTIVE = "active"
    ON_HOLD = "on_hold"


class AdTargetType(StrEnum):
    CHANNELS = "channels"
    USERS = "users"
    BOTS = "bots"
    SEARCH = "search"


class AdButtonType(StrEnum):
    SUBSCRIBE = "subscribe"
    VIEW = "view"
    READ = "read"
    LEARN_MORE = "learn_more"
    DOWNLOAD = "download"
    OPEN = "open"
    SIGN_UP = "sign_up"
    BUY = "buy"
    ORDER = "order"
    PLAY = "play"
    TRY = "try"
    LEAVE_REQUEST = "leave_request"


class DeviceType(StrEnum):
    IOS = "ios"
    ANDROID = "android"
    MOBILE = "mobile"
    DESKTOP = "desktop"


class TransactionStatusValue(StrEnum):
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    COMPLETED = "completed"


class PixelEventType(StrEnum):
    PAGE_VIEW = "page_view"
    LANDING_VIEW = "landing_view"
    ADD_TO_CART = "add_to_cart"
    ADD_TO_WISHLIST = "add_to_wishlist"
    CUSTOMIZE_PRODUCT = "customize_product"
    INITIATE_CHECKOUT = "initiate_checkout"
    ADD_PAYMENT_INFO = "add_payment_info"
    PURCHASE = "purchase"
    CONTACT = "contact"
    LEAD = "lead"
    SCHEDULE = "schedule"
    COMPLETE_REGISTRATION = "complete_registration"
    SUBMIT_APPLICATION = "submit_application"
    START_TRIAL = "start_trial"
    SUBSCRIBE = "subscribe"
    VIEW_CONTENT = "view_content"
    SEARCH = "search"
    FIND_LOCATION = "find_location"
    DONATE = "donate"
    CUSTOM = "custom"


class PixelEventStatus(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"


class ConversionActionType(StrEnum):
    JOIN = "join"
    START_BOT = "start_bot"
    LAUNCH_MINIAPP = "launch_miniapp"
    SEND_MESSAGE = "send_message"
    PAGE_VIEW = "page_view"
    LANDING_VIEW = "landing_view"
    ADD_TO_CART = "add_to_cart"
    ADD_TO_WISHLIST = "add_to_wishlist"
    CUSTOMIZE_PRODUCT = "customize_product"
    INITIATE_CHECKOUT = "initiate_checkout"
    ADD_PAYMENT_INFO = "add_payment_info"
    PURCHASE = "purchase"
    CONTACT = "contact"
    LEAD = "lead"
    SCHEDULE = "schedule"
    COMPLETE_REGISTRATION = "complete_registration"
    SUBMIT_APPLICATION = "submit_application"
    START_TRIAL = "start_trial"
    SUBSCRIBE = "subscribe"
    VIEW_CONTENT = "view_content"
    SEARCH = "search"
    FIND_LOCATION = "find_location"
    DONATE = "donate"
    CUSTOM = "custom"


class StatsInterval(IntEnum):
    FIVE_MINUTES = 300
    ONE_DAY = 86400


class AccountTransactionPeerType(StrEnum):
    EXTERNAL = "external"
    MAIN_ACCOUNT = "main_account"
    RELATED_ACCOUNT = "related_account"
    AD = "ad"


class AdTransactionPeerType(StrEnum):
    ACCOUNT = "account"
    VIEWS = "views"


CURRENCY_PRECISION = {
    Currency.EUR: {"cpm_budget": 2, "amount": 5},
    Currency.TON: {"cpm_budget": 2, "amount": 5},
    Currency.XTR: {"cpm_budget": 0, "amount": 3},
}

PLACEMENT_DEFAULT_TARGET: dict[AdPlacement, AdTargetType] = {
    AdPlacement.CHANNEL_POST: AdTargetType.CHANNELS,
    AdPlacement.BOT_BANNER: AdTargetType.BOTS,
    AdPlacement.SEARCH_RESULT: AdTargetType.SEARCH,
    AdPlacement.VIDEO_BANNER: AdTargetType.USERS,
}
