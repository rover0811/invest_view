from __future__ import annotations

from typing import Any

import pytest

from ai_signal.documents.processor import event_identity, regenerates_signal
from tests.document_fakes import judgement


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, True),
        ({"target_relevance": "COMPETITOR"}, True),
        ({"direction": -1}, True),
        ({"probability": 0.7}, True),
        ({"probability": 0.6999}, False),
        ({"direction": 0}, False),
        ({"fail_reason": "AD"}, False),
        ({"info_type": "PRICE_RECAP"}, False),
        ({"info_type": "OPINION"}, False),
        ({"info_type": "FLOW"}, False),
        ({"info_type": "OTHER"}, False),
        ({"target_relevance": "SUPPLY_CHAIN"}, False),
        ({"target_relevance": "SECTOR"}, False),
        ({"target_relevance": "UNRELATED"}, False),
        ({"event_type": "SCHEDULE"}, False),
        ({"event_type": "OTHER"}, False),
    ],
)
def test_regeneration_trigger_conditions(
    override: dict[str, Any], expected: bool
) -> None:
    assert regenerates_signal(judgement(**override)) is expected


def test_event_identity_adds_direction_only_for_flow() -> None:
    fact = judgement(
        event_subject="SK하이닉스", event_type="EARNINGS", event_object="a"
    )
    other_object = judgement(
        event_subject="SK하이닉스", event_type="EARNINGS", event_object="b"
    )
    assert event_identity(fact) == ("SK하이닉스", "EARNINGS")
    assert event_identity(fact) == event_identity(other_object)
    buy = judgement(info_type="FLOW", event_subject="외국인", event_type="CAPITAL")
    sell = judgement(
        info_type="FLOW", event_subject="외국인", event_type="CAPITAL", direction=-1
    )
    assert event_identity(buy) == ("외국인", "CAPITAL", 1)
    assert event_identity(buy) != event_identity(sell)
