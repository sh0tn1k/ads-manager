"""Complete Telegram Ads API method inventory from ads.telegram.org/docs/api."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdsMethod:
    name: str
    area: str
    returns: str
    returns_array: bool = False
    deprecated: bool = False
    upload: bool = False
    description: str = ""


ADS_API_METHODS: tuple[AdsMethod, ...] = (
    AdsMethod("createAccount", "accounts", "AccountInfo", description="Create a related advertiser account."),
    AdsMethod("editAccountInfo", "accounts", "AccountInfo", description="Edit advertiser account info."),
    AdsMethod("getAccountInfo", "accounts", "AccountInfo", description="Get advertiser account info."),
    AdsMethod("getRelatedAccountsList", "accounts", "RelatedAccountsList", description="List related accounts."),
    AdsMethod("increaseAccountBudget", "billing", "TransactionStatus", description="Move budget into a related account."),
    AdsMethod("decreaseAccountBudget", "billing", "TransactionStatus", description="Move budget out of a related account."),
    AdsMethod("getTransactionStatus", "billing", "TransactionStatus", description="Poll a budget transfer."),
    AdsMethod("getCurrentAccount", "accounts", "Account", description="Get the token's current account."),
    AdsMethod("getAccountsById", "accounts", "Account", returns_array=True, description="Get accounts by ids."),
    AdsMethod("getAccountTransactionsList", "billing", "AccountTransactionsList", description="Account ledger."),
    AdsMethod("getAccountStats", "stats", "AdStatItem", returns_array=True, description="Account time-series stats."),
    AdsMethod("getAccountReport", "stats", "AccountReportItem", returns_array=True, description="Monthly active-ads report."),
    AdsMethod("uploadAdPhoto", "media", "AdPhoto", upload=True, description="Upload an ad photo."),
    AdsMethod("uploadAdVideo", "media", "AdVideo", upload=True, description="Upload an ad video."),
    AdsMethod("uploadWebsitePhoto", "media", "WebsitePhoto", upload=True, description="Upload a website photo."),
    AdsMethod("createAd", "ads", "Ad", description="Create an advertisement."),
    AdsMethod("editAd", "ads", "Ad", description="Edit an advertisement."),
    AdsMethod(
        "editAdStatus",
        "ads",
        "Ad",
        deprecated=True,
        description="Deprecated. Use editAd with is_paused.",
    ),
    AdsMethod("increaseAdBudget", "billing", "Ad", description="Top up an ad budget."),
    AdsMethod("decreaseAdBudget", "billing", "Ad", description="Withdraw from an ad budget."),
    AdsMethod("submitAdForReview", "ads", "Ad", description="Submit an ad that is ready_for_review."),
    AdsMethod("deleteAd", "ads", "bool", description="Delete an inactive ad."),
    AdsMethod("getAdsById", "ads", "Ad", returns_array=True, description="Get ads by ids."),
    AdsMethod("getAdsList", "ads", "AdList", description="Paginated ads list."),
    AdsMethod("getAdTransactionsList", "billing", "AdTransactionsList", description="Ad budget ledger."),
    AdsMethod("getAdStats", "stats", "AdStatItem", returns_array=True, description="Ad time-series stats."),
    AdsMethod("createAudience", "audiences", "Audience", description="Create a retargeting audience."),
    AdsMethod("editAudience", "audiences", "Audience", description="Edit audience phones/title."),
    AdsMethod("deleteAudience", "audiences", "bool", description="Delete an unused audience."),
    AdsMethod("getAudiencesById", "audiences", "Audience", returns_array=True, description="Get audiences by ids."),
    AdsMethod("getAudiencesList", "audiences", "AudiencesList", description="List audiences."),
    AdsMethod("createPixel", "pixels", "Pixel", description="Create a website pixel tag."),
    AdsMethod("getPixel", "pixels", "Pixel", description="Get the account pixel."),
    AdsMethod("createPixelEvent", "pixels", "PixelEvent", description="Create a conversion event."),
    AdsMethod("editPixelEvent", "pixels", "PixelEvent", description="Rename a pixel event."),
    AdsMethod("deletePixelEvent", "pixels", "bool", description="Delete an unused pixel event."),
    AdsMethod("getPixelEventsById", "pixels", "PixelEvent", returns_array=True, description="Get pixel events by ids."),
    AdsMethod("getPixelEventsList", "pixels", "PixelEventsList", description="List pixel events."),
    AdsMethod("getTargetLanguagesList", "targeting", "TargetLanguage", returns_array=True, description="Language dictionary."),
    AdsMethod("getTargetTopicsList", "targeting", "TargetTopic", returns_array=True, description="Topic dictionary."),
    AdsMethod("getTargetCountriesList", "targeting", "TargetCountry", returns_array=True, description="Country dictionary."),
    AdsMethod("getTargetLocationsById", "targeting", "TargetLocation", returns_array=True, description="Locations by ids."),
    AdsMethod("getTargetLocationsList", "targeting", "TargetLocationList", description="Search locations."),
    AdsMethod("getTargetChannel", "targeting", "TargetChannel", description="Resolve a channel username/id."),
    AdsMethod("getTargetBot", "targeting", "TargetBot", description="Resolve a bot username/id."),
)

ADS_API_METHOD_NAMES: frozenset[str] = frozenset(m.name for m in ADS_API_METHODS)
