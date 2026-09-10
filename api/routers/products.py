"""Свои сохранённые продукты — база для поиска в шите добавления блюда.

Точка расширения: сюда же подключается внешняя база продуктов, если она понадобится.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import ai, repo
from ..auth import TelegramUser, current_user
from ..deps import not_found
from ..schemas import ProductIn, ProductOut, ProductPatch, ProductsEstimateOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["products"])

#: Сколько продуктов оцениваем за один пакетный запуск. Каждый — отдельный вызов
#: модели, поэтому ограничиваем, чтобы не подвесить запрос и не удивить счётом.
MAX_BATCH_ESTIMATE = 15


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


@router.patch("/products/{product_id}", response_model=ProductOut)
def update(
    product_id: int, payload: ProductPatch, user: TelegramUser = Depends(current_user)
) -> dict:
    product = repo.update_product(user.id, product_id, payload.model_dump(exclude_unset=True))
    if product is None:
        raise not_found("Продукт")
    return product


@router.post("/products/{product_id}/estimate", response_model=ProductOut)
def estimate(product_id: int, user: TelegramUser = Depends(current_user)) -> dict:
    """Оценить микронутриенты одного продукта и сохранить их."""
    product = repo.get_product(user.id, product_id)
    if product is None:
        raise not_found("Продукт")
    try:
        result = _estimate(product)
    except ai.AiBadInput as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ai.AiUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    updated = repo.update_product(user.id, product_id, result)
    return updated or product


@router.post("/products/estimate", response_model=ProductsEstimateOut)
def estimate_missing(user: TelegramUser = Depends(current_user)) -> dict:
    """Оценить все продукты без известного состава — по одному вызову модели на продукт.

    Отдельные неудачи не роняют пакет: продукт остаётся без состава и попадёт
    в следующий запуск. А вот если модель недоступна вовсе, продолжать незачем.
    """
    pending = repo.products_without_micros(user.id, MAX_BATCH_ESTIMATE + 1)
    batch = pending[:MAX_BATCH_ESTIMATE]

    updated: list[dict] = []
    failed = 0
    error: str | None = None

    for product in batch:
        try:
            result = _estimate(product)
        except ai.AiBadAnswer:
            # Модель не справилась именно с этим продуктом — идём дальше.
            logger.exception("Не удалось оценить продукт %s", product["id"])
            failed += 1
            continue
        except ai.AiUnavailable as exc:
            # Модель недоступна вовсе — остальные продукты тоже не оценить.
            error = str(exc)
            break
        except Exception:  # noqa: BLE001 — один плохой продукт не должен ронять пакет
            logger.exception("Не удалось оценить продукт %s", product["id"])
            failed += 1
            continue
        saved = repo.update_product(user.id, product["id"], result)
        if saved:
            updated.append(saved)

    if error and not updated:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error)

    return {
        "updated": updated,
        "failed": failed,
        "remaining": len(repo.products_without_micros(user.id, MAX_BATCH_ESTIMATE + 1)),
        "error": error,
    }


def _estimate(product: dict) -> dict:
    """ИИ-оценка состава продукта в виде патча для repo.update_product."""
    result = ai.estimate_product(
        name=product["name"],
        portion_g=product["portion_g"],
        macros={
            "calories_kcal": product["calories_kcal"],
            "protein_g": product["protein_g"],
            "fat_g": product["fat_g"],
            "carbs_g": product["carbs_g"],
        },
    )
    patch: dict = {"micros": result["micros"]}
    # Клетчатку модель считает вместе с микронутриентами; если своей нет — берём оценку.
    if not product["fiber_g"] and result["fiber_g"]:
        patch["fiber_g"] = result["fiber_g"]
    return patch
