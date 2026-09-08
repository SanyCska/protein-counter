"""Проверка подписи Telegram initData."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from api.auth import AuthError, user_from_fields, verify_init_data
from tests.conftest import TEST_BOT_TOKEN


def make_init_data(
    *, token: str = TEST_BOT_TOKEN, user_id: int = 777, auth_date: int | None = None
) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF",
        "user": json.dumps({"id": user_id, "first_name": "Саня"}, ensure_ascii=False),
    }
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TestVerifyInitData:
    def test_valid_signature_passes(self):
        fields = verify_init_data(make_init_data(), TEST_BOT_TOKEN)
        assert user_from_fields(fields).id == 777

    def test_cyrillic_first_name_survives_encoding(self):
        fields = verify_init_data(make_init_data(), TEST_BOT_TOKEN)
        assert user_from_fields(fields).first_name == "Саня"

    def test_wrong_token_rejected(self):
        with pytest.raises(AuthError):
            verify_init_data(make_init_data(token="другой:токен"), TEST_BOT_TOKEN)

    def test_tampered_user_id_rejected(self):
        raw = make_init_data(user_id=777).replace("777", "888")
        with pytest.raises(AuthError):
            verify_init_data(raw, TEST_BOT_TOKEN)

    def test_missing_hash_rejected(self):
        with pytest.raises(AuthError):
            verify_init_data("auth_date=1&user=%7B%7D", TEST_BOT_TOKEN)

    def test_empty_init_data_rejected(self):
        with pytest.raises(AuthError):
            verify_init_data("", TEST_BOT_TOKEN)

    def test_stale_auth_date_rejected(self):
        stale = int(time.time()) - 40 * 60 * 60
        with pytest.raises(AuthError, match="устарела"):
            verify_init_data(make_init_data(auth_date=stale), TEST_BOT_TOKEN)


class TestEndpointAuth:
    def test_request_without_credentials_is_401(self, client):
        client.headers.pop("X-Dev-User-Id")
        assert client.get("/api/profile").status_code == 401

    def test_valid_init_data_header_works(self, client):
        client.headers.pop("X-Dev-User-Id")
        client.headers["Authorization"] = f"tma {make_init_data(user_id=999)}"
        response = client.get("/api/profile")
        assert response.status_code == 200
        assert response.json()["user_id"] == 999

    def test_dev_header_ignored_outside_dev(self, client, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        assert client.get("/api/profile").status_code == 401

    def test_non_hex_hash_rejected_not_crashed(self):
        # compare_digest на не-ASCII строках бросает TypeError — должен быть 401, а не 500.
        with pytest.raises(AuthError, match="hash"):
            verify_init_data("auth_date=1&user=%7B%7D&hash=%C3%A9", TEST_BOT_TOKEN)

    def test_missing_auth_date_rejected(self):
        # Корректно подписанная initData без auth_date — TTL проверить нечем, отклоняем.
        fields = {"query_id": "AAF", "user": json.dumps({"id": 777})}
        check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        with pytest.raises(AuthError, match="auth_date"):
            verify_init_data(urlencode(fields), TEST_BOT_TOKEN)
