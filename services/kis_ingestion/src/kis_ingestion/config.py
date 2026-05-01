from pydantic_settings import BaseSettings, SettingsConfigDict
from kis_ingestion.models.constants import APPROVAL_URL, WS_URL

class KISConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KIS_")

    app_key: str
    app_secret: str
    ws_url: str = WS_URL
    approval_url: str = APPROVAL_URL
    token_url: str = "https://openapi.koreainvestment.com:9443/oauth2/tokenP"
    watch_symbols: list[str] = []
    subscription_cap: int = 40
