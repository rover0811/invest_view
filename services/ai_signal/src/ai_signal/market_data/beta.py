"""``gold.symbol_beta`` estimation (doc 22 section 11.4).

``as_of_date`` is the last KR business date included in the estimation
window. A move on day D uses the latest row with ``as_of_date < D``, so the
day's own movement never enters its beta.

* ``OLS_4L``: the rolling window has at least ``OLS_MIN_NOBS`` complete rows.
* ``UNIT_4L``: fewer rows; every beta is 1 (sector 0 when no sector factor).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID

import pandas as pd

from ai_signal.decomposition import (
    OLS_MIN_NOBS,
    OLS_WINDOW,
    LayerInputs,
    build_layer_inputs,
    fit_layer_betas,
)
from ai_signal.market_data.repository import BetaRow, SymbolRef, WeightSnapshot


@dataclass(frozen=True)
class SymbolHistory:
    symbol: SymbolRef
    stock_close: pd.Series
    market_close: pd.Series
    spy_close: pd.Series
    market_weights: list[WeightSnapshot]
    sector_close: pd.Series | None = None
    sector_weights: list[WeightSnapshot] | None = None
    etf_close: pd.Series | None = None


def _anchor(snapshots: list[WeightSnapshot], as_of: date) -> WeightSnapshot | None:
    if not snapshots:
        return None
    before = [s for s in snapshots if s.as_of_date <= as_of]
    return before[-1] if before else snapshots[0]


def symbol_layer_inputs(history: SymbolHistory, as_of: date) -> LayerInputs:
    """Layer inputs for one symbol via ``build_layer_inputs`` (shared by the sink)."""
    calendar = history.market_close.index
    stock = history.stock_close.reindex(calendar)
    market_anchor = _anchor(history.market_weights, as_of)
    sector_anchor = _anchor(history.sector_weights or [], as_of)
    return build_layer_inputs(
        stock,
        history.market_close,
        history.spy_close,
        market_weight=market_anchor.weight if market_anchor else 0.0,
        market_weight_anchor=_midnight(market_anchor.as_of_date)
        if market_anchor
        else None,
        sector_close=history.sector_close,
        sector_weight=sector_anchor.weight if sector_anchor else 0.0,
        sector_weight_anchor=_midnight(sector_anchor.as_of_date)
        if sector_anchor
        else None,
        etf_close=history.etf_close,
    )


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time())


def _finite(value: object) -> float | None:
    if not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def estimate_symbol_betas(
    history: SymbolHistory, as_of_dates: Iterable[date]
) -> list[BetaRow]:
    targets = sorted(set(as_of_dates))
    if not targets or history.market_close.empty or history.stock_close.empty:
        return []
    by_anchor: dict[tuple[date | None, date | None], list[date]] = {}
    for day in targets:
        m = _anchor(history.market_weights, day)
        s = _anchor(history.sector_weights or [], day)
        key = (m.as_of_date if m else None, s.as_of_date if s else None)
        by_anchor.setdefault(key, []).append(day)

    rows: list[BetaRow] = []
    for days in by_anchor.values():
        inputs = symbol_layer_inputs(history, days[-1])
        factors, fit = fit_layer_betas(inputs)
        complete = pd.concat([inputs.r.rename("r"), factors], axis=1).dropna()
        for day in days:
            stamp = _midnight(day)
            if stamp not in history.market_close.index:
                continue
            row = _beta_row(history, inputs, complete, fit.params, fit.r2, stamp)
            if row is not None:
                rows.append(row)
    return sorted(rows, key=lambda r: r.as_of_date)


def _beta_row(
    history: SymbolHistory,
    inputs: LayerInputs,
    complete: pd.DataFrame,
    params: pd.DataFrame,
    r2: pd.Series,
    stamp: datetime,
) -> BetaRow | None:
    n_obs = min(int((pd.DatetimeIndex(complete.index) <= stamp).sum()), OLS_WINDOW)
    if n_obs == 0:
        return None
    has_etf = inputs.etf is not None
    has_sector = inputs.s is not None
    if n_obs >= OLS_MIN_NOBS and stamp in params.index:
        p = params.loc[stamp].to_dict()
        beta_global = _finite(p.get("spy"))
        beta_market = _finite(p.get("m_res"))
        if beta_global is not None and beta_market is not None:
            return BetaRow(
                symbol_entity_id=history.symbol.entity_id,
                as_of_date=stamp.date(),
                method="OLS_4L",
                window_days=OLS_WINDOW,
                n_obs=n_obs,
                beta_global=beta_global,
                beta_global_sector=_finite(p.get("etf_res")) if has_etf else None,
                beta_market=beta_market,
                beta_sector=(_finite(p.get("s_res")) or 0.0) if has_sector else 0.0,
                r2=_finite(r2.loc[stamp]),
            )
    return BetaRow(
        symbol_entity_id=history.symbol.entity_id,
        as_of_date=stamp.date(),
        method="UNIT_4L",
        window_days=OLS_WINDOW,
        n_obs=n_obs,
        beta_global=1.0,
        beta_global_sector=None,
        beta_market=1.0,
        beta_sector=1.0 if has_sector else 0.0,
        r2=None,
    )


def symbol_ids(symbols: Iterable[SymbolRef]) -> dict[str, UUID]:
    return {s.ticker: s.entity_id for s in symbols}
