"""Дневник: лента дня и CRUD блюд."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends

from .. import repo, reports
from ..auth import TelegramUser, current_user
from ..db import today
from ..deps import not_found, parse_day
from ..schemas import MealIn, MealOut, MealPatch

router = APIRouter(tags=["diary"])


@router.get("/diary/{day}")
def read_day(day: str, user: TelegramUser = Depends(current_user)) -> dict:
    """Всё, что нужно главному экрану за один запрос."""
    target = parse_day(day)
    profile = repo.get_profile(user.id, first_name=user.first_name)
    norms = repo.norms_for(profile)

    meals = repo.meals_for_day(user.id, target.isoformat())
    workouts = repo.workouts_for_day(user.id, target.isoformat())
    supplements = reports.supplements_for_day(
        repo.list_supplements(user.id, only_active=True), target
    )
    totals = reports.day_totals(meals, workouts)
    net = totals["calories_eaten"] - totals["calories_burned"]

    return {
        "day": target.isoformat(),
        "meals": meals,
        "workouts": workouts,
        "supplements": supplements,
        "totals": {
            "calories_eaten": round(totals["calories_eaten"]),
            "calories_burned": round(totals["calories_burned"]),
            "calories_net": round(net),
            "calories_remaining": round(norms["calories"] - net),
            "balance_vs_norm": round(net - norms["calories"]),
            "protein_g": round(totals["protein_g"]),
            "fat_g": round(totals["fat_g"]),
            "carbs_g": round(totals["carbs_g"]),
            "fiber_g": round(totals["fiber_g"], 1),
        },
        "norms": norms,
    }


@router.get("/diary/{day}/week")
def read_week_strip(day: str, user: TelegramUser = Depends(current_user)) -> dict:
    """Полоса дней недели: калории по дням для индикаторов под числами."""
    target = parse_day(day)
    monday = target - timedelta(days=target.weekday())
    days = repo.day_strings(monday, monday + timedelta(days=6))

    profile = repo.get_profile(user.id, first_name=user.first_name)
    norms = repo.norms_for(profile)
    meals = repo.meals_for_range(user.id, days[0], days[-1])

    eaten = {d: 0.0 for d in days}
    for meal in meals:
        if meal["day"] in eaten:
            eaten[meal["day"]] += meal["calories_kcal"]

    return {
        "days": [
            {"day": d, "calories": round(eaten[d]), "is_today": d == today().isoformat()}
            for d in days
        ],
        "norm_calories": norms["calories"],
    }


@router.post("/diary/{day}/meals", response_model=MealOut, status_code=201)
def create_meal(
    day: str, payload: MealIn, user: TelegramUser = Depends(current_user)
) -> dict:
    target = parse_day(day)
    data = payload.model_dump()
    meal = repo.add_meal(user.id, target.isoformat(), data)
    if payload.save_as_product:
        repo.add_product(
            user.id,
            {
                "name": meal["name"],
                "protein_g": meal["protein_g"],
                "calories_kcal": meal["calories_kcal"],
                "fat_g": meal["fat_g"],
                "carbs_g": meal["carbs_g"],
                "fiber_g": meal["fiber_g"],
                "portion_g": meal["portion_g"],
                "micros": meal["micros"],
            },
        )
    return meal


@router.get("/meals/{meal_id}", response_model=MealOut)
def read_meal(meal_id: int, user: TelegramUser = Depends(current_user)) -> dict:
    meal = repo.get_meal(user.id, meal_id)
    if meal is None:
        raise not_found("Блюдо")
    return meal


@router.patch("/meals/{meal_id}", response_model=MealOut)
def patch_meal(
    meal_id: int, payload: MealPatch, user: TelegramUser = Depends(current_user)
) -> dict:
    patch = payload.model_dump(exclude_unset=True)
    if "day" in patch and patch["day"]:
        patch["day"] = parse_day(patch["day"]).isoformat()
    meal = repo.update_meal(user.id, meal_id, patch)
    if meal is None:
        raise not_found("Блюдо")
    return meal


@router.delete("/meals/{meal_id}", status_code=204)
def remove_meal(meal_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_meal(user.id, meal_id):
        raise not_found("Блюдо")
