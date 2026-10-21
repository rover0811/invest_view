from __future__ import annotations

import importlib
import io
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ai_signal.avro import schema_path
from ai_signal.replay.__main__ import main
from ai_signal.replay.bars import KST, PATH, TR_ID, Bar, fetch_minute_bars
from ai_signal.replay.export import (
    CauseRow,
    DocumentRow,
    LogEntry,
    SignalRow,
    StateRow,
    build_export,
    check_no_lookahead,
    particle,
)
from ai_signal.replay.ticks import bar_to_tick
from ai_signal.replay.timeline import merge_events

fastavro = importlib.import_module("fastavro")

DAY = date(2026, 10, 20)


def _row(day: str, hhmmss: str, close: int = 100) -> dict[str, str]:
    return {
        "stck_bsop_date": day,
        "stck_cntg_hour": hhmmss,
        "stck_oprc": str(close - 1),
        "stck_hgpr": str(close + 2),
        "stck_lwpr": str(close - 2),
        "stck_prpr": str(close),
        "cntg_vol": "10",
    }


class FakeKis:
    """Serves 120 rows per call, newest first, ending at FID_INPUT_HOUR_1."""

    def __init__(self) -> None:
        start = datetime(2026, 10, 19, 15, 0)
        self.minutes: list[datetime] = []
        t = start
        while t <= datetime(2026, 10, 20, 15, 30):
            if (
                t.time() <= datetime(2000, 1, 1, 15, 30).time()
                and t.time() >= datetime(2000, 1, 1, 9, 0).time()
            ):
                self.minutes.append(t)
            t += timedelta(minutes=1)
        self.calls: list[dict[str, str]] = []

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        assert path == PATH and tr_id == TR_ID
        self.calls.append(params)
        end = datetime.strptime(
            params["FID_INPUT_DATE_1"] + params["FID_INPUT_HOUR_1"], "%Y%m%d%H%M%S"
        )
        # overlap by one row with the previous page to exercise dedupe
        eligible = [m for m in self.minutes if m <= end + timedelta(minutes=1)]
        page = sorted(eligible, reverse=True)[:120]
        rows = [_row(m.strftime("%Y%m%d"), m.strftime("%H%M%S")) for m in page]
        return {"output2": rows}


def test_fetch_minute_bars_pages_dedupes_and_filters_day() -> None:
    client = FakeKis()
    bars = fetch_minute_bars(client, "000660", DAY)
    assert len(bars) == 391
    assert bars[0].t == datetime(2026, 10, 20, 9, 0, tzinfo=KST)
    assert bars[-1].t == datetime(2026, 10, 20, 15, 30, tzinfo=KST)
    assert [b.t for b in bars] == sorted({b.t for b in bars})
    assert client.calls[0]["FID_INPUT_HOUR_1"] == "153000"
    assert client.calls[1]["FID_INPUT_HOUR_1"] == "133000"
    assert all(
        c["FID_COND_MRKT_DIV_CODE"] == "J" and c["FID_PW_DATA_INCU_YN"] == "Y"
        for c in client.calls
    )
    assert all(
        c["FID_FAKE_TICK_INCU_YN"] == "" and c["FID_INPUT_ISCD"] == "000660"
        for c in client.calls
    )
    assert len(client.calls) == 4


def test_fetch_minute_bars_stops_on_empty() -> None:
    class Empty:
        def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
            return {"output2": []}

    assert fetch_minute_bars(Empty(), "000660", DAY) == []


def _bar(hh: int, mm: int, close: int) -> Bar:
    return Bar(
        datetime(2026, 10, 20, hh, mm, tzinfo=KST),
        close - 5,
        close + 10,
        close - 10,
        close,
        7,
    )


@pytest.mark.parametrize(
    ("close", "sign", "change"), [(1300, "2", 9), (1291, "3", 0), (1280, "5", -11)]
)
def test_bar_to_tick_sign_and_fields(close: int, sign: str, change: int) -> None:
    tick = bar_to_tick(_bar(9, 24, close), 1291, "000660")
    assert tick["market"] == "KRX"
    assert tick["business_date"] == "20261020"
    assert tick["trade_time"] == "092400"
    assert tick["price"] == close
    assert tick["change"] == change
    assert tick["change_sign"] == sign
    assert tick["change_rate"] == (Decimal(change) * 100 / Decimal(1291)).quantize(
        Decimal("0.00000001")
    )
    assert (tick["open"], tick["high"], tick["low"], tick["trade_volume"]) == (
        close - 5,
        close + 10,
        close - 10,
        7,
    )


def test_bar_to_tick_is_valid_for_schema() -> None:
    schema = fastavro.parse_schema(
        json.loads(Path(schema_path("stock-ticks")).read_text())
    )
    tick = bar_to_tick(_bar(9, 24, 1300), 1291, "000660")
    assert {f["name"] for f in schema["fields"]} == set(tick)
    buf = io.BytesIO()
    fastavro.schemaless_writer(buf, schema, tick)
    buf.seek(0)
    assert fastavro.schemaless_reader(buf, schema)["change_rate"] == tick["change_rate"]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_merge_events_orders_ties_and_premarket() -> None:
    ticks = [bar_to_tick(_bar(9, m, 1300), 1291, "000660") for m in (0, 1, 2)]
    early = {
        "raw_id": "early",
        "published_at": _ms(datetime(2026, 10, 20, 8, 12, tzinfo=KST)),
    }
    same_minute = {
        "raw_id": "tie",
        "published_at": _ms(datetime(2026, 10, 20, 9, 1, 30, tzinfo=KST)),
    }
    later = {
        "raw_id": "later",
        "published_at": datetime(2026, 10, 20, 9, 2, 0, tzinfo=KST),
    }
    events = merge_events(ticks, [later, same_minute, early])
    labels = [e.payload.get("raw_id") or e.payload["trade_time"] for e in events]
    assert labels == ["early", "090000", "tie", "090100", "later", "090200"]
    assert events[0].time == datetime(2026, 10, 20, 9, 0, tzinfo=KST)
    assert events[0].payload["published_at"] == early["published_at"]
    assert [e.kind for e in events][:2] == ["news", "tick"]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("HBM 장기 공급 계약", "으로"),
        ("실적 호조", "로"),
        ("수출 증가율", "로"),
        ("2분기 실적 3", "으로"),
        ("목표가 2", "로"),
        ("HBM", "로"),
        ("", "로"),
    ],
)
def test_particle(label: str, expected: str) -> None:
    assert particle(label) == expected


def _t(hh: int, mm: int) -> datetime:
    return datetime(2026, 10, 20, hh, mm, tzinfo=KST)


def _export(cited_published: datetime) -> dict[str, object]:
    return build_export(
        symbol="000660",
        name="SK하이닉스",
        day=DAY,
        prev_close=1291000,
        bars=[_bar(9, 0, 1293000)],
        states=[StateRow(_t(9, 24), "POSITIVE", "m1", 1, 1.08)],
        documents=[
            DocumentRow(
                "d1", "제목", "언론사", cited_published, True, 1, "SYMBOL", None
            )
        ],
        signals=[
            SignalRow(
                at=_t(9, 24),
                move_version=1,
                version=1,
                status="SHOWN",
                headline_label="HBM 장기 공급 계약",
                decided_by="llm",
                model="nvidia/x",
                prompt_version="signal_generation/v2",
                trigger="PRICE_STATE_CHANGE",
                causes=[CauseRow(1, "HBM", "설명", "SYMBOL", ["d1"])],
                decomposition={
                    "global": 0.4,
                    "market": 0.3,
                    "sector": 0.5,
                    "idio": 2.4,
                    "dominant_layer": "IDIO",
                },
            )
        ],
        log=[LogEntry(_t(9, 24), "state", "+1% 돌파")],
    )


def _contract_keys() -> dict[str, set[str]]:
    text = (
        Path(__file__).resolve().parents[3] / ".sisyphus" / "replay-contract.md"
    ).read_text()
    body = text.split("```jsonc", 1)[1].split("```", 1)[0]
    body = re.sub(r"//[^\n]*", "", body)
    body = body.replace("...", "x")
    data = json.loads(re.sub(r",(\s*[}\]])", r"\1", body))
    signal = data["signals"][0]
    return {
        "top": set(data),
        "bar": set(data["bars"][0]),
        "state": set(data["states"][0]),
        "document": set(data["documents"][0]),
        "signal": set(signal),
        "cause": set(signal["causes"][0]),
        "decomposition": set(signal["decomposition"]),
        "log": set(data["log"][0]),
        "thresholds": set(data["thresholds"]),
    }


def test_export_shape_matches_contract() -> None:
    keys = _contract_keys()
    export: dict[str, Any] = _export(_t(8, 12))
    signal = export["signals"][0]
    assert set(export) == keys["top"]
    assert set(export["thresholds"]) == keys["thresholds"]
    assert set(export["bars"][0]) == keys["bar"]
    assert set(export["states"][0]) == keys["state"]
    assert set(export["documents"][0]) == keys["document"]
    assert set(signal) == keys["signal"]
    assert set(signal["causes"][0]) == keys["cause"]
    assert set(signal["decomposition"]) == keys["decomposition"]
    assert set(export["log"][0]) == keys["log"]
    assert export["contract_version"] == 1
    assert export["date"] == "2026-10-20"
    assert signal["particle"] == "으로"
    assert signal["at"] == "2026-10-20T09:24:00+09:00"
    assert export["bars"][0]["t"] == "2026-10-20T09:00:00+09:00"
    json.dumps(export, ensure_ascii=False)


def test_check_no_lookahead() -> None:
    check_no_lookahead(_export(_t(9, 24)))
    with pytest.raises(ValueError, match="lookahead"):
        check_no_lookahead(_export(_t(9, 25)))


def test_main_dispatches_run_and_export(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_signal.replay import runner

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        runner, "run_replay", lambda s, d, **kw: calls.append(("run", kw))
    )
    monkeypatch.setattr(
        runner, "export_replay", lambda s, d, **kw: calls.append(("export", kw))
    )
    base = ["--symbol", "000660", "--date", "2026-10-20", "--pg-dsn", "postgresql://x"]
    main(
        [
            "run",
            *base,
            "--bootstrap",
            "b:9092",
            "--schema-registry",
            "http://sr",
            "--max-llm-calls",
            "5",
        ]
    )
    main(["export", *base, "--out", "/tmp/out.json"])
    assert calls[0] == (
        "run",
        {
            "bootstrap": "b:9092",
            "schema_registry": "http://sr",
            "pg_dsn": "postgresql://x",
            "max_llm_calls": 5,
        },
    )
    assert calls[1][1]["out_path"].as_posix() == "/tmp/out.json"
