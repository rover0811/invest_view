import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, cast

from typing_extensions import override
from uuid import NAMESPACE_URL, uuid5

from kis_ingestion.market_session import is_trading_window

from .base import KST, Poller, now_kst
from .kis_client import KISRestClient
from .symbols import SymbolSource


logger = logging.getLogger(__name__)

NEWS_TITLE_PATH = "/uapi/domestic-stock/v1/quotations/news-title"
NEWS_TITLE_TR_ID = "FHKST01011800"
NEWS_PAGE_SIZE = 40
SOURCE = "kis_news"
DEFAULT_SEEN_CAPACITY = 50_000


class NewsProducer(Protocol):
    def publish(
        self,
        *,
        key: str,
        value: Mapping[str, object],
        headers: Sequence[tuple[str, str]] = (),
        on_delivered: Callable[[], None] | None = None,
    ) -> None: ...

    def flush(self, timeout: float = 30.0) -> int: ...


@dataclass(frozen=True)
class NewsTitle:
    """One observed version of a KIS news title, shaped like schemas/news-raw.avsc.

    Identity follows docs/design/20-data-contracts.md: the same business fact
    (symbol, provider document id, title text) always maps to the same
    idempotency_key and raw_id, independent of poll time or Kafka offset.
    A changed title under the same document id is a new raw version.
    """

    symbol: str
    source_doc_id: str
    title: str
    provider_code: str | None
    category_code: str | None
    published_at: datetime
    observed_at: datetime
    received_at: datetime
    source_payload: dict[str, str] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        title_hash = hashlib.sha256(self.title.encode("utf-8")).hexdigest()
        return f"{SOURCE}:{self.symbol}:{self.source_doc_id}:{title_hash}"

    @property
    def raw_id(self) -> str:
        return str(uuid5(NAMESPACE_URL, f"invest-view:news-raw:{self.idempotency_key}"))

    def to_avro(self) -> dict[str, object]:
        return {
            "raw_id": self.raw_id,
            "idempotency_key": self.idempotency_key,
            "symbol": self.symbol,
            "source_doc_id": self.source_doc_id,
            "title": self.title,
            "provider_code": self.provider_code,
            "category_code": self.category_code,
            "published_at": _epoch_millis(self.published_at),
            "observed_at": _epoch_millis(self.observed_at),
            "received_at": _epoch_millis(self.received_at),
            "source_payload": dict(self.source_payload),
        }


def parse_news_row(
    symbol: str,
    row: Mapping[str, object],
    observed_at: datetime,
    received_at: datetime,
) -> NewsTitle | None:
    payload = {str(name): "" if value is None else str(value) for name, value in row.items()}
    source_doc_id = payload.get("cntt_usiq_srno", "").strip()
    title = payload.get("hts_pbnt_titl_cntt", "").strip()
    data_dt = payload.get("data_dt", "")
    data_tm = payload.get("data_tm", "")
    if not source_doc_id or not title:
        return None
    try:
        published_at = datetime.strptime(f"{data_dt}{data_tm}", "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None
    return NewsTitle(
        symbol=symbol,
        source_doc_id=source_doc_id,
        title=title,
        provider_code=payload.get("news_ofer_entp_code") or None,
        category_code=payload.get("news_lrdv_code") or None,
        published_at=published_at,
        observed_at=observed_at,
        received_at=received_at,
        source_payload=payload,
    )


class NewsPoller(Poller[NewsTitle]):
    """Polls KIS news titles for every reference symbol and publishes unseen versions.

    Each cycle reads the newest page per symbol and pages backwards while the
    page is full and contains nothing already published, up to max_pages.
    The in-memory seen set only saves API calls and Kafka writes; after a
    restart the same records are republished with the same raw_id, and the
    bronze sink deduplicates on that identity.
    """

    def __init__(
        self,
        kis_client: KISRestClient,
        symbol_source: SymbolSource,
        producer: NewsProducer,
        *,
        interval_seconds: float = 60.0,
        max_pages: int = 3,
        flush_timeout_seconds: float = 10.0,
        seen_capacity: int = DEFAULT_SEEN_CAPACITY,
        clock: Callable[[], datetime] = now_kst,
    ) -> None:
        self._kis_client: KISRestClient = kis_client
        self._symbol_source: SymbolSource = symbol_source
        self._producer: NewsProducer = producer
        self._interval_seconds: float = interval_seconds
        self._max_pages: int = max_pages
        self._flush_timeout: float = flush_timeout_seconds
        self._seen_capacity: int = seen_capacity
        self._clock: Callable[[], datetime] = clock
        self._seen: OrderedDict[str, None] = OrderedDict()

    @property
    @override
    def name(self) -> str:
        return "news"

    @property
    @override
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @override
    def is_active(self, now: datetime) -> bool:
        return is_trading_window(now.astimezone(KST))

    @override
    async def fetch(self, now: datetime) -> Sequence[NewsTitle]:
        records: list[NewsTitle] = []
        for symbol in await self._symbol_source.list_symbols():
            try:
                records.extend(await self.fetch_symbol(symbol, now))
            except Exception:
                logger.exception("News fetch failed symbol=%s", symbol)
        return records

    async def fetch_symbol(self, symbol: str, observed_at: datetime) -> list[NewsTitle]:
        records: dict[str, NewsTitle] = {}
        cursor = ("", "")
        for _ in range(self._max_pages):
            rows = await self._fetch_page(symbol, cursor)
            received_at = self._clock()
            reached_seen = False
            for row in rows:
                record = parse_news_row(symbol, row, observed_at, received_at)
                if record is None:
                    continue
                if record.raw_id in self._seen:
                    reached_seen = True
                    continue
                _ = records.setdefault(record.raw_id, record)
            if reached_seen or len(rows) < NEWS_PAGE_SIZE:
                break
            next_cursor = _oldest_cursor(rows)
            if next_cursor is None or next_cursor == cursor:
                break
            cursor = next_cursor
        return list(records.values())

    @override
    async def publish(self, records: Sequence[NewsTitle]) -> int:
        delivered: list[str] = []
        for record in records:
            try:
                self._producer.publish(
                    key=record.symbol,
                    value=record.to_avro(),
                    headers=[
                        ("raw_id", record.raw_id),
                        ("idempotency_key", record.idempotency_key),
                    ],
                    on_delivered=_append_to(delivered, record.raw_id),
                )
            except BufferError:
                logger.error("Kafka queue full, deferring %d news records", len(records))
                break
        remaining = await asyncio.to_thread(self._producer.flush, self._flush_timeout)
        if remaining > 0:
            logger.warning("News flush incomplete: %d messages pending", remaining)
        for raw_id in delivered:
            self._remember(raw_id)
        return len(delivered)

    async def _fetch_page(self, symbol: str, cursor: tuple[str, str]) -> list[Mapping[str, object]]:
        data_dt, data_tm = cursor
        body = await self._kis_client.get(
            NEWS_TITLE_PATH,
            NEWS_TITLE_TR_ID,
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": symbol,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": f"00{data_dt}" if data_dt else "",
                "FID_INPUT_HOUR_1": data_tm,
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            },
        )
        output = body.get("output")
        if not isinstance(output, list):
            return []
        return [
            cast(Mapping[str, object], row)
            for row in cast(list[object], output)
            if isinstance(row, dict)
        ]

    def _remember(self, raw_id: str) -> None:
        self._seen[raw_id] = None
        self._seen.move_to_end(raw_id)
        while len(self._seen) > self._seen_capacity:
            _ = self._seen.popitem(last=False)


def _oldest_cursor(rows: Sequence[Mapping[str, object]]) -> tuple[str, str] | None:
    stamps = [
        (str(row.get("data_dt", "")), str(row.get("data_tm", "")))
        for row in rows
        if row.get("data_dt") and row.get("data_tm")
    ]
    return min(stamps) if stamps else None


def _append_to(target: list[str], value: str) -> Callable[[], None]:
    def _append() -> None:
        target.append(value)

    return _append


def _epoch_millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)
