import io
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import fastavro
import httpx
import pytest

from kis_ingestion.poller.base import KST
from kis_ingestion.poller.kis_client import KISRestClient
from kis_ingestion.poller.news import (
    NEWS_TITLE_TR_ID,
    NewsPoller,
    parse_news_row,
)
from kis_ingestion.poller.rate_limiter import SharedRateLimiter
from kis_ingestion.token_manager import KISTokenManager

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "news-raw.avsc"
BASE_URL = "https://kis.example"
OBSERVED_AT = datetime(2026, 10, 19, 17, 40, 0, tzinfo=KST)


def _row(srno: str, tm: str, title: str | None = None, dt: str = "20261019") -> dict[str, str]:
    return {
        "cntt_usiq_srno": srno,
        "news_ofer_entp_code": "9",
        "data_dt": dt,
        "data_tm": tm,
        "hts_pbnt_titl_cntt": title or f"title {srno}",
        "news_lrdv_code": "10",
        "dorg": "provider",
        "iscd1": "005930",
        "kor_isnm1": "name",
    }


class FakeSymbols:
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    async def list_symbols(self) -> list[str]:
        return list(self.symbols)


class FakeProducer:
    def __init__(self, fail_delivery_for: set[str] | None = None) -> None:
        self.sent: list[dict[str, object]] = []
        self._pending: list[tuple[str, Callable[[], None] | None]] = []
        self._fail = fail_delivery_for or set()

    def publish(
        self,
        *,
        key: str,
        value: Mapping[str, object],
        headers: Sequence[tuple[str, str]] = (),
        on_delivered: Callable[[], None] | None = None,
    ) -> None:
        self.sent.append({"key": key, "value": dict(value), "headers": dict(headers)})
        self._pending.append((str(value["source_doc_id"]), on_delivered))

    def flush(self, timeout: float = 30.0) -> int:
        for doc_id, callback in self._pending:
            if doc_id not in self._fail and callback is not None:
                callback()
        self._pending.clear()
        return 0


class PagedNewsApi:
    """Serves newest-first pages per symbol; the cursor is inclusive like KIS."""

    def __init__(self, rows_by_symbol: dict[str, list[dict[str, str]]], page_size: int = 40) -> None:
        self.rows_by_symbol = rows_by_symbol
        self.page_size = page_size
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        self.requests.append(request)
        params = request.url.params
        rows = sorted(
            self.rows_by_symbol.get(params["FID_INPUT_ISCD"], []),
            key=lambda r: (r["data_dt"], r["data_tm"]),
            reverse=True,
        )
        if params["FID_INPUT_DATE_1"]:
            cursor = (params["FID_INPUT_DATE_1"][2:], params["FID_INPUT_HOUR_1"])
            rows = [r for r in rows if (r["data_dt"], r["data_tm"]) <= cursor]
        return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "", "msg1": "", "output": rows[: self.page_size]})


async def _no_sleep(_seconds: float) -> None:
    return None


def _poller(
    api: PagedNewsApi,
    http_client: httpx.AsyncClient,
    symbols: list[str],
    producer: FakeProducer,
    max_pages: int = 3,
) -> NewsPoller:
    kis_client = KISRestClient(
        base_url=BASE_URL,
        app_key="key",
        app_secret="secret",
        token_manager=KISTokenManager(BASE_URL, "key", "secret", http_client),
        http_client=http_client,
        rate_limiter=SharedRateLimiter(1000),
        sleep=_no_sleep,
    )
    return NewsPoller(
        kis_client=kis_client,
        symbol_source=FakeSymbols(symbols),
        producer=producer,
        max_pages=max_pages,
        clock=lambda: OBSERVED_AT,
    )


def test_parse_row_builds_contract_fields():
    record = parse_news_row("005930", _row("A1", "173535"), OBSERVED_AT, OBSERVED_AT)

    assert record is not None
    assert record.published_at == datetime(2026, 10, 19, 8, 35, 35, tzinfo=timezone.utc)
    assert record.provider_code == "9"
    assert record.category_code == "10"
    assert record.source_payload["dorg"] == "provider"
    assert UUID(record.raw_id).version == 5


def test_parse_row_skips_incomplete_rows():
    assert parse_news_row("005930", _row("", "173535"), OBSERVED_AT, OBSERVED_AT) is None
    assert parse_news_row("005930", _row("A1", "bad"), OBSERVED_AT, OBSERVED_AT) is None
    assert parse_news_row("005930", _row("A1", "173535", title="  "), OBSERVED_AT, OBSERVED_AT) is None


def test_identity_ignores_observation_time_but_tracks_title_and_symbol():
    base = parse_news_row("005930", _row("A1", "173535"), OBSERVED_AT, OBSERVED_AT)
    later = parse_news_row("005930", _row("A1", "173535"), datetime.now(KST), datetime.now(KST))
    retitled = parse_news_row("005930", _row("A1", "173535", title="new title"), OBSERVED_AT, OBSERVED_AT)
    other_symbol = parse_news_row("000660", _row("A1", "173535"), OBSERVED_AT, OBSERVED_AT)

    assert base is not None and later is not None and retitled is not None and other_symbol is not None
    assert base.raw_id == later.raw_id
    assert base.idempotency_key == later.idempotency_key
    assert retitled.raw_id != base.raw_id
    assert other_symbol.raw_id != base.raw_id


def test_avro_record_matches_news_raw_schema():
    record = parse_news_row("005930", _row("A1", "173535"), OBSERVED_AT, OBSERVED_AT)
    assert record is not None
    schema = fastavro.parse_schema(json.loads(SCHEMA_PATH.read_text()))
    buffer = io.BytesIO()

    fastavro.schemaless_writer(buffer, schema, record.to_avro())
    _ = buffer.seek(0)
    decoded = fastavro.schemaless_reader(buffer, schema)

    assert decoded["raw_id"] == record.raw_id
    assert decoded["idempotency_key"] == record.idempotency_key
    assert decoded["published_at"] == record.published_at


@pytest.mark.asyncio
async def test_cycle_reads_all_reference_symbols_and_publishes_keyed_by_symbol():
    api = PagedNewsApi({
        "005930": [_row("S1", "100000"), _row("S2", "110000")],
        "000660": [_row("H1", "120000")],
    })
    producer = FakeProducer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http_client:
        poller = _poller(api, http_client, ["000660", "005930"], producer)
        published = await poller.run_once(OBSERVED_AT)

    assert published == 3
    assert {(m["key"], m["value"]["source_doc_id"]) for m in producer.sent} == {
        ("005930", "S1"), ("005930", "S2"), ("000660", "H1"),
    }
    assert {r.headers["tr_id"] for r in api.requests} == {NEWS_TITLE_TR_ID}
    message = producer.sent[0]
    assert message["headers"]["raw_id"] == message["value"]["raw_id"]
    assert str(message["headers"]["idempotency_key"]).startswith("kis_news:")
    assert message["value"]["idempotency_key"] == message["headers"]["idempotency_key"]


@pytest.mark.asyncio
async def test_second_cycle_publishes_only_new_titles():
    rows = [_row("S1", "100000")]
    api = PagedNewsApi({"005930": rows})
    producer = FakeProducer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http_client:
        poller = _poller(api, http_client, ["005930"], producer)
        assert await poller.run_once(OBSERVED_AT) == 1
        assert await poller.run_once(OBSERVED_AT) == 0
        rows.append(_row("S2", "110000"))
        rows[0] = _row("S1", "100000", title="corrected title")
        assert await poller.run_once(OBSERVED_AT) == 2

    assert [m["value"]["title"] for m in producer.sent[-2:]] == ["title S2", "corrected title"]


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_next_cycle():
    api = PagedNewsApi({"005930": [_row("S1", "100000")]})
    producer = FakeProducer(fail_delivery_for={"S1"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http_client:
        poller = _poller(api, http_client, ["005930"], producer)
        assert await poller.run_once(OBSERVED_AT) == 0
        producer._fail.clear()
        assert await poller.run_once(OBSERVED_AT) == 1

    assert len(producer.sent) == 2
    assert producer.sent[0]["value"]["raw_id"] == producer.sent[1]["value"]["raw_id"]


@pytest.mark.asyncio
async def test_pages_backwards_until_seen_or_max_pages():
    rows = [_row(f"N{i:03d}", f"{9 + i // 60:02d}{i % 60:02d}00") for i in range(100)]
    api = PagedNewsApi({"005930": rows}, page_size=40)
    producer = FakeProducer()
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http_client:
        poller = _poller(api, http_client, ["005930"], producer, max_pages=5)
        assert await poller.run_once(OBSERVED_AT) == 100
        first_cycle_calls = len(api.requests)
        assert await poller.run_once(OBSERVED_AT) == 0

    assert first_cycle_calls == 3
    assert len(api.requests) - first_cycle_calls == 1
    second_page = api.requests[1].url.params
    assert second_page["FID_INPUT_DATE_1"] == "0020261019"


@pytest.mark.asyncio
async def test_one_symbol_failure_does_not_block_others():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        if request.url.params["FID_INPUT_ISCD"] == "BAD":
            return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "bad"})
        return httpx.Response(200, json={"rt_cd": "0", "output": [_row("S1", "100000")]})

    producer = FakeProducer()
    api = PagedNewsApi({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        poller = _poller(api, http_client, ["BAD", "005930"], producer)
        assert await poller.run_once(OBSERVED_AT) == 1


@pytest.mark.asyncio
async def test_active_only_in_weekday_trading_window():
    api = PagedNewsApi({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http_client:
        poller = _poller(api, http_client, [], FakeProducer())

    assert poller.is_active(datetime(2026, 10, 19, 8, 0, tzinfo=KST))
    assert not poller.is_active(datetime(2026, 10, 19, 20, 0, tzinfo=KST))
    assert not poller.is_active(datetime(2026, 10, 18, 12, 0, tzinfo=KST))
    assert poller.is_active(datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc))
