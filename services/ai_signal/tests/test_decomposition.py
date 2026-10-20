from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from ai_signal.decomposition import (
    LayerInputs,
    align_us_to_kr,
    build_layer_inputs,
    decompose_ols_4l,
    decompose_unit_4l,
    excluded_index_return,
    rolling_ols,
    self_weight_history,
)


def _synthetic(n: int = 260, seed: int = 7) -> LayerInputs:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", periods=n)
    spy = pd.Series(rng.normal(0, 0.01, n), index=idx)
    m = 0.3 * spy + pd.Series(rng.normal(0, 0.01, n), index=idx)
    s = 0.2 * spy + 0.8 * m + pd.Series(rng.normal(0, 0.01, n), index=idx)
    r = 0.5 * spy + 1.1 * m + 0.6 * s + pd.Series(rng.normal(0, 0.005, n), index=idx)
    return LayerInputs(r=r, spy=spy, m=m, s=s)


def test_rolling_ols_matches_statsmodels_expanding_rolling() -> None:
    rolling = pytest.importorskip("statsmodels.regression.rolling")
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2025-01-02", periods=200)
    x = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)}, index=idx)
    values = 0.4 + 1.5 * x["a"].to_numpy() - 0.7 * x["b"].to_numpy()
    values = values + rng.normal(scale=0.1, size=200)
    values[[10, 50]] = np.nan
    y = pd.Series(values, index=idx)

    ours = rolling_ols(y, x)
    joined = pd.concat([y.rename("y"), x], axis=1).dropna()
    exog = joined[["a", "b"]].assign(const=1.0)[["const", "a", "b"]]
    ref = (
        rolling.RollingOLS(joined["y"], exog, window=120, min_nobs=60, expanding=True)
        .fit()
        .params
    )
    common = ref.dropna().index
    np.testing.assert_allclose(
        ours.params.loc[common, ["const", "a", "b"]].to_numpy(),
        ref.loc[common].to_numpy(),
        rtol=1e-8,
        atol=1e-10,
    )
    assert ours.params.loc[joined.index[58]].isna().all()
    assert ours.n_obs.loc[joined.index[59]] == 60
    assert ours.n_obs.iloc[-1] == 120


def test_shift_moves_to_next_complete_row() -> None:
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2025-01-02", periods=100)
    x = pd.DataFrame({"a": rng.normal(size=100)}, index=idx)
    values = 2 * x["a"].to_numpy() + rng.normal(scale=0.01, size=100)
    values[80] = np.nan
    y = pd.Series(values, index=idx)
    plain = rolling_ols(y, x).params
    lagged = rolling_ols(y, x, shift=1).params
    assert lagged.loc[idx[81], "a"] == plain.loc[idx[79], "a"]
    assert np.isnan(lagged.loc[idx[80], "a"])


def test_align_us_to_kr_uses_previous_us_session() -> None:
    us = pd.Series(
        [0.01, 0.02, 0.03],
        index=pd.to_datetime(["2026-10-14", "2026-10-15", "2026-10-16"]),
    )
    kr = pd.DatetimeIndex(pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"]))
    aligned = align_us_to_kr(us, kr, 1)
    assert aligned.tolist() == [0.01, 0.02, 0.03]


def test_self_excluded_return_and_weight_history() -> None:
    idx = pd.bdate_range("2026-10-01", periods=3)
    stock = pd.Series([100.0, 110.0, 121.0], index=idx)
    index = pd.Series([1000.0, 1000.0, 1000.0], index=idx)
    w = self_weight_history(stock, index, 0.5)
    assert w.iloc[1] == pytest.approx(0.5 * 100 / 121)
    assert w.iloc[2] == pytest.approx(0.5 * 110 / 121)
    ex = excluded_index_return(pd.Series([0.02]), pd.Series([0.1]), pd.Series([0.2]))
    assert ex.iloc[0] == pytest.approx((0.02 - 0.02) / 0.8)


def test_ols_4l_contributions_add_up_and_recover_betas() -> None:
    inputs = _synthetic()
    out, betas = decompose_ols_4l(inputs)
    total = out[["global", "market", "sector", "idio"]].sum(axis=1)
    np.testing.assert_allclose(total.to_numpy(), out["r"].to_numpy())
    assert set(out["dominant"]) <= {"global", "market", "sector", "idio"}
    last = betas.dropna().iloc[-1]
    assert last["s_res"] == pytest.approx(0.6, abs=0.15)
    assert (betas["s_res"].dropna() >= 0).all()


def test_sector_beta_is_floored_at_zero() -> None:
    base = _synthetic(seed=3)
    assert base.s is not None
    rng = np.random.default_rng(4)
    noise = pd.Series(rng.normal(0, 0.005, len(base.r)), index=base.r.index)
    r = base.spy + base.m - 0.8 * base.s + noise
    _, betas = decompose_ols_4l(LayerInputs(r=r, spy=base.spy, m=base.m, s=base.s))
    assert betas["s_res"].dropna().min() == 0.0


def test_unit_4l_is_sequential() -> None:
    inputs = _synthetic(n=30)
    assert inputs.s is not None
    out = decompose_unit_4l(inputs)
    row = out.iloc[-1]
    day = out.index[-1]
    assert row["global"] == pytest.approx(inputs.spy[day])
    assert row["market"] == pytest.approx(inputs.m[day] - inputs.spy[day])
    assert row["sector"] == pytest.approx(inputs.s[day] - inputs.m[day])
    assert row["idio"] == pytest.approx(inputs.r[day] - inputs.s[day])


def test_unit_4l_uses_sector_etf_as_global_when_proxied() -> None:
    base = _synthetic(n=30)
    etf = base.spy * 1.5
    inputs = LayerInputs(r=base.r, spy=base.spy, m=base.m, etf=etf, s=base.s)
    out = decompose_unit_4l(inputs)
    day = out.index[-1]
    assert out.loc[day, "global"] == pytest.approx(etf[day])
    assert out.loc[day, "market"] == pytest.approx(base.m[day] - etf[day])


def test_build_layer_inputs_drops_dominant_sector_weight() -> None:
    idx = pd.bdate_range("2026-01-02", periods=5)
    close = pd.Series([100.0, 101, 102, 103, 104], index=idx)
    us = pd.Series([1.0, 1.01, 1.02, 1.03, 1.04], index=idx - pd.Timedelta(days=1))
    built = build_layer_inputs(
        close,
        close * 10,
        us,
        market_weight=0.1,
        sector_close=close,
        sector_weight=0.97,
    )
    assert built.s is None
    assert built.m.notna().sum() == 4


def _experiment_analysis() -> ModuleType:
    pytest.importorskip("statsmodels")
    pytest.importorskip("httpx")
    root = Path(__file__).resolve().parents[3] / "experiments" / "return_decomposition"
    if not (root / "analysis.py").is_file():
        pytest.skip("experiment code not available")
    spec = importlib.util.spec_from_file_location("rd_analysis", root / "analysis.py")
    if spec is None or spec.loader is None:
        pytest.skip("experiment analysis cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ols_4l_matches_experiment_implementation() -> None:
    analysis = _experiment_analysis()
    rng = np.random.default_rng(5)
    base = _synthetic(n=300, seed=9)
    assert base.s is not None
    etf = 0.6 * base.spy + pd.Series(rng.normal(0, 0.01, 300), index=base.r.index)
    inputs = LayerInputs(r=base.r, spy=base.spy, m=base.m, etf=etf, s=base.s)

    ours, our_betas = decompose_ols_4l(inputs)
    theirs, their_betas = analysis.decompose_ols_4l(
        base.r, base.spy, etf.rename("etf"), base.m, base.s
    )
    assert (their_betas["s_res"].dropna() > 0).all()
    common = ours.index.intersection(theirs.index)
    assert len(common) > 150
    for layer in ("global", "market", "sector", "idio"):
        np.testing.assert_allclose(
            ours.loc[common, layer].to_numpy(),
            theirs.loc[common, layer].to_numpy(),
            rtol=1e-6,
            atol=1e-10,
        )
    for column in ("spy", "etf_res", "m_res", "s_res"):
        np.testing.assert_allclose(
            our_betas.loc[common, column].to_numpy(),
            their_betas.loc[common, column].to_numpy(),
            rtol=1e-6,
            atol=1e-10,
        )
