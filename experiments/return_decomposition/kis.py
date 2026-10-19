from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://openapi.koreainvestment.com:9443"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
CACHE_DIR = HERE / ".cache"
TOKEN_PATH = CACHE_DIR / "kis_token.json"

MIN_INTERVAL_S = 0.25
RATE_LIMIT_CODE = "EGW00201"


def _load_credentials() -> tuple[str, str]:
    key = os.environ.get("KIS_APP_KEY")
    secret = os.environ.get("KIS_APP_SECRET")
    if key and secret:
        return key, secret

    env: dict[str, str] = {}
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        env[name.strip()] = value.strip().strip("'\"")
    return env["KIS_APP_KEY"], env["KIS_APP_SECRET"]


class KisClient:
    def __init__(self) -> None:
        self.app_key, self.app_secret = _load_credentials()
        self._http = httpx.Client(base_url=BASE_URL, timeout=15.0)
        self._last_call = 0.0
        self._token = self._load_token()

    def _load_token(self) -> str:
        if TOKEN_PATH.exists():
            cached = json.loads(TOKEN_PATH.read_text())
            expires_at = datetime.strptime(cached["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
            if expires_at - datetime.now() > timedelta(hours=1):
                return cached["access_token"]

        resp = self._http.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        CACHE_DIR.mkdir(exist_ok=True)
        TOKEN_PATH.write_text(json.dumps(data))
        return data["access_token"]

    def get(self, path: str, tr_id: str, params: dict[str, str], retries: int = 6) -> dict[str, Any]:
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        last_error = ""
        for attempt in range(retries):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

            resp = self._http.get(path, params=params, headers=headers)
            try:
                body = resp.json()
            except ValueError:
                body = {}

            if resp.status_code == 200 and body.get("rt_cd") == "0":
                return body

            last_error = f"http={resp.status_code} {body.get('msg_cd')} {body.get('msg1')}"
            retryable = resp.status_code >= 500 or body.get("msg_cd") == RATE_LIMIT_CODE
            if not retryable:
                break
            time.sleep(1.0 + attempt)

        raise RuntimeError(f"{tr_id} failed: {last_error} params={params}")
