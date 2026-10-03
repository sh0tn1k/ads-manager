"""Async Telegram Ads API client covering every documented method."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from ads_api.catalog import ADS_API_METHOD_NAMES
from ads_api.config import AdsApiConfig
from ads_api.errors import AdsApiError, AdsAuthError, AdsRateLimitError, AdsTransportError
from ads_api.models import (
    Account,
    AccountInfo,
    AccountReportItem,
    AccountTransactionsList,
    Ad,
    AdList,
    AdPhoto,
    AdStatItem,
    AdTransactionsList,
    AdVideo,
    Audience,
    AudiencesList,
    InputAdSchedule,
    InputAdTarget,
    JsonId,
    Pixel,
    PixelEvent,
    PixelEventsList,
    RelatedAccountsList,
    TargetBot,
    TargetChannel,
    TargetCountry,
    TargetLanguage,
    TargetLocation,
    TargetLocationList,
    TargetTopic,
    TransactionStatus,
    UploadFile,
    WebsitePhoto,
    dump_param,
)

logger = logging.getLogger("ads_api")

T = TypeVar("T")

_AUTH_ERRORS = frozenset(
    {
        "UNAUTHORIZED",
        "ACCESS_TOKEN_INVALID",
        "ACCESS_TOKEN_EXPIRED",
        "AUTH_KEY_INVALID",
    }
)


class AdsClient:
    """Facade for https://promoteapi.telegram.org.

    Auth: ``Authorization: Bearer <token>`` (docs also allow ``access_token``).
    Idempotency: header ``Idempotency-Key`` or parameter ``idempotency_key``.
    Uploads: ``multipart/form-data``. Everything else: JSON POST.
    """

    def __init__(
        self,
        access_token: str,
        *,
        account_id: str | None = None,
        base_url: str = "https://promoteapi.telegram.org",
        timeout: float = 60.0,
        upload_timeout: float = 120.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("Ads API access_token is required")
        self.access_token = access_token
        self.default_account_id = account_id or None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.max_retries = max_retries
        self._client = client
        self._owns_client = client is None

    @classmethod
    def from_config(cls, config: AdsApiConfig, *, client: httpx.AsyncClient | None = None) -> AdsClient:
        return cls(
            config.access_token,
            account_id=config.account_id,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            upload_timeout=config.upload_timeout_seconds,
            max_retries=config.max_retries,
            client=client,
        )

    @classmethod
    def from_env(cls, **overrides: Any) -> AdsClient:
        config = AdsApiConfig(**overrides) if overrides else AdsApiConfig()  # type: ignore[arg-type]
        return cls.from_config(config)

    async def __aenter__(self) -> AdsClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "User-Agent": "telegram-ads-manager/0.1",
                },
                timeout=self.timeout,
            )
        return self._client

    def _with_account(self, params: dict[str, Any], account_id: str | None) -> dict[str, Any]:
        aid = account_id if account_id is not None else self.default_account_id
        if aid:
            params.setdefault("account_id", aid)
        return params

    def _prepare_json(self, params: Mapping[str, Any] | None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if not params:
            return body
        for key, value in params.items():
            if value is None:
                continue
            body[key] = dump_param(value)
        return body

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        upload: UploadFile | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Call any Ads API method and return the raw ``result`` field."""
        http = await self._ensure_client()
        payload = self._prepare_json(params)
        key = idempotency_key or payload.pop("idempotency_key", None)
        headers: dict[str, str] = {}
        if key:
            headers["Idempotency-Key"] = str(key)

        last_error: Exception | None = None
        attempts = max(1, self.max_retries)
        for attempt in range(attempts):
            try:
                if upload is not None:
                    filename, raw, content_type = upload.as_multipart()
                    files = {"file": (filename, raw, content_type)}
                    response = await http.post(
                        f"/{method}",
                        data={k: _form_value(v) for k, v in payload.items()},
                        files=files,
                        headers=headers,
                        timeout=self.upload_timeout,
                    )
                else:
                    response = await http.post(
                        f"/{method}",
                        json=payload,
                        headers=headers,
                        timeout=self.timeout,
                    )
            except httpx.HTTPError as exc:
                last_error = AdsTransportError(str(exc), method=method)
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
                raise last_error from exc

            replayed = response.headers.get("Idempotent-Replayed", "").lower() == "true"
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))

            if response.status_code == 429:
                wait = retry_after if retry_after is not None else 0.5 * (2**attempt)
                last_error = AdsRateLimitError(
                    "RATE_LIMIT",
                    method=method,
                    status_code=429,
                    retry_after=wait,
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(wait)
                    continue
                raise last_error

            data = _decode_json(response)
            if response.status_code in {401, 403}:
                err = _error_code(data) or "UNAUTHORIZED"
                raise AdsAuthError(err, method=method, status_code=response.status_code, payload=data)

            if isinstance(data, dict) and data.get("ok") is True:
                return data.get("result")

            err = _error_code(data) or f"HTTP_{response.status_code}"
            if err.upper() in _AUTH_ERRORS:
                raise AdsAuthError(
                    err,
                    method=method,
                    status_code=response.status_code,
                    payload=data,
                    replayed=replayed,
                )
            if err in {"IDEMPOTENT_REQUEST_IN_PROGRESS"} and attempt + 1 < attempts:
                await asyncio.sleep(0.4 * (2**attempt))
                continue
            raise AdsApiError(
                err,
                method=method,
                status_code=response.status_code,
                payload=data,
                replayed=replayed,
            )

        raise last_error or AdsTransportError("exhausted retries", method=method)

    def _parse(self, adapter: TypeAdapter[T], result: Any, *, method: str | None = None) -> T:
        try:
            return adapter.validate_python(result)
        except ValidationError as exc:
            where = method or str(adapter)
            logger.warning("failed to parse Ads API %s: %s", where, exc)
            raise AdsApiError(
                "RESPONSE_INVALID",
                method=method,
                payload={"detail": exc.errors()},
            ) from exc

    # --- accounts ---

    async def create_account(
        self,
        *,
        title: str,
        full_name: str,
        email: str,
        phone_number: str,
        country: str,
        city: str,
        legal_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> AccountInfo:
        result = await self.request(
            "createAccount",
            {
                "title": title,
                "full_name": full_name,
                "email": email,
                "phone_number": phone_number,
                "country": country,
                "city": city,
                "legal_name": legal_name,
                "idempotency_key": idempotency_key,
            },
        )
        return self._parse(TypeAdapter(AccountInfo), result)

    async def edit_account_info(
        self,
        *,
        account_id: str | None = None,
        title: str | None = None,
        full_name: str | None = None,
        email: str | None = None,
        phone_number: str | None = None,
        country: str | None = None,
        city: str | None = None,
        legal_name: str | None = None,
    ) -> AccountInfo:
        params = self._with_account(
            {
                "title": title,
                "full_name": full_name,
                "email": email,
                "phone_number": phone_number,
                "country": country,
                "city": city,
                "legal_name": legal_name,
            },
            account_id,
        )
        return self._parse(TypeAdapter(AccountInfo), await self.request("editAccountInfo", params))

    async def get_account_info(self, *, account_id: str | None = None) -> AccountInfo:
        params = self._with_account({}, account_id)
        return self._parse(TypeAdapter(AccountInfo), await self.request("getAccountInfo", params))

    async def get_related_accounts_list(
        self, *, offset: int | None = None, limit: int | None = None
    ) -> RelatedAccountsList:
        result = await self.request(
            "getRelatedAccountsList",
            {"offset": offset, "limit": limit},
        )
        return self._parse(TypeAdapter(RelatedAccountsList), result)

    async def get_current_account(self) -> Account:
        return self._parse(TypeAdapter(Account), await self.request("getCurrentAccount", {}))

    async def get_accounts_by_id(self, account_ids: list[str]) -> list[Account]:
        result = await self.request("getAccountsById", {"account_ids": account_ids})
        return self._parse(TypeAdapter(list[Account]), result)

    # --- billing ---

    async def increase_account_budget(
        self,
        *,
        account_id: str,
        amount: float,
        idempotency_key: str | None = None,
    ) -> TransactionStatus:
        result = await self.request(
            "increaseAccountBudget",
            {"account_id": account_id, "amount": amount, "idempotency_key": idempotency_key},
        )
        return self._parse(TypeAdapter(TransactionStatus), result)

    async def decrease_account_budget(
        self,
        *,
        account_id: str,
        amount: float,
        idempotency_key: str | None = None,
    ) -> TransactionStatus:
        result = await self.request(
            "decreaseAccountBudget",
            {"account_id": account_id, "amount": amount, "idempotency_key": idempotency_key},
        )
        return self._parse(TypeAdapter(TransactionStatus), result)

    async def get_transaction_status(self, transaction_id: str) -> TransactionStatus:
        result = await self.request("getTransactionStatus", {"transaction_id": transaction_id})
        return self._parse(TypeAdapter(TransactionStatus), result)

    async def get_account_transactions_list(
        self,
        *,
        account_id: str | None = None,
        offset: int | str | None = None,
        limit: int | None = None,
    ) -> AccountTransactionsList:
        params = self._with_account({"offset": offset, "limit": limit}, account_id)
        result = await self.request("getAccountTransactionsList", params)
        return self._parse(
            TypeAdapter(AccountTransactionsList), result, method="getAccountTransactionsList"
        )

    async def get_account_stats(
        self,
        *,
        from_time: int,
        to_time: int,
        interval: int,
        account_id: str | None = None,
    ) -> list[AdStatItem]:
        params = self._with_account(
            {"from_time": from_time, "to_time": to_time, "interval": interval},
            account_id,
        )
        return self._parse(TypeAdapter(list[AdStatItem]), await self.request("getAccountStats", params))

    async def get_account_report(
        self, *, year: int, month: int, account_id: str | None = None
    ) -> list[AccountReportItem]:
        params = self._with_account({"year": year, "month": month}, account_id)
        return self._parse(
            TypeAdapter(list[AccountReportItem]),
            await self.request("getAccountReport", params),
        )

    # --- media ---

    async def upload_ad_photo(
        self,
        file: UploadFile | bytes | str | Any,
        *,
        account_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> AdPhoto:
        upload = _as_upload(file, filename=filename, content_type=content_type)
        params = self._with_account({}, account_id)
        result = await self.request("uploadAdPhoto", params, upload=upload)
        return self._parse(TypeAdapter(AdPhoto), result)

    async def upload_ad_video(
        self,
        file: UploadFile | bytes | str | Any,
        *,
        account_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> AdVideo:
        upload = _as_upload(file, filename=filename, content_type=content_type)
        params = self._with_account({}, account_id)
        result = await self.request("uploadAdVideo", params, upload=upload)
        return self._parse(TypeAdapter(AdVideo), result)

    async def upload_website_photo(
        self,
        file: UploadFile | bytes | str | Any,
        *,
        account_id: str | None = None,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> WebsitePhoto:
        upload = _as_upload(file, filename=filename, content_type=content_type)
        params = self._with_account({}, account_id)
        result = await self.request("uploadWebsitePhoto", params, upload=upload)
        return self._parse(TypeAdapter(WebsitePhoto), result)

    # --- ads ---

    async def create_ad(
        self,
        *,
        title: str,
        promote_url: str,
        cpm: float,
        target: InputAdTarget | Mapping[str, Any],
        placement: str | None = None,
        account_id: str | None = None,
        text: str | None = None,
        photo_id: str | None = None,
        video_id: str | None = None,
        impression_frequency: int | None = None,
        website_name: str | None = None,
        website_photo_id: str | None = None,
        button: str | None = None,
        conversion_event_id: str | None = None,
        additional_info: str | None = None,
        show_userpic: bool | None = None,
        initial_budget: float | None = None,
        daily_budget_limit: float | None = None,
        is_paused: bool | None = None,
        activate_date: int | None = None,
        deactivate_date: int | None = None,
        schedule: InputAdSchedule | Mapping[str, Any] | None = None,
        return_target: bool | None = True,
        idempotency_key: str | None = None,
    ) -> Ad:
        params = self._with_account(
            {
                "title": title,
                "text": text,
                "photo_id": photo_id,
                "video_id": video_id,
                "promote_url": promote_url,
                "cpm": cpm,
                "placement": placement,
                "target": target,
                "impression_frequency": impression_frequency,
                "website_name": website_name,
                "website_photo_id": website_photo_id,
                "button": button,
                "conversion_event_id": conversion_event_id,
                "additional_info": additional_info,
                "show_userpic": show_userpic,
                "initial_budget": initial_budget,
                "daily_budget_limit": daily_budget_limit,
                "is_paused": is_paused,
                "activate_date": activate_date,
                "deactivate_date": deactivate_date,
                "schedule": schedule,
                "return_target": return_target,
                "idempotency_key": idempotency_key,
            },
            account_id,
        )
        return self._parse(TypeAdapter(Ad), await self.request("createAd", params))

    async def edit_ad(
        self,
        *,
        ad_id: int,
        account_id: str | None = None,
        title: str | None = None,
        text: str | None = None,
        photo_id: str | None = None,
        video_id: str | None = None,
        promote_url: str | None = None,
        cpm: float | None = None,
        impression_frequency: int | None = None,
        website_name: str | None = None,
        website_photo_id: str | None = None,
        button: str | None = None,
        conversion_event_id: str | None = None,
        additional_info: str | None = None,
        show_userpic: bool | None = None,
        daily_budget_limit: float | None = None,
        is_paused: bool | None = None,
        activate_date: int | None = None,
        deactivate_date: int | None = None,
        schedule: InputAdSchedule | Mapping[str, Any] | bool | None = None,
        return_target: bool | None = True,
    ) -> Ad:
        params = self._with_account(
            {
                "ad_id": ad_id,
                "title": title,
                "text": text,
                "photo_id": photo_id,
                "video_id": video_id,
                "promote_url": promote_url,
                "cpm": cpm,
                "impression_frequency": impression_frequency,
                "website_name": website_name,
                "website_photo_id": website_photo_id,
                "button": button,
                "conversion_event_id": conversion_event_id,
                "additional_info": additional_info,
                "show_userpic": show_userpic,
                "daily_budget_limit": daily_budget_limit,
                "is_paused": is_paused,
                "activate_date": activate_date,
                "deactivate_date": deactivate_date,
                "schedule": schedule,
                "return_target": return_target,
            },
            account_id,
        )
        return self._parse(TypeAdapter(Ad), await self.request("editAd", params))

    async def edit_ad_status(self, **params: Any) -> Any:
        """Deprecated Ads API method. Prefer :meth:`edit_ad` with ``is_paused``."""
        logger.warning("editAdStatus is deprecated; prefer edit_ad(is_paused=...)")
        return await self.request("editAdStatus", params)

    async def increase_ad_budget(
        self,
        *,
        ad_id: int,
        amount: float,
        account_id: str | None = None,
        return_target: bool | None = None,
        idempotency_key: str | None = None,
    ) -> Ad:
        params = self._with_account(
            {
                "ad_id": ad_id,
                "amount": amount,
                "return_target": return_target,
                "idempotency_key": idempotency_key,
            },
            account_id,
        )
        return self._parse(TypeAdapter(Ad), await self.request("increaseAdBudget", params))

    async def decrease_ad_budget(
        self,
        *,
        ad_id: int,
        amount: float,
        account_id: str | None = None,
        return_target: bool | None = None,
        idempotency_key: str | None = None,
    ) -> Ad:
        params = self._with_account(
            {
                "ad_id": ad_id,
                "amount": amount,
                "return_target": return_target,
                "idempotency_key": idempotency_key,
            },
            account_id,
        )
        return self._parse(TypeAdapter(Ad), await self.request("decreaseAdBudget", params))

    async def submit_ad_for_review(
        self,
        *,
        ad_id: int,
        account_id: str | None = None,
        return_target: bool | None = True,
    ) -> Ad:
        params = self._with_account(
            {"ad_id": ad_id, "return_target": return_target},
            account_id,
        )
        return self._parse(TypeAdapter(Ad), await self.request("submitAdForReview", params))

    async def delete_ad(self, *, ad_id: int, account_id: str | None = None) -> bool:
        params = self._with_account({"ad_id": ad_id}, account_id)
        result = await self.request("deleteAd", params)
        return bool(result)

    async def get_ads_by_id(
        self,
        ad_ids: list[int],
        *,
        account_id: str | None = None,
        return_target: bool | None = True,
    ) -> list[Ad]:
        params = self._with_account({"ad_ids": ad_ids, "return_target": return_target}, account_id)
        return self._parse(TypeAdapter(list[Ad]), await self.request("getAdsById", params))

    async def get_ads_list(
        self,
        *,
        account_id: str | None = None,
        offset: int | str | None = None,
        limit: int | None = None,
        return_target: bool | None = None,
    ) -> AdList:
        params = self._with_account(
            {"offset": offset, "limit": limit, "return_target": return_target},
            account_id,
        )
        return self._parse(TypeAdapter(AdList), await self.request("getAdsList", params))

    async def iter_ads(
        self,
        *,
        account_id: str | None = None,
        limit: int = 100,
        return_target: bool | None = None,
    ) -> AsyncIterator[Ad]:
        offset: int | str | None = None
        while True:
            page = await self.get_ads_list(
                account_id=account_id, offset=offset, limit=limit, return_target=return_target
            )
            for ad in page.ads:
                yield ad
            if not page.next_offset:
                break
            offset = page.next_offset

    async def get_ad_transactions_list(
        self,
        *,
        ad_id: int,
        account_id: str | None = None,
        offset: int | str | None = None,
        limit: int | None = None,
    ) -> AdTransactionsList:
        params = self._with_account(
            {"ad_id": ad_id, "offset": offset, "limit": limit},
            account_id,
        )
        return self._parse(
            TypeAdapter(AdTransactionsList),
            await self.request("getAdTransactionsList", params),
            method="getAdTransactionsList",
        )

    async def get_ad_stats(
        self,
        *,
        ad_id: int,
        from_time: int,
        to_time: int,
        interval: int,
        account_id: str | None = None,
    ) -> list[AdStatItem]:
        params = self._with_account(
            {
                "ad_id": ad_id,
                "from_time": from_time,
                "to_time": to_time,
                "interval": interval,
            },
            account_id,
        )
        return self._parse(TypeAdapter(list[AdStatItem]), await self.request("getAdStats", params))

    # --- audiences ---

    async def create_audience(
        self,
        *,
        title: str,
        account_id: str | None = None,
        add_phones: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> Audience:
        params = self._with_account(
            {"title": title, "add_phones": add_phones, "idempotency_key": idempotency_key},
            account_id,
        )
        return self._parse(TypeAdapter(Audience), await self.request("createAudience", params))

    async def edit_audience(
        self,
        *,
        audience_id: int,
        account_id: str | None = None,
        title: str | None = None,
        add_phones: list[str] | None = None,
        remove_phones: list[str] | None = None,
        reset_phones: bool | None = None,
    ) -> Audience:
        params = self._with_account(
            {
                "audience_id": audience_id,
                "title": title,
                "add_phones": add_phones,
                "remove_phones": remove_phones,
                "reset_phones": reset_phones,
            },
            account_id,
        )
        return self._parse(TypeAdapter(Audience), await self.request("editAudience", params))

    async def delete_audience(self, *, audience_id: int, account_id: str | None = None) -> bool:
        params = self._with_account({"audience_id": audience_id}, account_id)
        return bool(await self.request("deleteAudience", params))

    async def get_audiences_by_id(
        self, audience_ids: list[int], *, account_id: str | None = None
    ) -> list[Audience]:
        params = self._with_account({"audience_ids": audience_ids}, account_id)
        return self._parse(
            TypeAdapter(list[Audience]),
            await self.request("getAudiencesById", params),
        )

    async def get_audiences_list(
        self,
        *,
        account_id: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> AudiencesList:
        params = self._with_account({"offset": offset, "limit": limit}, account_id)
        return self._parse(TypeAdapter(AudiencesList), await self.request("getAudiencesList", params))

    # --- pixels ---

    async def create_pixel(
        self, *, account_id: str | None = None, idempotency_key: str | None = None
    ) -> Pixel:
        params = self._with_account({"idempotency_key": idempotency_key}, account_id)
        return self._parse(TypeAdapter(Pixel), await self.request("createPixel", params))

    async def get_pixel(self, *, account_id: str | None = None) -> Pixel:
        params = self._with_account({}, account_id)
        return self._parse(TypeAdapter(Pixel), await self.request("getPixel", params))

    async def create_pixel_event(
        self,
        *,
        title: str,
        type: str,
        account_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> PixelEvent:
        params = self._with_account(
            {"title": title, "type": type, "idempotency_key": idempotency_key},
            account_id,
        )
        return self._parse(TypeAdapter(PixelEvent), await self.request("createPixelEvent", params))

    async def edit_pixel_event(
        self,
        *,
        event_id: str,
        title: str,
        account_id: str | None = None,
    ) -> PixelEvent:
        params = self._with_account({"event_id": event_id, "title": title}, account_id)
        return self._parse(TypeAdapter(PixelEvent), await self.request("editPixelEvent", params))

    async def delete_pixel_event(self, *, event_id: str, account_id: str | None = None) -> bool:
        params = self._with_account({"event_id": event_id}, account_id)
        return bool(await self.request("deletePixelEvent", params))

    async def get_pixel_events_by_id(
        self, event_ids: list[str], *, account_id: str | None = None
    ) -> list[PixelEvent]:
        params = self._with_account({"event_ids": event_ids}, account_id)
        return self._parse(
            TypeAdapter(list[PixelEvent]),
            await self.request("getPixelEventsById", params),
        )

    async def get_pixel_events_list(
        self,
        *,
        account_id: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> PixelEventsList:
        params = self._with_account({"offset": offset, "limit": limit}, account_id)
        return self._parse(
            TypeAdapter(PixelEventsList),
            await self.request("getPixelEventsList", params),
        )

    # --- targeting dictionaries ---

    async def get_target_languages_list(self) -> list[TargetLanguage]:
        return self._parse(
            TypeAdapter(list[TargetLanguage]),
            await self.request("getTargetLanguagesList", {}),
        )

    async def get_target_topics_list(self) -> list[TargetTopic]:
        return self._parse(
            TypeAdapter(list[TargetTopic]),
            await self.request("getTargetTopicsList", {}),
        )

    async def get_target_countries_list(self) -> list[TargetCountry]:
        return self._parse(
            TypeAdapter(list[TargetCountry]),
            await self.request("getTargetCountriesList", {}),
        )

    async def get_target_locations_by_id(self, location_ids: list[int]) -> list[TargetLocation]:
        result = await self.request("getTargetLocationsById", {"location_ids": location_ids})
        return self._parse(TypeAdapter(list[TargetLocation]), result)

    async def get_target_locations_list(
        self,
        *,
        country_code: str,
        query: str,
        offset: str | None = None,
        limit: int | None = None,
    ) -> TargetLocationList:
        result = await self.request(
            "getTargetLocationsList",
            {
                "country_code": country_code,
                "query": query,
                "offset": offset,
                "limit": limit,
            },
        )
        return self._parse(TypeAdapter(TargetLocationList), result)

    async def get_target_channel(
        self, channel_id: JsonId, *, for_excluding: bool | None = None
    ) -> TargetChannel:
        result = await self.request(
            "getTargetChannel",
            {"channel_id": channel_id, "for_excluding": for_excluding},
        )
        return self._parse(TypeAdapter(TargetChannel), result)

    async def get_target_bot(self, bot_id: JsonId) -> TargetBot:
        result = await self.request("getTargetBot", {"bot_id": bot_id})
        return self._parse(TypeAdapter(TargetBot), result)

    def new_idempotency_key(self) -> str:
        return str(uuid.uuid4())


# CamelCase aliases so an agent can call createAd / getAdsList the way the docs name them.
for _method in ADS_API_METHOD_NAMES:
    _snake = _camel_to_snake = "".join(
        f"_{ch.lower()}" if ch.isupper() else ch for ch in _method
    ).lstrip("_")
    if hasattr(AdsClient, _snake) and not hasattr(AdsClient, _method):
        setattr(AdsClient, _method, getattr(AdsClient, _snake))


def _as_upload(
    file: UploadFile | bytes | str | Any,
    *,
    filename: str | None,
    content_type: str | None,
) -> UploadFile:
    if isinstance(file, UploadFile):
        return file
    return UploadFile(file, filename=filename, content_type=content_type)


def _form_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _decode_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"ok": False, "error": response.text[:500] or f"HTTP_{response.status_code}"}


def _error_code(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    err = data.get("error") or data.get("description")
    if err is None:
        return None
    return str(err)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
