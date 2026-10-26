"""Four-layer return decomposition shared by the market data job and the move sink.

Ported from ``experiments/return_decomposition/analysis.py`` (doc 22 section 11).

    r_i = c_global + c_market + c_sector + e_i

The layers are built in order so that a movement explained by an earlier layer
is removed before the next layer is formed:

* ``spy``: previous US regular session SPY return (lag 1).
* ``etf_res``: sector ETF return not explained by SPY (only for sectors linked
  with ``PROXIED_BY``).
* ``m_res``: KOSPI return without symbol i, minus the part explained by SPY.
* ``s_res``: sector index return without symbol i, minus the part explained by
  SPY and the market.

Every rolling regression uses an expanding window that becomes a rolling window
of ``OLS_WINDOW`` rows once enough history exists, and needs ``OLS_MIN_NOBS``
rows. This matches statsmodels ``RollingOLS(window=120, min_nobs=60,
expanding=True)``. Parameters estimated at date t use data up to and including
t; they are shifted by one row before they are applied, so a day never uses its
own movement.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import NDArray

OLS_WINDOW = 120
OLS_MIN_NOBS = 60
MAX_SELF_WEIGHT = 0.95
SECTOR_BETA_FLOOR = 0.0
LAYERS = ("global", "market", "sector", "idio")

BetaMethod = Literal["OLS_4L", "UNIT_4L"]


@dataclass(frozen=True)
class RollingFit:
    """Rolling OLS output indexed like the regression input.

    ``params`` has a ``const`` column followed by the regressor columns. ``r2``
    and ``n_obs`` describe the window used at each row. Rows without enough
    observations are NaN (``n_obs`` is 0 there).
    """

    params: pd.DataFrame
    r2: pd.Series
    n_obs: pd.Series


def rolling_ols(
    y: pd.Series,
    x: pd.DataFrame,
    window: int = OLS_WINDOW,
    min_nobs: int = OLS_MIN_NOBS,
    shift: int = 0,
) -> RollingFit:
    """Expanding-then-rolling OLS of ``y`` on ``const + x`` over complete rows.

    With ``shift=0`` the row at t uses observations up to and including t.
    ``shift=1`` moves results to the next complete row, so the row at t uses
    data up to the previous complete row (the experiment's convention).
    """
    columns = ["const", *x.columns]
    joined = pd.concat([y.rename("__y__"), x], axis=1).dropna()
    params = np.full((len(joined), len(columns)), np.nan)
    r2 = np.full(len(joined), np.nan)
    n_obs = np.zeros(len(joined), dtype=int)
    if len(joined) >= min_nobs:
        yv = joined["__y__"].to_numpy(dtype=float)
        xv = np.column_stack(
            [np.ones(len(joined)), joined[list(x.columns)].to_numpy(dtype=float)]
        )
        for end in range(min_nobs, len(joined) + 1):
            start = max(0, end - window)
            xw, yw = xv[start:end], yv[start:end]
            coef = np.linalg.lstsq(xw, yw, rcond=None)[0]
            resid = yw - xw @ coef
            sst = float(((yw - yw.mean()) ** 2).sum())
            params[end - 1] = coef
            r2[end - 1] = 1.0 - float(resid @ resid) / sst if sst > 0 else np.nan
            n_obs[end - 1] = end - start
    index = joined.index
    params = _shift_rows(params, shift)
    r2 = _shift_rows(r2, shift)
    n_obs = np.nan_to_num(_shift_rows(n_obs.astype(float), shift)).astype(int)
    full_n_obs = pd.Series(n_obs, index=index).reindex(y.index, fill_value=0)
    return RollingFit(
        params=pd.DataFrame(params, index=index, columns=columns).reindex(y.index),
        r2=pd.Series(r2, index=index).reindex(y.index),
        n_obs=full_n_obs.astype(int),
    )


def _shift_rows(values: NDArray[np.float64], shift: int) -> NDArray[np.float64]:
    if shift == 0:
        return values
    out = np.full_like(values, np.nan, dtype=float)
    out[shift:] = values[:-shift]
    return out


def lagged_params(y: pd.Series, x: pd.DataFrame) -> pd.DataFrame:
    return rolling_ols(y, x, shift=1).params


def align_us_to_kr(
    us_ret: pd.Series, kr_dates: pd.DatetimeIndex, lag: int = 1
) -> pd.Series:
    """Map US returns onto KR dates.

    ``lag=1`` picks the last US session that ended strictly before the KR date
    (the previous US regular session).
    """
    us = us_ret.dropna().sort_index()
    us_dates = pd.DatetimeIndex(us.index).to_numpy()
    kr_values = kr_dates.to_numpy()
    if lag == 0:
        pos = np.searchsorted(us_dates, kr_values, side="right") - 1
    else:
        pos = np.searchsorted(us_dates, kr_values, side="left") - lag
    values = np.where(pos >= 0, us.to_numpy()[np.clip(pos, 0, None)], np.nan)
    return pd.Series(values, index=kr_dates, dtype=float)


def _value_at(series: pd.Series, anchor: datetime) -> float:
    valid = series.dropna()
    before = valid.loc[pd.DatetimeIndex(valid.index) <= anchor]
    if not before.empty:
        return float(before.iloc[-1])
    return float(valid.iloc[0])


def _last_date(series: pd.Series) -> datetime:
    last = pd.DatetimeIndex(series.dropna().index).max()
    if not isinstance(last, datetime):
        raise TypeError("series has no dated values")
    return last


def self_weight_history(
    stock_close: pd.Series,
    index_close: pd.Series,
    weight_at_anchor: float,
    anchor: datetime | None = None,
) -> pd.Series:
    """Approximate past index weights from one weight snapshot.

    w(t) = w(T) * (P_i(t) / P_i(T)) / (I(t) / I(T)), shifted by one row so the
    weight at t is the weight at the start of day t. Assumes the index level
    tracks the market cap of its members.
    """
    if stock_close.dropna().empty or index_close.dropna().empty:
        return pd.Series(np.nan, index=stock_close.index)
    at = anchor or _last_date(stock_close)
    price_ratio = stock_close.to_numpy(dtype=float) / _value_at(stock_close, at)
    index_ratio = index_close.to_numpy(dtype=float) / _value_at(index_close, at)
    weight = weight_at_anchor * price_ratio / index_ratio
    return pd.Series(_shift_rows(weight, 1), index=stock_close.index)


def excluded_index_return(
    index_ret: pd.Series, stock_ret: pd.Series, weight: pd.Series
) -> pd.Series:
    return (index_ret - weight * stock_ret) / (1 - weight)


@dataclass(frozen=True)
class LayerInputs:
    """Raw daily returns on the KR calendar for one symbol.

    ``spy`` and ``etf`` are already aligned to KR dates (lag 1). ``m`` and ``s``
    are self-excluded index returns. ``etf`` is None when the sector has no
    ``PROXIED_BY`` ETF; ``s`` is None when no sector factor is formed.
    """

    r: pd.Series
    spy: pd.Series
    m: pd.Series
    etf: pd.Series | None = None
    s: pd.Series | None = None


def build_layer_inputs(
    stock_close: pd.Series,
    market_close: pd.Series,
    spy_close: pd.Series,
    *,
    market_weight: float,
    market_weight_anchor: datetime | None = None,
    sector_close: pd.Series | None = None,
    sector_weight: float | None = None,
    sector_weight_anchor: datetime | None = None,
    etf_close: pd.Series | None = None,
) -> LayerInputs:
    """Build the layer inputs from closes (port of ``build_factors``).

    ``stock_close``, ``market_close`` and ``sector_close`` must share the KR
    calendar index. US closes are indexed by US business date. A missing
    weight means the symbol is outside the ranking, so its weight is treated
    as 0. A sector weight of 95% or more removes the sector factor on that
    day.
    """
    kr_dates = pd.DatetimeIndex(stock_close.index)
    r = stock_close.pct_change(fill_method=None)
    market_ret = market_close.reindex(kr_dates).pct_change(fill_method=None)
    mw = self_weight_history(
        stock_close, market_close.reindex(kr_dates), market_weight, market_weight_anchor
    )
    m = excluded_index_return(market_ret, r, mw)
    spy = align_us_to_kr(spy_close.pct_change(fill_method=None), kr_dates, 1)
    etf = (
        align_us_to_kr(etf_close.pct_change(fill_method=None), kr_dates, 1)
        if etf_close is not None
        else None
    )
    s = None
    if sector_close is not None:
        sector_aligned = sector_close.reindex(kr_dates)
        sector_ret = sector_aligned.pct_change(fill_method=None)
        sw = self_weight_history(
            stock_close, sector_aligned, sector_weight or 0.0, sector_weight_anchor
        )
        sw = sw.where(sw < MAX_SELF_WEIGHT)
        candidate = excluded_index_return(sector_ret, r, sw)
        if candidate.notna().any():
            s = candidate
    return LayerInputs(r=r, spy=spy, m=m, etf=etf, s=s)


def _resid(y: pd.Series, regs: list[pd.Series], name: str) -> pd.Series:
    frame = pd.concat(regs, axis=1)
    p = lagged_params(y, frame)
    fitted = p["const"] + sum(p[str(c.name)] * c for c in regs)
    return (y - fitted).rename(name)


def build_layer_factors(inputs: LayerInputs) -> pd.DataFrame:
    """Residualized layer factors (``spy``, ``etf_res``, ``m_res``, ``s_res``).

    ``etf_res`` and ``s_res`` columns exist only when their inputs exist.
    """
    spy = inputs.spy.rename("spy")
    m = inputs.m.rename("m")
    cols: list[pd.Series] = [spy]
    if inputs.etf is not None:
        cols.append(_resid(inputs.etf.rename("etf"), [spy], "etf_res"))
    cols.append(_resid(m, [spy], "m_res"))
    if inputs.s is not None:
        cols.append(_resid(inputs.s.rename("s"), [spy, m], "s_res"))
    return pd.concat(cols, axis=1)


def fit_layer_betas(inputs: LayerInputs) -> tuple[pd.DataFrame, RollingFit]:
    """Rolling betas of r on the layer factors, unshifted (row t uses data to t).

    Returns the factor frame and the fit. The sector beta is floored at 0.
    """
    factors = build_layer_factors(inputs)
    fit = rolling_ols(inputs.r, factors)
    return factors, RollingFit(
        params=_floor_sector(fit.params), r2=fit.r2, n_obs=fit.n_obs
    )


def _floor_sector(params: pd.DataFrame) -> pd.DataFrame:
    if "s_res" not in params:
        return params
    out = params.copy()
    out["s_res"] = out["s_res"].clip(lower=SECTOR_BETA_FLOOR)
    return out


def layer_contributions(
    r: pd.Series, factors: pd.DataFrame, betas: pd.DataFrame
) -> pd.DataFrame:
    """Contributions per layer given betas aligned to the move dates.

    ``betas`` must already be the betas usable on each row (estimated with data
    before the row). Missing ``etf_res`` or ``s_res`` contribute 0.
    """
    zero = pd.Series(0.0, index=r.index)

    def term(col: str) -> pd.Series:
        if col not in factors or col not in betas:
            return zero
        return betas[col] * factors[col]

    contrib_global = term("spy") + term("etf_res")
    contrib_market = term("m_res")
    contrib_sector = term("s_res")
    contrib_idio = r - contrib_global - contrib_market - contrib_sector
    out = pd.DataFrame(
        {
            "r": r,
            "global": contrib_global,
            "market": contrib_market,
            "sector": contrib_sector,
            "idio": contrib_idio,
        }
    ).dropna()
    out["dominant"] = out[list(LAYERS)].abs().idxmax(axis=1)
    return out


def decompose_ols_4l(inputs: LayerInputs) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``OLS_4L``: four-layer decomposition with rolling betas.

    Port of ``decompose_ols_4l`` without the group layer (doc 22 section 11.6).
    Returns (contributions, betas used on each row).
    """
    factors = build_layer_factors(inputs)
    betas = _floor_sector(rolling_ols(inputs.r, factors, shift=1).params)
    return layer_contributions(inputs.r, factors, betas), betas


def decompose_unit_4l(inputs: LayerInputs) -> pd.DataFrame:
    """``UNIT_4L``: betas fixed to 1, layers removed in order.

    Port of the experiment's ``sequential_4l``: g is the symbol's US factor
    (the ``PROXIED_BY`` sector ETF when present, otherwise SPY), then
    global = g, market = m - g, sector = s - m, idio = r - s. Without a
    sector factor, idio = r - m.
    """
    zero = pd.Series(0.0, index=inputs.r.index)
    g = inputs.etf if inputs.etf is not None else inputs.spy
    contrib_global = g
    contrib_market = inputs.m - g
    if inputs.s is not None:
        contrib_sector = inputs.s - inputs.m
        contrib_idio = inputs.r - inputs.s
    else:
        contrib_sector = zero
        contrib_idio = inputs.r - inputs.m
    out = pd.DataFrame(
        {
            "r": inputs.r,
            "global": contrib_global,
            "market": contrib_market,
            "sector": contrib_sector,
            "idio": contrib_idio,
        }
    ).dropna()
    out["dominant"] = out[list(LAYERS)].abs().idxmax(axis=1)
    return out
