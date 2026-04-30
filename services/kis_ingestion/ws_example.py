"""
KIS Open API WebSocket 실시간 수신 예제 (KRX + NXT)

사용법:
    export KIS_APP_KEY="발급받은 앱키"
    export KIS_APP_SECRET="발급받은 앱 시크릿"

    uv run python examples/ws_h0stcnt0_example.py 005930                # KRX 체결가
    uv run python examples/ws_h0stcnt0_example.py --nxt 005930          # NXT 체결가
    uv run python examples/ws_h0stcnt0_example.py --nxt --hoga 005930   # NXT 체결가 + 호가
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Self

import requests
import websockets
from pydantic import BaseModel, Field

APPROVAL_URL = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
WS_URL = "ws://ops.koreainvestment.com:21000"

# 모의투자 시 아래 두 줄로 교체
# APPROVAL_URL = "https://openapivts.koreainvestment.com:29443/oauth2/Approval"
# WS_URL = "ws://ops.koreainvestment.com:31000"

SIGN_LABELS: dict[str, str] = {
    "1": "++(상한)",
    "2": "+(상승)",
    "3": " (보합)",
    "4": "--(하한)",
    "5": "-(하락)",
}

TR_IDS = {
    "krx_tick": "H0STCNT0",
    "nxt_tick": "H0NXCNT0",
    "krx_hoga": "H0STASP0",
    "nxt_hoga": "H0NXASP0",
}


class SubscribeInput(BaseModel):
    tr_id: str
    tr_key: str


class SubscribeHeader(BaseModel):
    approval_key: str
    custtype: str = "P"
    tr_type: str
    content_type: str = Field(default="utf-8", alias="content-type")

    model_config = {"populate_by_name": True}


class SubscribeMessage(BaseModel):
    header: SubscribeHeader
    body: dict[str, SubscribeInput]

    def to_wire(self) -> str:
        return json.dumps(self.model_dump(by_alias=True), ensure_ascii=False)

    @classmethod
    def subscribe(cls, approval_key: str, tr_id: str, stock_code: str) -> Self:
        return cls(
            header=SubscribeHeader(approval_key=approval_key, tr_type="1"),
            body={"input": SubscribeInput(tr_id=tr_id, tr_key=stock_code)},
        )


# KRX H0STCNT0 / NXT H0NXCNT0 공용 — 46필드, index 21만 wire name 상이 (CCLD_DVSN vs CNTG_CLS_CODE)
class TickRecord(BaseModel):
    symbol: str
    trade_time: str
    price: int
    change_sign: str
    change: int
    change_rate: Decimal
    vwap: Decimal
    open: int
    high: int
    low: int
    ask_price_1: int
    bid_price_1: int
    trade_volume: int
    cumulative_volume: int
    cumulative_amount: int
    sell_count: int
    buy_count: int
    net_buy_count: int
    trade_strength: Decimal
    total_sell_volume: int
    total_buy_volume: int
    trade_type: str
    buy_ratio: Decimal
    prev_day_volume_rate: Decimal
    open_time: str
    open_vs_sign: str
    open_vs_price: int
    high_time: str
    high_vs_sign: str
    high_vs_price: int
    low_time: str
    low_vs_sign: str
    low_vs_price: int
    business_date: str
    market_session_code: str
    trading_halted: str
    ask_remain_1: int
    bid_remain_1: int
    total_ask_remain: int
    total_bid_remain: int
    volume_turnover: Decimal
    prev_same_hour_volume: int
    prev_same_hour_volume_rate: Decimal
    hour_class_code: str
    market_termination_code: str
    vi_trigger_price: int

    # wire index → field name (순서 변경 금지)
    _FIELD_ORDER: ClassVar[list[str]] = [
        "symbol", "trade_time", "price", "change_sign", "change",
        "change_rate", "vwap", "open", "high", "low",
        "ask_price_1", "bid_price_1", "trade_volume", "cumulative_volume",
        "cumulative_amount", "sell_count", "buy_count", "net_buy_count",
        "trade_strength", "total_sell_volume", "total_buy_volume", "trade_type",
        "buy_ratio", "prev_day_volume_rate", "open_time", "open_vs_sign",
        "open_vs_price", "high_time", "high_vs_sign", "high_vs_price",
        "low_time", "low_vs_sign", "low_vs_price", "business_date",
        "market_session_code", "trading_halted", "ask_remain_1", "bid_remain_1",
        "total_ask_remain", "total_bid_remain", "volume_turnover",
        "prev_same_hour_volume", "prev_same_hour_volume_rate",
        "hour_class_code", "market_termination_code", "vi_trigger_price",
    ]
    _N: ClassVar[int] = 46

    @classmethod
    def parse_payload(cls, raw_data: str, data_cnt: int) -> list[Self]:
        """
        Wire: 0|H0STCNT0|004|005930^123929^73100^...
              ┬ ────┬─── ─┬─ ──────────┬──────────
              │     │     │  ^구분 46필드 * data_cnt건
              │     │     └─ 데이터 건수
              │     └─ TR_ID
              └─ 암호화 (0=평문, 1=AES256)
        """
        vals = raw_data.split("^")
        return [
            cls(**dict(zip(cls._FIELD_ORDER, vals[i * cls._N : (i + 1) * cls._N])))
            for i in range(data_cnt)
            if len(vals[i * cls._N : (i + 1) * cls._N]) == cls._N
        ]

    def format_line(self) -> str:
        t = self.trade_time
        sign = SIGN_LABELS.get(self.change_sign, "?")
        return (
            f"[{t[:2]}:{t[2:4]}:{t[4:6]}] {self.symbol}  "
            f"현재가: {self.price:>8,}  {sign} {self.change:>+7,} ({self.change_rate:>6}%)  "
            f"체결량: {self.trade_volume:>8,}  누적: {self.cumulative_volume:>12,}  "
            f"체결강도: {self.trade_strength}"
        )


# NXT H0NXASP0 호가 — 54필드 (9호가)
class OrderBookRecord(BaseModel):
    symbol: str
    business_time: str
    hour_class_code: str
    ask_prices: list[int]       # 매도호가 1~9
    bid_prices: list[int]       # 매수호가 1~9
    ask_volumes: list[int]      # 매도호가잔량 1~9
    bid_volumes: list[int]      # 매수호가잔량 1~9
    total_ask_remain: int
    total_bid_remain: int
    overtime_total_ask: int
    overtime_total_bid: int
    expected_price: int
    expected_quantity: int
    expected_volume: int
    expected_change: int
    expected_change_sign: str
    expected_change_rate: Decimal
    cumulative_volume: int
    total_ask_remain_change: Decimal
    total_bid_remain_change: Decimal
    overtime_ask_change: Decimal
    overtime_bid_change: Decimal

    _N: ClassVar[int] = 54

    @classmethod
    def parse_payload(cls, raw_data: str, data_cnt: int) -> list[Self]:
        vals = raw_data.split("^")
        records: list[Self] = []
        for i in range(data_cnt):
            chunk = vals[i * cls._N : (i + 1) * cls._N]
            if len(chunk) < cls._N:
                break
            records.append(cls(
                symbol=chunk[0],
                business_time=chunk[1],
                hour_class_code=chunk[2],
                ask_prices=[int(chunk[j]) for j in range(3, 12)],
                bid_prices=[int(chunk[j]) for j in range(12, 21)],
                ask_volumes=[int(chunk[j]) for j in range(21, 30)],
                bid_volumes=[int(chunk[j]) for j in range(30, 39)],
                total_ask_remain=int(chunk[39]),
                total_bid_remain=int(chunk[40]),
                overtime_total_ask=int(chunk[41]),
                overtime_total_bid=int(chunk[42]),
                expected_price=int(chunk[43]),
                expected_quantity=int(chunk[44]),
                expected_volume=int(chunk[45]),
                expected_change=int(chunk[46]),
                expected_change_sign=chunk[47],
                expected_change_rate=Decimal(chunk[48]),
                cumulative_volume=int(chunk[49]),
                total_ask_remain_change=Decimal(chunk[50]),
                total_bid_remain_change=Decimal(chunk[51]),
                overtime_ask_change=Decimal(chunk[52]),
                overtime_bid_change=Decimal(chunk[53]),
            ))
        return records

    def format_line(self) -> str:
        t = self.business_time
        time_str = f"{t[:2]}:{t[2:4]}:{t[4:6]}" if len(t) >= 6 else t
        lines = [f"[{time_str}] {self.symbol}  ── 호가 ──"]
        for i in reversed(range(9)):
            lines.append(
                f"  매도 {i+1:>2}  {self.ask_prices[i]:>8,}  [{self.ask_volumes[i]:>10,}]"
            )
        lines.append("  " + "-" * 40)
        for i in range(9):
            lines.append(
                f"  매수 {i+1:>2}  {self.bid_prices[i]:>8,}  [{self.bid_volumes[i]:>10,}]"
            )
        lines.append(
            f"  총매도잔량: {self.total_ask_remain:>12,}  "
            f"총매수잔량: {self.total_bid_remain:>12,}"
        )
        return "\n".join(lines)


def get_approval_key(app_key: str, app_secret: str) -> str:
    resp = requests.post(
        APPROVAL_URL,
        headers={"content-type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            # KIS approval key 발급 시 필드명이 "appsecret"이 아니라 "secretkey"
            "secretkey": app_secret,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["approval_key"]


async def run(stock_codes: list[str], nxt: bool, hoga: bool) -> None:
    app_key = os.environ.get("KIS_APP_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET", "")

    if not app_key or not app_secret:
        print("ERROR: KIS_APP_KEY, KIS_APP_SECRET 환경변수를 설정하세요.")
        sys.exit(1)

    exchange = "NXT" if nxt else "KRX"
    tick_tr = TR_IDS["nxt_tick"] if nxt else TR_IDS["krx_tick"]
    hoga_tr = TR_IDS["nxt_hoga"] if nxt else TR_IDS["krx_hoga"]

    print("Approval key 발급 중...")
    approval_key = get_approval_key(app_key, app_secret)
    print(f"Approval key: {approval_key[:16]}...")

    print(f"WebSocket 연결: {WS_URL}")
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        for code in stock_codes:
            await ws.send(SubscribeMessage.subscribe(approval_key, tick_tr, code).to_wire())
            print(f"[{exchange}] 체결가 구독: {code} ({tick_tr})")
            await asyncio.sleep(0.3)

            if hoga:
                await ws.send(SubscribeMessage.subscribe(approval_key, hoga_tr, code).to_wire())
                print(f"[{exchange}] 호가 구독: {code} ({hoga_tr})")
                await asyncio.sleep(0.3)

        active_trs = {tick_tr}
        if hoga:
            active_trs.add(hoga_tr)

        print("=" * 90)
        print(f"[{exchange}] 수신 대기 중... (종목: {', '.join(stock_codes)}, TR: {', '.join(sorted(active_trs))})")
        print("종료: Ctrl+C")
        print("=" * 90)

        while True:
            data = await ws.recv()

            if data[0] in ("0", "1"):
                parts = data.split("|")
                encrypted, recv_tr, data_cnt, payload = (
                    parts[0], parts[1], int(parts[2]), parts[3]
                )

                if encrypted == "1":
                    continue

                if recv_tr == tick_tr:
                    for tick in TickRecord.parse_payload(payload, data_cnt):
                        print(tick.format_line())
                elif recv_tr == hoga_tr:
                    for ob in OrderBookRecord.parse_payload(payload, data_cnt):
                        print(ob.format_line())
            else:
                json_msg = json.loads(data)
                tr_id = json_msg.get("header", {}).get("tr_id", "")

                if tr_id == "PINGPONG":
                    await ws.pong(data)
                    continue

                rt_cd = json_msg.get("body", {}).get("rt_cd", "")
                msg1 = json_msg.get("body", {}).get("msg1", "")
                tr_key = json_msg.get("header", {}).get("tr_key", "")

                if rt_cd == "0":
                    print(f"[OK] {tr_key}: {msg1}")
                else:
                    print(f"[ERR] {tr_key} (rt_cd={rt_cd}): {msg1}")


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS 실시간 WebSocket 예제")
    parser.add_argument("codes", nargs="*", default=["005930"], help="종목코드 (기본: 005930)")
    parser.add_argument("--nxt", action="store_true", help="NXT 대체거래소 (08:00~20:00)")
    parser.add_argument("--hoga", action="store_true", help="호가 데이터도 구독")
    args = parser.parse_args()

    exchange = "NXT" if args.nxt else "KRX"
    print(f"KIS {exchange} 실시간 예제 — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"종목: {args.codes}  호가: {'ON' if args.hoga else 'OFF'}")
    print()

    try:
        asyncio.run(run(args.codes, args.nxt, args.hoga))
    except KeyboardInterrupt:
        print("\n종료됨.")


if __name__ == "__main__":
    main()
