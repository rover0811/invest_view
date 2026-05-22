import time
import jwt
import pytest
from alert_service.auth.jwt import JWTVerifier, JWTVerificationError


SECRET = "test-secret-32chars-min-for-hs256"


def make_token(payload: dict, secret: str = SECRET, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=algorithm)


def test_verify_returns_user_id_on_valid_token():
    token = make_token({"sub": "user-123", "exp": time.time() + 3600})
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    assert v.verify(token) == "user-123"


def test_verify_raises_on_expired_token():
    token = make_token({"sub": "user-123", "exp": 1})  # epoch 1
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    with pytest.raises(JWTVerificationError, match="expired"):
        v.verify(token)


def test_verify_raises_on_bad_signature():
    token = jwt.encode({"sub": "user-123"}, "wrong-secret-also-32chars-xxxxxxxx", algorithm="HS256")
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    with pytest.raises(JWTVerificationError):
        v.verify(token)


def test_verify_raises_on_missing_sub_claim():
    token = make_token({"foo": "bar"})  # no sub
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    with pytest.raises(JWTVerificationError, match="missing claim"):
        v.verify(token)


def test_verify_uses_custom_claim():
    token = make_token({"user_id": "abc-456"})
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="user_id")
    assert v.verify(token) == "abc-456"


def test_verify_raises_on_malformed_token():
    v = JWTVerifier(secret=SECRET, algorithm="HS256", user_id_claim="sub")
    with pytest.raises(JWTVerificationError):
        v.verify("not-a-jwt")
