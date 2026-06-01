from __future__ import annotations

from event_pattern_persistence.config import EventPatternPersistenceConfig
from event_pattern_persistence.db.session import create_engine, create_session_factory
from event_pattern_persistence.handler import make_pattern_handler
from event_pattern_persistence.kafka.consumer import PatternConsumer
from event_pattern_persistence.repository.pattern_events import PatternEventRepository


class Container:
    def __init__(self, config: EventPatternPersistenceConfig) -> None:
        self.config = config

        self.engine = create_engine(config.database_url)
        self.session_factory = create_session_factory(self.engine)

        self.pattern_repo = PatternEventRepository(self.session_factory)

        self.consumer = PatternConsumer(
            config=config,
            on_message=make_pattern_handler(self.pattern_repo),
        )
