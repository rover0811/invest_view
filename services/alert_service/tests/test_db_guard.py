import pytest

from alert_service.agent.db_guard import (
    SqlGuardError,
    assert_select_only,
    assert_tables_allowed,
    guard,
)


def test_select_simple_ok():
    assert_select_only("SELECT 1")


def test_select_with_cte_ok():
    assert_select_only("WITH x AS (SELECT 1) SELECT * FROM x")


def test_delete_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("DELETE FROM reference.financial_metrics")


def test_update_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("UPDATE reference.financial_metrics SET x = 1")


def test_insert_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("INSERT INTO reference.financial_metrics VALUES (1)")


def test_drop_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("DROP TABLE reference.financial_metrics")


def test_truncate_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("TRUNCATE reference.financial_metrics")


def test_stacked_query_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("SELECT 1; DROP TABLE x")


def test_stacked_query_select_select_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("SELECT 1; SELECT 2")


def test_trailing_semicolon_ok():
    assert_select_only("SELECT 1;")


def test_tables_allowed_single_ok():
    assert_tables_allowed("SELECT * FROM reference.financial_metrics WHERE 1=1")


def test_tables_allowed_not_in_list_raises():
    with pytest.raises(SqlGuardError):
        assert_tables_allowed("SELECT * FROM alert_service.users")


def test_tables_allowed_join_ok():
    assert_tables_allowed(
        "SELECT * FROM serving.symbol_snapshot s JOIN reference.bronze_market_ticker t ON s.id = t.id"
    )


def test_tables_allowed_all_allowed_tables_ok():
    for table in [
        "reference.financial_metrics",
        "reference.bronze_consensus_report",
        "reference.bronze_market_ticker",
        "reference.bronze_stock_overview",
        "serving.symbol_snapshot",
        "serving.symbol_intraday_5m",
        "serving.symbol_daily_ohlc",
        "serving.symbol_signal_timeline",
    ]:
        assert_tables_allowed(f"SELECT * FROM {table}")


def test_guard_returns_sql_unchanged():
    sql = "SELECT * FROM reference.financial_metrics"
    result = guard(sql)
    assert result == sql


def test_guard_rejects_dml():
    with pytest.raises(SqlGuardError):
        guard("DELETE FROM reference.financial_metrics")


def test_guard_rejects_disallowed_table():
    with pytest.raises(SqlGuardError):
        guard("SELECT * FROM alert_service.users")


def test_select_with_leading_whitespace_ok():
    assert_select_only("   SELECT 1")


def test_alter_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("ALTER TABLE reference.financial_metrics ADD COLUMN x INT")


def test_create_raises():
    with pytest.raises(SqlGuardError):
        assert_select_only("CREATE TABLE foo (id INT)")
