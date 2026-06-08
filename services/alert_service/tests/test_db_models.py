from alert_service.db.models import AlertEvent, Base, NotificationEvent, WatchlistItem


def test_metadata_contains_all_tables():
    names = {t.fullname for t in Base.metadata.tables.values()}
    assert "alert_service.users" in names
    assert "alert_service.watchlist_items" in names
    assert "alert_service.alert_events" in names
    assert "alert_service.notification_events" in names
    # agent chat tables (0002_agent_chat) share the same Base.metadata
    assert "agent.chat_sessions" in names
    assert "agent.chat_messages" in names


def test_watchlist_composite_pk():
    pk_cols = list(WatchlistItem.__table__.primary_key.columns.keys())
    assert pk_cols == ["user_id", "symbol"]


def test_alert_events_check_constraints():
    names = {c.name for c in AlertEvent.__table__.constraints if c.name and "check" in c.name}
    assert "alert_events_market_check" in names
    assert "alert_events_alert_type_check" in names
    assert "alert_events_severity_check" in names


def test_notification_events_constraints():
    names = {c.name for c in NotificationEvent.__table__.constraints if c.name}
    assert "notification_events_status_check" in names
    assert "notification_events_failure_reason_check" in names
    assert "notification_events_user_alert_uq" in names


def test_alert_event_uses_observation_columns():
    cols = set(AlertEvent.__table__.columns.keys())
    assert "observation_start_at" in cols
    assert "observation_end_at" in cols
    assert "window_start" not in cols
    assert "window_end" not in cols
