from __future__ import annotations

# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportImplicitStringConcatenation=false

import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from alert_service.agent.financial_items import (
    CASH_FROM_OPERATIONS,
    COMMON_ITEMS_BY_STMT,
    EBIT,
    EBITDA,
    EPS,
    NET_INCOME,
    OPERATING_PROFIT,
    PERIOD_TYPE_DEFAULT,
    REVENUE,
    STMT_TYPES,
    TOTAL_ASSETS,
    TOTAL_EQUITY,
    TOTAL_LIABILITIES,
    UNIT,
    resolve_item_names,
)


def test_constant_string_values():
    assert REVENUE == "매출액(수익)"
    assert OPERATING_PROFIT == "영업이익"
    assert NET_INCOME == "당기순이익"
    assert EBITDA == "*EBITDA"
    assert EBIT == "*EBIT"
    assert EPS == "*주당순이익"
    assert TOTAL_ASSETS == "자산총계"
    assert TOTAL_LIABILITIES == "부채총계"
    assert TOTAL_EQUITY == "자본총계"
    assert CASH_FROM_OPERATIONS == "*영업에서창출된현금흐름"


def test_ebitda_has_leading_asterisk():
    assert EBITDA.startswith("*")
    assert EBITDA == "*EBITDA"


def test_stmt_types():
    assert set(STMT_TYPES) == {"BAL", "INC", "CAS"}


def test_common_items_by_stmt_keys():
    assert set(COMMON_ITEMS_BY_STMT.keys()) == {"INC", "BAL", "CAS"}


def test_common_items_by_stmt_inc():
    inc = COMMON_ITEMS_BY_STMT["INC"]
    assert REVENUE in inc
    assert OPERATING_PROFIT in inc
    assert NET_INCOME in inc
    assert EBITDA in inc
    assert EBIT in inc
    assert EPS in inc


def test_common_items_by_stmt_bal():
    bal = COMMON_ITEMS_BY_STMT["BAL"]
    assert TOTAL_ASSETS in bal
    assert TOTAL_LIABILITIES in bal
    assert TOTAL_EQUITY in bal


def test_common_items_by_stmt_cas():
    cas = COMMON_ITEMS_BY_STMT["CAS"]
    assert CASH_FROM_OPERATIONS in cas


def test_period_type_default():
    assert PERIOD_TYPE_DEFAULT == "Y"


def test_unit():
    assert UNIT == "천원"


def test_resolve_item_names_maps_friendly_inc_names_to_canonical_db_names():
    assert resolve_item_names("INC", ["주당순이익", "EPS", "eps", "매출액", "영업이익"]) == [
        EPS,
        REVENUE,
        OPERATING_PROFIT,
    ]


def test_resolve_item_names_maps_balance_and_cash_flow_aliases():
    assert resolve_item_names("BAL", ["자산", "부채총계", "자본"]) == [
        TOTAL_ASSETS,
        TOTAL_LIABILITIES,
        TOTAL_EQUITY,
    ]
    assert resolve_item_names("CAS", ["영업현금흐름", "영업활동현금흐름"]) == [
        CASH_FROM_OPERATIONS
    ]


def test_resolve_item_names_preserves_none_unknown_and_specific_eps_variants():
    assert resolve_item_names("INC", None) is None
    assert resolve_item_names("INC", ["  ", "알수없는항목"]) == ["알수없는항목"]
    assert resolve_item_names("INC", ["*(지배주주지분)주당순이익"]) == [
        "*(지배주주지분)주당순이익"
    ]


@pytest.mark.qa
def test_constants_exist_in_live_db():
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    db_url = os.environ.get("ALERT_SERVICE_DATABASE_URL")
    if not db_url:
        pytest.skip("no DB: ALERT_SERVICE_DATABASE_URL not set")

    async def _check():
        engine = create_async_engine(db_url)
        results: dict[str, set[str]] = {}
        async with engine.connect() as conn:
            for stmt_type in ("INC", "BAL", "CAS"):
                rows = await conn.execute(
                    text(
                        "SELECT DISTINCT item_name FROM reference.financial_metrics "
                        "WHERE ticker = '005930' AND stmt_type = :stmt AND period_type = 'Y'"
                    ),
                    {"stmt": stmt_type},
                )
                results[stmt_type] = {row[0] for row in rows}
        await engine.dispose()
        return results

    db_items = asyncio.run(_check())

    for stmt_type, expected_items in COMMON_ITEMS_BY_STMT.items():
        present = db_items.get(stmt_type, set())
        for item in expected_items:
            assert item in present, (
                f"{item!r} not found in DB for stmt_type={stmt_type!r}. "
                f"Available: {sorted(present)}"
            )
