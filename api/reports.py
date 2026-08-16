"""Агрегация: дневные итоги, отчёты за период, ряды для графиков."""

from __future__ import annotations

from datetime import date

from .nutrition.catalog import BY_KEY, KEYS, NUTRIENTS, sum_micros
from .nutrition.sources import advice_for, sources_for

#: Порог, ниже которого нутриент считается недобранным.
DEFICIT_THRESHOLD = 0.8

#: Коэффициенты к базовой единице нутриента.
_UNIT_TO_BASE: dict[tuple[str, str], float] = {
    ("г", "мг"): 1000.0,
    ("г", "мкг"): 1_000_000.0,
    ("мг", "г"): 0.001,
    ("мг", "мкг"): 1000.0,
    ("мкг", "г"): 0.000001,
    ("мкг", "мг"): 0.001,
}


def convert_dose(dose: float, unit: str, target_unit: str) -> float | None:
    """Перевести дозу добавки в единицу нутриента. None — если перевод невозможен."""
    unit = unit.strip()
    target_unit = target_unit.strip()
    if unit == target_unit:
        return dose
    factor = _UNIT_TO_BASE.get((unit, target_unit))
    return dose * factor if factor is not None else None


def supplements_for_day(supplements: list[dict], day: date) -> list[dict]:
    """Какие добавки принимаются в конкретный день.

    `every_other_day` привязываем к чётности порядкового номера дня — так добавка
    стабильно попадает в один и тот же набор дней, а не «половинкой дозы» в каждый.
    """
    out: list[dict] = []
    for supplement in supplements:
        if not supplement.get("active", True):
            continue
        frequency = supplement.get("frequency") or "daily"
        if frequency == "every_other_day" and day.toordinal() % 2 != 0:
            continue
        out.append(supplement)
    return out


def supplement_micros(supplements: list[dict]) -> dict[str, float]:
    """Вклад добавок в микронутриенты. Добавки без привязки к нутриенту игнорируем."""
    totals: dict[str, float] = {}
    for supplement in supplements:
        key = supplement.get("nutrient_key")
        if not key or key not in BY_KEY:
            continue
        converted = convert_dose(float(supplement["dose"]), supplement["unit"], BY_KEY[key].unit)
        if converted is None:
            continue
        totals[key] = totals.get(key, 0.0) + converted
    return totals


def day_totals(meals: list[dict], workouts: list[dict]) -> dict:
    return {
        "calories_eaten": sum(m["calories_kcal"] for m in meals),
        "protein_g": sum(m["protein_g"] for m in meals),
        "fat_g": sum(m["fat_g"] for m in meals),
        "carbs_g": sum(m["carbs_g"] for m in meals),
        "fiber_g": sum(m["fiber_g"] for m in meals),
        "calories_burned": sum(w["kcal"] for w in workouts),
        "meals_count": len(meals),
        "workouts_count": len(workouts),
    }


def micro_coverage(meals: list[dict]) -> float:
    """Доля блюд, у которых вообще есть данные по микронутриентам.

    Без этого числа отчёт врёт: у блюда, внесённого вручную, микронутриенты нулевые,
    и дефицит выглядит страшнее, чем есть.
    """
    if not meals:
        return 1.0
    with_micros = sum(1 for m in meals if m.get("micros"))
    return with_micros / len(meals)


def micro_rows(
    consumed: dict[str, float], norms: dict[str, float]
) -> list[dict]:
    rows: list[dict] = []
    for nutrient in NUTRIENTS:
        norm = norms.get(nutrient.key, 0.0)
        value = consumed.get(nutrient.key, 0.0)
        pct = (value / norm * 100.0) if norm > 0 else 0.0
        rows.append(
            {
                "key": nutrient.key,
                "name": nutrient.name,
                "unit": nutrient.unit,
                "value": round(value, 2),
                "norm": norm,
                "pct": round(pct),
                "over_limit": bool(
                    nutrient.upper_limit is not None and value > nutrient.upper_limit
                ),
            }
        )
    return rows


def deficits(rows: list[dict], limit: int = 5) -> list[dict]:
    """Недобранные нутриенты — сильнее всего недобранные первыми."""
    candidates = [r for r in rows if r["pct"] < DEFICIT_THRESHOLD * 100 and r["norm"] > 0]
    candidates.sort(key=lambda r: r["pct"])
    out: list[dict] = []
    for row in candidates[:limit]:
        gap = max(row["norm"] - row["value"], 0.0)
        out.append(
            {
                "key": row["key"],
                "name": row["name"],
                "unit": row["unit"],
                "gap": round(gap, 2),
                "pct": row["pct"],
                "sources": sources_for(row["key"]),
                "advice": advice_for(row["key"]),
            }
        )
    return out


def excesses(rows: list[dict]) -> list[dict]:
    """Нутриенты выше безопасного верхнего предела — их стоит показать отдельно."""
    return [
        {"key": r["key"], "name": r["name"], "unit": r["unit"], "value": r["value"], "pct": r["pct"]}
        for r in rows
        if r["over_limit"]
    ]


def build_day_report(
    *,
    day: date,
    meals: list[dict],
    workouts: list[dict],
    supplements: list[dict],
    norms: dict,
) -> dict:
    totals = day_totals(meals, workouts)
    taken = supplements_for_day(supplements, day)
    consumed = sum_micros(
        [m.get("micros", {}) for m in meals] + [supplement_micros(taken)]
    )
    consumed.setdefault("fiber", 0.0)
    consumed["fiber"] = max(consumed.get("fiber", 0.0), totals["fiber_g"])

    rows = micro_rows(consumed, norms["micros"])
    norm_calories = norms["calories"]
    net = totals["calories_eaten"] - totals["calories_burned"]

    return {
        "day": day.isoformat(),
        "totals": {
            **{k: round(v, 1) if isinstance(v, float) else v for k, v in totals.items()},
            "calories_net": round(net, 1),
            "balance_vs_norm": round(net - norm_calories, 1),
        },
        "norms": norms,
        "macros": [
            {
                "key": "calories",
                "name": "Калории с нагрузкой",
                "unit": "ккал",
                "value": round(net),
                "norm": norm_calories,
                "pct": _pct(net, norm_calories),
            },
            {
                "key": "protein",
                "name": "Белки",
                "unit": "г",
                "value": round(totals["protein_g"]),
                "norm": norms["protein_g"],
                "pct": _pct(totals["protein_g"], norms["protein_g"]),
            },
            {
                "key": "fat",
                "name": "Жиры",
                "unit": "г",
                "value": round(totals["fat_g"]),
                "norm": norms["fat_g"],
                "pct": _pct(totals["fat_g"], norms["fat_g"]),
            },
            {
                "key": "carbs",
                "name": "Углеводы",
                "unit": "г",
                "value": round(totals["carbs_g"]),
                "norm": norms["carbs_g"],
                "pct": _pct(totals["carbs_g"], norms["carbs_g"]),
            },
        ],
        "micros": rows,
        "deficits": deficits(rows),
        "excesses": excesses(rows),
        "supplements_taken": taken,
        "micro_coverage": round(micro_coverage(meals), 2),
    }


def build_period_report(
    *,
    days: list[str],
    meals: list[dict],
    workouts: list[dict],
    supplements: list[dict],
    norms: dict,
) -> dict:
    """Отчёт за период: всё в пересчёте на средний день, чтобы сравнивать с нормой."""
    day_count = max(len(days), 1)

    meals_by_day: dict[str, list[dict]] = {d: [] for d in days}
    for meal in meals:
        meals_by_day.setdefault(meal["day"], []).append(meal)

    supplement_parts: list[dict[str, float]] = []
    for day_str in days:
        taken = supplements_for_day(supplements, date.fromisoformat(day_str))
        supplement_parts.append(supplement_micros(taken))

    consumed_total = sum_micros([m.get("micros", {}) for m in meals] + supplement_parts)
    fiber_total = sum(m["fiber_g"] for m in meals)
    consumed_total["fiber"] = max(consumed_total.get("fiber", 0.0), fiber_total)

    avg_consumed = {k: v / day_count for k, v in consumed_total.items()}
    rows = micro_rows(avg_consumed, norms["micros"])

    calories_eaten = sum(m["calories_kcal"] for m in meals)
    calories_burned = sum(w["kcal"] for w in workouts)
    protein_total = sum(m["protein_g"] for m in meals)

    logged_days = sorted({m["day"] for m in meals})

    return {
        "days": days,
        "day_count": day_count,
        "logged_days": len(logged_days),
        "averages": {
            "calories_eaten": round(calories_eaten / day_count),
            "calories_burned": round(calories_burned / day_count),
            "calories_net": round((calories_eaten - calories_burned) / day_count),
            "protein_g": round(protein_total / day_count),
        },
        "norms": norms,
        "micros": rows,
        "deficits": deficits(rows, limit=6),
        "excesses": excesses(rows),
        "micro_coverage": round(micro_coverage(meals), 2),
    }


def build_progress(
    *,
    days: list[str],
    meals: list[dict],
    workouts: list[dict],
    norms: dict,
    previous: dict | None = None,
) -> dict:
    """Ряды для четырёх графиков прогресса плюс плитки статистики."""
    eaten: dict[str, float] = {d: 0.0 for d in days}
    protein: dict[str, float] = {d: 0.0 for d in days}
    burned: dict[str, float] = {d: 0.0 for d in days}
    macro_ok: dict[str, bool] = {d: False for d in days}

    per_day_macros: dict[str, dict[str, float]] = {
        d: {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0} for d in days
    }

    for meal in meals:
        day = meal["day"]
        if day not in eaten:
            continue
        eaten[day] += meal["calories_kcal"]
        protein[day] += meal["protein_g"]
        per_day_macros[day]["protein_g"] += meal["protein_g"]
        per_day_macros[day]["fat_g"] += meal["fat_g"]
        per_day_macros[day]["carbs_g"] += meal["carbs_g"]

    by_kind: dict[str, float] = {}
    for workout in workouts:
        day = workout["day"]
        if day in burned:
            burned[day] += workout["kcal"]
        by_kind[workout["kind_name"]] = by_kind.get(workout["kind_name"], 0.0) + workout["kcal"]

    for day in days:
        macros = per_day_macros[day]
        macro_ok[day] = all(
            _within(macros[key], norms[key]) for key in ("protein_g", "fat_g", "carbs_g")
        )

    active_days = [d for d in days if eaten[d] > 0]
    active_count = max(len(active_days), 1)
    avg_calories = sum(eaten[d] for d in active_days) / active_count
    avg_protein = sum(protein[d] for d in active_days) / active_count

    net = {d: eaten[d] - burned[d] - norms["calories"] for d in days}

    tiles = [
        {
            "key": "avg_calories",
            "label": "Средние калории",
            "value": round(avg_calories),
            "unit": "ккал",
            "delta": _delta(avg_calories, (previous or {}).get("avg_calories")),
            "hint": _delta_hint(avg_calories, (previous or {}).get("avg_calories"), "к прошлому периоду"),
        },
        {
            "key": "avg_protein",
            "label": "Средний белок",
            "value": round(avg_protein),
            "unit": "г",
            "delta": round(avg_protein - norms["protein_g"]),
            "hint": f"{_signed(round(avg_protein - norms['protein_g']))} г к цели",
        },
        {
            "key": "workouts",
            "label": "Тренировок",
            "value": len(workouts),
            "unit": "",
            "delta": None,
            "hint": f"{round(sum(burned.values()))} ккал потрачено",
        },
        {
            "key": "macro_days",
            "label": "Дней в норме БЖУ",
            "value": sum(1 for d in days if macro_ok[d]),
            "unit": f"/{len(days)}",
            "delta": None,
            "hint": f"было {(previous or {}).get('macro_days', 0)}/{len(days)}",
        },
    ]

    return {
        "days": days,
        "calories": [round(eaten[d]) for d in days],
        "protein": [round(protein[d]) for d in days],
        "burned": [round(burned[d]) for d in days],
        "net": [round(net[d]) for d in days],
        "norms": norms,
        "avg_calories": round(avg_calories),
        "avg_protein": round(avg_protein),
        "burned_by_kind": [
            {"name": name, "kcal": round(kcal)}
            for name, kcal in sorted(by_kind.items(), key=lambda kv: -kv[1])
        ],
        "tiles": tiles,
        "summary": _progress_summary(eaten, burned, norms["calories"], days),
    }


def _pct(value: float, norm: float) -> int:
    return round(value / norm * 100) if norm > 0 else 0


def _within(value: float, norm: float, tolerance: float = 0.1) -> bool:
    if norm <= 0:
        return False
    return abs(value - norm) / norm <= tolerance


def _signed(value: float) -> str:
    return f"+{value:g}" if value > 0 else f"{value:g}"


def _delta(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    return round(current - previous)


def _delta_hint(current: float, previous: float | None, suffix: str) -> str:
    if previous is None:
        return "нет данных за прошлый период"
    return f"{_signed(round(current - previous))} {suffix}"


def _progress_summary(
    eaten: dict[str, float], burned: dict[str, float], norm: float, days: list[str]
) -> str:
    active = [d for d in days if eaten[d] > 0]
    if not active:
        return "За период ещё нет записей."
    nets = [eaten[d] - burned[d] - norm for d in active]
    avg = sum(nets) / len(nets)
    deficit_days = sum(1 for n in nets if n < 0)
    direction = "дефицит" if avg < 0 else "профицит"
    return (
        f"В среднем {direction} {abs(round(avg))} ккал в день; "
        f"дней с дефицитом — {deficit_days} из {len(active)}."
    )


__all__ = [
    "KEYS",
    "build_day_report",
    "build_period_report",
    "build_progress",
    "convert_dose",
    "day_totals",
    "deficits",
    "micro_rows",
    "supplement_micros",
    "supplements_for_day",
]
