"""Агрегация отчётов: дефициты, добавки, прогресс."""

from __future__ import annotations

from datetime import date

import pytest

from api import reports
from api.nutrition.norms import compute_norms

NORMS = compute_norms(
    sex="m", age=34, height_cm=182, weight_kg=84, activity=1.55, goal="maintain"
).as_dict()


def meal(day="2026-08-10", *, kcal=500.0, protein=30.0, fat=15.0, carbs=50.0, micros=None):
    return {
        "day": day,
        "calories_kcal": kcal,
        "protein_g": protein,
        "fat_g": fat,
        "carbs_g": carbs,
        "fiber_g": (micros or {}).get("fiber", 0.0),
        "micros": micros or {},
    }


def workout(day="2026-08-10", *, kcal=400.0, kind_name="Плавание", kind="swimming"):
    return {"day": day, "kcal": kcal, "kind": kind, "kind_name": kind_name, "minutes": 45}


class TestUnitConversion:
    def test_same_unit_passes_through(self):
        assert reports.convert_dose(200, "мг", "мг") == 200

    def test_mg_to_mcg(self):
        assert reports.convert_dose(2, "мг", "мкг") == pytest.approx(2000)

    def test_g_to_mg(self):
        assert reports.convert_dose(1.5, "г", "мг") == pytest.approx(1500)

    def test_iu_converts_for_vitamin_d(self):
        assert reports.convert_dose(2000, "МЕ", "мкг") == pytest.approx(50)
        assert reports.convert_dose(25, "мкг", "МЕ") == pytest.approx(1000)

    def test_incompatible_units_return_none(self):
        assert reports.convert_dose(1, "шт", "мг") is None


class TestSupplements:
    def test_inactive_supplement_is_skipped(self):
        supplements = [{"active": False, "frequency": "daily", "nutrient_key": "iron", "dose": 10, "unit": "мг"}]
        assert reports.supplements_for_day(supplements, date(2026, 8, 10)) == []

    def test_every_other_day_appears_on_half_the_days(self):
        supplement = {
            "active": True,
            "frequency": "every_other_day",
            "nutrient_key": "magnesium",
            "dose": 200,
            "unit": "мг",
        }
        days = [date(2026, 8, d) for d in range(1, 15)]
        taken = [d for d in days if reports.supplements_for_day([supplement], d)]
        assert len(taken) == 7

    def test_micros_are_converted_to_nutrient_unit(self):
        # Селен считается в мкг, добавка указана в мг.
        supplements = [{"active": True, "frequency": "daily", "nutrient_key": "selenium", "dose": 0.05, "unit": "мг"}]
        assert reports.supplement_micros(supplements)["selenium"] == pytest.approx(50)

    def test_supplement_without_nutrient_key_is_ignored(self):
        supplements = [{"active": True, "frequency": "daily", "nutrient_key": None, "dose": 5, "unit": "г"}]
        assert reports.supplement_micros(supplements) == {}


class TestDayReport:
    def test_totals_and_balance(self):
        report = reports.build_day_report(
            day=date(2026, 8, 10),
            meals=[meal(kcal=1000, protein=60), meal(kcal=840, protein=85)],
            workouts=[workout(kcal=520)],
            supplements=[],
            norms=NORMS,
        )
        assert report["totals"]["calories_eaten"] == 1840
        assert report["totals"]["calories_burned"] == 520
        assert report["totals"]["calories_net"] == 1320
        assert report["totals"]["balance_vs_norm"] == pytest.approx(1320 - NORMS["calories"])

    def test_supplement_counts_toward_micronutrient(self):
        supplements = [
            {"active": True, "frequency": "daily", "nutrient_key": "vit_d", "dose": 2000, "unit": "МЕ"}
        ]
        report = reports.build_day_report(
            day=date(2026, 8, 10), meals=[], workouts=[], supplements=supplements, norms=NORMS
        )
        vit_d = next(r for r in report["micros"] if r["key"] == "vit_d")
        assert vit_d["value"] == 2000
        assert vit_d["pct"] == 250

    def test_deficits_sorted_by_severity_and_carry_sources(self):
        report = reports.build_day_report(
            day=date(2026, 8, 10),
            meals=[meal(micros={"iron": 9.0, "fiber": 18.0})],
            workouts=[],
            supplements=[],
            norms=NORMS,
        )
        percentages = [d["pct"] for d in report["deficits"]]
        assert percentages == sorted(percentages)
        assert all(d["sources"] for d in report["deficits"])

    def test_fiber_from_macros_counts_as_micronutrient(self):
        report = reports.build_day_report(
            day=date(2026, 8, 10),
            meals=[{**meal(), "fiber_g": 22.0}],
            workouts=[],
            supplements=[],
            norms=NORMS,
        )
        fiber = next(r for r in report["micros"] if r["key"] == "fiber")
        assert fiber["value"] == 22.0

    def test_excess_over_upper_limit_is_flagged(self):
        supplements = [
            {"active": True, "frequency": "daily", "nutrient_key": "selenium", "dose": 500, "unit": "мкг"}
        ]
        report = reports.build_day_report(
            day=date(2026, 8, 10), meals=[], workouts=[], supplements=supplements, norms=NORMS
        )
        assert any(e["key"] == "selenium" for e in report["excesses"])

    def test_coverage_reports_share_of_meals_with_micro_data(self):
        report = reports.build_day_report(
            day=date(2026, 8, 10),
            meals=[meal(micros={"iron": 3.0}), meal(), meal(), meal()],
            workouts=[],
            supplements=[],
            norms=NORMS,
        )
        assert report["micro_coverage"] == 0.25

    def test_empty_day_does_not_crash(self):
        report = reports.build_day_report(
            day=date(2026, 8, 10), meals=[], workouts=[], supplements=[], norms=NORMS
        )
        assert report["totals"]["calories_eaten"] == 0
        assert report["micro_coverage"] == 1.0
        assert len(report["micros"]) == 25


class TestPeriodReport:
    def test_averages_divide_by_period_length_not_logged_days(self):
        days = [f"2026-08-{d:02d}" for d in range(1, 8)]
        report = reports.build_period_report(
            days=days,
            meals=[meal(day=days[0], kcal=2100), meal(day=days[1], kcal=2100)],
            workouts=[],
            supplements=[],
            norms=NORMS,
        )
        assert report["averages"]["calories_eaten"] == 600  # 4200 / 7
        assert report["logged_days"] == 2

    def test_micro_average_compared_against_daily_norm(self):
        days = [f"2026-08-{d:02d}" for d in range(1, 8)]
        report = reports.build_period_report(
            days=days,
            meals=[meal(day=d, micros={"iron": 10.0}) for d in days],
            workouts=[],
            supplements=[],
            norms=NORMS,
        )
        iron = next(r for r in report["micros"] if r["key"] == "iron")
        assert iron["value"] == pytest.approx(10.0)


class TestProgress:
    def test_series_align_with_days(self):
        days = [f"2026-08-{d:02d}" for d in range(1, 8)]
        result = reports.build_progress(
            days=days,
            meals=[meal(day=days[0], kcal=2310), meal(day=days[2], kcal=1980)],
            workouts=[workout(day=days[0], kcal=420)],
            norms=NORMS,
        )
        assert len(result["calories"]) == 7
        assert result["calories"][0] == 2310
        assert result["calories"][1] == 0
        assert result["burned"][0] == 420

    def test_average_ignores_days_without_records(self):
        days = [f"2026-08-{d:02d}" for d in range(1, 8)]
        result = reports.build_progress(
            days=days,
            meals=[meal(day=days[0], kcal=2000), meal(day=days[1], kcal=3000)],
            workouts=[],
            norms=NORMS,
        )
        assert result["avg_calories"] == 2500

    def test_net_balance_subtracts_burn_and_norm(self):
        days = ["2026-08-01"]
        result = reports.build_progress(
            days=days,
            meals=[meal(day=days[0], kcal=3000)],
            workouts=[workout(day=days[0], kcal=500)],
            norms=NORMS,
        )
        assert result["net"][0] == pytest.approx(round(3000 - 500 - NORMS["calories"]))

    def test_burn_breakdown_by_kind(self):
        days = ["2026-08-01", "2026-08-02"]
        result = reports.build_progress(
            days=days,
            meals=[],
            workouts=[
                workout(day=days[0], kcal=420, kind_name="Плавание"),
                workout(day=days[1], kcal=300, kind_name="Плавание"),
                workout(day=days[1], kcal=590, kind_name="Падел"),
            ],
            norms=NORMS,
        )
        assert result["burned_by_kind"][0] == {"name": "Плавание", "kcal": 720}

    def test_empty_period_gives_zero_averages_and_message(self):
        days = [f"2026-08-{d:02d}" for d in range(1, 8)]
        result = reports.build_progress(days=days, meals=[], workouts=[], norms=NORMS)
        assert result["avg_calories"] == 0
        assert "нет записей" in result["summary"]
