"""Профиль пользователя и расчётная норма."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import repo
from ..auth import TelegramUser, current_user
from ..nutrition.catalog import NUTRIENTS
from ..nutrition.norms import ACTIVITY_LEVELS
from ..nutrition.workouts import WORKOUT_KINDS
from ..schemas import ProfileIn, ProfileOut

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=ProfileOut)
def read_profile(user: TelegramUser = Depends(current_user)) -> dict:
    profile = repo.get_profile(user.id, first_name=user.first_name)
    return {**profile, "norms": repo.norms_for(profile)}


@router.put("/profile", response_model=ProfileOut)
def write_profile(
    payload: ProfileIn, user: TelegramUser = Depends(current_user)
) -> dict:
    profile = repo.update_profile(user.id, payload.model_dump(exclude_unset=True))
    return {**profile, "norms": repo.norms_for(profile)}


@router.get("/reference")
def reference() -> dict:
    """Справочники для фронта: нутриенты, уровни активности, виды нагрузки."""
    return {
        "nutrients": [
            {
                "key": n.key,
                "name": n.name,
                "unit": n.unit,
                "rda_male": n.rda_male,
                "rda_female": n.rda_female,
            }
            for n in NUTRIENTS
        ],
        "activity_levels": [
            {"key": key, "factor": factor, "name": name}
            for (key, factor), name in zip(
                ACTIVITY_LEVELS.items(),
                [
                    "Сидячий образ жизни",
                    "Лёгкая активность",
                    "Умеренная активность",
                    "Высокая активность",
                    "Спортсмен",
                ],
            )
        ],
        "workout_kinds": [
            {"key": k.key, "name": k.name, "met": k.met, "icon": k.icon}
            for k in WORKOUT_KINDS
        ],
    }
