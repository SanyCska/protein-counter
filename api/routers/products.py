"""Свои сохранённые продукты — база для поиска в шите добавления блюда.

Точка расширения: сюда же подключается внешняя база продуктов, если она понадобится.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import repo
from ..auth import TelegramUser, current_user
from ..deps import not_found
from ..schemas import ProductIn, ProductOut

router = APIRouter(tags=["products"])


@router.get("/products", response_model=list[ProductOut])
def search(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    user: TelegramUser = Depends(current_user),
) -> list[dict]:
    return repo.search_products(user.id, q, limit)


@router.post("/products", response_model=ProductOut, status_code=201)
def create(payload: ProductIn, user: TelegramUser = Depends(current_user)) -> dict:
    return repo.add_product(user.id, payload.model_dump())


@router.delete("/products/{product_id}", status_code=204)
def remove(product_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_product(user.id, product_id):
        raise not_found("Продукт")
