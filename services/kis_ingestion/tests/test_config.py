from kis_ingestion.config import KISConfig
from kis_ingestion.models.constants import APPROVAL_URL, WS_URL

def test_kis_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "test_key")
    monkeypatch.setenv("KIS_APP_SECRET", "test_secret")
    
    config = KISConfig()
    assert config.app_key == "test_key"
    assert config.app_secret == "test_secret"

def test_kis_config_watch_symbols_parses_json(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "test_key")
    monkeypatch.setenv("KIS_APP_SECRET", "test_secret")
    monkeypatch.setenv("KIS_WATCH_SYMBOLS", '["005930", "000660"]')
    
    config = KISConfig()
    assert config.watch_symbols == ["005930", "000660"]

def test_kis_config_defaults(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "test_key")
    monkeypatch.setenv("KIS_APP_SECRET", "test_secret")
    
    config = KISConfig()
    assert config.subscription_cap == 40
    assert config.ws_url == WS_URL
    assert config.approval_url == APPROVAL_URL
    assert config.token_url == "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    assert config.watch_symbols == []
