"""Pydantic-модели запросов и ответов API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .nutrition.catalog import KEYS, normalize_dose_unit, normalize_portion_unit

Sex = Literal["m", "f"]
Goal = Literal["lose", "maintain", "gain"]
MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
Frequency = Literal["daily", "every_other_day", "course"]
Range = Literal["week", "month"]
NutrientKey = Literal[KEYS]

#: Время внутри дня — "HH:MM"; иначе сортировка ленты по строке ломается.
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

DoseUnit = Annotated[Literal["г", "мг", "мкг", "МЕ"], BeforeValidator(normalize_dose_unit)]

#: Единица порции: граммы для еды, миллилитры для напитков. Пересчёт между ними
#: не делаем — плотность продукта нам неизвестна, это только подпись к числу.
PortionUnit = Annotated[Literal["г", "мл"], BeforeValidator(normalize_portion_unit)]


class Micros(BaseModel):
    """Свободная карта нутриентов; ключи валидируются в catalog.clean_micros."""

    model_config = ConfigDict(extra="allow")


class MealItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    grams: float = Field(ge=0, le=5000)
    #: КБЖУ и микронутриенты на 100 г продукта
    per100: dict[str, float] = Field(default_factory=dict)


class MealItemOut(MealItemIn):
    id: int


class MealIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    protein_g: float = Field(default=0, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fiber_g: float | None = Field(default=None, ge=0, le=200)
    portion_g: float | None = Field(default=None, ge=0, le=10000)
    portion_unit: PortionUnit = "г"
    meal_type: MealType = "other"
    eaten_at: str | None = Field(default=None, pattern=TIME_PATTERN, description="HH:MM")
    ingredients: str | None = Field(default=None, max_length=4000)
    micros: dict[str, float] = Field(default_factory=dict)
    items: list[MealItemIn] = Field(default_factory=list)
    source: str = "webapp"
    #: сохранить блюдо в справочник продуктов
    save_as_product: bool = False


class MealPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fiber_g: float | None = Field(default=None, ge=0, le=200)
    portion_g: float | None = Field(default=None, ge=0, le=10000)
    portion_unit: PortionUnit | None = None
    meal_type: MealType | None = None
    eaten_at: str | None = Field(default=None, pattern=TIME_PATTERN)
    ingredients: str | None = Field(default=None, max_length=4000)
    micros: dict[str, float] | None = None
    items: list[MealItemIn] | None = None
    day: str | None = Field(default=None, min_length=10, max_length=10, description="перенести блюдо на другой день")


class MealOut(BaseModel):
    id: int
    day: str
    name: str
    calories_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    portion_g: float | None
    portion_unit: PortionUnit
    meal_type: MealType
    eaten_at: str | None
    ingredients: str | None
    source: str
    micros: dict[str, float]
    items: list[MealItemOut] = Field(default_factory=list)


class WorkoutIn(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    minutes: float = Field(ge=1, le=600)
    kcal: float | None = Field(default=None, ge=0, le=10000)
    note: str | None = Field(default=None, max_length=200)
    done_at: str | None = Field(default=None, pattern=TIME_PATTERN)
    save_as_template: bool = False
    template_name: str | None = Field(default=None, max_length=60)


class WorkoutPatch(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    minutes: float | None = Field(default=None, ge=1, le=600)
    kcal: float | None = Field(default=None, ge=0, le=10000)
    note: str | None = Field(default=None, max_length=200)
    done_at: str | None = Field(default=None, pattern=TIME_PATTERN)


class WorkoutOut(BaseModel):
    id: int
    day: str
    kind: str
    kind_name: str
    icon: str
    minutes: float
    kcal: float
    note: str | None
    done_at: str | None


class WorkoutTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(min_length=1, max_length=40)
    minutes: float = Field(ge=1, le=600)


class WorkoutTemplateOut(WorkoutTemplateIn):
    id: int
    icon: str
    #: расход по текущему весу пользователя
    kcal: float


class SupplementIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    #: Название банки, если вещество приехало с этикетки: по нему добавка собирается
    #: в одну запись. None — самостоятельная добавка, сама себе название.
    group_name: str | None = Field(default=None, max_length=80)
    nutrient_key: NutrientKey | None = None
    dose: float = Field(ge=0, le=100000)
    unit: DoseUnit
    when_label: str | None = Field(default=None, max_length=40)
    frequency: Frequency = "daily"
    active: bool = True


class SupplementPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    group_name: str | None = Field(default=None, max_length=80)
    nutrient_key: NutrientKey | None = None
    dose: float | None = Field(default=None, ge=0, le=100000)
    unit: DoseUnit | None = None
    when_label: str | None = Field(default=None, max_length=40)
    frequency: Frequency | None = None
    active: bool | None = None


class SupplementOut(SupplementIn):
    id: int


class SupplementBulkIn(BaseModel):
    """Одна банка — несколько веществ: с этикетки мультивитаминов их приезжает десяток.

    Название даёт пользователь, и оно общее для всех веществ: в списке добавок и в ленте
    дня банка должна выглядеть одной записью, а в отчёт попасть каждым веществом.
    """

    name: str = Field(min_length=1, max_length=80)
    items: list[SupplementIn] = Field(min_length=1, max_length=40)


class SupplementIdsIn(BaseModel):
    """Удаление банки целиком — по идентификаторам её веществ."""

    ids: list[int] = Field(min_length=1, max_length=40)


#: Границы своей нормы: ниже 800 ккал — уже не диета, а вред; выше 8000 — опечатка.
CALORIES_OVERRIDE_MIN = 800
CALORIES_OVERRIDE_MAX = 8000


class ProfileIn(BaseModel):
    sex: Sex | None = None
    age: int | None = Field(default=None, ge=10, le=110)
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=350)
    activity: float | None = Field(default=None, ge=1.2, le=1.9)
    body_fat_pct: float | None = Field(default=None, ge=1, le=70)
    goal: Goal | None = None
    #: null — вернуться к расчёту по формуле
    calories_override: float | None = Field(
        default=None, ge=CALORIES_OVERRIDE_MIN, le=CALORIES_OVERRIDE_MAX
    )


class ProfileOut(BaseModel):
    user_id: int
    sex: Sex
    age: int
    height_cm: float
    weight_kg: float
    activity: float
    body_fat_pct: float | None
    calories_override: float | None
    goal: Goal
    first_name: str | None
    norms: dict


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    protein_g: float = Field(ge=0, le=1000)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fiber_g: float | None = Field(default=None, ge=0, le=200)
    portion_g: float | None = Field(default=None, ge=0, le=10000)
    portion_unit: PortionUnit = "г"
    micros: dict[str, float] = Field(default_factory=dict)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)
    fiber_g: float | None = Field(default=None, ge=0, le=200)
    portion_g: float | None = Field(default=None, ge=0, le=10000)
    portion_unit: PortionUnit | None = None
    micros: dict[str, float] | None = None


class ProductOut(ProductIn):
    id: int


class AiParseIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    image_base64: str | None = Field(default=None, description="data:image/...;base64,...")


class AiPhotoIn(BaseModel):
    """Фото этикетки; текстом можно уточнить, что именно снято."""

    image_base64: str = Field(min_length=1, description="data:image/...;base64,...")
    text: str = Field(default="", max_length=2000)


class AiLabelOut(BaseModel):
    """Состав продукта, снятый с упаковки."""

    name: str
    #: порция с упаковки в единицах `portion_unit`; None — на упаковке её нет
    portion_g: float | None
    portion_unit: PortionUnit
    #: КБЖУ и микронутриенты на 100 г (или 100 мл)
    per100: dict[str, float]
    #: КБЖУ порции — то, что подставляем в форму
    calories_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    #: микронутриенты порции
    micros: dict[str, float]
    confidence: str
    comment: str


class AiSupplementItem(BaseModel):
    name: str = Field(max_length=80)
    nutrient_key: NutrientKey | None = None
    dose: float = Field(ge=0, le=100000)
    unit: DoseUnit


class AiSupplementLabelOut(BaseModel):
    """Этикетка банки: название и все вещества, которые удалось прочитать."""

    name: str
    #: На сколько единиц приёма этикетка считает дозы — база для «принимаю одну вместо двух»
    serving: float = Field(ge=0, le=20)
    serving_unit: str
    items: list[AiSupplementItem]
    when_label: str | None = None
    confidence: str
    comment: str


class AiProductIn(BaseModel):
    """Что знаем о продукте на момент оценки — КБЖУ помогают модели его узнать."""

    name: str = Field(min_length=1, max_length=200)
    portion_g: float | None = Field(default=None, ge=0, le=10000)
    calories_kcal: float | None = Field(default=None, ge=0, le=20000)
    protein_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=1000)
    carbs_g: float | None = Field(default=None, ge=0, le=2000)


class AiProductOut(BaseModel):
    #: На какую массу пересчитаны `micros`
    portion_g: float
    per100: dict[str, float]
    micros: dict[str, float]
    fiber_g: float
    confidence: str
    comment: str


class ProductsEstimateOut(BaseModel):
    """Итог пакетной оценки: что обновилось и что не получилось."""

    updated: list[ProductOut]
    failed: int
    remaining: int
    error: str | None = None


class AiParsedItem(BaseModel):
    name: str
    grams: float = Field(ge=0, le=5000)
    per100: dict[str, float]


class AiParseOut(BaseModel):
    name: str
    calories_kcal: float
    protein_g: float
    fat_g: float
    carbs_g: float
    fiber_g: float
    micros: dict[str, float]
    items: list[AiParsedItem]
    confidence: str
    comment: str
