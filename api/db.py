"""Доступ к SQLite и аддитивные миграции поверх схемы бота.

База общая с ботом (`storage.py`). Все миграции только добавляют колонки и таблицы,
поэтому старый код бота продолжает работать без изменений.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DB_PATH = "data/protein.sqlite3"


def db_path() -> Path:
    return Path(os.environ.get("PROTEIN_DB_PATH", DEFAULT_DB_PATH))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def tz() -> ZoneInfo:
    name = (os.environ.get("TZ") or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def today() -> date:
    return datetime.now(tz()).date()


def now_iso() -> str:
    return datetime.now(tz()).isoformat(timespec="seconds")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate() -> None:
    """Привести базу к схеме мини-аппа. Идемпотентно, вызывается при старте."""
    with connect() as conn:
        # --- таблицы бота: создаём, если API стартовал раньше бота ---
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS protein_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                food_name TEXT NOT NULL,
                protein_g REAL NOT NULL,
                ingredients TEXT,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_user_day ON protein_entries (user_id, day)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                protein_g REAL NOT NULL,
                calories_kcal REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_saved_user ON saved_products (user_id)")

        # --- расширения под мини-апп ---
        for column, ddl in (
            ("calories_kcal", "REAL"),
            ("fat_g", "REAL"),
            ("carbs_g", "REAL"),
            ("fiber_g", "REAL"),
            ("micros_json", "TEXT"),
            ("portion_g", "REAL"),
            ("portion_unit", "TEXT"),
            ("meal_type", "TEXT"),
            ("eaten_at", "TEXT"),
            ("updated_at", "TEXT"),
        ):
            _add_column(conn, "protein_entries", column, ddl)

        for column, ddl in (
            ("fat_g", "REAL"),
            ("carbs_g", "REAL"),
            ("fiber_g", "REAL"),
            ("micros_json", "TEXT"),
            ("portion_g", "REAL"),
            ("portion_unit", "TEXT"),
        ):
            _add_column(conn, "saved_products", column, ddl)

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meal_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL REFERENCES protein_entries(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                grams REAL NOT NULL,
                per100_json TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_items_entry ON meal_items (entry_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id INTEGER PRIMARY KEY,
                sex TEXT NOT NULL DEFAULT 'm',
                age INTEGER NOT NULL DEFAULT 30,
                height_cm REAL NOT NULL DEFAULT 178,
                weight_kg REAL NOT NULL DEFAULT 75,
                activity REAL NOT NULL DEFAULT 1.55,
                body_fat_pct REAL,
                goal TEXT NOT NULL DEFAULT 'maintain',
                first_name TEXT,
                updated_at TEXT
            )
            """
        )
        # Своя норма калорий вместо расчётной; NULL — считать по формуле.
        _add_column(conn, "user_profile", "calories_override", "REAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                kind TEXT NOT NULL,
                minutes REAL NOT NULL,
                kcal REAL NOT NULL,
                note TEXT,
                done_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_workouts_user_day ON workouts (user_id, day)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workout_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                minutes REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_templates_user ON workout_templates (user_id)"
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS supplements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                nutrient_key TEXT,
                dose REAL NOT NULL,
                unit TEXT NOT NULL,
                when_label TEXT,
                frequency TEXT NOT NULL DEFAULT 'daily',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_supplements_user ON supplements (user_id)"
        )
        # Название банки: одна добавка — это часто десяток веществ, и в списке они
        # должны стоять одной строкой, а не десятью.
        _add_column(conn, "supplements", "group_name", "TEXT")


def load_json(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def dump_json(value: dict | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)
