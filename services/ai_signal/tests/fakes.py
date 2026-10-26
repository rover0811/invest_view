from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Self, cast

from confluent_kafka import Message, TopicPartition
from confluent_kafka.schema_registry import Schema, SchemaRegistryClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ClauseElement

from ai_signal.avro import schema_str


def mock_registry(*topics: str) -> SchemaRegistryClient:
    client = SchemaRegistryClient.new_client({"url": "mock://ai-signal-tests"})
    for topic in topics:
        _ = client.register_schema(f"{topic}-value", Schema(schema_str(topic), "AVRO"))
    return client


def compiled_params(statement: ClauseElement) -> dict[str, object]:
    params = statement.compile(dialect=postgresql.dialect()).params
    return {str(name): value for name, value in (params or {}).items()}


def compiled_sql(statement: ClauseElement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


class FakeResult:
    def __init__(self, rows: Sequence[object] = ()) -> None:
        self._rows = list(rows)

    def all(self) -> list[object]:
        return self._rows


@dataclass
class FakeTransaction:
    session: FakeSession

    async def __aenter__(self) -> Self:
        self.session.began += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.session.committed += 1
        else:
            self.session.rolled_back += 1


@dataclass
class FakeSession:
    results: list[FakeResult] = field(default_factory=list)
    statements: list[ClauseElement] = field(default_factory=list)
    began: int = 0
    committed: int = 0
    rolled_back: int = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def execute(self, statement: ClauseElement) -> FakeResult:
        self.statements.append(statement)
        return self.results.pop(0) if self.results else FakeResult()


@dataclass
class FakeSessionFactory:
    sessions: list[FakeSession] = field(default_factory=list)
    results: list[FakeResult] = field(default_factory=list)

    def __call__(self) -> FakeSession:
        session = FakeSession(results=list(self.results))
        self.sessions.append(session)
        return session

    def as_factory(self) -> async_sessionmaker[AsyncSession]:
        return cast(async_sessionmaker[AsyncSession], cast(object, self))


def as_session(session: FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(object, session))


@dataclass
class FakeSerializer:
    def serialize(self, topic: str, value: Mapping[str, object]) -> bytes:
        return f"{topic}:{sorted(value.items())}".encode()

    def record_name(self, topic: str) -> str:
        return f"Record<{topic}>"


def fake_message(
    *,
    topic: str = "news-raw",
    partition: int = 0,
    offset: int = 7,
    key: bytes | None = b"000660",
    value: bytes | None = b"payload",
    headers: list[tuple[str, str | bytes | None]] | None = None,
) -> Message:
    return Message(
        topic=topic,
        partition=partition,
        offset=offset,
        key=key,
        value=value,
        headers=headers,
        timestamp=(1, 1_700_000_000_000),
    )


@dataclass
class FakeConsumer:
    messages: list[Message] = field(default_factory=list)
    commits: list[list[TopicPartition]] = field(default_factory=list)
    subscribed: list[str] = field(default_factory=list)
    closed: bool = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed.extend(topics)

    def poll(self, timeout: float) -> Message | None:
        return self.messages.pop(0) if self.messages else None

    def commit(
        self, *, offsets: list[TopicPartition], asynchronous: Literal[False]
    ) -> list[TopicPartition]:
        assert asynchronous is False
        self.commits.append(offsets)
        return offsets

    def close(self) -> None:
        self.closed = True


@dataclass
class FakeDeserializer:
    fail_on: bytes | None = None

    def deserialize(self, topic: str, payload: bytes) -> dict[str, Any]:
        if payload == self.fail_on:
            raise ValueError("bad avro")
        return {"topic": topic, "payload": payload.decode()}
