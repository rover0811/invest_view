from tick_persistence.db.models import (
    SCHEMA_BRONZE,
    SCHEMA_SERVING,
    SCHEMA_SILVER,
    Base,
    Symbol5mMetrics,
    SymbolSnapshot,
    TickHistory,
    TickQuarantine,
)

KIS_FIELDS = {
    "source_tr_id", "market", "received_at", "symbol", "trade_time", "price", "change_sign",
    "change", "change_rate", "vwap", "open", "high", "low", "ask_price_1", "bid_price_1",
    "trade_volume", "cumulative_volume", "cumulative_amount", "sell_count", "buy_count",
    "net_buy_count", "trade_strength", "total_sell_volume", "total_buy_volume", "trade_type",
    "buy_ratio", "prev_day_volume_rate", "open_time", "open_vs_sign", "open_vs_price",
    "high_time", "high_vs_sign", "high_vs_price", "low_time", "low_vs_sign", "low_vs_price",
    "business_date", "market_session_code", "trading_halted", "ask_remain_1", "bid_remain_1",
    "total_ask_remain", "total_bid_remain", "volume_turnover", "prev_same_hour_volume",
    "prev_same_hour_volume_rate", "hour_class_code", "market_termination_code", "vi_trigger_price",
}


def _types(model):
    return {c.name: str(c.type) for c in model.__table__.columns}


def _unique_column_sets(model):
    return {
        tuple(col.name for col in c.columns)
        for c in model.__table__.constraints
        if type(c).__name__ == "UniqueConstraint"
    }


def test_kis_field_set_is_49():
    assert len(KIS_FIELDS) == 49


def test_metadata_has_layered_and_quarantine_tables():
    names = {t.fullname for t in Base.metadata.tables.values()}
    assert names == {
        "bronze.tick_history",
        "bronze.tick_quarantine",
        "silver.symbol_5m_metrics",
        "serving.symbol_snapshot",
    }


def test_tick_quarantine_schema_columns_and_lineage_unique():
    assert TickQuarantine.__table__.schema == "bronze"
    cols = TickQuarantine.__table__.columns
    assert set(cols.keys()) == {
        "id",
        "raw_payload",
        "kafka_topic",
        "kafka_partition",
        "kafka_offset",
        "reason",
        "quarantined_at",
    }
    assert "JSONB" in str(cols["raw_payload"].type)
    assert cols["raw_payload"].nullable is False
    assert cols["reason"].nullable is False
    assert ("kafka_topic", "kafka_partition", "kafka_offset") in _unique_column_sets(TickQuarantine)


def test_schema_constants_and_table_schemas():
    assert (SCHEMA_BRONZE, SCHEMA_SILVER, SCHEMA_SERVING) == ("bronze", "silver", "serving")
    assert TickHistory.__table__.schema == "bronze"
    assert Symbol5mMetrics.__table__.schema == "silver"
    assert SymbolSnapshot.__table__.schema == "serving"


def test_tick_history_has_all_kis_fields_plus_meta():
    cols = set(TickHistory.__table__.columns.keys())
    assert KIS_FIELDS <= cols, f"missing KIS fields: {KIS_FIELDS - cols}"
    for meta in ("tick_id", "persisted_at", "kafka_topic", "kafka_partition", "kafka_offset", "tick_dedupe_key"):
        assert meta in cols
    for contract_col in ("event_id", "event_ts"):
        assert contract_col in cols, f"missing event-time contract column (migration 0006): {contract_col}"
    assert len(cols) == 57


def test_tick_history_column_types():
    cols = _types(TickHistory)
    assert cols["vwap"].startswith("NUMERIC(20")
    assert cols["change_rate"].startswith("NUMERIC(18")
    assert "BIGINT" in cols["trade_volume"]
    assert "INTEGER" in cols["price"]
    assert "BIGINT" in cols["kafka_offset"]
    assert "INTEGER" in cols["kafka_partition"]


def test_tick_history_dedupe_unique_and_nullability():
    assert ("tick_dedupe_key",) in _unique_column_sets(TickHistory)
    not_null = {c.name for c in TickHistory.__table__.columns if not c.nullable}
    assert "symbol" in not_null
    assert "tick_dedupe_key" in not_null
    assert TickHistory.__table__.columns["price"].nullable is True
    assert TickHistory.__table__.columns["vwap"].nullable is True


def test_symbol_5m_metrics_is_final_and_unique():
    cols = Symbol5mMetrics.__table__.columns
    assert "is_final" in cols
    assert "BOOLEAN" in str(cols["is_final"].type)
    assert cols["is_final"].nullable is False
    assert ("symbol", "bucket_start") in _unique_column_sets(Symbol5mMetrics)
    types = _types(Symbol5mMetrics)
    assert "INTEGER" in types["open"]
    assert "BIGINT" in types["volume"]
    assert types["vwap"].startswith("NUMERIC(20")


def test_symbol_snapshot_pk_and_types():
    pk_cols = [c.name for c in SymbolSnapshot.__table__.columns if c.primary_key]
    assert pk_cols == ["symbol"]
    types = _types(SymbolSnapshot)
    assert "INTEGER" in types["last_price"]
    assert "BIGINT" in types["cumulative_volume"]
    assert types["change_rate"].startswith("NUMERIC(18")
