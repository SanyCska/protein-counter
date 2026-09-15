"""Дневник: лента дня и CRUD блюд."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query

from .. import repo, reports
from ..auth import TelegramUser, current_user
from ..db import today
from ..deps import not_found, parse_day
from ..schemas import MealIn, MealOut, MealPatch, StepsIn, StepsOut, WeightIn, WeightOut

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
    eaten = totals["calories_eaten"]

    return {
        "day": target.isoformat(),
        "meals": meals,
        "workouts": workouts,
        "supplements": supplements,
        "weight_kg": repo.weight_for_day(user.id, target.isoformat()),
        "steps": repo.steps_for_day(user.id, target.isoformat()),
        "totals": {
            "calories_eaten": round(totals["calories_eaten"]),
            "calories_burned": round(totals["calories_burned"]),
            # Нагрузка в бюджет калорий не входит: норму сравниваем со съеденным,
            # а расход показываем отдельной строкой и отдельным графиком.
            "calories_net": round(eaten - totals["calories_burned"]),
            "calories_remaining": round(norms["calories"] - eaten),
            "balance_vs_norm": round(eaten - norms["calories"]),
            "protein_g": round(totals["protein_g"]),
            "fat_g": round(totals["fat_g"]),
            "carbs_g": round(totals["carbs_g"]),
            "fiber_g": round(totals["fiber_g"], 1),
        },
        "norms": norms,
    }


@router.put("/diary/{day}/weight", response_model=WeightOut)
def set_weight(day: str, payload: WeightIn, user: TelegramUser = Depends(current_user)) -> dict:
    """Записать вес за день.

    Свежайшее взвешивание заодно становится весом профиля: от него считаются нормы
    и расход на нагрузке, и держать их на цифре месячной давности незачем. Запись
    задним числом профиль не трогает — прошлый вес не отменяет сегодняшний.
    """
    target = parse_day(day)
    result = repo.set_weight(user.id, target.isoformat(), payload.weight_kg)
    latest = repo.last_weight_day(user.id)
    if latest is None or target.isoformat() >= latest:
        repo.update_profile(user.id, {"weight_kg": payload.weight_kg})
    return result


@router.delete("/diary/{day}/weight", status_code=204)
def remove_weight(day: str, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_weight(user.id, parse_day(day).isoformat()):
        raise not_found("Взвешивание")


@router.put("/diary/{day}/steps", response_model=StepsOut)
def set_steps(day: str, payload: StepsIn, user: TelegramUser = Depends(current_user)) -> dict:
    """Записать шаги за день.

    В отличие от веса, профиль они не трогают: расход на нагрузке считается по записям
    тренировок, и приписывать к нему ещё и шаги значило бы посчитать их дважды.
    """
    return repo.set_steps(user.id, parse_day(day).isoformat(), payload.steps)


@router.delete("/diary/{day}/steps", status_code=204)
def remove_steps(day: str, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_steps(user.id, parse_day(day).isoformat()):
        raise not_found("Запись шагов")


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
                "portion_unit": meal["portion_unit"],
                "micros": meal["micros"],
            },
        )
    return meal


#: За сколько дней назад предлагаем повторить блюдо и сколько строк показываем.
RECENT_DAYS = 14
MAX_RECENT = 30


@router.get("/meals/recent", response_model=list[MealOut])
def recent_meals(
    days: int = Query(default=RECENT_DAYS, ge=1, le=60),
    limit: int = Query(default=20, ge=1, le=MAX_RECENT),
    user: TelegramUser = Depends(current_user),
) -> list[dict]:
    """Что ел на днях — чтобы повторить запись, а не вносить её заново.

    Объявлено до `/meals/{meal_id}`: иначе путь уехал бы в разбор идентификатора.
    """
    since = today() - timedelta(days=days - 1)
    return repo.recent_meals(user.id, since.isoformat(), limit)


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
    if "day" in patch:
        if patch["day"] is None:
            del patch["day"]
        else:
            patch["day"] = parse_day(patch["day"]).isoformat()
    meal = repo.update_meal(user.id, meal_id, patch)
    if meal is None:
        raise not_found("Блюдо")
    return meal


@router.delete("/meals/{meal_id}", status_code=204)
def remove_meal(meal_id: int, user: TelegramUser = Depends(current_user)) -> None:
    if not repo.delete_meal(user.id, meal_id):
        raise not_found("Блюдо")
