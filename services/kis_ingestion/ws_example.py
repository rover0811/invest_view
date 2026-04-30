"""
KIS Open API WebSocket 실시간 수신 예제 (KRX + NXT)

사용법:
    export KIS_APP_KEY="발급받은 앱키"
    export KIS_APP_SECRET="발급받은 앱 시크릿"

    uv run python services/kis_ingestion/ws_example.py 005930                # KRX 체결가
    uv run python services/kis_ingestion/ws_example.py --nxt 005930          # NXT 체결가
    uv run python services/kis_ingestion/ws_example.py --nxt --hoga 005930   # NXT 체결가 + 호가
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

import requests
import websockets

from models import (
    APPROVAL_URL,
    WS_URL,
    TR_IDS,
    SubscribeMessage,
    TickRecord,
    OrderBookRecord,
)


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
