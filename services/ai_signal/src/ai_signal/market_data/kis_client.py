from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

RATE_LIMIT_CODE = "EGW00201"
TOKEN_EXPIRED_CODE = "EGW00123"
TOKEN_BUFFER = timedelta(minutes=5)
TOKEN_ISSUE_LIMIT_CODE = "EGW00133"
TOKEN_ISSUE_RETRIES = 2
TOKEN_ISSUE_WAIT_S = 61.0


class KisApiError(RuntimeError):
    pass


class KisGetter(Protocol):
    async def get(
        self, path: str, tr_id: str, params: dict[str, str]
    ) -> dict[str, Any]: ...


class KisRestClient:
    """Sequential KIS REST client with a minimum interval between calls."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        app_key: str,
        app_secret: str,
        min_interval_s: float = 0.5,
        retries: int = 5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._app_key = app_key
        self._app_secret = app_secret
        self._min_interval_s = min_interval_s
        self._retries = retries
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._last_call = 0.0
        self.calls = 0

    async def _ensure_token(self, force: bool = False) -> str:
        now = datetime.now(UTC)
        if (
            not force
            and self._token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return self._token
        response = await self._post_token()
        for _ in range(TOKEN_ISSUE_RETRIES):
            if TOKEN_ISSUE_LIMIT_CODE not in response.text:
                break
            logger.warning("KIS token issue limited; waiting %.0fs", TOKEN_ISSUE_WAIT_S)
            await self._sleep(TOKEN_ISSUE_WAIT_S)
            response = await self._post_token()
        response.raise_for_status()
        body = response.json()
        self._token = str(body["access_token"])
        expires_in = int(body.get("expires_in", 86400))
        self._token_expires_at = now + timedelta(seconds=expires_in) - TOKEN_BUFFER
        return self._token

    async def _post_token(self) -> httpx.Response:
        return await self._http.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            },
        )

    async def _pace(self) -> None:
        wait = self._min_interval_s - (time.monotonic() - self._last_call)
        if wait > 0:
            await self._sleep(wait)
        self._last_call = time.monotonic()

    async def get(
        self, path: str, tr_id: str, params: dict[str, str]
    ) -> dict[str, Any]:
        last_error = ""
        force_token = False
        for attempt in range(self._retries):
            token = await self._ensure_token(force=force_token)
            force_token = False
            await self._pace()
            self.calls += 1
            response = await self._http.get(
                path,
                params=params,
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": self._app_key,
                    "appsecret": self._app_secret,
                    "tr_id": tr_id,
                    "custtype": "P",
                },
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            if response.status_code == 200 and body.get("rt_cd") == "0":
                return body
            msg_cd = body.get("msg_cd")
            last_error = f"http={response.status_code} {msg_cd} {body.get('msg1')}"
            if msg_cd == TOKEN_EXPIRED_CODE:
                force_token = True
                continue
            if response.status_code < 500 and msg_cd != RATE_LIMIT_CODE:
                break
            logger.warning("KIS %s retry %d: %s", tr_id, attempt + 1, last_error)
            await self._sleep(1.0 + attempt)
        raise KisApiError(f"{tr_id} failed: {last_error}")
