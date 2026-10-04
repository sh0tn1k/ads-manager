"""Static rules from docs.html (see docs/agent-recon.md section 5)."""

from __future__ import annotations

from ads_api.enums import PLACEMENT_DEFAULT_TARGET

# [VERIFY] docs.html has no placement <-> target.type table; the repo mapping is used until the owner
# confirms more pairs (docs/agent-questions.md #7). Extend here only.
ALLOWED_PLACEMENT_TARGETS: frozenset[tuple[str, str]] = frozenset(
    (placement.value, target.value) for placement, target in PLACEMENT_DEFAULT_TARGET.items()
)
# v1 supports users, search and channels (section 9). bots are out of scope.
SUPPORTED_TARGET_TYPES = frozenset({"users", "search", "channels"})

TITLE_MAX_BYTES = 128
TEXT_MAX_CHARS = 160
PROMOTE_URL_MAX = 256
IMPRESSION_FREQUENCY_RANGE = (1, 4)
SEARCH_QUERIES_MAX = 10
COUNTRIES_MAX = 8
LOCATIONS_MAX = 20
LANGUAGES_MAX = 8
TOPICS_TOTAL_MAX = 20
CHANNELS_TOTAL_MAX = 100
AUDIENCES_MAX = 4
AUDIENCES_TOTAL_MAX = 10
WEEK_HOURS_MASK_MAX = 16777215
DEVICES = frozenset({"ios", "android", "mobile", "desktop"})
BUTTONS = frozenset(
    {"subscribe", "view", "read", "learn_more", "download", "open", "sign_up", "buy", "order", "play", "try", "leave_request"}
)
MONEY_DECIMALS = {"TON": 2, "EUR": 2, "XTR": 0}

USERS_FIELDS = frozenset(
    {
        "type",
        "country_codes",
        "location_ids",
        "language_codes",
        "topic_ids",
        "intersect_topics",
        "exclude_topic_ids",
        "channel_ids",
        "exclude_channel_ids",
        "audience_ids",
        "exclude_audience_ids",
        "device",
        "exclude_political_channels",
        "political_channels_only",
    }
)
CHANNELS_FIELDS = frozenset({"type", "language_codes", "topic_ids", "exclude_topic_ids", "channel_ids", "exclude_channel_ids"})
SEARCH_FIELDS = frozenset({"type", "search_queries"})
