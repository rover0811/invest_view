from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from kis import KisClient

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"

FETCH_START = date(2025, 4, 1)
FETCH_END = date(2026, 10, 16)

UNIVERSE = [
    "005930", "000660", "005380", "035420", "035720", "051910", "006400", "068270",
    "105560", "055550", "000270", "012330", "005490", "028260", "066570", "015760",
    "032830", "003550", "017670", "096770", "034730", "018260", "009150", "011200",
    "086790", "033780", "010130", "259960", "316140", "024110", "138040", "030200",
    "090430", "010950", "011170", "047050", "002790", "161390", "078930", "071050",
]

KRX_SECTOR_DRAFT = {
    "005930": "4003", "000660": "4003",
    "005380": "4002", "000270": "4002", "012330": "4002", "161390": "4002",
    "068270": "4004",
    "105560": "4005", "055550": "4005", "086790": "4005", "316140": "4005", "024110": "4005",
    "051910": "4007", "011170": "4007", "096770": "4007", "010950": "4007",
    "005490": "4008",
    "017670": "4010", "030200": "4010",
    "071050": "4013",
    "032830": "4015",
    "011200": "4016",
}

US_ETF_BY_KOSPI_SECTOR = {
    "전기·전자": "SOXX",
    "운송장비·부품": "XLY",
    "화학": "XLB",
    "제약": "XLV",
    "의료·정밀기기": "XLV",
    "금속": "XLB",
    "비금속": "XLB",
    "금융": "XLF",
    "증권": "XLF",
    "보험": "XLF",
    "통신": "XLC",
    "IT 서비스": "XLC",
    "오락·문화": "XLC",
    "전기·가스": "XLU",
    "운송·창고": "XLI",
    "건설": "XLI",
    "기계·장비": "XLI",
    "유통": "XLY",
    "음식료·담배": "XLP",
    "섬유·의류": "XLY",
    "일반서비스": "XLI",
}

US_BROAD = ["SPY", "QQQ"]
US_ETFS = sorted(set(US_ETF_BY_KOSPI_SECTOR.values()) | set(US_BROAD))
US_EXCHANGES = ["AMS", "NAS", "NYS"]
MARKET_INDEX = "0001"

DOMESTIC = "/uapi/domestic-stock/v1"


def _windows(start: date, end: date, days: int) -> list[tuple[date, date]]:
    out = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        out.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return out


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def fetch_stock_daily(client: KisClient, symbol: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
    rows = []
    name = ""
    for a, b in _windows(start, end, 120):
        body = client.get(
            f"{DOMESTIC}/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": _ymd(a),
                "FID_INPUT_DATE_2": _ymd(b),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        name = name or body.get("output1", {}).get("hts_kor_isnm", "")
        rows += [
            {"date": r["stck_bsop_date"], "close": float(r["stck_clpr"])}
            for r in body.get("output2", [])
            if r.get("stck_bsop_date")
        ]
    df = pd.DataFrame(rows).drop_duplicates("date")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date"), name


def fetch_index_daily(client: KisClient, code: str, start: date, end: date) -> pd.DataFrame:
    rows = []
    for a, b in _windows(start, end, 60):
        body = client.get(
            f"{DOMESTIC}/quotations/inquire-daily-indexchartprice",
            "FHKUP03500100",
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": _ymd(a),
                "FID_INPUT_DATE_2": _ymd(b),
                "FID_PERIOD_DIV_CODE": "D",
            },
        )
        rows += [
            {"date": r["stck_bsop_date"], "close": float(r["bstp_nmix_prpr"])}
            for r in body.get("output2", [])
            if r.get("stck_bsop_date")
        ]
    df = pd.DataFrame(rows).drop_duplicates("date")
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")


def fetch_us_daily(client: KisClient, symbol: str, start: date, end: date) -> tuple[pd.DataFrame, str]:
    for exchange in US_EXCHANGES:
        rows = []
        cursor = end
        while cursor >= start:
            body = client.get(
                "/uapi/overseas-price/v1/quotations/dailyprice",
                "HHDFS76240000",
                {
                    "AUTH": "",
                    "EXCD": exchange,
                    "SYMB": symbol,
                    "GUBN": "0",
                    "BYMD": _ymd(cursor),
                    "MODP": "1",
                },
            )
            page = [r for r in body.get("output2", []) if r.get("xymd")]
            if not page:
                break
            rows += [{"date": r["xymd"], "close": float(r["clos"])} for r in page]
            earliest = min(pd.to_datetime(r["xymd"]).date() for r in page)
            cursor = earliest - timedelta(days=1)
        if rows:
            df = pd.DataFrame(rows).drop_duplicates("date")
            df["date"] = pd.to_datetime(df["date"])
            df = df[df["date"] >= pd.Timestamp(start)]
            return df.sort_values("date"), exchange
    raise RuntimeError(f"no US daily data for {symbol}")


def fetch_kospi_sector_of(client: KisClient, symbol: str) -> str:
    body = client.get(
        f"{DOMESTIC}/quotations/inquire-price",
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
    )
    return body["output"]["bstp_kor_isnm"]


def fetch_index_codes(client: KisClient) -> dict[str, str]:
    body = client.get(
        f"{DOMESTIC}/quotations/inquire-index-category-price",
        "FHPUP02140000",
        {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "0001",
            "FID_COND_SCR_DIV_CODE": "20214",
            "FID_MRKT_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
        },
    )
    return {r["hts_kor_isnm"]: r["bstp_cls_code"] for r in body["output2"]}


def fetch_sector_weights(client: KisClient, sector_code: str) -> pd.DataFrame:
    body = client.get(
        f"{DOMESTIC}/ranking/market-cap",
        "FHPST01740000",
        {
            "fid_input_price_2": "",
            "fid_cond_mrkt_div_code": "J",
            "fid_cond_scr_div_code": "20174",
            "fid_div_cls_code": "0",
            "fid_input_iscd": sector_code,
            "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0",
            "fid_input_price_1": "",
            "fid_vol_cnt": "",
        },
    )
    return pd.DataFrame(
        [
            {
                "sector_code": sector_code,
                "symbol": r["mksc_shrn_iscd"],
                "name": r["hts_kor_isnm"],
                "market_cap_100m_krw": float(r["stck_avls"]),
                "weight_pct": float(r["mrkt_whol_avls_rlim"]),
            }
            for r in body.get("output", [])
        ]
    )


NEWS_DIR = DATA_DIR / "news"
NEWS_MAX_PAGES = 8


def fetch_news_titles(client: KisClient, symbol: str, day: date) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    hour = ""
    for _ in range(NEWS_MAX_PAGES):
        body = client.get(
            f"{DOMESTIC}/quotations/news-title",
            "FHKST01011800",
            {
                "FID_NEWS_OFER_ENTP_CODE": "",
                "FID_COND_MRKT_CLS_CODE": "",
                "FID_INPUT_ISCD": symbol,
                "FID_TITL_CNTT": "",
                "FID_INPUT_DATE_1": "00" + _ymd(day),
                "FID_INPUT_HOUR_1": hour,
                "FID_RANK_SORT_CLS_CODE": "",
                "FID_INPUT_SRNO": "",
            },
        )
        page = [r for r in body.get("output", []) if r.get("data_dt") == _ymd(day)]
        rows += [
            {
                "time": r["data_tm"],
                "title": r["hts_pbnt_titl_cntt"],
                "provider": r.get("dorg", ""),
                "srno": r.get("cntt_usiq_srno", ""),
            }
            for r in page
        ]
        if len(page) < 40:
            break
        next_hour = min(r["data_tm"] for r in page)
        if next_hour == hour:
            break
        hour = next_hour
    df = pd.DataFrame(rows, columns=["time", "title", "provider", "srno"]).drop_duplicates("srno")
    return df.sort_values("time").reset_index(drop=True)


def load_or_fetch_news(symbol: str, day: date, client: KisClient | None = None) -> pd.DataFrame:
    path = NEWS_DIR / f"{symbol}_{_ymd(day)}.csv"
    if path.exists():
        return pd.read_csv(path, dtype=str).fillna("")
    client = client or KisClient()
    df = fetch_news_titles(client, symbol, day)
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def _wide(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat({k: v.set_index("date")["close"] for k, v in frames.items()}, axis=1).sort_index()


def fetch_all(start: date = FETCH_START, end: date = FETCH_END) -> dict[str, pd.DataFrame]:
    client = KisClient()

    index_codes = fetch_index_codes(client)

    meta_rows = []
    stock_frames = {}
    for symbol in UNIVERSE:
        frame, name = fetch_stock_daily(client, symbol, start, end)
        stock_frames[symbol] = frame
        kospi_sector = fetch_kospi_sector_of(client, symbol)
        meta_rows.append(
            {
                "symbol": symbol,
                "name": name,
                "kospi_sector": kospi_sector,
                "kospi_sector_code": index_codes.get(kospi_sector),
                "krx_sector_code": KRX_SECTOR_DRAFT.get(symbol),
                "us_etf": US_ETF_BY_KOSPI_SECTOR.get(kospi_sector),
            }
        )
        print(f"stock {symbol} {name} {kospi_sector} rows={len(frame)}", flush=True)
    sector_map = pd.DataFrame(meta_rows)

    index_needed = {MARKET_INDEX}
    index_needed |= set(sector_map["kospi_sector_code"].dropna())
    index_needed |= set(sector_map["krx_sector_code"].dropna())
    index_frames = {}
    for code in sorted(index_needed):
        index_frames[code] = fetch_index_daily(client, code, start, end)
        print(f"index {code} rows={len(index_frames[code])}", flush=True)

    us_frames = {}
    us_exchange = {}
    for symbol in US_ETFS:
        us_frames[symbol], us_exchange[symbol] = fetch_us_daily(client, symbol, start, end)
        print(f"us {symbol} {us_exchange[symbol]} rows={len(us_frames[symbol])}", flush=True)

    weight_codes = sorted(set(sector_map["kospi_sector_code"].dropna()) | {MARKET_INDEX})
    weight_frames = [fetch_sector_weights(client, code) for code in weight_codes]
    sector_weights = pd.concat(weight_frames, ignore_index=True)
    sector_weights["as_of"] = pd.Timestamp(end)

    index_names = {code: name for name, code in index_codes.items()}
    return {
        "stock_close": _wide(stock_frames),
        "index_close": _wide(index_frames),
        "us_close": _wide(us_frames),
        "sector_map": sector_map,
        "sector_weights": sector_weights,
        "index_names": pd.DataFrame(
            [{"code": c, "name": index_names.get(c, "")} for c in sorted(index_frames)]
        ),
    }


def save(dataset: dict[str, pd.DataFrame]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    for name, df in dataset.items():
        df.to_csv(DATA_DIR / f"{name}.csv")


def load() -> dict[str, pd.DataFrame]:
    dataset = {}
    for path in sorted(DATA_DIR.glob("*.csv")):
        df = pd.read_csv(path, index_col=0, dtype={"symbol": str, "code": str, "sector_code": str,
                                                   "kospi_sector_code": str, "krx_sector_code": str})
        if path.stem in {"stock_close", "index_close", "us_close"}:
            df.index = pd.to_datetime(df.index)
        dataset[path.stem] = df
    return dataset


def ensure_market_weights(dataset: dict[str, pd.DataFrame]) -> None:
    weights = dataset["sector_weights"]
    if (weights["sector_code"] == MARKET_INDEX).any():
        return
    extra = fetch_sector_weights(KisClient(), MARKET_INDEX)
    extra["as_of"] = weights["as_of"].iloc[0]
    dataset["sector_weights"] = pd.concat([weights, extra], ignore_index=True)
    dataset["sector_weights"].to_csv(DATA_DIR / "sector_weights.csv")


def ensure_us_etfs(dataset: dict[str, pd.DataFrame]) -> None:
    us_close = dataset["us_close"]
    missing = [s for s in US_ETFS if s not in us_close.columns]
    if not missing:
        return
    client = KisClient()
    start = us_close.index.min().date()
    end = us_close.index.max().date()
    extra = {s: fetch_us_daily(client, s, start, end)[0].set_index("date")["close"] for s in missing}
    dataset["us_close"] = pd.concat([us_close, pd.DataFrame(extra)], axis=1).sort_index()
    dataset["us_close"].to_csv(DATA_DIR / "us_close.csv")


KOSPI_SECTOR_NAMES = {
    "0005": "음식료·담배", "0006": "섬유·의류", "0007": "종이·목재", "0008": "화학", "0009": "제약",
    "0010": "비금속", "0011": "금속", "0012": "기계·장비", "0013": "전기·전자", "0014": "의료·정밀기기",
    "0015": "운송장비·부품", "0016": "유통", "0017": "전기·가스", "0018": "건설", "0019": "운송·창고",
    "0020": "통신", "0021": "금융", "0024": "증권", "0025": "보험", "0026": "일반서비스", "0028": "부동산",
    "0029": "IT 서비스", "0030": "오락·문화",
}
KOSPI_SECTOR_CODES = list(KOSPI_SECTOR_NAMES)


def ensure_kospi_sectors(dataset: dict[str, pd.DataFrame]) -> None:
    index_close = dataset["index_close"]
    weights = dataset["sector_weights"]
    missing_idx = [c for c in KOSPI_SECTOR_CODES if c not in index_close.columns]
    missing_w = [c for c in KOSPI_SECTOR_CODES if not (weights["sector_code"] == c).any()]
    if not missing_idx and not missing_w:
        return
    client = KisClient()
    if missing_idx:
        start, end = index_close.index.min().date(), index_close.index.max().date()
        extra = {c: fetch_index_daily(client, c, start, end).set_index("date")["close"] for c in missing_idx}
        dataset["index_close"] = pd.concat([index_close, pd.DataFrame(extra)], axis=1).sort_index()
        dataset["index_close"].to_csv(DATA_DIR / "index_close.csv")
    if missing_w:
        extra_w = [fetch_sector_weights(client, c) for c in missing_w]
        frame = pd.concat(extra_w, ignore_index=True)
        frame["as_of"] = weights["as_of"].iloc[0]
        dataset["sector_weights"] = pd.concat([weights, frame], ignore_index=True)
        dataset["sector_weights"].to_csv(DATA_DIR / "sector_weights.csv")


def load_or_fetch(refresh: bool = False) -> dict[str, pd.DataFrame]:
    if refresh or not (DATA_DIR / "stock_close.csv").exists():
        dataset = fetch_all()
        save(dataset)
    dataset = load()
    ensure_market_weights(dataset)
    ensure_us_etfs(dataset)
    ensure_kospi_sectors(dataset)
    return dataset


if __name__ == "__main__":
    dataset = load_or_fetch(refresh=False)
    stock_close = dataset["stock_close"]
    index_close = dataset["index_close"]
    us_close = dataset["us_close"]
    sector_map = dataset["sector_map"]
    sector_weights = dataset["sector_weights"]
    index_names = dataset["index_names"]
    unmapped_us = sector_map[sector_map["us_etf"].isna()]
    missing_close = stock_close.isna().sum().sort_values(ascending=False)
    print(sector_map.to_string())
