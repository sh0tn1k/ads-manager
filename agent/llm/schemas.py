"""Strict output schemas of the four LLM tasks (section 10). extra=forbid everywhere.

The schemas contain no fields for promote_url, budgets, CPM, targets or tools: whatever untrusted
content says, the model cannot express those through its output.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl

_DIGITS = re.compile(r"\d")

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Sources = Annotated[list[HttpUrl], Field(min_length=1, max_length=5)]
Button = Literal["subscribe", "view", "read", "learn_more", "download", "open", "sign_up", "buy", "order", "play",
                 "try", "leave_request"]


def _no_digits(value: str) -> str:
    if _DIGITS.search(value):
        raise ValueError("numbers are not allowed here: card numbers come only from the model")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchQueryIdea(_Strict):
    query: Annotated[str, Field(min_length=1, max_length=64)]
    confidence: Confidence
    sources: Sources


class GeoPrior(_Strict):
    country_code: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    direction: Literal["higher", "lower"]
    confidence: Confidence
    sources: Sources


class TopicPrior(_Strict):
    topic_id: Annotated[int, Field(ge=1, le=100000)]
    direction: Literal["higher", "lower"]
    confidence: Confidence
    sources: Sources


class EventIdea(_Strict):
    date: dt.date
    title: Annotated[str, Field(min_length=1, max_length=120)]
    confidence: Confidence
    sources: Sources


class ResearchOutput(_Strict):
    search_queries: Annotated[list[SearchQueryIdea], Field(max_length=20)] = []
    geo_priors: Annotated[list[GeoPrior], Field(max_length=20)] = []
    topic_priors: Annotated[list[TopicPrior], Field(max_length=20)] = []
    events: Annotated[list[EventIdea], Field(max_length=20)] = []


class CreativeVariant(_Strict):
    angle: Annotated[str, Field(pattern=r"^[a-z0-9_]{1,32}$")]
    text: Annotated[str, Field(min_length=1, max_length=160)]
    length: Literal["short", "medium", "long"]
    cta: Annotated[str, Field(max_length=40)] = ""
    button: Button | None = None


class CreativesOutput(_Strict):
    variants: Annotated[list[CreativeVariant], Field(min_length=1, max_length=10)]


class PolicyCheckOutput(_Strict):
    compliant: bool
    issues: Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=10)] = []
    fixed_text: Annotated[str, Field(min_length=1, max_length=160)] | None = None


class ExplanationOutput(_Strict):
    rationale_ru: Annotated[str, Field(min_length=1, max_length=600), AfterValidator(_no_digits)]
