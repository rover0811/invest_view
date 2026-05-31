"""FastAPI app factory.

Container is provided at create_app time; its objects are attached to
``app.state`` so route dependencies and the websocket handler can reach them.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from alert_service.api.heartbeat import heartbeat_loop
from alert_service.api.routes import health, notifications, watchlist, ws


logger = logging.getLogger(__name__)


def create_app(container: Any) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.alert_consumer.start()
        heartbeat_task = asyncio.create_task(
            heartbeat_loop(container.connection_registry), name="heartbeat-loop"
        )
        try:
            yield
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

            try:
                await asyncio.to_thread(container.alert_consumer.stop)
            except Exception as exc:
                logger.warning("consumer.stop raised: %s", exc)
            await container.engine.dispose()

    app = FastAPI(title="alert-service", lifespan=lifespan)

    app.state.jwt_verifier = container.jwt_verifier
    app.state.connection_registry = container.connection_registry
    app.state.watchlist_repo = container.watchlist_repo
    app.state.notification_repo = container.notification_repo
    app.state.alert_consumer = container.alert_consumer
    app.state.engine = container.engine

    app.add_middleware(
        CORSMiddleware,
        allow_origins=container.config.allow_origins or [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(watchlist.router)
    app.include_router(notifications.router)
    app.include_router(ws.router)
    return app
