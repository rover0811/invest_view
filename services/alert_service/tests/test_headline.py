from __future__ import annotations

from decimal import Decimal

import pytest

from alert_service.headline import assemble_headline, direction_particle


@pytest.mark.parametrize(
    ("label", "particle"),
    [
        ("신고가 경신", "으로"),
        ("외국인 매도세", "로"),
        ("업황 상승", "으로"),
        ("호실적", "으로"),
        ("수주 기대감", "으로"),
        ("AI 수익성 우려", "로"),
        ("특허 소송 판결", "로"),
        ("HBM4", "로"),
        ("2분기 실적 3", "으로"),
        ("목표가 10", "으로"),
        ("지분 7", "로"),
        ("HBM", "로"),
        ("AI 수익성 우려 ", "로"),
        ("", "로"),
    ],
)
def test_direction_particle(label: str, particle: str) -> None:
    assert direction_particle(label) == particle


def test_assemble_headline_up_down_and_flat() -> None:
    assert (
        assemble_headline("신고가 경신", "SK하이닉스", Decimal("4.25"))
        == "신고가 경신으로 SK하이닉스 4.3% 상승"
    )
    assert (
        assemble_headline("외국인 매도세", "삼성전자", Decimal("-2.04"))
        == "외국인 매도세로 삼성전자 2.0% 하락"
    )
    assert (
        assemble_headline("외국인 매도세", "삼성전자", Decimal("-0.04"))
        == "외국인 매도세에도 삼성전자 보합"
    )
    assert (
        assemble_headline("외국인 매도세", "삼성전자", Decimal("0"))
        == "외국인 매도세에도 삼성전자 보합"
    )
