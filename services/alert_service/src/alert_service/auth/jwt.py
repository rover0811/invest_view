"""JWT verification for alert_service.

v1 only verifies tokens. Issuance (signup/login) is a separate plan.
"""
from __future__ import annotations

import jwt
from jwt import InvalidTokenError, ExpiredSignatureError


class JWTVerificationError(Exception):
    """Raised when JWT verification fails (signature, expiry, claims)."""


class JWTVerifier:
    """Verifies HS256 JWT tokens and extracts user_id from claim."""

    def __init__(self, secret: str, algorithm: str, user_id_claim: str) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._user_id_claim = user_id_claim

    def verify(self, token: str) -> str:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
        except ExpiredSignatureError as exc:
            raise JWTVerificationError(f"token expired: {exc}") from exc
        except InvalidTokenError as exc:
            raise JWTVerificationError(f"invalid token: {exc}") from exc

        user_id = payload.get(self._user_id_claim)
        if not user_id:
            raise JWTVerificationError(f"missing claim {self._user_id_claim}")
        return str(user_id)
