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

from .nutrition.catalog import (
    BY_KEY,
    DOSE_UNITS,
    NUTRIENTS,
    clean_micros,
    normalize_dose_unit,
    normalize_portion_unit,
)

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

PRODUCT_SYSTEM = f"""Ты — нутрициолог-ассистент. По названию продукта и его КБЖУ оцени
содержание микронутриентов.

Ответь ОДНИМ JSON-объектом без markdown:
{{
  "confidence": "low"|"medium"|"high",
  "comment": "<одно короткое предложение на русском: на что опирался>",
  "per100": {{ <микронутриенты на 100 г продукта>, "fiber_g": <число> }}
}}

Правила:
- Значения в `per100` — НА 100 ГРАММ продукта, а не на порцию.
- Микронутриенты указывай в этих единицах: {_MICRO_SPEC}.
- Пропускай нутриенты, которых в продукте практически нет, вместо нулей.
- Опирайся на КБЖУ из запроса: они описывают этот же продукт, противоречить им нельзя.
- Если продукт узнан плохо, оценивай осторожно и ставь confidence: low.
- Никакого текста вне JSON."""

LABEL_SYSTEM = f"""Ты — нутрициолог-ассистент. На фото — упаковка продукта: таблица
пищевой ценности, состав, название. Прочитай её и верни данные продукта.

Ответь ОДНИМ JSON-объектом без markdown:
{{
  "name": "<название продукта на русском>",
  "portion": <порция с упаковки числом или null>,
  "portion_unit": "г"|"мл",
  "confidence": "low"|"medium"|"high",
  "comment": "<одно короткое предложение на русском: что удалось прочитать>",
  "per100": {{
    "calories_kcal": <число>, "protein_g": <число>, "fat_g": <число>,
    "carbs_g": <число>, "fiber_g": <число>,
    <микронутриенты на 100 г или 100 мл>
  }}
}}

Правила:
- Всё в `per100` — НА 100 Г (или 100 МЛ для напитков), даже если таблица дана на порцию:
  пересчитай сам.
- `portion_unit` — "мл" для напитков и жидкостей, иначе "г".
- `portion` — рекомендуемая порция с упаковки (порция, ломтик, стакан) или масса нетто,
  если порция не указана. Не знаешь — null.
- Микронутриенты указывай в этих единицах: {_MICRO_SPEC}.
- Пропускай нутриенты, которых на упаковке нет — не выдумывай их.
- Если таблица читается плохо, оценивай осторожно и ставь confidence: low.
- Никакого текста вне JSON."""

_DOSE_UNIT_SPEC = ", ".join(f'"{u}"' for u in DOSE_UNITS)
_NUTRIENT_KEY_SPEC = ", ".join(f"{n.key} — {n.name}" for n in NUTRIENTS)

#: На что этикетка считает дозы. Формы для склонения знает фронт, здесь — только
#: словарь допустимых значений, чтобы модель не выдумывала «драже» и «софтгели».
SERVING_UNITS: tuple[str, ...] = ("капсула", "таблетка", "мерная ложка", "пакетик", "порция")

_SERVING_UNIT_SPEC = " | ".join(f'"{u}"' for u in SERVING_UNITS)

SUPPLEMENT_SYSTEM = f"""Ты — нутрициолог-ассистент. На фото — банка витаминов или добавки.
Прочитай этикетку и верни ВСЕ вещества из состава ровно с теми дозами, что на ней указаны.

Ответь ОДНИМ JSON-объектом без markdown:
{{
  "name": "<название добавки с банки>",
  "serving": <на сколько единиц приёма указаны дозы, числом>,
  "serving_unit": {_SERVING_UNIT_SPEC},
  "when_label": "утром"|"днём"|"вечером"|"с едой"|"после тренировки"|null,
  "confidence": "low"|"medium"|"high",
  "comment": "<одно короткое предложение на русском>",
  "items": [
    {{
      "name": "<вещество на русском>",
      "nutrient_key": "<ключ из справочника или null>",
      "dose": <число>,
      "unit": "г"|"мг"|"мкг"|"МЕ"
    }}
  ]
}}

Правила:
- Дозы бери ровно как на этикетке, НИЧЕГО не пересчитывая. Если таблица дана на две
  капсулы — так и верни, поставив "serving": 2 и "serving_unit": "капсула".
- `serving` — на сколько единиц приёма даны дозы в таблице (обычно 1 или 2). Не указано
  прямо — считай, что на одну единицу, и ставь 1.
- `nutrient_key` бери из справочника: {_NUTRIENT_KEY_SPEC}. Если вещества там нет
  (коллаген, пробиотики, экстракты) — null, но саму позицию всё равно верни.
- `unit` — ровно та, что на этикетке, одна из: {_DOSE_UNIT_SPEC}. НИЧЕГО не переводи:
  2800 мкг так и остаются 2800 мкг, а перевод в МЕ и обратно сделаем сами.
- Верни каждое вещество отдельной позицией: у мультивитаминов их бывает два десятка.
- Никакого текста вне JSON."""

MACRO_KEYS = ("calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g")

#: На какую массу считаем, если у продукта не указана порция.
DEFAULT_PORTION_G = 100.0


class AiUnavailable(RuntimeError):
    """OpenAI не настроен или недоступен."""


class AiBadAnswer(AiUnavailable):
    """Модель ответила, но не тем. Наружу это та же 503, а вот в пакетной оценке
    отличается от недоступности: испортить ответ модель может на одном продукте,
    и это не повод бросать остальные."""


class AiBadInput(ValueError):
    """Клиент не дал ни текста, ни фото — модель тут ни при чём."""


#: Верхняя граница веса ингредиента — согласована с MealItemIn.grams.
MAX_ITEM_GRAMS = 5000.0

#: Границы порции и дозы — согласованы с ProductIn.portion_g и SupplementIn.dose.
MAX_PORTION = 10000.0
MAX_DOSE = 100000.0


#: Сколько веществ берём с одной этикетки — у мультивитаминов список длинный,
#: но не бесконечный, а схема добавок ограничена сорока позициями.
MAX_SUPPLEMENT_ITEMS = 40

#: Столько единиц приёма ещё бывает в таблице на банке; больше — ошибка распознавания.
MAX_SERVING = 20.0


def client() -> OpenAI:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise AiUnavailable("OPENAI_API_KEY не задан")
    return OpenAI(api_key=key)


def model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"


def _content(text: str, image_base64: str | None, *, detail: str = "low") -> Any:
    """Тело пользовательского сообщения: текст или текст с картинкой.

    С этикеток читаем мелкий шрифт, поэтому там detail="high" — на "low" таблица
    пищевой ценности превращается в кашу.
    """
    if not image_base64:
        return text
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_base64, "detail": detail}},
    ]


def _ask(system: str, content: Any, openai_client: OpenAI | None) -> dict:
    """Один вызов модели с разбором JSON-ответа. Ошибки — единым типом наружу."""
    api = openai_client or client()
    try:
        response = api.chat.completions.create(
            model=model(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 — наружу отдаём единый тип ошибки
        logger.exception("OpenAI request failed")
        raise AiUnavailable(f"Модель недоступна: {exc}") from exc

    if not response.choices:
        raise AiBadAnswer("Модель вернула пустой ответ")
    raw = (response.choices[0].message.content or "").strip()
    try:
        return _parse_json_loose(raw)
    except ValueError as exc:
        raise AiBadAnswer("Не удалось разобрать ответ модели") from exc


def _require_photo(image_base64: str) -> None:
    if not image_base64.strip():
        raise AiBadInput("Нужно фото этикетки")
    if not image_base64.startswith("data:image/"):
        raise AiBadInput("Фото ожидается как data URL: data:image/...;base64,...")


def parse_meal(
    *, text: str, image_base64: str | None = None, openai_client: OpenAI | None = None
) -> dict:
    """Разобрать блюдо. Бросает AiUnavailable, если модель недоступна или ответила мусором."""
    if not text.strip() and not image_base64:
        raise AiBadInput("Нужен текст или фото блюда")
    if image_base64:
        _require_photo(image_base64)

    user_text = text.strip() or "Определи блюдо по фото."
    payload = _ask(SYSTEM, _content(user_text, image_base64), openai_client)
    return normalize(payload, fallback_name=text.strip() or "Блюдо")


def parse_label(
    *, image_base64: str, text: str = "", openai_client: OpenAI | None = None
) -> dict:
    """Снять состав продукта с фото упаковки."""
    _require_photo(image_base64)
    user_text = text.strip() or "Прочитай пищевую ценность с упаковки."
    payload = _ask(
        LABEL_SYSTEM, _content(user_text, image_base64, detail="high"), openai_client
    )
    return normalize_label(payload, fallback_name=text.strip() or "Продукт")


def parse_supplement_label(
    *, image_base64: str, text: str = "", openai_client: OpenAI | None = None
) -> dict:
    """Прочитать состав добавки с фото банки — обычно это несколько веществ сразу."""
    _require_photo(image_base64)
    user_text = text.strip() or "Прочитай состав добавки с этикетки."
    payload = _ask(
        SUPPLEMENT_SYSTEM, _content(user_text, image_base64, detail="high"), openai_client
    )
    return normalize_supplement_label(payload, fallback_name=text.strip() or "Добавка")


def estimate_product(
    *,
    name: str,
    portion_g: float | None = None,
    macros: dict[str, float | None] | None = None,
    openai_client: OpenAI | None = None,
) -> dict:
    """Оценить микронутриенты продукта по названию и КБЖУ.

    Возвращает и `per100`, и `micros` — пересчитанные на порцию продукта: в справочнике
    продукт хранится абсолютными числами за свою порцию, а не за 100 г.
    """
    if not name.strip():
        raise AiBadInput("Нужно название продукта")

    portion = float(portion_g) if portion_g and portion_g > 0 else DEFAULT_PORTION_G
    known = _macros_line(macros or {}, portion)
    prompt = f"Продукт: {name.strip()}\nПорция: {portion:g} г"
    if known:
        prompt += f"\nИзвестное КБЖУ порции: {known}"

    payload = _ask(PRODUCT_SYSTEM, prompt, openai_client)
    return normalize_product(payload, portion_g=portion)


def normalize_product(payload: dict, *, portion_g: float) -> dict:
    """Привести ответ модели к схеме AiProductOut и пересчитать на порцию."""
    raw = payload.get("per100")
    raw = raw if isinstance(raw, dict) else {}
    per100 = clean_micros(raw)
    # Клетчатка живёт и среди макросов, и среди микронутриентов — держим одно значение.
    fiber_per100 = max(per100.get("fiber", 0.0), _positive(raw.get("fiber_g")))
    if fiber_per100 > 0:
        per100["fiber"] = fiber_per100

    factor = portion_g / 100.0
    micros = {key: round(value * factor, 3) for key, value in per100.items()}
    return {
        "portion_g": portion_g,
        "per100": {k: round(v, 3) for k, v in per100.items()},
        "micros": micros,
        "fiber_g": round(fiber_per100 * factor, 1),
        "confidence": str(payload.get("confidence") or "low"),
        "comment": str(payload.get("comment") or "").strip(),
    }


def normalize_label(payload: dict, *, fallback_name: str) -> dict:
    """Привести ответ по фото упаковки к схеме AiLabelOut.

    На выходе и `per100`, и КБЖУ порции: форма продукта заполняется абсолютными
    числами за порцию, а `per100` нужен, чтобы пересчитать её при смене граммовки.
    """
    raw = payload.get("per100")
    raw = raw if isinstance(raw, dict) else {}
    per100: dict[str, float] = {key: _positive(raw.get(key)) for key in MACRO_KEYS}
    per100.update(clean_micros(raw))
    # Клетчатка живёт и среди макросов, и среди микронутриентов — держим одно значение.
    fiber = max(per100.get("fiber", 0.0), per100["fiber_g"])
    per100["fiber_g"] = fiber
    if fiber > 0:
        per100["fiber"] = fiber

    unit = normalize_portion_unit(payload.get("portion_unit"))
    if unit not in ("г", "мл"):
        unit = "г"

    portion_raw = _positive(payload.get("portion"))
    portion = min(portion_raw, MAX_PORTION) if portion_raw > 0 else None
    factor = (portion if portion else DEFAULT_PORTION_G) / 100.0

    name = str(payload.get("name") or "").strip() or fallback_name
    return {
        "name": name[:200],
        "portion_g": portion,
        "portion_unit": unit,
        "per100": {k: round(v, 3) for k, v in per100.items()},
        "calories_kcal": round(per100["calories_kcal"] * factor, 1),
        "protein_g": round(per100["protein_g"] * factor, 1),
        "fat_g": round(per100["fat_g"] * factor, 1),
        "carbs_g": round(per100["carbs_g"] * factor, 1),
        "fiber_g": round(fiber * factor, 1),
        "micros": {k: round(v * factor, 3) for k, v in clean_micros(per100).items()},
        "confidence": str(payload.get("confidence") or "low"),
        "comment": str(payload.get("comment") or "").strip(),
    }


def normalize_supplement_label(payload: dict, *, fallback_name: str) -> dict:
    """Привести ответ по фото банки к схеме AiSupplementLabelOut.

    Позиции с неизвестным ключом не выбрасываем: коллаген и пробиотики в справочник
    не входят, но в ленте дня им место есть — пользователь сам решит, что оставить.
    """
    items: list[dict] = []
    for raw_item in payload.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "").strip()
        dose = _positive(raw_item.get("dose"))
        if not name or dose <= 0 or dose > MAX_DOSE:
            continue
        unit = normalize_dose_unit(raw_item.get("unit"))
        if unit not in DOSE_UNITS:
            unit = "мг"
        # Ключ проверяем на строку: словарь или список в этом поле уронил бы поиск по BY_KEY.
        key = raw_item.get("nutrient_key")
        items.append(
            {
                "name": name[:80],
                "nutrient_key": key if isinstance(key, str) and key in BY_KEY else None,
                "dose": round(dose, 3),
                "unit": unit,
            }
        )
        if len(items) >= MAX_SUPPLEMENT_ITEMS:
            break

    # На сколько капсул этикетка считает дозы: без этого «принимаю одну вместо двух»
    # не пересчитать, а таблица на банке чаще всего дана именно на приём, а не на штуку.
    serving = _positive(payload.get("serving"))
    serving = min(serving, MAX_SERVING) if serving > 0 else 1.0
    serving_unit = str(payload.get("serving_unit") or "").strip().lower()
    if serving_unit not in SERVING_UNITS:
        serving_unit = "порция"

    when = str(payload.get("when_label") or "").strip()
    name = str(payload.get("name") or "").strip() or fallback_name
    return {
        "name": name[:80],
        "serving": round(serving, 2),
        "serving_unit": serving_unit,
        "items": items,
        "when_label": when[:40] or None,
        "confidence": str(payload.get("confidence") or "low"),
        "comment": str(payload.get("comment") or "").strip(),
    }


def _macros_line(macros: dict[str, float | None], portion_g: float) -> str:
    labels = (
        ("calories_kcal", "ккал"),
        ("protein_g", "белки, г"),
        ("fat_g", "жиры, г"),
        ("carbs_g", "углеводы, г"),
    )
    parts = [
        f"{label} {float(macros[key]):g}"
        for key, label in labels
        if macros.get(key) is not None
    ]
    return ", ".join(parts)


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
