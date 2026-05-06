import asyncio
from datetime import datetime, timedelta

import httpx

class KISApprovalKeyManager:
    """
    Manages KIS WebSocket approval keys.
    Handles issuance, caching, and automatic refresh before expiry.
    """

    _approval_url: str
    _app_key: str
    _app_secret: str
    _http_client: httpx.AsyncClient
    _key: str | None
    _expires_at: datetime | None
    _lock: asyncio.Lock
    _buffer: int

    def __init__(
        self,
        approval_url: str,
        app_key: str,
        app_secret: str,
        http_client: httpx.AsyncClient,
    ):
        self._approval_url = approval_url
        self._app_key = app_key
        self._app_secret = app_secret
        self._http_client = http_client

        self._key = None
        self._expires_at = None
        self._lock = asyncio.Lock()
        self._buffer = 300

    async def get_approval_key(self) -> str:
        """
        Returns a valid approval key.
        Uses cached key if valid, otherwise refreshes.
        """
        if self._is_valid() and self._key is not None:
            return self._key

        async with self._lock:
            if self._is_valid() and self._key is not None:
                return self._key
            await self._refresh()

        if self._key is None:
            raise RuntimeError("Failed to fetch approval key")
        return self._key

    async def force_refresh(self) -> str:
        """
        Forces an approval key refresh regardless of current expiry status.
        """
        async with self._lock:
            await self._refresh()

        if self._key is None:
            raise RuntimeError("Failed to fetch approval key")
        return self._key

    def _is_valid(self) -> bool:
        """Checks if the cached key exists and is not expired (considering buffer)."""
        if not self._key or not self._expires_at:
            return False
        return datetime.now() < self._expires_at

    async def _refresh(self) -> None:
        """Performs the actual approval key refresh via KIS API."""
        # CRITICAL: The field name is secretkey, NOT appsecret!
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._app_secret,
        }

        response = await self._http_client.post(self._approval_url, json=payload)
        _ = response.raise_for_status()

        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid response from KIS API")

        approval_key = data.get("approval_key")
        if not approval_key:
            raise RuntimeError("Missing approval_key in KIS response")

        self._key = str(approval_key)

        self._expires_at = datetime.now() + timedelta(hours=24) - timedelta(
            seconds=self._buffer
        )
