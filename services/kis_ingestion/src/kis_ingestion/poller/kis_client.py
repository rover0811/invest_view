import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

import httpx

from kis_ingestion.token_manager import KISTokenManager

from .rate_limiter import SharedRateLimiter


logger = logging.getLogger(__name__)

RATE_LIMIT_CODE = "EGW00201"
TOKEN_EXPIRED_CODE = "EGW00123"

Sleep = Callable[[float], Awaitable[None]]


class KISApiError(RuntimeError):
    def __init__(self, tr_id: str, status_code: int, msg_cd: str, msg: str) -> None:
        super().__init__(f"{tr_id} failed: http={status_code} msg_cd={msg_cd} msg={msg}")
        self.tr_id: str = tr_id
        self.status_code: int = status_code
        self.msg_cd: str = msg_cd


class KISRestClient:
    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        token_manager: KISTokenManager,
        http_client: httpx.AsyncClient,
        rate_limiter: SharedRateLimiter,
        *,
        max_attempts: int = 4,
        backoff_seconds: float = 1.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._base_url: str = base_url.rstrip("/")
        self._app_key: str = app_key
        self._app_secret: str = app_secret
        self._token_manager: KISTokenManager = token_manager
        self._http_client: httpx.AsyncClient = http_client
        self._rate_limiter: SharedRateLimiter = rate_limiter
        self._max_attempts: int = max_attempts
        self._backoff_seconds: float = backoff_seconds
        self._sleep: Sleep = sleep

    async def get(self, path: str, tr_id: str, params: Mapping[str, str]) -> dict[str, object]:
        last_error: KISApiError | None = None
        for attempt in range(self._max_attempts):
            await self._rate_limiter.acquire()
            token = await self._token_manager.get_token()
            response = await self._http_client.get(
                f"{self._base_url}{path}",
                params=dict(params),
                headers=self._headers(token, tr_id),
            )
            body = _json_object(response)
            msg_cd = str(body.get("msg_cd", ""))
            if response.status_code == 200 and body.get("rt_cd") == "0":
                return body

            last_error = KISApiError(tr_id, response.status_code, msg_cd, str(body.get("msg1", "")))
            if msg_cd == TOKEN_EXPIRED_CODE:
                _ = await self._token_manager.force_refresh()
                continue
            if msg_cd == RATE_LIMIT_CODE or response.status_code >= 500:
                logger.warning("KIS call retry tr_id=%s attempt=%d msg_cd=%s", tr_id, attempt + 1, msg_cd)
                await self._sleep(self._backoff_seconds * (1 << attempt))
                continue
            raise last_error

        assert last_error is not None
        raise last_error

    def _headers(self, token: str, tr_id: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        body: object = cast(object, response.json())
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    return cast(dict[str, object], body)
