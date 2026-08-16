"""Pydantic-модели запросов и ответов API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sex = Literal["m", "f"]
Goal = Literal["lose", "maintain", "gain"]
MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
Frequency = Literal["daily", "every_other_day", "course"]
Range = Literal["week", "month"]


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
    meal_type: MealType = "other"
    eaten_at: str | None = Field(default=None, max_length=5, description="HH:MM")
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
    meal_type: MealType | None = None
    eaten_at: str | None = Field(default=None, max_length=5)
    ingredients: str | None = Field(default=None, max_length=4000)
    micros: dict[str, float] | None = None
    items: list[MealItemIn] | None = None
    day: str | None = Field(default=None, description="перенести блюдо на другой день")


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
    done_at: str | None = Field(default=None, max_length=5)
    save_as_template: bool = False
    template_name: str | None = Field(default=None, max_length=60)


class WorkoutPatch(BaseModel):
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    minutes: float | None = Field(default=None, ge=1, le=600)
    kcal: float | None = Field(default=None, ge=0, le=10000)
    note: str | None = Field(default=None, max_length=200)
    done_at: str | None = Field(default=None, max_length=5)


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
    nutrient_key: str | None = None
    dose: float = Field(ge=0, le=100000)
    unit: str = Field(min_length=1, max_length=10)
    when_label: str | None = Field(default=None, max_length=40)
    frequency: Frequency = "daily"
    active: bool = True


class SupplementPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    nutrient_key: str | None = None
    dose: float | None = Field(default=None, ge=0, le=100000)
    unit: str | None = Field(default=None, min_length=1, max_length=10)
    when_label: str | None = Field(default=None, max_length=40)
    frequency: Frequency | None = None
    active: bool | None = None


class SupplementOut(SupplementIn):
    id: int


class ProfileIn(BaseModel):
    sex: Sex | None = None
    age: int | None = Field(default=None, ge=10, le=110)
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=350)
    activity: float | None = Field(default=None, ge=1.2, le=1.9)
    body_fat_pct: float | None = Field(default=None, ge=1, le=70)
    goal: Goal | None = None


class ProfileOut(BaseModel):
    user_id: int
    sex: Sex
    age: int
    height_cm: float
    weight_kg: float
    activity: float
    body_fat_pct: float | None
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
    micros: dict[str, float] = Field(default_factory=dict)


class ProductOut(ProductIn):
    id: int


class AiParseIn(BaseModel):
    text: str = Field(default="", max_length=2000)
    image_base64: str | None = Field(default=None, description="data:image/...;base64,...")


class AiParsedItem(BaseModel):
    name: str
    grams: float
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
