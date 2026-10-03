from __future__ import annotations

from typing import Any


class AdsApiError(Exception):
    """Error returned by the Telegram Ads API (`ok: false`)."""

    def __init__(
        self,
        error: str,
        *,
        method: str | None = None,
        status_code: int | None = None,
        payload: Any = None,
        replayed: bool = False,
    ) -> None:
        super().__init__(error)
        self.error = error
        self.method = method
        self.status_code = status_code
        self.payload = payload
        self.replayed = replayed

    def __str__(self) -> str:
        where = f" in {self.method}" if self.method else ""
        code = f" HTTP {self.status_code}" if self.status_code else ""
        return f"Ads API error{where}{code}: {self.error}"


class AdsAuthError(AdsApiError):
    """Invalid or missing Ads API token."""


class AdsRateLimitError(AdsApiError):
    """HTTP 429 / retryable throttling."""

    def __init__(self, error: str, *, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(error, **kwargs)
        self.retry_after = retry_after


class AdsTransportError(Exception):
    """Network / HTTP failure talking to the Ads API."""

    def __init__(self, message: str, *, method: str | None = None) -> None:
        super().__init__(message)
        self.method = method
