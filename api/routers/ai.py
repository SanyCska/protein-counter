"""ИИ-разбор блюда для шита добавления."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import ai
from ..auth import TelegramUser, current_user
from ..schemas import AiParseIn, AiParseOut

router = APIRouter(tags=["ai"])

MAX_IMAGE_CHARS = 12 * 1024 * 1024  # ~9 МБ бинарных данных в base64


@router.post("/ai/parse", response_model=AiParseOut)
def parse(payload: AiParseIn, user: TelegramUser = Depends(current_user)) -> dict:
    if payload.image_base64 and len(payload.image_base64) > MAX_IMAGE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Фото слишком большое, сожмите перед отправкой",
        )
    try:
        return ai.parse_meal(text=payload.text, image_base64=payload.image_base64)
    except ai.AiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
