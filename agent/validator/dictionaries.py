"""Read-only cache of target dictionaries for the validator."""

from __future__ import annotations

import time

from ads_api.client import AdsClient
from agent.validator.validator import Dictionaries


class DictionaryCache:
    def __init__(self, ads: AdsClient, ttl_seconds: int = 24 * 3600) -> None:
        self.ads = ads
        self.ttl_seconds = ttl_seconds
        self._value: Dictionaries | None = None
        self._loaded_at = 0.0

    async def get(self) -> Dictionaries:
        if self._value is not None and time.monotonic() - self._loaded_at < self.ttl_seconds:
            return self._value
        countries = await self.ads.get_target_countries_list()
        languages = await self.ads.get_target_languages_list()
        topics = await self.ads.get_target_topics_list()
        self._value = Dictionaries(
            countries={c.country_code for c in countries},
            languages={lang.language_code for lang in languages},
            topics={t.topic_id for t in topics},
        )
        self._loaded_at = time.monotonic()
        return self._value
