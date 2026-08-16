"""Аутентификация по Telegram WebApp initData.

Клиент присылает `Authorization: tma <initData>` — строку из `window.Telegram.WebApp.initData`.
Подпись проверяем по документации Telegram: ключ = HMAC_SHA256("WebAppData", bot_token),
хеш = HMAC_SHA256(key, data_check_string). user_id берём только из проверенных данных.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)

#: Насколько долго initData считается свежей. Telegram рекомендует ограничивать окно.
MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str | None = None
    username: str | None = None


class AuthError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _is_dev() -> bool:
    return (os.environ.get("APP_ENV") or "").strip().lower() == "dev"


def verify_init_data(init_data: str, bot_token: str) -> dict[str, str]:
    """Проверить подпись initData и вернуть распарсенные поля.

    Бросает AuthError при неверной подписи, отсутствии hash или протухшем auth_date.
    """
    if not init_data:
        raise AuthError("Пустая initData")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    fields = dict(pairs)
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise AuthError("В initData нет hash")

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise AuthError("Подпись initData не совпала")

    auth_date = fields.get("auth_date")
    if auth_date:
        try:
            age = time.time() - int(auth_date)
        except ValueError:
            raise AuthError("Некорректный auth_date") from None
        if age > MAX_AUTH_AGE_SECONDS:
            raise AuthError("initData устарела, переоткройте приложение")

    return fields


def user_from_fields(fields: dict[str, str]) -> TelegramUser:
    raw_user = fields.get("user")
    if not raw_user:
        raise AuthError("В initData нет пользователя")
    try:
        payload = json.loads(raw_user)
        user_id = int(payload["id"])
    except (ValueError, KeyError, TypeError):
        raise AuthError("Не удалось разобрать пользователя из initData") from None
    return TelegramUser(
        id=user_id,
        first_name=payload.get("first_name"),
        username=payload.get("username"),
    )


async def current_user(
    authorization: str | None = Header(default=None),
    x_dev_user_id: str | None = Header(default=None, alias="X-Dev-User-Id"),
) -> TelegramUser:
    """FastAPI-зависимость: текущий пользователь мини-аппа."""
    if authorization and authorization.startswith("tma "):
        token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise AuthError("Сервер не настроен: нет TELEGRAM_BOT_TOKEN")
        fields = verify_init_data(authorization[4:].strip(), token)
        return user_from_fields(fields)

    if _is_dev() and x_dev_user_id:
        try:
            return TelegramUser(id=int(x_dev_user_id), first_name="Dev")
        except ValueError:
            raise AuthError("X-Dev-User-Id должен быть числом") from None

    raise AuthError("Нужен заголовок Authorization: tma <initData>")
