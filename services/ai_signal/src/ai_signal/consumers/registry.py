"""Consumer registry. Add a processor with one ``ConsumerSpec`` line here."""

from __future__ import annotations

from ai_signal.consumers.frame import ConsumerSpec
from ai_signal.documents.news import NEWS_RAW_TOPIC
from ai_signal.documents.processor import DocumentProcessor
from ai_signal.moves.sink import PRICE_MOVES_TOPIC, MoveSink
from ai_signal.serving import SIGNAL_UPDATES_TOPIC, ServingSink
from ai_signal.signal_requests import SIGNAL_REQUESTS_TOPIC
from ai_signal.signals import SIGNAL_DLQ_TOPIC, SignalWorker

CONSUMERS: tuple[ConsumerSpec, ...] = (
    ConsumerSpec("document_processor", NEWS_RAW_TOPIC, DocumentProcessor()),
    ConsumerSpec("move_sink", PRICE_MOVES_TOPIC, MoveSink()),
    ConsumerSpec(
        "signal_worker",
        SIGNAL_REQUESTS_TOPIC,
        SignalWorker(),
        dlq_topic=SIGNAL_DLQ_TOPIC,
    ),
    ConsumerSpec("serving_sink", SIGNAL_UPDATES_TOPIC, ServingSink()),
)
