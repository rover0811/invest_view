import asyncio
import json
from datetime import datetime, timedelta

import httpx
import pytest
from kis_ingestion.approval_key_manager import KISApprovalKeyManager

@pytest.mark.asyncio
async def test_happy_path():
    async def mock_handler(request):
        return httpx.Response(200, json={"approval_key": "test_approval_key"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "key", "secret", client
        )
        key = await manager.get_approval_key()
        assert key == "test_approval_key"

@pytest.mark.asyncio
async def test_caching():
    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"approval_key": f"key_{call_count}"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "key", "secret", client
        )
        key1 = await manager.get_approval_key()
        key2 = await manager.get_approval_key()

        assert key1 == "key_1"
        assert key2 == "key_1"
        assert call_count == 1

@pytest.mark.asyncio
async def test_expiry():
    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"approval_key": f"key_{call_count}"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "key", "secret", client
        )

        key1 = await manager.get_approval_key()
        assert key1 == "key_1"

        manager._expires_at = datetime.now() - timedelta(seconds=1)

        key2 = await manager.get_approval_key()
        assert key2 == "key_2"
        assert call_count == 2

@pytest.mark.asyncio
async def test_concurrent_calls():
    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        await asyncio.sleep(0.1)
        call_count += 1
        return httpx.Response(200, json={"approval_key": "concurrent_key"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "key", "secret", client
        )

        keys = await asyncio.gather(
            manager.get_approval_key(),
            manager.get_approval_key(),
            manager.get_approval_key(),
        )

        assert all(k == "concurrent_key" for k in keys)
        assert call_count == 1

@pytest.mark.asyncio
async def test_force_refresh():
    call_count = 0

    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"approval_key": f"key_{call_count}"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "key", "secret", client
        )

        key1 = await manager.get_approval_key()
        assert key1 == "key_1"

        key2 = await manager.force_refresh()
        assert key2 == "key_2"
        assert call_count == 2

@pytest.mark.asyncio
async def test_verify_request_body():
    request_body = None

    async def mock_handler(request):
        nonlocal request_body
        request_body = json.loads(request.read())
        return httpx.Response(200, json={"approval_key": "test_key"})

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISApprovalKeyManager(
            "https://api.com/approval", "my_app_key", "my_app_secret", client
        )
        await manager.get_approval_key()

        assert request_body["grant_type"] == "client_credentials"
        assert request_body["appkey"] == "my_app_key"
        assert request_body["secretkey"] == "my_app_secret"
        assert "appsecret" not in request_body
