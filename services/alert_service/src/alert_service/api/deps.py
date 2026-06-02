"""FastAPI dependencies: JWT auth, etc.

DB session and registry are injected via app.state from create_app's container.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Header, HTTPException, Query, Request, status

from alert_service.auth.jwt import JWTVerificationError, JWTVerifier


def _extract_token(authorization: str | None, token_query: str | None) -> str:
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value
        return authorization
    if token_query:
        return token_query
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")


def get_jwt_verifier(request: Request) -> JWTVerifier:
    return request.app.state.jwt_verifier


def current_user_id(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    token: Annotated[str | None, Query()] = None,
) -> uuid.UUID:
    """Extract and verify JWT, return user_id as UUID."""
    raw_token = _extract_token(authorization, token)
    verifier: JWTVerifier = request.app.state.jwt_verifier
    try:
        user_id_str = verifier.verify(raw_token)
    except JWTVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    try:
        return uuid.UUID(user_id_str)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid user_id in token") from exc
