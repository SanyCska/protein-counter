"""Справочник микронутриентов: ключи, названия, единицы, суточные нормы.

Один источник истины для всего проекта: ИИ-промпт, хранение в БД, отчёты и добавки
используют один и тот же набор ключей и единиц. Числа — взрослые RDA/AI (в основном
рекомендации ВОЗ/EFSA, витамин D — в МЕ, как привычнее на упаковках добавок).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Nutrient:
    key: str
    name: str
    unit: str
    rda_male: float
    rda_female: float
    #: верхний безопасный предел, если он практически достижим с добавками
    upper_limit: float | None = None


NUTRIENTS: tuple[Nutrient, ...] = (
    Nutrient("fiber", "Клетчатка", "г", 30, 25),
    Nutrient("iron", "Железо", "мг", 10, 18, upper_limit=45),
    Nutrient("calcium", "Кальций", "мг", 1000, 1000, upper_limit=2500),
    Nutrient("magnesium", "Магний", "мг", 400, 310),
    Nutrient("potassium", "Калий", "мг", 3500, 3500),
    Nutrient("zinc", "Цинк", "мг", 11, 8, upper_limit=40),
    Nutrient("selenium", "Селен", "мкг", 55, 55, upper_limit=400),
    Nutrient("iodine", "Йод", "мкг", 150, 150, upper_limit=1100),
    Nutrient("vit_a", "Витамин A", "мкг", 900, 700, upper_limit=3000),
    Nutrient("vit_c", "Витамин C", "мг", 90, 75, upper_limit=2000),
    Nutrient("vit_d", "Витамин D", "МЕ", 800, 800, upper_limit=4000),
    Nutrient("vit_e", "Витамин E", "мг", 15, 15, upper_limit=1000),
    Nutrient("vit_k", "Витамин K", "мкг", 120, 90),
    Nutrient("b1", "B1 (тиамин)", "мг", 1.2, 1.1),
    Nutrient("b2", "B2 (рибофлавин)", "мг", 1.3, 1.1),
    Nutrient("b3", "B3 (ниацин)", "мг", 16, 14, upper_limit=35),
    Nutrient("b5", "B5 (пантотен.)", "мг", 5, 5),
    Nutrient("b6", "B6 (пиридоксин)", "мг", 1.7, 1.5, upper_limit=100),
    Nutrient("b7", "B7 (биотин)", "мкг", 30, 30),
    Nutrient("b9", "B9 (фолат)", "мкг", 400, 400, upper_limit=1000),
    Nutrient("b12", "B12 (кобаламин)", "мкг", 2.4, 2.4),
    Nutrient("omega3", "Омега-3", "г", 1.6, 1.1),
    Nutrient("choline", "Холин", "мг", 550, 425, upper_limit=3500),
    Nutrient("phosphorus", "Фосфор", "мг", 700, 700, upper_limit=4000),
    Nutrient("copper", "Медь", "мг", 0.9, 0.9, upper_limit=10),
)

BY_KEY: dict[str, Nutrient] = {n.key: n for n in NUTRIENTS}
KEYS: tuple[str, ...] = tuple(n.key for n in NUTRIENTS)

#: Единицы дозы добавок. Латиницу и разнобой с упаковок приводим к кириллице справочника.
DOSE_UNITS: tuple[str, ...] = ("г", "мг", "мкг", "МЕ")

_DOSE_ALIASES = {
    "g": "г",
    "mg": "мг",
    "mcg": "мкг",
    "µg": "мкг",
    "ug": "мкг",
    "iu": "МЕ",
    "ме": "МЕ",
    "ед": "МЕ",
}

#: Единицы порции: массу и объём различаем только подписью — плотность мы не знаем.
PORTION_UNITS: tuple[str, ...] = ("г", "мл")

_PORTION_ALIASES = {
    "g": "г",
    "gr": "г",
    "gram": "г",
    "grams": "г",
    "гр": "г",
    "ml": "мл",
    "milliliter": "мл",
    "millilitre": "мл",
    "мл.": "мл",
}


def normalize_dose_unit(value: object) -> object:
    """`mg`, `mcg`, `IU` с упаковки → единицы справочника. Незнакомое отдаём как есть:
    отбраковкой занимается схема, а не этот словарь."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    return _DOSE_ALIASES.get(cleaned.lower(), cleaned)


def normalize_portion_unit(value: object) -> object:
    """`g`/`ml`/`гр` → `г`/`мл`."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    return _PORTION_ALIASES.get(cleaned.lower(), cleaned)


def rda(key: str, sex: str) -> float:
    """Суточная норма нутриента для пола (`m`/`f`, всё остальное — как мужская)."""
    n = BY_KEY[key]
    return n.rda_female if sex == "f" else n.rda_male


def rda_map(sex: str) -> dict[str, float]:
    return {n.key: rda(n.key, sex) for n in NUTRIENTS}


def clean_micros(raw: object) -> dict[str, float]:
    """Оставить только известные ключи и неотрицательные числа.

    Данные приходят от ИИ и из клиента, поэтому любой мусор отбрасываем молча —
    частично разобранное блюдо полезнее, чем ошибка на весь запрос.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in BY_KEY:
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if num < 0 or num != num:  # NaN
            continue
        out[key] = num
    return out


def scale_micros(micros: dict[str, float], factor: float) -> dict[str, float]:
    return {k: v * factor for k, v in micros.items()}


def sum_micros(items: list[dict[str, float]]) -> dict[str, float]:
    total: dict[str, float] = {}
    for item in items:
        for key, value in item.items():
            total[key] = total.get(key, 0.0) + value
    return total
