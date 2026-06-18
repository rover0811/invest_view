from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backfill_daily_ohlc as _mod


def test_load_root_env_missing_file_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mod, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("SENTINEL_VAR", "original")

    _mod._load_root_env()

    assert os.environ["SENTINEL_VAR"] == "original"


def test_load_root_env_loads_values_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("FOO_TEST_VAR=bar\n# comment\nBAZ_TEST_VAR=qux\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("FOO_TEST_VAR", raising=False)
    monkeypatch.delenv("BAZ_TEST_VAR", raising=False)

    _mod._load_root_env()

    assert os.environ.get("FOO_TEST_VAR") == "bar"
    assert os.environ.get("BAZ_TEST_VAR") == "qux"


def test_load_root_env_does_not_overwrite_existing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("OVERWRITE_TEST_VAR=fromfile\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("OVERWRITE_TEST_VAR", "existing")

    _mod._load_root_env()

    assert os.environ["OVERWRITE_TEST_VAR"] == "existing"
