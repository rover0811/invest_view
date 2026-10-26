import httpx
import pytest

from kis_ingestion.poller.kis_client import KISApiError, KISRestClient
from kis_ingestion.poller.rate_limiter import SharedRateLimiter
from kis_ingestion.token_manager import KISTokenManager

BASE_URL = "https://kis.example"


async def _no_sleep(_seconds: float) -> None:
    return None


def _client(http_client: httpx.AsyncClient) -> KISRestClient:
    return KISRestClient(
        base_url=BASE_URL,
        app_key="key",
        app_secret="secret",
        token_manager=KISTokenManager(BASE_URL, "key", "secret", http_client),
        http_client=http_client,
        rate_limiter=SharedRateLimiter(100),
        sleep=_no_sleep,
    )


def _token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})


@pytest.mark.asyncio
async def test_get_sends_auth_headers_and_returns_body():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return _token_response()
        seen.append(request)
        return httpx.Response(200, json={"rt_cd": "0", "output": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        body = await _client(http_client).get("/q", "TR1", {"A": "1"})

    assert body["output"] == []
    request = seen[0]
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["tr_id"] == "TR1"
    assert request.headers["custtype"] == "P"
    assert request.url.params["A"] == "1"


@pytest.mark.asyncio
async def test_retries_rate_limit_then_succeeds():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path == "/oauth2/tokenP":
            return _token_response()
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "limit"})
        return httpx.Response(200, json={"rt_cd": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        body = await _client(http_client).get("/q", "TR1", {})

    assert body["rt_cd"] == "0"
    assert calls == 2


@pytest.mark.asyncio
async def test_refreshes_token_when_expired():
    tokens = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tokens
        if request.url.path == "/oauth2/tokenP":
            tokens += 1
            return httpx.Response(200, json={"access_token": f"tok{tokens}", "expires_in": 86400})
        if request.headers["authorization"] == "Bearer tok1":
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "expired"})
        return httpx.Response(200, json={"rt_cd": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        body = await _client(http_client).get("/q", "TR1", {})

    assert body["rt_cd"] == "0"
    assert tokens == 2


@pytest.mark.asyncio
async def test_non_retryable_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/tokenP":
            return _token_response()
        return httpx.Response(200, json={"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "bad input"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(KISApiError) as excinfo:
            _ = await _client(http_client).get("/q", "TR1", {})

    assert excinfo.value.msg_cd == "OPSQ0001"
