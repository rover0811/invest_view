from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd
from statsmodels.regression.rolling import RollingOLS

import data

EVAL_START = pd.Timestamp("2025-10-17")
BIG_MOVE = 0.01
OLS_WINDOW = 120
OLS_MIN_NOBS = 60
MAX_SELF_WEIGHT = 0.95

SECTOR_VARIANTS = ["kospi_raw", "kospi_ex", "krx_raw"]
METHODS = ["simultaneous_unit", "sequential_unit", "rolling_ols_2f", "sequential_4l"]
LAYERS = ["global", "market", "sector", "group", "idio"]
CASE_SYMBOLS = ["000660", "005930"]
EXTRA_CASE_SYMBOLS = ["005380", "105560", "035420", "068270", "005490", "015760", "011200"]
CASE_COUNT = 10


def align_us_to_kr(us_ret: pd.Series, kr_dates: pd.DatetimeIndex, lag: int) -> pd.Series:
    us = us_ret.dropna()
    us_dates = us.index.values
    if lag == 0:
        pos = np.searchsorted(us_dates, kr_dates.values, side="right") - 1
    else:
        pos = np.searchsorted(us_dates, kr_dates.values, side="left") - lag
    values = np.where(pos >= 0, us.values[np.clip(pos, 0, None)], np.nan)
    return pd.Series(values, index=kr_dates)


def self_weight_history(
    stock_close: pd.Series, index_close: pd.Series, weight_now: float
) -> pd.Series:
    # w(t) = w(T) * (P_i(t) / P_i(T)) / (I(t) / I(T)); assumes index level tracks sector market cap.
    price_ratio = stock_close / stock_close.dropna().iloc[-1]
    index_ratio = index_close / index_close.dropna().iloc[-1]
    return (weight_now * price_ratio / index_ratio).shift(1)


def build_factors(dataset: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    stock_close = dataset["stock_close"]
    index_close = dataset["index_close"].reindex(stock_close.index)
    us_close = dataset["us_close"]
    sector_map = dataset["sector_map"].set_index("symbol")
    weights = dataset["sector_weights"]

    stock_ret = stock_close.pct_change(fill_method=None)
    index_ret = index_close.pct_change(fill_method=None)
    us_ret = us_close.pct_change(fill_method=None)

    kospi_raw, kospi_ex, krx_raw, self_weight = {}, {}, {}, {}
    market_ex, market_weight = {}, {}
    market_ret = index_ret[data.MARKET_INDEX]
    global_ret = {lag: {} for lag in (0, 1, 2)}
    for symbol in stock_close.columns:
        meta = sector_map.loc[symbol]
        kospi_code = meta["kospi_sector_code"]
        krx_code = meta["krx_sector_code"]
        etf = meta["us_etf"]

        if isinstance(kospi_code, str):
            kospi_raw[symbol] = index_ret[kospi_code]
            row = weights[(weights["sector_code"] == kospi_code) & (weights["symbol"] == symbol)]
            if not row.empty:
                w = self_weight_history(stock_close[symbol], index_close[kospi_code], row["weight_pct"].iloc[0] / 100)
                w = w.where(w < MAX_SELF_WEIGHT)
                self_weight[symbol] = w
                kospi_ex[symbol] = (index_ret[kospi_code] - w * stock_ret[symbol]) / (1 - w)
        mrow = weights[(weights["sector_code"] == data.MARKET_INDEX) & (weights["symbol"] == symbol)]
        mw_now = mrow["weight_pct"].iloc[0] / 100 if not mrow.empty else 0.0
        mw = self_weight_history(stock_close[symbol], index_close[data.MARKET_INDEX], mw_now)
        market_weight[symbol] = mw
        market_ex[symbol] = (market_ret - mw * stock_ret[symbol]) / (1 - mw)
        if isinstance(krx_code, str) and krx_code in index_ret:
            krx_raw[symbol] = index_ret[krx_code]
        if isinstance(etf, str) and etf in us_ret:
            for lag in global_ret:
                global_ret[lag][symbol] = align_us_to_kr(us_ret[etf], stock_close.index, lag)

    frame = lambda d: pd.DataFrame(d, index=stock_close.index)
    return {
        "stock_ret": stock_ret,
        "kospi_raw": frame(kospi_raw),
        "kospi_ex": frame(kospi_ex),
        "krx_raw": frame(krx_raw),
        "self_weight": frame(self_weight),
        "market_ex": frame(market_ex),
        "market_weight": frame(market_weight),
        "global_lag0": frame(global_ret[0]),
        "global_lag1": frame(global_ret[1]),
        "global_lag2": frame(global_ret[2]),
    }


def _ewm_params(y: pd.Series, exog: pd.DataFrame, halflife: float) -> pd.DataFrame:
    decay = 0.5 ** (1.0 / halflife)
    xv, yv = exog.to_numpy(), y.to_numpy()
    k = xv.shape[1]
    sxx, sxy = np.zeros((k, k)), np.zeros(k)
    out = np.full((len(yv), k), np.nan)
    for t in range(len(yv)):
        sxx = decay * sxx + np.outer(xv[t], xv[t])
        sxy = decay * sxy + xv[t] * yv[t]
        if t + 1 >= OLS_MIN_NOBS:
            out[t] = np.linalg.lstsq(sxx, sxy, rcond=None)[0]
    return pd.DataFrame(out, index=exog.index, columns=exog.columns)


def _rolling_params(
    y: pd.Series, x: pd.DataFrame, window: int = OLS_WINDOW, halflife: float | None = None
) -> pd.DataFrame:
    joined = pd.concat([y.rename("y"), x], axis=1).dropna()
    if len(joined) < OLS_MIN_NOBS:
        return pd.DataFrame(index=y.index, columns=["const", *x.columns], dtype=float)
    exog = joined[x.columns].assign(const=1.0)[["const", *x.columns]]
    if halflife is not None:
        params = _ewm_params(joined["y"], exog, halflife)
    else:
        params = RollingOLS(joined["y"], exog, window=window, min_nobs=OLS_MIN_NOBS, expanding=True).fit().params
    return params.shift(1).reindex(y.index)


def decompose(r: pd.Series, g: pd.Series, s: pd.Series, m: pd.Series, method: str) -> pd.DataFrame:
    contrib_market = pd.Series(0.0, index=r.index)
    if method == "simultaneous_unit":
        contrib_global, contrib_sector = g, s
        contrib_idio = r - g - s
    elif method == "sequential_unit":
        contrib_global, contrib_sector = g, s - g
        contrib_idio = r - s
    elif method == "rolling_ols_2f":
        step1 = _rolling_params(s, g.rename("g").to_frame())
        s_resid = s - step1["g"] * g
        step2 = _rolling_params(r, pd.concat([g.rename("g"), s_resid.rename("s_resid")], axis=1))
        contrib_global = step2["g"] * g
        contrib_sector = step2["s_resid"] * s_resid
        contrib_idio = r - contrib_global - contrib_sector
    elif method == "sequential_4l":
        contrib_global = g
        contrib_market = m - g
        contrib_sector = s - m
        contrib_idio = r - s
    else:
        raise ValueError(method)

    out = pd.DataFrame(
        {"r": r, "global": contrib_global, "market": contrib_market, "sector": contrib_sector, "group": 0.0,
         "idio": contrib_idio}
    )
    out = out.dropna()
    out["dominant"] = out[LAYERS].abs().idxmax(axis=1)
    return out


def run_decompositions(factors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    parts = []
    stock_ret = factors["stock_ret"]
    g_all = factors["global_lag1"]
    for variant in SECTOR_VARIANTS:
        s_all = factors[variant]
        for symbol in s_all.columns:
            if symbol not in g_all:
                continue
            for method in METHODS:
                out = decompose(stock_ret[symbol], g_all[symbol], s_all[symbol], factors["market_ex"][symbol], method)
                out = out[out.index >= EVAL_START]
                parts.append(out.assign(symbol=symbol, variant=variant, method=method))
    return pd.concat(parts).rename_axis("date").reset_index()


def e1_self_correlation(factors: dict[str, pd.DataFrame], sector_map: pd.DataFrame) -> pd.DataFrame:
    stock_ret = factors["stock_ret"][factors["stock_ret"].index >= EVAL_START]
    rows = []
    for symbol in stock_ret.columns:
        row = {"symbol": symbol}
        for variant in SECTOR_VARIANTS:
            s = factors[variant].get(symbol)
            row[f"corr_{variant}"] = stock_ret[symbol].corr(s) if s is not None else np.nan
        w = factors["self_weight"].get(symbol)
        row["self_weight_last"] = w.dropna().iloc[-1] if w is not None and w.notna().any() else np.nan
        rows.append(row)
    names = sector_map.set_index("symbol")[["name", "kospi_sector"]]
    return pd.DataFrame(rows).set_index("symbol").join(names).sort_values("self_weight_last", ascending=False)


def e2_summary(decomp: pd.DataFrame) -> pd.DataFrame:
    def per_group(g: pd.DataFrame) -> pd.Series:
        big = g[g["r"].abs() >= BIG_MOVE]
        shares = big["dominant"].value_counts(normalize=True).reindex(LAYERS, fill_value=0.0)
        explained = 1 - g["idio"].var() / g["r"].var()
        return pd.Series(
            {
                "days": len(g),
                "explained_share": explained,
                "idio_std_pct": g["idio"].std() * 100,
                "big_days": len(big),
                **{f"dominant_{k}": v for k, v in shares.items()},
            }
        )

    per_symbol = decomp.groupby(["variant", "method", "symbol"]).apply(per_group, include_groups=False)
    return per_symbol.groupby(["variant", "method"]).median()


def e3_lag_correlation(factors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    stock_ret = factors["stock_ret"][factors["stock_ret"].index >= EVAL_START]
    rows = []
    for symbol in factors["global_lag1"].columns:
        row = {"symbol": symbol}
        for lag in (0, 1, 2):
            g = factors[f"global_lag{lag}"][symbol]
            row[f"lag{lag}"] = stock_ret[symbol].corr(g)
        rows.append(row)
    return pd.DataFrame(rows).set_index("symbol")


def e4_cases(decomp: pd.DataFrame, symbol: str, variant: str = "kospi_ex") -> pd.DataFrame:
    sub = decomp[(decomp["symbol"] == symbol) & (decomp["variant"] == variant)]
    wide = sub.pivot(index="date", columns="method", values=["global", "market", "sector", "idio", "dominant"])
    wide.columns = [f"{method}.{field}" for field, method in wide.columns]
    r = sub.drop_duplicates("date").set_index("date")["r"]
    top_dates = r.abs().sort_values(ascending=False).head(CASE_COUNT).index
    table = wide.loc[top_dates].copy()
    table.insert(0, "r", r.loc[top_dates])
    numeric = [c for c in table.columns if not c.endswith("dominant")]
    table[numeric] = (table[numeric].astype(float) * 100).round(2)
    return table.sort_index()


US_CANDIDATES = ["sector_etf", "SPY", "QQQ"]
FALLBACK_THRESHOLDS = [0.0, 0.3, 0.5, 0.7]
FALLBACK_CHECKS = [("015760", "2026-03-03"), ("015760", "2026-03-09"), ("005490", "2026-03-03"),
                   ("011200", "2026-03-03"), ("035420", "2026-06-08")]


def _ols_r2(y: pd.Series, x: pd.DataFrame) -> float:
    joined = pd.concat([y.rename("y"), x], axis=1).dropna()
    exog = np.column_stack([np.ones(len(joined)), joined[x.columns].to_numpy()])
    coef, *_ = np.linalg.lstsq(exog, joined["y"].to_numpy(), rcond=None)
    resid = joined["y"].to_numpy() - exog @ coef
    return 1 - resid.var() / joined["y"].var(ddof=0)


def us_factor_compare(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    stock_ret = factors["stock_ret"]
    us_ret = dataset["us_close"].pct_change(fill_method=None)
    sector_map = dataset["sector_map"].set_index("symbol")
    eval_mask = stock_ret.index >= EVAL_START
    rows = []
    for symbol in stock_ret.columns:
        r = stock_ret.loc[eval_mask, symbol]
        series = {
            "sector_etf": factors["global_lag1"][symbol],
            "SPY": align_us_to_kr(us_ret["SPY"], stock_ret.index, 1),
            "QQQ": align_us_to_kr(us_ret["QQQ"], stock_ret.index, 1),
        }
        series = {k: v[eval_mask] for k, v in series.items()}
        row = {"symbol": symbol, "name": sector_map.loc[symbol, "name"], "us_etf": sector_map.loc[symbol, "us_etf"]}
        for k, v in series.items():
            row[f"corr_{k}"] = r.corr(v)
        row["r2_spy"] = _ols_r2(r, series["SPY"].rename("spy").to_frame())
        row["r2_spy_plus_sector"] = _ols_r2(r, pd.concat([series["SPY"].rename("spy"), series["sector_etf"].rename("etf")], axis=1))
        row["r2_gain_sector"] = row["r2_spy_plus_sector"] - row["r2_spy"]
        rows.append(row)
    return pd.DataFrame(rows).set_index("symbol").sort_values("r2_gain_sector", ascending=False)


def sector_with_fallback(factors: dict[str, pd.DataFrame], threshold: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    remaining = 1 - factors["self_weight"]
    use_market = remaining.reindex_like(factors["kospi_ex"]) < threshold
    sector = factors["kospi_ex"].where(~use_market, factors["market_ex"])
    return sector, use_market


def fallback_experiment(factors: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    stock_ret = factors["stock_ret"]
    g_all = factors["global_lag1"]
    summary_rows, check_rows, parts = [], [], []
    for t in FALLBACK_THRESHOLDS:
        sector, use_market = sector_with_fallback(factors, t)
        for symbol in sector.columns:
            if symbol not in g_all:
                continue
            out = decompose(stock_ret[symbol], g_all[symbol], sector[symbol], factors["market_ex"][symbol], "sequential_4l")
            out = out[out.index >= EVAL_START]
            parts.append(out.assign(symbol=symbol, threshold=t))
            big = out[out["r"].abs() >= BIG_MOVE]
            summary_rows.append(
                {
                    "threshold": t,
                    "symbol": symbol,
                    "fallback_days_share": use_market.loc[out.index, symbol].mean(),
                    "corr_sector_idio": out["sector"].corr(out["idio"]),
                    "idio_std_pct": out["idio"].std() * 100,
                    "sector_std_pct": out["sector"].std() * 100,
                    "dominant_idio_big": (big["dominant"] == "idio").mean(),
                    "dominant_market_big": (big["dominant"] == "market").mean(),
                }
            )
    per_symbol = pd.DataFrame(summary_rows)
    decomp_fb = pd.concat(parts).rename_axis("date").reset_index()
    checks = decomp_fb.set_index(["symbol", "date"]).loc[
        [(sym, pd.Timestamp(d)) for sym, d in FALLBACK_CHECKS]
    ].reset_index()
    checks[["r", *LAYERS]] = (checks[["r", *LAYERS]] * 100).round(2)
    return per_symbol, decomp_fb, checks


US_SECTOR_ETF_KEEP = {"SOXX", "XLB"}
OLS_CHECKS = FALLBACK_CHECKS + [("000660", "2026-04-08"), ("000660", "2026-08-20"), ("006400", "2026-03-04"),
                                ("005490", "2026-01-13"), ("005930", "2026-06-08")]


BETA_CLIP = {"m_res": (0.0, 3.0), "s_res": (0.0, 2.5), "grp_res": (0.0, 2.5)}


def decompose_ols_4l(
    r: pd.Series, spy: pd.Series, etf: pd.Series | None, m: pd.Series, s: pd.Series | None,
    grp: pd.Series | None = None, cfg: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = cfg or {}
    fit = {"window": cfg.get("window", OLS_WINDOW), "halflife": cfg.get("halflife")}

    def resid(y: pd.Series, regs: list[pd.Series], name: str) -> pd.Series:
        frame = pd.concat(regs, axis=1)
        p = _rolling_params(y, frame, **fit)
        fitted = p["const"] + sum(p[c.name] * c for c in regs)
        return (y - fitted).rename(name)

    spy = spy.rename("spy")
    m = m.rename("m")
    m_res = resid(m, [spy], "m_res")
    cols = [spy]
    if etf is not None:
        cols.append(resid(etf, [spy], "etf_res"))
    us_cols = list(cols)
    cols.append(m_res)
    s_res = None
    if s is not None:
        s_res = resid(s, [spy, m], "s_res")
        cols.append(s_res)
    g_res = None
    if grp is not None:
        regs = [spy, m] + ([s.rename("s")] if s is not None else [])
        g_res = resid(grp, regs, "grp_res")
        cols.append(g_res)

    beta = _rolling_params(r, pd.concat(cols, axis=1), **fit)
    if cfg.get("clip"):
        for col, (lo, hi) in BETA_CLIP.items():
            if col in beta:
                beta[col] = beta[col].clip(lo, hi)

    zero = pd.Series(0.0, index=r.index)
    contrib_global = sum(beta[c.name] * c for c in us_cols)
    contrib_market = beta["m_res"] * m_res
    contrib_sector = beta["s_res"] * s_res if s_res is not None else zero
    contrib_group = beta["grp_res"] * g_res if g_res is not None else zero
    contrib_idio = r - contrib_global - contrib_market - contrib_sector - contrib_group
    for col in ("s_res", "grp_res"):
        if col not in beta:
            beta[col] = np.nan

    out = pd.DataFrame(
        {"r": r, "global": contrib_global, "market": contrib_market, "sector": contrib_sector,
         "group": contrib_group, "idio": contrib_idio}
    ).dropna()
    out["dominant"] = out[LAYERS].abs().idxmax(axis=1)
    return out, beta


def ols_experiment(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame]):
    stock_ret = factors["stock_ret"]
    us_ret = dataset["us_close"].pct_change(fill_method=None)
    spy = align_us_to_kr(us_ret["SPY"], stock_ret.index, 1)
    sector_map = dataset["sector_map"].set_index("symbol")
    parts, betas = [], []
    for symbol in stock_ret.columns:
        etf_name = sector_map.loc[symbol, "us_etf"]
        etf = factors["global_lag1"][symbol] if etf_name in US_SECTOR_ETF_KEEP else None
        r = stock_ret[symbol]
        ols, beta = decompose_ols_4l(r, spy, etf, factors["market_ex"][symbol], factors["kospi_ex"][symbol])
        parts.append(ols[ols.index >= EVAL_START].assign(symbol=symbol, method="ols_4l"))
        betas.append(beta[beta.index >= EVAL_START].assign(symbol=symbol))

        seq = decompose(r, factors["global_lag1"][symbol], factors["kospi_ex"][symbol],
                        factors["market_ex"][symbol], "sequential_4l")
        parts.append(seq[seq.index >= EVAL_START].assign(symbol=symbol, method="sequential_4l"))
    decomp_ols = pd.concat(parts).rename_axis("date").reset_index()
    beta_panel = pd.concat(betas).rename_axis("date").reset_index()

    def per_symbol(g: pd.DataFrame) -> pd.Series:
        big = g[g["r"].abs() >= BIG_MOVE]
        return pd.Series(
            {
                "explained_share": 1 - g["idio"].var() / g["r"].var(),
                "idio_std_pct": g["idio"].std() * 100,
                "corr_sector_idio": g["sector"].corr(g["idio"]),
                "corr_market_idio": g["market"].corr(g["idio"]),
                **{f"dominant_{k}": (big["dominant"] == k).mean() for k in LAYERS},
            }
        )

    metrics = decomp_ols.groupby(["method", "symbol"]).apply(per_symbol, include_groups=False)
    summary = metrics.groupby("method").median()
    corr_compare = metrics["corr_sector_idio"].unstack("method").join(sector_map["name"])

    beta_last = beta_panel.groupby("symbol").last().join(sector_map[["name", "kospi_sector"]])

    keys = [(sym, pd.Timestamp(d)) for sym, d in OLS_CHECKS]
    checks = decomp_ols.set_index(["symbol", "date"]).loc[keys].reset_index()
    checks[["r", *LAYERS]] = (checks[["r", *LAYERS]] * 100).round(2)
    return decomp_ols, beta_panel, summary, corr_compare, beta_last, checks


RECLASS_MIN_GAIN = 0.01
SELECT_START = pd.Timestamp("2025-04-01")


def candidate_sector_returns(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    stock_close = dataset["stock_close"]
    index_close = dataset["index_close"].reindex(stock_close.index)
    weights = dataset["sector_weights"]
    stock_ret = factors["stock_ret"]
    out = {}
    for code in data.KOSPI_SECTOR_CODES:
        idx_ret = index_close[code].pct_change(fill_method=None)
        cols = {}
        for symbol in stock_close.columns:
            row = weights[(weights["sector_code"] == code) & (weights["symbol"] == symbol)]
            if row.empty:
                cols[symbol] = idx_ret
                continue
            w = self_weight_history(stock_close[symbol], index_close[code], row["weight_pct"].iloc[0] / 100)
            w = w.where(w < MAX_SELF_WEIGHT)
            cols[symbol] = (idx_ret - w * stock_ret[symbol]) / (1 - w)
        out[code] = pd.DataFrame(cols)
    return out


def _resid(y: pd.Series, x: pd.DataFrame) -> pd.Series:
    joined = pd.concat([y.rename("y"), x], axis=1).dropna()
    exog = np.column_stack([np.ones(len(joined)), joined[x.columns].to_numpy()])
    coef, *_ = np.linalg.lstsq(exog, joined["y"].to_numpy(), rcond=None)
    return pd.Series(joined["y"].to_numpy() - exog @ coef, index=joined.index)


def reclass_scores(
    dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame], candidates: dict[str, pd.DataFrame]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stock_ret = factors["stock_ret"]
    window = (stock_ret.index >= SELECT_START) & (stock_ret.index < EVAL_START)
    us_ret = dataset["us_close"].pct_change(fill_method=None)
    spy = align_us_to_kr(us_ret["SPY"], stock_ret.index, 1)[window].rename("spy")
    gains, betas = {}, {}
    for symbol in stock_ret.columns:
        r = stock_ret.loc[window, symbol]
        m = factors["market_ex"].loc[window, symbol].rename("m")
        m_res = _resid(m, spy.to_frame()).rename("m_res")
        base = pd.concat([spy, m_res], axis=1)
        base_r2 = _ols_r2(r, base)
        g_row, b_row = {}, {}
        for code, frame in candidates.items():
            s_res = _resid(frame.loc[window, symbol].rename("s"), pd.concat([spy, m], axis=1)).rename("s_res")
            x = pd.concat([base, s_res], axis=1).dropna()
            joined = pd.concat([r.rename("y"), x], axis=1).dropna()
            exog = np.column_stack([np.ones(len(joined)), joined[x.columns].to_numpy()])
            coef, *_ = np.linalg.lstsq(exog, joined["y"].to_numpy(), rcond=None)
            g_row[code] = _ols_r2(r, x) - base_r2
            b_row[code] = coef[-1]
        gains[symbol], betas[symbol] = g_row, b_row
    return pd.DataFrame(gains).T, pd.DataFrame(betas).T


def choose_sectors(gains: pd.DataFrame, betas: pd.DataFrame) -> pd.Series:
    eligible = gains.where(betas > 0)
    best = eligible.idxmax(axis=1)
    best_gain = eligible.max(axis=1)
    return best.where(best_gain >= RECLASS_MIN_GAIN)


def run_ols_4l(
    dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame], sector_by_symbol: dict[str, pd.Series | None],
    method: str, cfg: dict | None = None, group_by_symbol: dict[str, pd.Series | None] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    stock_ret = factors["stock_ret"]
    us_ret = dataset["us_close"].pct_change(fill_method=None)
    spy = align_us_to_kr(us_ret["SPY"], stock_ret.index, 1)
    sector_map = dataset["sector_map"].set_index("symbol")
    parts, betas = [], []
    for symbol in stock_ret.columns:
        etf = factors["global_lag1"][symbol] if sector_map.loc[symbol, "us_etf"] in US_SECTOR_ETF_KEEP else None
        grp = (group_by_symbol or {}).get(symbol)
        out, beta = decompose_ols_4l(stock_ret[symbol], spy, etf, factors["market_ex"][symbol],
                                     sector_by_symbol[symbol], grp, cfg)
        parts.append(out[out.index >= EVAL_START].assign(symbol=symbol, method=method))
        betas.append(beta[beta.index >= EVAL_START].assign(symbol=symbol, method=method))
    return (pd.concat(parts).rename_axis("date").reset_index(),
            pd.concat(betas).rename_axis("date").reset_index())


def method_metrics(decomp: pd.DataFrame) -> pd.DataFrame:
    def per_symbol(g: pd.DataFrame) -> pd.Series:
        big = g[g["r"].abs() >= BIG_MOVE]
        return pd.Series(
            {
                "explained_share": 1 - g["idio"].var() / g["r"].var(),
                "idio_std_pct": g["idio"].std() * 100,
                "corr_sector_idio": g["sector"].corr(g["idio"]) if g["sector"].std() > 0 else 0.0,
                "corr_group_idio": g["group"].corr(g["idio"]) if g["group"].std() > 0 else np.nan,
                **{f"dominant_{k}": (big["dominant"] == k).mean() for k in LAYERS},
            }
        )

    return decomp.groupby(["method", "symbol"]).apply(per_symbol, include_groups=False)


def reclass_experiment(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame]):
    candidates = candidate_sector_returns(dataset, factors)
    gains, cand_betas = reclass_scores(dataset, factors, candidates)
    chosen = choose_sectors(gains, cand_betas)

    sector_map = dataset["sector_map"].set_index("symbol")
    default = {s: factors["kospi_ex"][s] for s in factors["stock_ret"].columns}
    reclassed = {s: (candidates[c][s] if isinstance(c, str) else None) for s, c in chosen.items()}

    decomp_default, beta_default = run_ols_4l(dataset, factors, default, "ols_4l")
    decomp_reclass, beta_reclass = run_ols_4l(dataset, factors, reclassed, "ols_4l_reclass")
    decomp_all = pd.concat([decomp_default, decomp_reclass], ignore_index=True)
    beta_all = pd.concat([beta_default, beta_reclass], ignore_index=True)

    choice = pd.DataFrame(
        {
            "name": sector_map["name"],
            "kospi_sector": sector_map["kospi_sector"],
            "chosen_sector": chosen.map(data.KOSPI_SECTOR_NAMES).fillna("(업종 층 없음)"),
            "gain_default": [gains.loc[s, c] if isinstance(c, str) else np.nan for s, c in sector_map["kospi_sector_code"].items()],
            "gain_chosen": [gains.loc[s, c] if isinstance(c, str) else np.nan for s, c in chosen.items()],
        }
    )
    return candidates, gains, cand_betas, choice, decomp_all, beta_all


STABILITY_VARIANTS = {
    "roll120": {"window": 120},
    "roll250": {"window": 250},
    "ewm_hl60": {"halflife": 60},
    "roll120_clip": {"window": 120, "clip": True},
}

GROUPS = {
    "삼성": ["005930", "028260", "006400", "032830", "018260", "009150"],
    "SK": ["000660", "034730", "017670", "096770"],
    "현대차": ["005380", "000270", "012330"],
    "LG": ["051910", "066570", "003550"],
    "POSCO": ["005490", "047050"],
    "아모레": ["090430", "002790"],
}


def beta_roughness(beta_panel: pd.DataFrame) -> pd.DataFrame:
    cols = ["spy", "m_res", "s_res"]
    def per_symbol(g: pd.DataFrame) -> pd.Series:
        g = g.sort_values("date")
        out = {}
        for c in cols:
            out[f"{c}_step"] = g[c].diff().abs().median()
            out[f"{c}_range"] = g[c].max() - g[c].min()
            out[f"{c}_neg_share"] = (g[c] < 0).mean()
        return pd.Series(out)
    return beta_panel.groupby(["method", "symbol"]).apply(per_symbol, include_groups=False)


def stability_experiment(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame]):
    default = {s: factors["kospi_ex"][s] for s in factors["stock_ret"].columns}
    decomps, betas = [], []
    for name, cfg in STABILITY_VARIANTS.items():
        d, b = run_ols_4l(dataset, factors, default, name, cfg)
        decomps.append(d)
        betas.append(b)
    decomp = pd.concat(decomps, ignore_index=True)
    beta_panel = pd.concat(betas, ignore_index=True)
    metrics = method_metrics(decomp)
    rough = beta_roughness(beta_panel)
    summary = metrics.groupby("method").median().join(rough.groupby("method").median())
    return decomp, beta_panel, metrics, rough, summary.loc[list(STABILITY_VARIANTS)]


def group_baskets(factors: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.Series], dict[str, str]]:
    stock_ret = factors["stock_ret"]
    baskets, group_of = {}, {}
    for group, members in GROUPS.items():
        for symbol in members:
            others = [m for m in members if m != symbol and m in stock_ret]
            baskets[symbol] = stock_ret[others].mean(axis=1)
            group_of[symbol] = group
    return baskets, group_of


GROUP_CASES = [("028260", None), ("005380", None), ("000270", None), ("034730", None), ("003550", None)]


def group_experiment(dataset: dict[str, pd.DataFrame], factors: dict[str, pd.DataFrame], cfg: dict):
    default = {s: factors["kospi_ex"][s] for s in factors["stock_ret"].columns}
    baskets, group_of = group_baskets(factors)
    d0, b0 = run_ols_4l(dataset, factors, default, "no_group", cfg)
    d1, b1 = run_ols_4l(dataset, factors, default, "with_group", cfg, baskets)
    decomp = pd.concat([d0, d1], ignore_index=True)
    beta_panel = pd.concat([b0, b1], ignore_index=True)
    metrics = method_metrics(decomp)
    in_group = metrics.index.get_level_values("symbol").isin(list(group_of))
    by_symbol = metrics[in_group]["explained_share"].unstack("method")
    by_symbol["gain"] = by_symbol["with_group"] - by_symbol["no_group"]
    names = dataset["sector_map"].set_index("symbol")["name"]
    by_symbol = by_symbol.join(names).assign(group=lambda f: f.index.map(group_of)).sort_values("gain", ascending=False)
    grp_beta_last = b1[b1["symbol"].isin(list(group_of))].groupby("symbol")["grp_res"].last()
    by_symbol["grp_beta_last"] = grp_beta_last
    summary = metrics[in_group].groupby("method").median()
    return decomp, beta_panel, metrics, by_symbol, summary


MARKET_CLOSE = "153000"
NEWS_PER_CASE = 15


def case_news(symbol: str, case_dates: pd.DatetimeIndex) -> dict[pd.Timestamp, pd.DataFrame]:
    client = data.KisClient()
    out = {}
    for d in case_dates:
        news = data.load_or_fetch_news(symbol, d.date(), client)
        out[d] = news[news["time"] <= MARKET_CLOSE]
    return out


def case_report(symbol: str, cases: pd.DataFrame, news: dict[pd.Timestamp, pd.DataFrame]) -> str:
    lines = []
    for d, row in cases.iterrows():
        lines.append(
            f"\n=== {symbol} {d.date()} r={row['r']:+.2f}% | "
            f"seq: g={row['sequential_unit.global']:+.2f} s={row['sequential_unit.sector']:+.2f} "
            f"i={row['sequential_unit.idio']:+.2f} ({row['sequential_unit.dominant']}) | "
            f"ols: g={row['rolling_ols_2f.global']:+.2f} s={row['rolling_ols_2f.sector']:+.2f} "
            f"i={row['rolling_ols_2f.idio']:+.2f} ({row['rolling_ols_2f.dominant']}) | "
            f"4l: g={row['sequential_4l.global']:+.2f} m={row['sequential_4l.market']:+.2f} "
            f"s={row['sequential_4l.sector']:+.2f} i={row['sequential_4l.idio']:+.2f} "
            f"({row['sequential_4l.dominant']})"
        )
        titles = news[d]
        lines.append(f"    news before close: {len(titles)}")
        for _, n in titles.tail(NEWS_PER_CASE).iloc[::-1].iterrows():
            lines.append(f"    {n['time'][:4]} {n['title'][:80]}")
    return "\n".join(lines)


if __name__ == "__main__":
    dataset = data.load_or_fetch(refresh=False)
    sector_map = dataset["sector_map"]
    sector_weights = dataset["sector_weights"]

    factors = build_factors(dataset)
    stock_ret = factors["stock_ret"]
    self_weight = factors["self_weight"]

    e1 = e1_self_correlation(factors, sector_map)

    decomp = run_decompositions(factors)
    e2 = e2_summary(decomp)

    krx_symbols = set(decomp.loc[decomp["variant"] == "krx_raw", "symbol"])
    e2_common = e2_summary(decomp[decomp["symbol"].isin(krx_symbols)])
    days_per_method = decomp.groupby(["variant", "method"])["date"].agg(["min", "max", "count"])

    e3 = e3_lag_correlation(factors)
    e3_median = e3.median()

    e4 = {symbol: e4_cases(decomp, symbol) for symbol in CASE_SYMBOLS}
    e4_hynix = e4["000660"]
    e4_samsung = e4["005930"]
    news_hynix = case_news("000660", e4_hynix.index)
    news_samsung = case_news("005930", e4_samsung.index)
    report_hynix = case_report("000660", e4_hynix, news_hynix)
    report_samsung = case_report("005930", e4_samsung, news_samsung)

    e4_extra = {symbol: e4_cases(decomp, symbol) for symbol in EXTRA_CASE_SYMBOLS}
    news_extra = {symbol: case_news(symbol, cases.index) for symbol, cases in e4_extra.items()}
    reports_extra = {symbol: case_report(symbol, e4_extra[symbol], news_extra[symbol]) for symbol in EXTRA_CASE_SYMBOLS}
    dominant_extra = pd.concat(
        {
            symbol: cases[[c for c in cases.columns if c.endswith("dominant")]].assign(r=cases["r"])
            for symbol, cases in e4_extra.items()
        }
    )

    us_compare = us_factor_compare(dataset, factors)
    us_compare_median = us_compare.drop(columns=["name", "us_etf"]).median()

    fb_per_symbol, decomp_fb, fb_checks = fallback_experiment(factors)
    fb_summary = fb_per_symbol.drop(columns="symbol").groupby("threshold").median()
    fb_by_symbol = fb_per_symbol.pivot(index="symbol", columns="threshold", values="corr_sector_idio")

    decomp_ols, beta_panel, ols_summary, ols_corr_compare, beta_last, ols_checks = ols_experiment(dataset, factors)

    candidates, reclass_gains, reclass_betas, reclass_choice, decomp_reclass, beta_reclass = reclass_experiment(dataset, factors)
    reclass_metrics = method_metrics(decomp_reclass)
    reclass_summary = reclass_metrics.groupby("method").median()

    stab_decomp, stab_beta, stab_metrics, stab_rough, stab_summary = stability_experiment(dataset, factors)
    grp_decomp, grp_beta, grp_metrics, grp_by_symbol, grp_summary = group_experiment(
        dataset, factors, STABILITY_VARIANTS["roll120"]
    )

    pd.set_option("display.width", 200)
    print(e1.round(3).to_string())
    print(e2.round(3).to_string())
    print(e2_common.round(3).to_string())
    print(days_per_method.to_string())
    print(e3_median.round(3).to_string())
    print(report_hynix)
    print(report_samsung)
    print(us_compare.round(3).to_string())
    print(us_compare_median.round(3).to_string())
    print(fb_summary.round(3).to_string())
    print(fb_by_symbol.round(2).sort_values(0.0).to_string())
    print(fb_checks.sort_values(["symbol", "date", "threshold"]).to_string())
    print(ols_summary.round(3).to_string())
    print(ols_corr_compare.round(2).sort_values("sequential_4l").to_string())
    print(beta_last.round(2).to_string())
    print(ols_checks.sort_values(["symbol", "date", "method"]).to_string())
    print(reclass_choice.round(3).to_string())
    print(reclass_summary.round(3).to_string())
    print(stab_summary.round(3).T.to_string())
    print(grp_summary.round(3).to_string())
    print(grp_by_symbol.round(3).to_string())
