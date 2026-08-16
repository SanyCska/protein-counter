"""Общие зависимости и хелперы роутеров."""

from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException, status

from . import repo
from .auth import TelegramUser, current_user

CurrentUser = Depends(current_user)


def parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Дата должна быть в формате YYYY-MM-DD, получено {value!r}",
        ) from None


def profile_of(user: TelegramUser) -> dict:
    return repo.get_profile(user.id, first_name=user.first_name)


def not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} не найдено")
