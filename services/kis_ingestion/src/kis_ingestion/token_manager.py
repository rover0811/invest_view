import asyncio
from datetime import datetime, timedelta
import httpx

class KISTokenManager:
    def __init__(
        self, 
        base_url: str, 
        app_key: str, 
        app_secret: str, 
        http_client: httpx.AsyncClient
    ):
        self._base_url = base_url.rstrip("/")
        self._app_key = app_key
        self._app_secret = app_secret
        self._http_client = http_client
        
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()
        self._buffer_seconds = 60

    async def get_token(self) -> str:
        if self._is_valid():
            return self._token
        
        async with self._lock:
            if self._is_valid():
                return self._token
            await self._refresh()
            
        return self._token

    async def force_refresh(self) -> str:
        async with self._lock:
            await self._refresh()
        return self._token

    def _is_valid(self) -> bool:
        if not self._token or not self._expires_at:
            return False
        return datetime.now() < self._expires_at

    async def _refresh(self) -> None:
        url = f"{self._base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret
        }
        
        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        self._token = data["access_token"]
        expires_in = int(data["expires_in"])
        
        self._expires_at = datetime.now() + timedelta(seconds=expires_in) - timedelta(seconds=self._buffer_seconds)
