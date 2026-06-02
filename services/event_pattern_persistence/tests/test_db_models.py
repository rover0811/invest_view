from event_pattern_persistence.db.models import SCHEMA, Base, PatternEvent


def test_schema_constant_and_table_schema():
    assert SCHEMA == "gold"
    assert PatternEvent.__table__.schema == "gold"
    assert PatternEvent.__table__.fullname == "gold.pattern_events"


def test_metadata_has_single_gold_table():
    names = {t.fullname for t in Base.metadata.tables.values()}
    assert names == {"gold.pattern_events"}


def test_pattern_event_pk_is_uuid():
    pk_cols = [c.name for c in PatternEvent.__table__.columns if c.primary_key]
    assert pk_cols == ["pattern_event_id"]
    assert "UUID" in str(PatternEvent.__table__.columns["pattern_event_id"].type)


def test_pattern_event_column_types():
    cols = PatternEvent.__table__.columns
    assert "JSONB" in str(cols["trigger_values"].type)
    assert cols["triggered_at"].type.timezone is True
    assert cols["window_start"].type.timezone is True
    assert cols["window_end"].type.timezone is True
    assert cols["received_at"].type.timezone is True


def test_pattern_event_nullability():
    cols = PatternEvent.__table__.columns
    not_null = {c.name for c in cols if not c.nullable}
    assert {
        "pattern_event_id",
        "symbol",
        "pattern_type",
        "triggered_at",
        "trigger_values",
        "received_at",
    } <= not_null
    assert cols["market"].nullable is True
    assert cols["strategy_name"].nullable is True
    assert cols["source_tick_event_id"].nullable is True
    assert cols["window_start"].nullable is True
    assert cols["window_end"].nullable is True


def test_symbol_triggered_index_exists():
    idx_names = {i.name for i in PatternEvent.__table__.indexes}
    assert "ix_pattern_events_symbol_triggered" in idx_names
