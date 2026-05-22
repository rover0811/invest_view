from decimal import Decimal

from stream_detection.rules import MovingAverageCross, SurgePlungeThresholds


def test_surge_threshold() -> None:
    rule = SurgePlungeThresholds()

    assert rule.classify(Decimal("0.06")) == "SURGE"
    assert rule.classify(Decimal("0.05")) == "SURGE"
    assert rule.classify(Decimal("0.04")) is None


def test_plunge_threshold() -> None:
    rule = SurgePlungeThresholds()

    assert rule.classify(Decimal("-0.06")) == "PLUNGE"
    assert rule.classify(Decimal("-0.05")) == "PLUNGE"
    assert rule.classify(Decimal("-0.04")) is None


def test_golden_cross() -> None:
    rule = MovingAverageCross()

    result = rule.detect(
        prev_short=Decimal("100"),
        prev_long=Decimal("101"),
        cur_short=Decimal("102"),
        cur_long=Decimal("101"),
    )
    assert result == "GOLDEN_CROSS"


def test_dead_cross() -> None:
    rule = MovingAverageCross()

    result = rule.detect(
        prev_short=Decimal("102"),
        prev_long=Decimal("101"),
        cur_short=Decimal("100"),
        cur_long=Decimal("101"),
    )
    assert result == "DEAD_CROSS"


def test_no_cross_when_relation_unchanged() -> None:
    rule = MovingAverageCross()

    result = rule.detect(
        prev_short=Decimal("103"),
        prev_long=Decimal("101"),
        cur_short=Decimal("102"),
        cur_long=Decimal("101"),
    )
    assert result is None
