"""Расчёт дневной нормы: Mifflin–St Jeor + активность + цель."""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import rda_map

#: Коэффициенты активности (стандартная шкала Харриса-Бенедикта).
ACTIVITY_LEVELS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "high": 1.725,
    "athlete": 1.9,
}

GOALS = ("lose", "maintain", "gain")

#: Поправка калорий под цель.
GOAL_FACTOR: dict[str, float] = {"lose": 0.85, "maintain": 1.0, "gain": 1.12}

#: Белок, г на кг веса тела.
GOAL_PROTEIN_PER_KG: dict[str, float] = {"lose": 2.0, "maintain": 1.8, "gain": 1.9}

#: Жиры, г на кг веса тела — нижняя здоровая граница с запасом.
FAT_PER_KG = 0.9

KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_FAT = 9.0
KCAL_PER_G_CARBS = 4.0


@dataclass(frozen=True)
class Norms:
    bmr: float
    calories: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    activity_factor: float
    goal: str
    micros: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "bmr": round(self.bmr),
            "calories": round(self.calories),
            "protein_g": round(self.protein_g),
            "fat_g": round(self.fat_g),
            "carbs_g": round(self.carbs_g),
            "fiber_g": round(self.fiber_g),
            "activity_factor": self.activity_factor,
            "goal": self.goal,
            "micros": {k: round(v, 3) for k, v in self.micros.items()},
        }


def bmr_mifflin(*, sex: str, age: int, height_cm: float, weight_kg: float) -> float:
    """Базовый обмен по Mifflin–St Jeor."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex != "f" else -161)


def activity_factor(activity: str | float) -> float:
    """Принять как название уровня, так и готовый коэффициент (1.2–1.9)."""
    if isinstance(activity, (int, float)):
        return _clamp(float(activity), 1.2, 1.9)
    return ACTIVITY_LEVELS.get(str(activity), ACTIVITY_LEVELS["moderate"])


def compute_norms(
    *,
    sex: str,
    age: int,
    height_cm: float,
    weight_kg: float,
    activity: str | float,
    goal: str,
) -> Norms:
    goal = goal if goal in GOALS else "maintain"
    factor = activity_factor(activity)
    bmr = bmr_mifflin(sex=sex, age=age, height_cm=height_cm, weight_kg=weight_kg)
    calories = bmr * factor * GOAL_FACTOR[goal]

    protein_g = GOAL_PROTEIN_PER_KG[goal] * weight_kg
    fat_g = FAT_PER_KG * weight_kg
    rest_kcal = calories - protein_g * KCAL_PER_G_PROTEIN - fat_g * KCAL_PER_G_FAT
    carbs_g = max(rest_kcal, 0.0) / KCAL_PER_G_CARBS

    micros = rda_map(sex)
    return Norms(
        bmr=bmr,
        calories=calories,
        protein_g=protein_g,
        fat_g=fat_g,
        carbs_g=carbs_g,
        fiber_g=micros["fiber"],
        activity_factor=factor,
        goal=goal,
        micros=micros,
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
