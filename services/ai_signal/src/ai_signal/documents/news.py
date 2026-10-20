"""``news-raw`` message to ``NewsRecord`` with deterministic identities (doc 20, 6.1).

The poller (``kis_ingestion.poller.news``) defines
``idempotency_key = kis_news:{symbol}:{source_doc_id}:{sha256(title)}`` and
``raw_id = UUIDv5(NAMESPACE_URL, "invest-view:news-raw:" + idempotency_key)``.
The poller writes the key into the ``idempotency_key`` value field, which is the
only source the processor reads.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ai_signal.consumers.frame import ConsumedMessage

NEWS_RAW_TOPIC = "news-raw"
POLLER_SOURCE = "kis_news"
DOCUMENT_SOURCE = "KIS_NEWS"
PUBLISHER_FIELD = "dorg"

_BRACKETED = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|<[^>]*>")
_NON_WORD = re.compile(r"[\W_]+")


def news_idempotency_key(symbol: str, source_doc_id: str, title: str) -> str:
    title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()
    return f"{POLLER_SOURCE}:{symbol}:{source_doc_id}:{title_hash}"


def news_raw_id(idempotency_key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"invest-view:news-raw:{idempotency_key}")


def document_id(source: str, source_doc_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"invest-view:document:{source}:{source_doc_id}")


def dedup_key(title: str) -> str:
    """Hash of the title without bracket tags, whitespace and symbols (6.5)."""
    normalized = _NON_WORD.sub("", _BRACKETED.sub("", title)).lower()
    return hashlib.sha256((normalized or title).encode("utf-8")).hexdigest()


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    raise TypeError(f"unsupported timestamp {value!r}")


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class NewsRecord:
    raw_id: UUID
    idempotency_key: str
    symbol: str
    source_doc_id: str
    title: str
    provider_code: str | None
    category_code: str | None
    published_at: datetime
    observed_at: datetime
    received_at: datetime
    source_payload: Mapping[str, str]

    @property
    def document_id(self) -> UUID:
        return document_id(DOCUMENT_SOURCE, self.source_doc_id)

    @property
    def publisher(self) -> str | None:
        return _optional_text(self.source_payload.get(PUBLISHER_FIELD))

    @property
    def dedup_key(self) -> str:
        return dedup_key(self.title)

    @classmethod
    def from_message(cls, message: ConsumedMessage) -> NewsRecord:
        value: Mapping[str, Any] = message.value
        key = str(value["idempotency_key"])
        payload = value.get("source_payload") or {}
        return cls(
            raw_id=news_raw_id(key),
            idempotency_key=key,
            symbol=str(value["symbol"]),
            source_doc_id=str(value["source_doc_id"]),
            title=str(value["title"]),
            provider_code=_optional_text(value.get("provider_code")),
            category_code=_optional_text(value.get("category_code")),
            published_at=_timestamp(value["published_at"]),
            observed_at=_timestamp(value["observed_at"]),
            received_at=_timestamp(value["received_at"]),
            source_payload={str(k): str(v) for k, v in dict(payload).items()},
        )
