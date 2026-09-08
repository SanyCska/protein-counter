"""Отчёты за день и период, ряды для графиков прогресса."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from .. import repo, reports
from ..auth import TelegramUser, current_user
from ..db import today
from ..deps import parse_day

router = APIRouter(tags=["analytics"])


def _end_date(end: str | None) -> date:
    return parse_day(end) if end else today()


@router.get("/report/day/{day}")
def day_report(day: str, user: TelegramUser = Depends(current_user)) -> dict:
    target = parse_day(day)
    profile = repo.get_profile(user.id, first_name=user.first_name)
    return reports.build_day_report(
        day=target,
        meals=repo.meals_for_day(user.id, target.isoformat()),
        workouts=repo.workouts_for_day(user.id, target.isoformat()),
        supplements=repo.list_supplements(user.id, only_active=True),
        norms=repo.norms_for(profile),
    )


@router.get("/report/period")
def period_report(
    range: str = Query(default="week", pattern="^(week|month)$"),
    end: str | None = Query(default=None),
    user: TelegramUser = Depends(current_user),
) -> dict:
    end_date = _end_date(end)
    start, stop = repo.range_bounds(end_date, range)
    days = repo.day_strings(start, stop)
    profile = repo.get_profile(user.id, first_name=user.first_name)

    report = reports.build_period_report(
        days=days,
        meals=repo.meals_for_range(user.id, days[0], days[-1]),
        workouts=repo.workouts_for_range(user.id, days[0], days[-1]),
        supplements=repo.list_supplements(user.id, only_active=True),
        norms=repo.norms_for(profile),
    )
    return {"range": range, "start": days[0], "end": days[-1], **report}


@router.get("/progress")
def progress(
    range: str = Query(default="week", pattern="^(week|month)$"),
    end: str | None = Query(default=None),
    user: TelegramUser = Depends(current_user),
) -> dict:
    end_date = _end_date(end)
    start, stop = repo.range_bounds(end_date, range)
    days = repo.day_strings(start, stop)
    profile = repo.get_profile(user.id, first_name=user.first_name)
    norms = repo.norms_for(profile)

    # Предыдущий период того же размера — для дельт на плитках статистики.
    span = len(days)
    prev_stop = start - timedelta(days=1)
    prev_days = repo.day_strings(prev_stop - timedelta(days=span - 1), prev_stop)
    previous = reports.build_progress(
        days=prev_days,
        meals=repo.meals_for_range(user.id, prev_days[0], prev_days[-1]),
        workouts=repo.workouts_for_range(user.id, prev_days[0], prev_days[-1]),
        norms=norms,
    )

    # Пустой прошлый период — не «0 ккал», а отсутствие данных: иначе плитка
    # покажет фиктивный прирост на всю среднюю калорийность.
    previous_summary = (
        {
            "avg_calories": previous["avg_calories"],
            "macro_days": next(
                (t["value"] for t in previous["tiles"] if t["key"] == "macro_days"), 0
            ),
        }
        if any(previous["calories"])
        else None
    )

    current = reports.build_progress(
        days=days,
        meals=repo.meals_for_range(user.id, days[0], days[-1]),
        workouts=repo.workouts_for_range(user.id, days[0], days[-1]),
        norms=norms,
        previous=previous_summary,
    )
    return {"range": range, "start": days[0], "end": days[-1], **current}
