from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_BOT_TOKEN = "123456:TEST-TOKEN-FOR-TESTS"
TEST_USER_ID = 424242


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Каждый тест получает свою базу — так тесты не видят данных друг друга."""
    monkeypatch.setenv("PROTEIN_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TEST_BOT_TOKEN)
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("TZ", "UTC")
    from api.db import migrate

    migrate()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        test_client.headers.update({"X-Dev-User-Id": str(TEST_USER_ID)})
        yield test_client


@pytest.fixture
def user_id() -> int:
    return TEST_USER_ID
