from __future__ import annotations

import asyncio

import pytest
from confluent_kafka import Message

from ai_signal.consumers import (
    ConsumedMessage,
    ConsumerLoop,
    ConsumerSpec,
    HandlerContext,
)
from ai_signal.consumers.frame import ConsumerHandlerError
from ai_signal.consumers.registry import CONSUMERS
from tests.fakes import (
    FakeConsumer,
    FakeDeserializer,
    FakeSerializer,
    FakeSessionFactory,
    compiled_params,
    fake_message,
)


def _loop(
    spec: ConsumerSpec,
    consumer: FakeConsumer,
    factory: FakeSessionFactory,
    deserializer: FakeDeserializer | None = None,
) -> ConsumerLoop:
    return ConsumerLoop(
        spec,
        consumer=consumer,
        deserializer=deserializer or FakeDeserializer(),
        serializer=FakeSerializer(),
        session_factory=factory.as_factory(),
        poll_timeout=0.01,
        max_attempts=3,
        retry_backoff_seconds=0,
    )


def test_handler_writes_data_and_outbox_in_one_transaction_then_commits_offset() -> (
    None
):
    seen: list[ConsumedMessage] = []

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        seen.append(message)
        _ = await context.outbox.write(
            topic="signal-requests",
            key=message.key or "",
            value={"symbol": message.key},
        )

    spec = ConsumerSpec(name="document_processor", topic="news-raw", handler=handler)
    consumer = FakeConsumer()
    factory = FakeSessionFactory()
    message = fake_message(headers=[("trace", b"abc"), ("empty", None)])

    asyncio.run(_loop(spec, consumer, factory).process(message))

    (consumed,) = seen
    assert consumed.key == "000660"
    assert consumed.value == {"topic": "news-raw", "payload": "payload"}
    assert consumed.headers == {"trace": "abc", "empty": ""}
    assert consumed.timestamp_ms == 1_700_000_000_000
    (session,) = factory.sessions
    assert session.committed == 1
    assert compiled_params(session.statements[0])["aggregatetype"] == "signal-requests"
    (offsets,) = consumer.commits
    assert [(tp.topic, tp.partition, tp.offset) for tp in offsets] == [
        ("news-raw", 0, 8)
    ]


def test_failing_handler_is_retried_then_raises_without_offset_commit() -> None:
    attempts = 0

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        nonlocal attempts
        attempts += 1
        _ = await context.outbox.write(topic="signal-requests", key="k", value={})
        raise RuntimeError("db constraint")

    spec = ConsumerSpec(name="move_sink", topic="price-moves", handler=handler)
    consumer = FakeConsumer()
    factory = FakeSessionFactory()

    with pytest.raises(ConsumerHandlerError):
        asyncio.run(_loop(spec, consumer, factory).process(fake_message()))

    assert attempts == 3
    assert [session.rolled_back for session in factory.sessions] == [1, 1, 1]
    assert all(session.committed == 0 for session in factory.sessions)
    assert consumer.commits == []


def test_handler_recovers_on_retry() -> None:
    attempts = 0

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient")

    spec = ConsumerSpec(name="p", topic="news-raw", handler=handler)
    consumer = FakeConsumer()
    factory = FakeSessionFactory()
    asyncio.run(_loop(spec, consumer, factory).process(fake_message()))
    assert [s.committed for s in factory.sessions] == [0, 1]
    assert len(consumer.commits) == 1


@pytest.mark.parametrize(
    "message",
    [fake_message(value=b"corrupt"), fake_message(value=None)],
)
def test_undecodable_or_empty_messages_are_skipped_and_committed(
    message: Message,
) -> None:
    called = False

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        nonlocal called
        called = True

    spec = ConsumerSpec(name="p", topic="news-raw", handler=handler)
    consumer = FakeConsumer()
    factory = FakeSessionFactory()
    loop = _loop(spec, consumer, factory, FakeDeserializer(fail_on=b"corrupt"))

    asyncio.run(loop.process(message))

    assert called is False
    assert factory.sessions == []
    assert len(consumer.commits) == 1


def test_run_subscribes_processes_and_closes() -> None:
    handled: list[int] = []

    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        handled.append(message.offset)

    spec = ConsumerSpec(name="p", topic="news-raw", handler=handler)
    consumer = FakeConsumer(messages=[fake_message(offset=1), fake_message(offset=2)])
    loop = _loop(spec, consumer, FakeSessionFactory())

    async def run_then_stop() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(loop.run(stop))
        while len(handled) < 2:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run_then_stop())
    assert consumer.subscribed == ["news-raw"]
    assert handled == [1, 2]
    assert consumer.closed is True


def test_group_id_defaults_to_service_prefix() -> None:
    async def handler(message: ConsumedMessage, context: HandlerContext) -> None:
        return None

    assert (
        ConsumerSpec("move_sink", "price-moves", handler).group_id
        == "ai-signal-move_sink"
    )
    assert ConsumerSpec("x", "t", handler, group="custom").group_id == "custom"
    names = [spec.name for spec in CONSUMERS]
    assert len(names) == len(set(names))
    assert all(spec.group_id == f"ai-signal-{spec.name}" for spec in CONSUMERS)
    assert ("document_processor", "news-raw") in [
        (spec.name, spec.topic) for spec in CONSUMERS
    ]
