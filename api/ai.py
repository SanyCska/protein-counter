"""ИИ-разбор блюда: текст и/или фото → состав, КБЖУ, микронутриенты.

Отличается от `ai_protein.estimate_protein` (который использует бот) тем, что за один
вызов возвращает разбивку по ингредиентам и микронутриенты — мини-апп даёт править
граммовку каждой позиции, и для этого нужен состав, а не одно число.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

from .nutrition.catalog import NUTRIENTS, clean_micros

logger = logging.getLogger(__name__)

_MICRO_SPEC = ", ".join(f'"{n.key}" ({n.unit})' for n in NUTRIENTS)

SYSTEM = f"""Ты — нутрициолог-ассистент. По названию блюда, описанию и фото (если есть)
разбери приём пищи на ингредиенты и оцени пищевую ценность.

Ответь ОДНИМ JSON-объектом без markdown:
{{
  "name": "<короткое название блюда на русском>",
  "confidence": "low"|"medium"|"high",
  "comment": "<одно короткое предложение на русском>",
  "items": [
    {{
      "name": "<ингредиент на русском>",
      "grams": <вес порции этого ингредиента в граммах>,
      "per100": {{
        "calories_kcal": <число>, "protein_g": <число>, "fat_g": <число>,
        "carbs_g": <число>, "fiber_g": <число>,
        <микронутриенты на 100 г>
      }}
    }}
  ]
}}

Правила:
- Все значения в `per100` — НА 100 ГРАММ продукта, а вес порции — в поле `grams`.
- Микронутриенты в `per100` указывай в этих единицах: {_MICRO_SPEC}.
- Пропускай микронутриенты, которых в продукте практически нет, вместо нулей.
- Разбивай блюдо на 1–8 понятных ингредиентов. Если это готовый продукт — один элемент.
- Все числа неотрицательные. Если данных мало — оценивай осторожно и ставь confidence: low.
- Никакого текста вне JSON."""

MACRO_KEYS = ("calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")


class AiUnavailable(RuntimeError):
    """OpenAI не настроен или недоступен."""


class AiBadInput(ValueError):
    """Клиент не дал ни текста, ни фото — модель тут ни при чём."""


#: Верхняя граница веса ингредиента — согласована с MealItemIn.grams.
MAX_ITEM_GRAMS = 5000.0


def client() -> OpenAI:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise AiUnavailable("OPENAI_API_KEY не задан")
    return OpenAI(api_key=key)


def model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"


def parse_meal(
    *, text: str, image_base64: str | None = None, openai_client: OpenAI | None = None
) -> dict:
    """Разобрать блюдо. Бросает AiUnavailable, если модель недоступна или ответила мусором."""
    if not text.strip() and not image_base64:
        raise AiBadInput("Нужен текст или фото блюда")
    if image_base64 and not image_base64.startswith("data:image/"):
        raise AiBadInput("Фото ожидается как data URL: data:image/...;base64,...")

    api = openai_client or client()
    user_text = text.strip() or "Определи блюдо по фото."
    content: str | list[dict[str, Any]]
    if image_base64:
        content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_base64, "detail": "low"}},
        ]
    else:
        content = user_text

    try:
        response = api.chat.completions.create(
            model=model(),
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 — наружу отдаём единый тип ошибки
        logger.exception("OpenAI request failed")
        raise AiUnavailable(f"Модель недоступна: {exc}") from exc

    if not response.choices:
        raise AiUnavailable("Модель вернула пустой ответ")
    raw = (response.choices[0].message.content or "").strip()
    try:
        payload = _parse_json_loose(raw)
    except ValueError as exc:
        raise AiUnavailable("Не удалось разобрать ответ модели") from exc

    return normalize(payload, fallback_name=text.strip() or "Блюдо")


def normalize(payload: dict, *, fallback_name: str) -> dict:
    """Привести ответ модели к схеме AiParseOut и посчитать итоги по составу."""
    items: list[dict] = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "").strip()
        if not name:
            continue
        grams = min(_positive(raw_item.get("grams")), MAX_ITEM_GRAMS)
        per100_raw = raw_item.get("per100")
        per100_raw = per100_raw if isinstance(per100_raw, dict) else {}
        per100: dict[str, float] = {
            key: _positive(per100_raw.get(key)) for key in MACRO_KEYS
        }
        per100.update(clean_micros(per100_raw))
        items.append({"name": name[:120], "grams": grams, "per100": per100})

    totals = {key: 0.0 for key in MACRO_KEYS}
    micros: dict[str, float] = {}
    for item in items:
        factor = item["grams"] / 100.0
        for key in MACRO_KEYS:
            totals[key] += item["per100"].get(key, 0.0) * factor
        for key, value in clean_micros(item["per100"]).items():
            micros[key] = micros.get(key, 0.0) + value * factor

    # Клетчатка живёт и в макросах, и в справочнике микронутриентов — держим их согласованными.
    micros["fiber"] = max(micros.get("fiber", 0.0), totals["fiber_g"])
    totals["fiber_g"] = micros["fiber"]

    name = str(payload.get("name") or "").strip() or fallback_name
    return {
        "name": name[:200],
        "calories_kcal": round(totals["calories_kcal"], 1),
        "protein_g": round(totals["protein_g"], 1),
        "fat_g": round(totals["fat_g"], 1),
        "carbs_g": round(totals["carbs_g"], 1),
        "fiber_g": round(totals["fiber_g"], 1),
        "micros": {k: round(v, 3) for k, v in micros.items()},
        "items": items,
        "confidence": str(payload.get("confidence") or "low"),
        "comment": str(payload.get("comment") or "").strip(),
    }


def _positive(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    if num != num or num < 0:  # NaN или отрицательное
        return 0.0
    return num


def _parse_json_loose(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise ValueError("Ответ модели не является JSON-объектом")
