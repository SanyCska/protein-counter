from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ProteinEntry:
    id: int
    food_name: str
    protein_g: float
    ingredients: str | None
    source: str


class ProteinStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
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

    def add_entry(
        self,
        *,
        user_id: int,
        day: date,
        food_name: str,
        protein_g: float,
        source: str,
        ingredients: str | None = None,
    ) -> int:
        day_s = day.isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO protein_entries (user_id, day, food_name, protein_g, ingredients, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, day_s, food_name.strip(), float(protein_g), ingredients, source),
            )
            return int(cur.lastrowid)

    def entries_for_day(self, user_id: int, day: date) -> list[ProteinEntry]:
        day_s = day.isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, food_name, protein_g, ingredients, source
                FROM protein_entries
                WHERE user_id = ? AND day = ?
                ORDER BY id ASC
                """,
                (user_id, day_s),
            ).fetchall()
        return [
            ProteinEntry(
                id=r["id"],
                food_name=r["food_name"],
                protein_g=float(r["protein_g"]),
                ingredients=r["ingredients"],
                source=r["source"],
            )
            for r in rows
        ]

    def total_for_day(self, user_id: int, day: date) -> float:
        entries = self.entries_for_day(user_id, day)
        return sum(e.protein_g for e in entries)

    def delete_entry(self, user_id: int, entry_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM protein_entries WHERE id = ? AND user_id = ?",
                (entry_id, user_id),
            )
            return cur.rowcount > 0
