from __future__ import annotations

from tick_persistence.config import TickPersistenceConfig
from tick_persistence.db.session import create_engine, create_session_factory


class Container:
    def __init__(self, config: TickPersistenceConfig) -> None:
        self.config = config
        self.engine = create_engine(config.database_url)
        self.session_factory = create_session_factory(self.engine)
