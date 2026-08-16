"""Виды физнагрузки и расчёт расхода калорий.

Расход считаем по MET: ккал = MET × вес(кг) × часы. Формула приблизительная, но
устойчивая и предсказуемая — пользователь видит одинаковое число для одинаковой
тренировки, а при изменении веса оно пересчитывается само.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkoutKind:
    key: str
    name: str
    met: float
    icon: str


WORKOUT_KINDS: tuple[WorkoutKind, ...] = (
    WorkoutKind("swimming", "Плавание", 7.0, "person-simple-swim"),
    WorkoutKind("football", "Футбол", 8.0, "soccer-ball"),
    WorkoutKind("padel", "Падел", 6.5, "tennis-ball"),
    WorkoutKind("cycling", "Велосипед", 7.5, "bicycle"),
    WorkoutKind("treadmill", "Дорожка", 4.5, "sneaker-move"),
    WorkoutKind("home_workout", "Домашняя тренировка", 5.5, "barbell"),
    WorkoutKind("running", "Бег", 9.8, "person-simple-run"),
    WorkoutKind("gym", "Зал / силовая", 5.0, "barbell"),
    WorkoutKind("walking", "Ходьба", 3.5, "sneaker-move"),
    WorkoutKind("tennis", "Теннис", 7.3, "tennis-ball"),
    WorkoutKind("other", "Другое", 5.0, "person-simple-run"),
)

BY_KEY: dict[str, WorkoutKind] = {k.key: k for k in WORKOUT_KINDS}

DEFAULT_WEIGHT_KG = 75.0

#: Шаблоны, которые получает новый пользователь — чтобы экран не был пустым.
STARTER_TEMPLATES: tuple[tuple[str, str, int], ...] = (
    ("Плавание", "swimming", 45),
    ("Футбол", "football", 60),
    ("Падел", "padel", 75),
    ("Велосипед", "cycling", 40),
    ("Дорожка", "treadmill", 30),
    ("Домашняя", "home_workout", 35),
)


def met_for(kind: str) -> float:
    return BY_KEY.get(kind, BY_KEY["other"]).met


def kcal_for(*, kind: str, minutes: float, weight_kg: float | None) -> float:
    """Расход за тренировку. Вес по умолчанию — если профиль ещё не заполнен."""
    weight = weight_kg if weight_kg and weight_kg > 0 else DEFAULT_WEIGHT_KG
    return met_for(kind) * weight * (max(minutes, 0.0) / 60.0)


def icon_for(kind: str) -> str:
    return BY_KEY.get(kind, BY_KEY["other"]).icon


def name_for(kind: str) -> str:
    return BY_KEY.get(kind, BY_KEY["other"]).name
