"""Plain factory DI container for alert_service.

Matches the 12번 kis_ingestion container.py pattern (no external DI framework).
Builds the object graph in __init__ and exposes everything via attributes so
the FastAPI app factory can attach them to app.state.
"""
from __future__ import annotations

# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportExplicitAny=false, reportAny=false

from typing import Any

from alert_service.agent.market_analyst import build_market_analyst_agent
from alert_service.agent.model import build_gemini_model
from alert_service.auth.jwt import JWTVerifier
from alert_service.config import AlertServiceConfig
from alert_service.db.session import create_engine, create_session_factory
from alert_service.kafka.consumer import AlertConsumer
from alert_service.repository.alert_events import AlertEventRepository
from alert_service.repository.notifications import NotificationRepository
from alert_service.repository.users import UserRepository
from alert_service.repository.watchlist import WatchlistRepository
from alert_service.ws.pusher import AlertPusher
from alert_service.ws.registry import ConnectionRegistry


class Container:
    def __init__(self, config: AlertServiceConfig) -> None:
        self.config = config

        self.engine = create_engine(config.database_url)
        self.session_factory = create_session_factory(self.engine)

        self.jwt_verifier = JWTVerifier(
            secret=config.jwt_secret,
            algorithm=config.jwt_algorithm,
            user_id_claim=config.jwt_user_id_claim,
        )

        self.connection_registry = ConnectionRegistry()

        self.user_repo = UserRepository(self.session_factory)
        self.watchlist_repo = WatchlistRepository(self.session_factory)
        self.alert_event_repo = AlertEventRepository(self.session_factory)
        self.notification_repo = NotificationRepository(self.session_factory)

        self.alert_pusher = AlertPusher(
            alert_repo=self.alert_event_repo,
            watchlist_repo=self.watchlist_repo,
            notification_repo=self.notification_repo,
            registry=self.connection_registry,
            fanout_fail_after_alert=config.fanout_fail_after_alert,
        )

        self.alert_consumer = AlertConsumer(
            config=config,
            on_message=self.alert_pusher.handle,
        )

    def build_agent_model(self) -> Any:
        return build_gemini_model(self.config)

    def build_market_analyst(
        self,
        *,
        model: Any | None = None,
        messages: Any | None = None,
        conversation_manager: Any | None = None,
    ) -> Any:
        return build_market_analyst_agent(
            self.config,
            model=model,
            messages=messages,
            conversation_manager=conversation_manager,
        )
