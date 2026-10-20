from __future__ import annotations

import os
from datetime import time
from pathlib import Path

from pydantic import BaseModel

KIS_REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
ROOT_ENV_NAMES = ("KIS_APP_KEY", "KIS_APP_SECRET")


class MarketDataSettings(BaseModel):
    """Settings for the after-close market data job.

    KIS credentials come from ``KIS_APP_KEY`` and ``KIS_APP_SECRET``; outside a
    container they fall back to the repository root ``.env``.
    """

    kis_app_key: str
    kis_app_secret: str
    kis_base_url: str = KIS_REAL_BASE_URL
    min_call_interval_s: float = 0.5
    run_at_kst: time = time(17, 0)
    refresh_days: int = 10
    history_days: int = 400


def _root_env_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    return values


def _find_root_env() -> Path | None:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.is_file() and (parent / "services").is_dir():
            return candidate
    return None


def load_market_data_settings() -> MarketDataSettings:
    values = {name: os.environ.get(name, "") for name in ROOT_ENV_NAMES}
    if not all(values.values()):
        env_path = _find_root_env()
        file_values = _root_env_values(env_path) if env_path else {}
        values = {name: values[name] or file_values.get(name, "") for name in values}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"missing KIS credentials: {', '.join(missing)}")
    overrides: dict[str, object] = {}
    for field, env_name in (
        ("kis_base_url", "AI_SIGNAL_MARKET_DATA_KIS_BASE_URL"),
        ("min_call_interval_s", "AI_SIGNAL_MARKET_DATA_MIN_CALL_INTERVAL_S"),
        ("run_at_kst", "AI_SIGNAL_MARKET_DATA_RUN_AT_KST"),
        ("refresh_days", "AI_SIGNAL_MARKET_DATA_REFRESH_DAYS"),
        ("history_days", "AI_SIGNAL_MARKET_DATA_HISTORY_DAYS"),
    ):
        if env_name in os.environ:
            overrides[field] = os.environ[env_name]
    return MarketDataSettings.model_validate(
        {
            "kis_app_key": values["KIS_APP_KEY"],
            "kis_app_secret": values["KIS_APP_SECRET"],
            **overrides,
        }
    )
