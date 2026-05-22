from stream_detection.config import StreamDetectionSettings


def test_default_topics() -> None:
    settings = StreamDetectionSettings(_env_file=None)

    assert settings.source_topic == "stock-ticks"
    assert settings.alert_topic == "stock-alerts"
    assert settings.pattern_topic == "stock-patterns"


def test_window_defaults_are_5min_sliding() -> None:
    settings = StreamDetectionSettings(_env_file=None)

    assert settings.window_size_seconds == 300
    assert settings.window_slide_seconds == 60
