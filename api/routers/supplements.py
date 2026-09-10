"""Добавки и витамины пользователя."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import repo
from ..auth import TelegramUser, current_user
from ..deps import not_found
from ..schemas import (
    SupplementBulkIn,
    SupplementIdsIn,
    SupplementIn,
    SupplementOut,
    SupplementPatch,
)

router = APIRouter(tags=["supplements"])


@router.get("/supplements", response_model=list[SupplementOut])
def read_all(user: TelegramUser = Depends(current_user)) -> list[dict]:
    return repo.list_supplements(user.id)


@router.post("/supplements", response_model=SupplementOut, status_code=201)
def create(payload: SupplementIn, user: TelegramUser = Depends(current_user)) -> dict:
    return repo.add_supplement(user.id, payload.model_dump())


@router.post("/supplements/bulk", response_model=list[SupplementOut], status_code=201)
def create_many(payload: SupplementBulkIn, user: TelegramUser = Depends(current_user)) -> list[dict]:
    """Сохранить банку целиком: с этикетки мультивитаминов приезжает десяток веществ,
    и подтверждать каждое по отдельности незачем.

    Название банки общее для всех веществ и ставится здесь, а не приходит в каждом
    элементе: тогда список добавок гарантированно не расползётся на десять строк.
    """
    # Название и доли приёма — общие для банки: одна таблетка не может быть «одной
    # третью» для витамина A и «целой» для магния.
    shared = {
        "group_name": payload.name,
        "label_serving": payload.label_serving,
        "taken_serving": payload.taken_serving,
    }
    return [repo.add_supplement(user.id, {**item.model_dump(), **shared}) for item in payload.items]


@router.post("/supplements/bulk-delete", status_code=204)
def remove_many(payload: SupplementIdsIn, user: TelegramUser = Depends(current_user)) -> None:
    """Убрать банку из списка — одним запросом, а не по веществу за раз."""
    if not repo.delete_supplements(user.id, payload.ids):
        raise not_found("Добавка")


@router.patch("/supplements/{supplement_id}", response_model=SupplementOut)
def patch(
    supplement_id: int,
    payload: SupplementPatch,
    user: TelegramUser = Depends(current_user),
) -> dict:
    supplement = repo.update_supplement(
        user.id, supplement_id, payload.model_dump(exclude_unset=True)
    )
    if supplement is None:
        raise not_found("Добавка")
    return supplement


@router.delete("/supplements/{supplement_id}", status_code=204)
def remove(supplement_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_supplement(user.id, supplement_id):
        raise not_found("Добавка")
