"""Расчёты норм, микронутриентов и расхода на нагрузке."""

from __future__ import annotations

import pytest

from api.nutrition import workouts as wk
from api.nutrition.catalog import BY_KEY, NUTRIENTS, clean_micros, scale_micros, sum_micros
from api.nutrition.norms import bmr_mifflin, compute_norms


class TestCatalog:
    def test_has_25_nutrients_with_unique_keys(self):
        assert len(NUTRIENTS) == 25
        assert len(BY_KEY) == 25

    def test_clean_micros_drops_unknown_and_invalid(self):
        cleaned = clean_micros(
            {"iron": 3.1, "unknown_key": 5, "zinc": "не число", "calcium": -10, "magnesium": "120"}
        )
        assert cleaned == {"iron": 3.1, "magnesium": 120.0}

    def test_clean_micros_on_garbage_input(self):
        assert clean_micros(None) == {}
        assert clean_micros("строка") == {}
        assert clean_micros([1, 2]) == {}

    def test_scale_and_sum(self):
        scaled = scale_micros({"iron": 2.0, "zinc": 4.0}, 0.5)
        assert scaled == {"iron": 1.0, "zinc": 2.0}
        assert sum_micros([{"iron": 1.0}, {"iron": 2.0, "zinc": 3.0}]) == {"iron": 3.0, "zinc": 3.0}


class TestNorms:
    def test_bmr_male_matches_formula(self):
        # 10×84 + 6.25×182 − 5×34 + 5 = 840 + 1137.5 − 170 + 5
        assert bmr_mifflin(sex="m", age=34, height_cm=182, weight_kg=84) == pytest.approx(1812.5)

    def test_bmr_female_is_166_lower_than_male(self):
        male = bmr_mifflin(sex="m", age=30, height_cm=170, weight_kg=65)
        female = bmr_mifflin(sex="f", age=30, height_cm=170, weight_kg=65)
        assert male - female == pytest.approx(166)

    def test_goal_shifts_calories(self):
        common = dict(sex="m", age=34, height_cm=182, weight_kg=84, activity=1.55)
        lose = compute_norms(**common, goal="lose")
        maintain = compute_norms(**common, goal="maintain")
        gain = compute_norms(**common, goal="gain")
        assert lose.calories < maintain.calories < gain.calories
        assert lose.calories == pytest.approx(maintain.calories * 0.85)

    def test_macros_sum_to_calorie_target(self):
        norms = compute_norms(
            sex="m", age=34, height_cm=182, weight_kg=84, activity=1.55, goal="maintain"
        )
        from_macros = norms.protein_g * 4 + norms.fat_g * 9 + norms.carbs_g * 4
        assert from_macros == pytest.approx(norms.calories, rel=1e-6)

    def test_carbs_never_negative_on_extreme_profile(self):
        # Тяжёлый малоподвижный человек на снижении: белок и жир могут съесть весь бюджет.
        norms = compute_norms(
            sex="f", age=60, height_cm=150, weight_kg=140, activity=1.2, goal="lose"
        )
        assert norms.carbs_g >= 0

    def test_activity_accepts_name_and_number(self):
        by_name = compute_norms(
            sex="m", age=30, height_cm=180, weight_kg=80, activity="moderate", goal="maintain"
        )
        by_number = compute_norms(
            sex="m", age=30, height_cm=180, weight_kg=80, activity=1.55, goal="maintain"
        )
        assert by_name.calories == pytest.approx(by_number.calories)

    def test_female_micros_differ_from_male(self):
        male = compute_norms(
            sex="m", age=30, height_cm=180, weight_kg=80, activity=1.55, goal="maintain"
        )
        female = compute_norms(
            sex="f", age=30, height_cm=165, weight_kg=60, activity=1.55, goal="maintain"
        )
        assert female.micros["iron"] > male.micros["iron"]


class TestWorkoutKcal:
    def test_kcal_scales_with_weight_and_time(self):
        base = wk.kcal_for(kind="swimming", minutes=60, weight_kg=80)
        assert base == pytest.approx(7.0 * 80)
        assert wk.kcal_for(kind="swimming", minutes=30, weight_kg=80) == pytest.approx(base / 2)
        assert wk.kcal_for(kind="swimming", minutes=60, weight_kg=160) == pytest.approx(base * 2)

    def test_unknown_kind_falls_back_to_other(self):
        assert wk.met_for("телепортация") == wk.met_for("other")
        assert wk.name_for("телепортация") == "Другое"

    def test_missing_weight_uses_default(self):
        assert wk.kcal_for(kind="running", minutes=60, weight_kg=None) == pytest.approx(
            9.8 * wk.DEFAULT_WEIGHT_KG
        )

    def test_negative_minutes_give_zero(self):
        assert wk.kcal_for(kind="running", minutes=-30, weight_kg=80) == 0.0
