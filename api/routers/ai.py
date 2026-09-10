"""ИИ-разбор для шитов: блюдо по тексту и фото, состав продукта и добавки с этикетки."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from .. import ai
from ..auth import TelegramUser, current_user
from ..schemas import (
    AiLabelOut,
    AiParseIn,
    AiParseOut,
    AiPhotoIn,
    AiProductIn,
    AiProductOut,
    AiSupplementLabelOut,
)

router = APIRouter(tags=["ai"])

MAX_IMAGE_CHARS = 12 * 1024 * 1024  # ~9 МБ бинарных данных в base64


def _check_image(image_base64: str | None) -> None:
    if image_base64 and len(image_base64) > MAX_IMAGE_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Фото слишком большое, сожмите перед отправкой",
        )


def _guard(call):
    """Ошибки ИИ — в HTTP: плохой ввод это 400, всё остальное 503."""
    try:
        return call()
    except ai.AiBadInput as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ai.AiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/ai/parse", response_model=AiParseOut)
def parse(payload: AiParseIn, user: TelegramUser = Depends(current_user)) -> dict:
    _check_image(payload.image_base64)
    return _guard(lambda: ai.parse_meal(text=payload.text, image_base64=payload.image_base64))


@router.post("/ai/label", response_model=AiLabelOut)
def label(payload: AiPhotoIn, user: TelegramUser = Depends(current_user)) -> dict:
    """Прочитать пищевую ценность с фото упаковки — для формы «своё блюдо»."""
    _check_image(payload.image_base64)
    return _guard(
        lambda: ai.parse_label(image_base64=payload.image_base64, text=payload.text)
    )


@router.post("/ai/supplement-label", response_model=AiSupplementLabelOut)
def supplement_label(payload: AiPhotoIn, user: TelegramUser = Depends(current_user)) -> dict:
    """Прочитать состав добавки с фото банки — обычно это несколько веществ сразу."""
    _check_image(payload.image_base64)
    return _guard(
        lambda: ai.parse_supplement_label(
            image_base64=payload.image_base64, text=payload.text
        )
    )


@router.post("/ai/product", response_model=AiProductOut)
def product(payload: AiProductIn, user: TelegramUser = Depends(current_user)) -> dict:
    """Оценить микронутриенты продукта до его сохранения — для формы «своё блюдо»."""
    return _guard(
        lambda: ai.estimate_product(
            name=payload.name,
            portion_g=payload.portion_g,
            macros={
                "calories_kcal": payload.calories_kcal,
                "protein_g": payload.protein_g,
                "fat_g": payload.fat_g,
                "carbs_g": payload.carbs_g,
            },
        )
    )
