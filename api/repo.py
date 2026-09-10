"""Работа с данными: блюда, нагрузки, добавки, профиль, продукты.

Слой между роутерами и SQLite. Роутеры не знают про SQL, тесты работают с этими
функциями напрямую.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from .db import connect, dump_json, load_json, now_iso
from .nutrition import workouts as wk
from .nutrition.catalog import clean_micros, scale_micros, sum_micros
from .nutrition.norms import compute_norms

# ─────────────────────────────── профиль ────────────────────────────────

PROFILE_DEFAULTS: dict[str, Any] = {
    "sex": "m",
    "age": 30,
    "height_cm": 178.0,
    "weight_kg": 75.0,
    "activity": 1.55,
    "body_fat_pct": None,
    "goal": "maintain",
    "first_name": None,
}


def get_profile(user_id: int, *, first_name: str | None = None) -> dict:
    """Профиль пользователя; при первом обращении создаётся из дефолтов."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            # OR IGNORE, а не голый INSERT: при первом открытии мини-аппа фронт
            # шлёт несколько запросов разом, и все они видят пустой профиль —
            # второй вставке иначе прилетает UNIQUE constraint failed.
            conn.execute(
                """
                INSERT OR IGNORE INTO user_profile
                    (user_id, sex, age, height_cm, weight_kg, activity, body_fat_pct, goal, first_name, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    PROFILE_DEFAULTS["sex"],
                    PROFILE_DEFAULTS["age"],
                    PROFILE_DEFAULTS["height_cm"],
                    PROFILE_DEFAULTS["weight_kg"],
                    PROFILE_DEFAULTS["activity"],
                    None,
                    PROFILE_DEFAULTS["goal"],
                    first_name,
                    now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM user_profile WHERE user_id = ?", (user_id,)
            ).fetchone()
        elif first_name and not row["first_name"]:
            conn.execute(
                "UPDATE user_profile SET first_name = ? WHERE user_id = ?",
                (first_name, user_id),
            )
    profile = _profile_row(row)
    if first_name and not profile["first_name"]:
        profile["first_name"] = first_name
    return profile


#: Необязательные поля профиля — их можно сбросить, передав null.
#: calories_override в NULL означает «вернуться к расчётной норме».
NULLABLE_PROFILE_FIELDS = frozenset({"body_fat_pct", "calories_override"})


def update_profile(user_id: int, patch: dict) -> dict:
    fields = {
        k: v for k, v in patch.items() if v is not None or k in NULLABLE_PROFILE_FIELDS
    }
    get_profile(user_id)  # гарантируем, что строка существует
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with connect() as conn:
            conn.execute(
                f"UPDATE user_profile SET {assignments}, updated_at = ? WHERE user_id = ?",
                (*fields.values(), now_iso(), user_id),
            )
    return get_profile(user_id)


def _profile_row(row: sqlite3.Row) -> dict:
    return {
        "user_id": row["user_id"],
        "sex": row["sex"],
        "age": row["age"],
        "height_cm": row["height_cm"],
        "weight_kg": row["weight_kg"],
        "activity": row["activity"],
        "body_fat_pct": row["body_fat_pct"],
        "calories_override": row["calories_override"],
        "goal": row["goal"],
        "first_name": row["first_name"],
    }


def norms_for(profile: dict) -> dict:
    return compute_norms(
        sex=profile["sex"],
        age=profile["age"],
        height_cm=profile["height_cm"],
        weight_kg=profile["weight_kg"],
        activity=profile["activity"],
        goal=profile["goal"],
        calories_override=profile.get("calories_override"),
    ).as_dict()


# ──────────────────────────────── блюда ─────────────────────────────────


def _meal_row(row: sqlite3.Row, items: list[dict] | None = None) -> dict:
    return {
        "id": row["id"],
        "day": row["day"],
        "name": row["food_name"],
        "calories_kcal": float(row["calories_kcal"] or 0.0),
        "protein_g": float(row["protein_g"] or 0.0),
        "fat_g": float(row["fat_g"] or 0.0),
        "carbs_g": float(row["carbs_g"] or 0.0),
        "fiber_g": float(row["fiber_g"] or 0.0),
        "portion_g": row["portion_g"],
        "portion_unit": row["portion_unit"] or "г",
        "meal_type": row["meal_type"] or "other",
        "eaten_at": row["eaten_at"],
        "ingredients": row["ingredients"],
        "source": row["source"],
        "micros": clean_micros(load_json(row["micros_json"])),
        "items": items or [],
    }


def _items_for(conn: sqlite3.Connection, entry_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, grams, per100_json FROM meal_items WHERE entry_id = ? ORDER BY position, id",
        (entry_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "grams": float(r["grams"]),
            "per100": load_json(r["per100_json"]),
        }
        for r in rows
    ]


def _replace_items(conn: sqlite3.Connection, entry_id: int, items: list[dict]) -> None:
    conn.execute("DELETE FROM meal_items WHERE entry_id = ?", (entry_id,))
    for position, item in enumerate(items):
        conn.execute(
            "INSERT INTO meal_items (entry_id, name, grams, per100_json, position) VALUES (?, ?, ?, ?, ?)",
            (
                entry_id,
                item["name"],
                float(item["grams"]),
                dump_json(item.get("per100") or {}) or "{}",
                position,
            ),
        )


def totals_from_items(items: list[dict]) -> dict:
    """Пересчитать КБЖУ и микронутриенты блюда из ингредиентов.

    Единственный источник истины, когда у блюда есть состав: пользователь двигает
    граммовку в шите, и итог должен пересчитаться, а не остаться от ИИ.
    """
    totals = {"calories_kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0, "fiber_g": 0.0}
    micro_parts: list[dict[str, float]] = []
    portion = 0.0
    for item in items:
        grams = float(item.get("grams") or 0.0)
        portion += grams
        factor = grams / 100.0
        per100 = item.get("per100") or {}
        totals["calories_kcal"] += float(per100.get("calories_kcal") or 0.0) * factor
        totals["protein_g"] += float(per100.get("protein_g") or 0.0) * factor
        totals["fat_g"] += float(per100.get("fat_g") or 0.0) * factor
        totals["carbs_g"] += float(per100.get("carbs_g") or 0.0) * factor
        totals["fiber_g"] += float(per100.get("fiber_g") or 0.0) * factor
        micro_parts.append(scale_micros(clean_micros(per100), factor))
    totals["micros"] = sum_micros(micro_parts)
    totals["portion_g"] = portion
    return totals


def add_meal(user_id: int, day: str, payload: dict) -> dict:
    items = payload.get("items") or []
    data = dict(payload)
    if items:
        computed = totals_from_items(items)
        data.update(
            {
                "calories_kcal": computed["calories_kcal"],
                "protein_g": computed["protein_g"],
                "fat_g": computed["fat_g"],
                "carbs_g": computed["carbs_g"],
                "fiber_g": computed["fiber_g"],
                "micros": computed["micros"],
                "portion_g": data.get("portion_g") or computed["portion_g"],
            }
        )
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO protein_entries
                (user_id, day, food_name, protein_g, calories_kcal, fat_g, carbs_g, fiber_g,
                 micros_json, portion_g, portion_unit, meal_type, eaten_at, ingredients, source,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                day,
                data["name"].strip(),
                float(data.get("protein_g") or 0.0),
                data.get("calories_kcal"),
                data.get("fat_g"),
                data.get("carbs_g"),
                data.get("fiber_g"),
                dump_json(clean_micros(data.get("micros"))),
                data.get("portion_g"),
                data.get("portion_unit") or "г",
                data.get("meal_type") or "other",
                data.get("eaten_at"),
                data.get("ingredients"),
                data.get("source") or "webapp",
                now_iso(),
            ),
        )
        entry_id = int(cur.lastrowid)
        if items:
            _replace_items(conn, entry_id, items)
    return get_meal(user_id, entry_id)  # type: ignore[return-value]


def get_meal(user_id: int, meal_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM protein_entries WHERE id = ? AND user_id = ?",
            (meal_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return _meal_row(row, _items_for(conn, meal_id))


#: Поля блюда, которые PATCH может сбросить в NULL.
NULLABLE_MEAL_FIELDS = frozenset({"eaten_at", "ingredients", "portion_g"})


def update_meal(user_id: int, meal_id: int, patch: dict) -> dict | None:
    current = get_meal(user_id, meal_id)
    if current is None:
        return None

    items = patch.get("items")
    # Роутер отдаёт только явно переданные поля (exclude_unset), поэтому None здесь —
    # осознанное «очистить». Разрешаем это только для колонок, допускающих NULL.
    data = {
        k: v
        for k, v in patch.items()
        if k != "items" and (v is not None or k in NULLABLE_MEAL_FIELDS)
    }

    if items:
        # Состав — источник истины для итогов, как и при создании блюда.
        computed = totals_from_items(items)
        data.update(
            {
                "calories_kcal": computed["calories_kcal"],
                "protein_g": computed["protein_g"],
                "fat_g": computed["fat_g"],
                "carbs_g": computed["carbs_g"],
                "fiber_g": computed["fiber_g"],
                "micros": computed["micros"],
                "portion_g": data.get("portion_g") or computed["portion_g"],
            }
        )
    # Пустой список items = «убрать разбор по ингредиентам», итоги при этом не трогаем.

    column_map = {
        "name": "food_name",
        "calories_kcal": "calories_kcal",
        "protein_g": "protein_g",
        "fat_g": "fat_g",
        "carbs_g": "carbs_g",
        "fiber_g": "fiber_g",
        "portion_g": "portion_g",
        "portion_unit": "portion_unit",
        "meal_type": "meal_type",
        "eaten_at": "eaten_at",
        "ingredients": "ingredients",
        "day": "day",
    }
    assignments: list[str] = []
    values: list[Any] = []
    for key, column in column_map.items():
        if key in data:
            assignments.append(f"{column} = ?")
            values.append(data[key])
    if "micros" in data:
        assignments.append("micros_json = ?")
        values.append(dump_json(clean_micros(data["micros"])))

    with connect() as conn:
        if assignments:
            conn.execute(
                f"UPDATE protein_entries SET {', '.join(assignments)}, updated_at = ? WHERE id = ? AND user_id = ?",
                (*values, now_iso(), meal_id, user_id),
            )
        if items is not None:
            _replace_items(conn, meal_id, items)
    return get_meal(user_id, meal_id)


def delete_meal(user_id: int, meal_id: int) -> bool:
    with connect() as conn:
        # Сначала владельческая проверка — иначе чужой состав можно стереть, зная id.
        cur = conn.execute(
            "DELETE FROM protein_entries WHERE id = ? AND user_id = ?", (meal_id, user_id)
        )
        if cur.rowcount == 0:
            return False
        # ON DELETE CASCADE уже сработал; явный DELETE — страховка на случай выключенного pragma.
        conn.execute("DELETE FROM meal_items WHERE entry_id = ?", (meal_id,))
        return True


def meals_for_day(user_id: int, day: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM protein_entries WHERE user_id = ? AND day = ? ORDER BY COALESCE(eaten_at, '99:99'), id",
            (user_id, day),
        ).fetchall()
        return [_meal_row(r, _items_for(conn, r["id"])) for r in rows]


def meals_for_range(user_id: int, start: str, end: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM protein_entries WHERE user_id = ? AND day BETWEEN ? AND ? ORDER BY day, id",
            (user_id, start, end),
        ).fetchall()
        return [_meal_row(r) for r in rows]


# ─────────────────────────────── нагрузки ───────────────────────────────


def _workout_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "day": row["day"],
        "kind": row["kind"],
        "kind_name": wk.name_for(row["kind"]),
        "icon": wk.icon_for(row["kind"]),
        "minutes": float(row["minutes"]),
        "kcal": float(row["kcal"]),
        "note": row["note"],
        "done_at": row["done_at"],
    }


def add_workout(user_id: int, day: str, payload: dict, weight_kg: float) -> dict:
    kcal = payload.get("kcal")
    if kcal is None:
        kcal = wk.kcal_for(kind=payload["kind"], minutes=payload["minutes"], weight_kg=weight_kg)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO workouts (user_id, day, kind, minutes, kcal, note, done_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                day,
                payload["kind"],
                float(payload["minutes"]),
                float(kcal),
                payload.get("note"),
                payload.get("done_at"),
            ),
        )
        row = conn.execute("SELECT * FROM workouts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _workout_row(row)


def update_workout(user_id: int, workout_id: int, patch: dict, weight_kg: float) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM workouts WHERE id = ? AND user_id = ?", (workout_id, user_id)
        ).fetchone()
        if row is None:
            return None
        kind = patch.get("kind") or row["kind"]
        minutes = patch.get("minutes") if patch.get("minutes") is not None else row["minutes"]
        kcal = patch.get("kcal")
        if kcal is None:
            # Тип или длительность изменились — расход пересчитываем, а не тянем старый.
            kcal = (
                wk.kcal_for(kind=kind, minutes=minutes, weight_kg=weight_kg)
                if (patch.get("kind") or patch.get("minutes") is not None)
                else row["kcal"]
            )
        conn.execute(
            "UPDATE workouts SET kind = ?, minutes = ?, kcal = ?, note = ?, done_at = ? WHERE id = ? AND user_id = ?",
            (
                kind,
                float(minutes),
                float(kcal),
                patch["note"] if "note" in patch else row["note"],
                patch["done_at"] if "done_at" in patch else row["done_at"],
                workout_id,
                user_id,
            ),
        )
        updated = conn.execute("SELECT * FROM workouts WHERE id = ?", (workout_id,)).fetchone()
    return _workout_row(updated)


def delete_workout(user_id: int, workout_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM workouts WHERE id = ? AND user_id = ?", (workout_id, user_id)
        )
        return cur.rowcount > 0


def workouts_for_day(user_id: int, day: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workouts WHERE user_id = ? AND day = ? ORDER BY COALESCE(done_at, '99:99'), id",
            (user_id, day),
        ).fetchall()
    return [_workout_row(r) for r in rows]


def workouts_for_range(user_id: int, start: str, end: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workouts WHERE user_id = ? AND day BETWEEN ? AND ? ORDER BY day, id",
            (user_id, start, end),
        ).fetchall()
    return [_workout_row(r) for r in rows]


# ─────────────────────────── шаблоны нагрузки ───────────────────────────


def list_templates(user_id: int, weight_kg: float) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM workout_templates WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        if not rows:
            for name, kind, minutes in wk.STARTER_TEMPLATES:
                conn.execute(
                    "INSERT INTO workout_templates (user_id, name, kind, minutes) VALUES (?, ?, ?, ?)",
                    (user_id, name, kind, minutes),
                )
            rows = conn.execute(
                "SELECT * FROM workout_templates WHERE user_id = ? ORDER BY id", (user_id,)
            ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "kind": r["kind"],
            "minutes": float(r["minutes"]),
            "icon": wk.icon_for(r["kind"]),
            "kcal": round(wk.kcal_for(kind=r["kind"], minutes=r["minutes"], weight_kg=weight_kg)),
        }
        for r in rows
    ]


def add_template(user_id: int, payload: dict, weight_kg: float) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO workout_templates (user_id, name, kind, minutes) VALUES (?, ?, ?, ?)",
            (user_id, payload["name"].strip(), payload["kind"], float(payload["minutes"])),
        )
        template_id = int(cur.lastrowid)
    return {
        "id": template_id,
        "name": payload["name"].strip(),
        "kind": payload["kind"],
        "minutes": float(payload["minutes"]),
        "icon": wk.icon_for(payload["kind"]),
        "kcal": round(wk.kcal_for(kind=payload["kind"], minutes=payload["minutes"], weight_kg=weight_kg)),
    }


def delete_template(user_id: int, template_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM workout_templates WHERE id = ? AND user_id = ?", (template_id, user_id)
        )
        return cur.rowcount > 0


# ──────────────────────────────── добавки ───────────────────────────────


def _supplement_row(row: sqlite3.Row) -> dict:
    # Старые записи заведены до появления долей приёма: для них этикетка и приём
    # равны единице, то есть доза и есть то, что принимается.
    label = float(row["label_serving"] or 1.0) or 1.0
    taken = float(row["taken_serving"] or 1.0)
    dose = float(row["dose"])
    return {
        "id": row["id"],
        "name": row["name"],
        "group_name": row["group_name"],
        "nutrient_key": row["nutrient_key"],
        "dose": dose,
        "label_serving": label,
        "taken_serving": taken,
        "effective_dose": round(dose * taken / label, 4),
        "unit": row["unit"],
        "when_label": row["when_label"],
        "frequency": row["frequency"],
        "active": bool(row["active"]),
    }


def list_supplements(user_id: int, *, only_active: bool = False) -> list[dict]:
    sql = "SELECT * FROM supplements WHERE user_id = ?"
    if only_active:
        sql += " AND active = 1"
    sql += " ORDER BY id"
    with connect() as conn:
        rows = conn.execute(sql, (user_id,)).fetchall()
    return [_supplement_row(r) for r in rows]


def add_supplement(user_id: int, payload: dict) -> dict:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO supplements
                (user_id, name, group_name, nutrient_key, dose, unit, label_serving,
                 taken_serving, when_label, frequency, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload["name"].strip(),
                (payload.get("group_name") or "").strip() or None,
                payload.get("nutrient_key"),
                float(payload["dose"]),
                payload["unit"],
                float(payload.get("label_serving") or 1.0),
                float(payload.get("taken_serving") or 1.0),
                payload.get("when_label"),
                payload.get("frequency") or "daily",
                1 if payload.get("active", True) else 0,
            ),
        )
        row = conn.execute("SELECT * FROM supplements WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _supplement_row(row)


#: Поля добавки, которые PATCH может сбросить в NULL.
NULLABLE_SUPPLEMENT_FIELDS = frozenset({"nutrient_key", "when_label", "group_name"})


def update_supplement(user_id: int, supplement_id: int, patch: dict) -> dict | None:
    fields = {
        k: v for k, v in patch.items() if v is not None or k in NULLABLE_SUPPLEMENT_FIELDS
    }
    if "active" in fields:
        fields["active"] = 1 if fields["active"] else 0
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM supplements WHERE id = ? AND user_id = ?", (supplement_id, user_id)
        ).fetchone()
        if row is None:
            return None
        if fields:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE supplements SET {assignments} WHERE id = ? AND user_id = ?",
                (*fields.values(), supplement_id, user_id),
            )
        updated = conn.execute(
            "SELECT * FROM supplements WHERE id = ?", (supplement_id,)
        ).fetchone()
    return _supplement_row(updated)


def delete_supplement(user_id: int, supplement_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM supplements WHERE id = ? AND user_id = ?", (supplement_id, user_id)
        )
        return cur.rowcount > 0


def delete_supplements(user_id: int, ids: list[int]) -> int:
    """Удалить несколько веществ разом — банку из списка убирают целиком."""
    if not ids:
        return 0
    placeholders = ", ".join("?" for _ in ids)
    with connect() as conn:
        cur = conn.execute(
            f"DELETE FROM supplements WHERE user_id = ? AND id IN ({placeholders})",
            (user_id, *ids),
        )
        return cur.rowcount


# ──────────────────────────────── продукты ──────────────────────────────


def _product_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "protein_g": float(row["protein_g"] or 0.0),
        "calories_kcal": row["calories_kcal"],
        "fat_g": row["fat_g"],
        "carbs_g": row["carbs_g"],
        "fiber_g": row["fiber_g"],
        "portion_g": row["portion_g"],
        "portion_unit": row["portion_unit"] or "г",
        "micros": clean_micros(load_json(row["micros_json"])),
    }


def search_products(user_id: int, query: str, limit: int = 20) -> list[dict]:
    """Поиск по названию. Фильтруем в Python: SQLite LIKE не знает регистр кириллицы."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_products WHERE user_id = ? ORDER BY name", (user_id,)
        ).fetchall()
    needle = query.strip().casefold()
    matched = [r for r in rows if not needle or needle in (r["name"] or "").casefold()]
    return [_product_row(r) for r in matched[:limit]]


def find_product_by_name(user_id: int, name: str) -> dict | None:
    """Поиск по имени без учёта регистра — SQLite не знает регистра кириллицы."""
    needle = name.strip().casefold()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_products WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    return next((_product_row(r) for r in rows if (r["name"] or "").casefold() == needle), None)


def add_product(user_id: int, payload: dict) -> dict:
    """Сохранить продукт. Одноимённый обновляем, а не плодим: пользователь отмечает
    «сохранить как своё блюдо» при каждом повторе, а список продуктов должен
    оставаться коротким — и оценивать состав ИИ дважды незачем."""
    existing = find_product_by_name(user_id, payload["name"])
    if existing is not None:
        patch = {k: v for k, v in payload.items() if v is not None}
        # Уже известный состав не затираем пустым: ручная запись микронутриентов не даёт.
        if not patch.get("micros"):
            patch.pop("micros", None)
        return update_product(user_id, existing["id"], patch) or existing

    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO saved_products
                (user_id, name, protein_g, calories_kcal, fat_g, carbs_g, fiber_g, micros_json,
                 portion_g, portion_unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                payload["name"].strip(),
                float(payload.get("protein_g") or 0.0),
                payload.get("calories_kcal"),
                payload.get("fat_g"),
                payload.get("carbs_g"),
                payload.get("fiber_g"),
                dump_json(clean_micros(payload.get("micros"))),
                payload.get("portion_g"),
                payload.get("portion_unit") or "г",
            ),
        )
        row = conn.execute("SELECT * FROM saved_products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _product_row(row)


def get_product(user_id: int, product_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM saved_products WHERE id = ? AND user_id = ?", (product_id, user_id)
        ).fetchone()
    return _product_row(row) if row else None


#: Поля продукта, которые PATCH может сбросить в NULL.
NULLABLE_PRODUCT_FIELDS = frozenset({"calories_kcal", "fat_g", "carbs_g", "fiber_g", "portion_g"})


def update_product(user_id: int, product_id: int, patch: dict) -> dict | None:
    fields = {
        k: v
        for k, v in patch.items()
        if k != "micros" and (v is not None or k in NULLABLE_PRODUCT_FIELDS)
    }
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM saved_products WHERE id = ? AND user_id = ?", (product_id, user_id)
        ).fetchone()
        if row is None:
            return None
        assignments = [f"{k} = ?" for k in fields]
        values: list[Any] = list(fields.values())
        if "micros" in patch and patch["micros"] is not None:
            assignments.append("micros_json = ?")
            values.append(dump_json(clean_micros(patch["micros"])))
        if assignments:
            conn.execute(
                f"UPDATE saved_products SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
                (*values, product_id, user_id),
            )
    return get_product(user_id, product_id)


def products_without_micros(user_id: int, limit: int) -> list[dict]:
    """Продукты, у которых состав ещё не известен — кандидаты на ИИ-оценку."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_products WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
    missing = [_product_row(r) for r in rows]
    return [p for p in missing if not p["micros"]][:limit]


def delete_product(user_id: int, product_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM saved_products WHERE id = ? AND user_id = ?", (product_id, user_id)
        )
        return cur.rowcount > 0


# ──────────────────────────────── даты ──────────────────────────────────


def range_bounds(end: date, range_name: str) -> tuple[date, date]:
    """Границы периода, включительно с обоих концов."""
    days = 7 if range_name == "week" else 30
    return end - timedelta(days=days - 1), end


def day_strings(start: date, end: date) -> list[str]:
    out: list[str] = []
    cursor = start
    while cursor <= end:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out
