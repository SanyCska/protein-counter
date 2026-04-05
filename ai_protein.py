from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI


SYSTEM = """Оцени пищевой белок (граммы) и энергетическую ценность (килокалории) по названию блюда и описанию ингредиентов.
Ответь только одним JSON-объектом, без markdown:
{"protein_g": <число>, "calories_kcal": <число>, "confidence": "low"|"medium"|"high", "short_reason": "<одно короткое предложение на русском>"}
Правила:
- protein_g — неотрицательное число (граммы белка для описанной порции/приёма пищи).
- calories_kcal — неотрицательное число (ккал для той же порции).
- short_reason — по-русски, кратко почему такая оценка.
- Если данных мало — дай осторожную оценку и поставь confidence: low."""


def estimate_protein(
    client: OpenAI,
    *,
    food_name: str,
    ingredients_text: str,
    model: str,
) -> tuple[float, float | None, str]:
    user_msg = (
        f"Название блюда: {food_name}\n"
        f"Ингредиенты / описание:\n{ingredients_text.strip()}"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    data = _parse_json_loose(text)
    protein = float(data.get("protein_g", 0))
    if protein < 0:
        protein = 0.0
    raw_kcal = data.get("calories_kcal")
    calories: float | None
    if raw_kcal is None:
        calories = None
    else:
        try:
            calories = float(raw_kcal)
        except (TypeError, ValueError):
            calories = None
        if calories is not None and calories < 0:
            calories = 0.0
    reason = str(data.get("short_reason", "")).strip() or "Оценка по ингредиентам."
    return protein, calories, reason


def _parse_json_loose(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise ValueError("Не удалось разобрать ответ модели как JSON.")
