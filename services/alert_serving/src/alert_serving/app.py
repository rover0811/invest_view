from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import AlertServingSettings
from .connection_manager import ConnectionManager


def create_app(settings: AlertServingSettings | None = None) -> FastAPI:
    settings = settings or AlertServingSettings()
    app = FastAPI(title="alert-serving", version="0.1.0")
    manager = ConnectionManager()

    app.state.settings = settings
    app.state.manager = manager

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "alert-serving"}

    @app.get("/alerts")
    def list_alerts(symbol: str | None = None, limit: int = 20) -> dict[str, list[dict]]:
        del symbol, limit
        return {"items": []}

    @app.get("/patterns")
    def list_patterns(symbol: str | None = None, limit: int = 20) -> dict[str, list[dict]]:
        del symbol, limit
        return {"items": []}

    @app.websocket("/ws/alerts/{user_id}")
    async def alerts_ws(websocket: WebSocket, user_id: str) -> None:
        await manager.connect(user_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(user_id, websocket)

    return app


app = create_app()
