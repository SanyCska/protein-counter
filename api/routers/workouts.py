"""Физнагрузка: записи дня, шаблоны, расчёт расхода."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .. import repo
from ..auth import TelegramUser, current_user
from ..deps import not_found, parse_day
from ..nutrition import workouts as wk
from ..schemas import (
    WorkoutIn,
    WorkoutOut,
    WorkoutPatch,
    WorkoutTemplateIn,
    WorkoutTemplateOut,
)

router = APIRouter(tags=["workouts"])


def _weight(user: TelegramUser) -> float:
    return repo.get_profile(user.id, first_name=user.first_name)["weight_kg"]


@router.get("/workouts/{day}", response_model=list[WorkoutOut])
def read_day(day: str, user: TelegramUser = Depends(current_user)) -> list[dict]:
    return repo.workouts_for_day(user.id, parse_day(day).isoformat())


@router.post("/diary/{day}/workouts", response_model=WorkoutOut, status_code=201)
def create_workout(
    day: str, payload: WorkoutIn, user: TelegramUser = Depends(current_user)
) -> dict:
    target = parse_day(day)
    weight = _weight(user)
    workout = repo.add_workout(user.id, target.isoformat(), payload.model_dump(), weight)
    if payload.save_as_template:
        repo.add_template(
            user.id,
            {
                "name": payload.template_name or wk.name_for(payload.kind),
                "kind": payload.kind,
                "minutes": payload.minutes,
            },
            weight,
        )
    return workout


@router.patch("/workouts/{workout_id}", response_model=WorkoutOut)
def patch_workout(
    workout_id: int, payload: WorkoutPatch, user: TelegramUser = Depends(current_user)
) -> dict:
    workout = repo.update_workout(
        user.id, workout_id, payload.model_dump(exclude_unset=True), _weight(user)
    )
    if workout is None:
        raise not_found("Тренировка")
    return workout


@router.delete("/workouts/{workout_id}", status_code=204)
def remove_workout(workout_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_workout(user.id, workout_id):
        raise not_found("Тренировка")


@router.get("/workout-templates", response_model=list[WorkoutTemplateOut])
def read_templates(user: TelegramUser = Depends(current_user)) -> list[dict]:
    return repo.list_templates(user.id, _weight(user))


@router.post("/workout-templates", response_model=WorkoutTemplateOut, status_code=201)
def create_template(
    payload: WorkoutTemplateIn, user: TelegramUser = Depends(current_user)
) -> dict:
    return repo.add_template(user.id, payload.model_dump(), _weight(user))


@router.delete("/workout-templates/{template_id}", status_code=204)
def remove_template(template_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_template(user.id, template_id):
        raise not_found("Шаблон")


@router.get("/workout-estimate")
def estimate(
    kind: str = Query(...),
    minutes: float = Query(..., ge=0, le=600),
    user: TelegramUser = Depends(current_user),
) -> dict:
    """Живой пересчёт расхода для слайдера длительности в шите нагрузки."""
    weight = _weight(user)
    return {
        "kind": kind,
        "kind_name": wk.name_for(kind),
        "minutes": minutes,
        "weight_kg": weight,
        "met": wk.met_for(kind),
        "kcal": round(wk.kcal_for(kind=kind, minutes=minutes, weight_kg=weight)),
    }
