from __future__ import annotations

import hashlib
from uuid import NAMESPACE_URL, uuid5

from ai_signal.documents.news import NewsRecord, dedup_key, document_id
from tests.document_fakes import PUBLISHED_AT, news_message


def test_identity_matches_the_poller_formula() -> None:
    title = "SK하이닉스, 3분기 영업이익 사상 최대"
    expected_key = (
        f"kis_news:000660:2026101900001:{hashlib.sha256(title.encode()).hexdigest()}"
    )
    record = NewsRecord.from_message(news_message(title=title))
    assert record.idempotency_key == expected_key
    assert record.raw_id == uuid5(NAMESPACE_URL, f"invest-view:news-raw:{expected_key}")
    assert record.document_id == document_id("KIS_NEWS", "2026101900001")
    assert record.publisher == "연합뉴스"
    assert record.published_at == PUBLISHED_AT


def test_identity_comes_from_the_value_field_not_headers() -> None:
    message = news_message()
    assert message.headers == {}
    record = NewsRecord.from_message(message)
    assert record.idempotency_key == message.value["idempotency_key"]


def test_document_id_ignores_symbol_and_title() -> None:
    first = NewsRecord.from_message(news_message(symbol="000660"))
    second = NewsRecord.from_message(news_message(symbol="005930", title="다른 제목"))
    assert first.document_id == second.document_id
    assert first.raw_id != second.raw_id


def test_dedup_key_drops_bracket_tags_spaces_and_symbols() -> None:
    assert dedup_key("[속보] SK하이닉스, HBM 공급 확대(종합)") == dedup_key(
        "SK하이닉스 HBM 공급 확대"
    )
    assert dedup_key("SK하이닉스 HBM 공급 확대") != dedup_key(
        "SK하이닉스 HBM 공급 축소"
    )
