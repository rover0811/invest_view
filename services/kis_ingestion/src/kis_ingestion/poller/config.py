from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


# Same parent-hop invariant as container.py: this file sits at
# <root>/services/kis_ingestion/src/kis_ingestion/poller/config.py.
DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[5] / "schemas"


class PollerConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(env_prefix="KIS_POLLER_")

    database_url: str
    rest_base_url: str = "https://openapi.koreainvestment.com:9443"
    rate_limit_per_second: int = 10
    schema_dir: Path = DEFAULT_SCHEMA_DIR
    flush_timeout_seconds: float = 10.0

    news_topic: str = "news-raw"
    news_interval_seconds: float = 60.0
    news_max_pages: int = 3
