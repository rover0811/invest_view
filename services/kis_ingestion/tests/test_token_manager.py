import pytest
import asyncio
from datetime import datetime, timedelta
import httpx
from kis_ingestion.token_manager import KISTokenManager

@pytest.mark.asyncio
async def test_happy_path():
    async def mock_handler(request):
        return httpx.Response(200, json={
            "access_token": "test_token",
            "expires_in": 86400
        })
    
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISTokenManager("https://api.com", "key", "secret", client)
        token = await manager.get_token()
        assert token == "test_token"

@pytest.mark.asyncio
async def test_caching():
    call_count = 0
    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={
            "access_token": f"token_{call_count}",
            "expires_in": 86400
        })
    
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISTokenManager("https://api.com", "key", "secret", client)
        token1 = await manager.get_token()
        token2 = await manager.get_token()
        
        assert token1 == "token_1"
        assert token2 == "token_1"
        assert call_count == 1

@pytest.mark.asyncio
async def test_expiry():
    call_count = 0
    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={
            "access_token": f"token_{call_count}",
            "expires_in": 2
        })
    
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISTokenManager("https://api.com", "key", "secret", client)
        manager._buffer_seconds = 1 
        
        token1 = await manager.get_token()
        assert token1 == "token_1"
        
        await asyncio.sleep(1.1)
        
        token2 = await manager.get_token()
        assert token2 == "token_2"
        assert call_count == 2

@pytest.mark.asyncio
async def test_concurrent_calls():
    call_count = 0
    async def mock_handler(request):
        nonlocal call_count
        await asyncio.sleep(0.1)
        call_count += 1
        return httpx.Response(200, json={
            "access_token": "concurrent_token",
            "expires_in": 86400
        })
    
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISTokenManager("https://api.com", "key", "secret", client)
        
        tokens = await asyncio.gather(
            manager.get_token(),
            manager.get_token(),
            manager.get_token()
        )
        
        assert all(t == "concurrent_token" for t in tokens)
        assert call_count == 1

@pytest.mark.asyncio
async def test_force_refresh():
    call_count = 0
    async def mock_handler(request):
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={
            "access_token": f"token_{call_count}",
            "expires_in": 86400
        })
    
    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        manager = KISTokenManager("https://api.com", "key", "secret", client)
        
        token1 = await manager.get_token()
        assert token1 == "token_1"
        
        token2 = await manager.force_refresh()
        assert token2 == "token_2"
        assert call_count == 2
