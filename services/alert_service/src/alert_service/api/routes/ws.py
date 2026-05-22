from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from alert_service.auth.jwt import JWTVerificationError


logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
    verifier = websocket.app.state.jwt_verifier
    registry = websocket.app.state.connection_registry

    try:
        user_id = verifier.verify(token)
    except JWTVerificationError as exc:
        logger.warning("ws auth failed: %s", exc)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    registry.add(user_id, websocket)
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                return
    finally:
        registry.remove(user_id, websocket)
