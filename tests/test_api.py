"""Сквозные проверки HTTP-ручек."""

from __future__ import annotations

import pytest

DAY = "2026-08-10"


def add_meal(client, **overrides):
    payload = {
        "name": "Обед",
        "calories_kcal": 640,
        "protein_g": 52,
        "fat_g": 18,
        "carbs_g": 61,
        "meal_type": "lunch",
        "eaten_at": "12:40",
        "micros": {"iron": 3.1, "fiber": 6.2},
        **overrides,
    }
    response = client.post(f"/api/diary/{DAY}/meals", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestHealthAndReference:
    def test_health(self, client):
        assert client.get("/api/health").json()["status"] == "ok"

    def test_reference_lists_nutrients_and_kinds(self, client):
        data = client.get("/api/reference").json()
        assert len(data["nutrients"]) == 25
        assert any(k["key"] == "swimming" for k in data["workout_kinds"])


class TestProfile:
    def test_first_read_creates_default_profile_with_norms(self, client):
        data = client.get("/api/profile").json()
        assert data["goal"] == "maintain"
        assert data["norms"]["calories"] > 0
        assert len(data["norms"]["micros"]) == 25

    def test_update_recalculates_norms(self, client):
        before = client.get("/api/profile").json()["norms"]["calories"]
        after = client.put(
            "/api/profile",
            json={"weight_kg": 110, "height_cm": 190, "age": 30, "sex": "m", "goal": "gain"},
        ).json()
        assert after["norms"]["calories"] > before
        assert after["weight_kg"] == 110

    def test_out_of_range_values_rejected(self, client):
        assert client.put("/api/profile", json={"age": 500}).status_code == 422
        assert client.put("/api/profile", json={"activity": 5}).status_code == 422

    def test_body_fat_can_be_cleared(self, client):
        client.put("/api/profile", json={"body_fat_pct": 18})
        assert client.get("/api/profile").json()["body_fat_pct"] == 18
        client.put("/api/profile", json={"body_fat_pct": None})
        assert client.get("/api/profile").json()["body_fat_pct"] is None

    def test_omitted_field_is_not_touched(self, client):
        client.put("/api/profile", json={"body_fat_pct": 18})
        client.put("/api/profile", json={"weight_kg": 80})
        assert client.get("/api/profile").json()["body_fat_pct"] == 18

    def test_profile_persists_between_requests(self, client):
        client.put("/api/profile", json={"weight_kg": 84.2})
        assert client.get("/api/profile").json()["weight_kg"] == 84.2


class TestMeals:
    def test_create_and_read_day(self, client):
        add_meal(client)
        day = client.get(f"/api/diary/{DAY}").json()
        assert len(day["meals"]) == 1
        assert day["totals"]["calories_eaten"] == 640
        assert day["totals"]["protein_g"] == 52

    def test_meal_with_items_computes_totals_from_composition(self, client):
        meal = add_meal(
            client,
            calories_kcal=None,
            protein_g=0,
            items=[
                {
                    "name": "Куриная грудка",
                    "grams": 200,
                    "per100": {"calories_kcal": 165, "protein_g": 31, "fat_g": 3.6, "iron": 1.0},
                },
                {
                    "name": "Рис",
                    "grams": 150,
                    "per100": {"calories_kcal": 130, "protein_g": 2.7, "carbs_g": 28},
                },
            ],
        )
        assert meal["calories_kcal"] == pytest.approx(330 + 195)
        assert meal["protein_g"] == pytest.approx(62 + 4.05)
        assert meal["micros"]["iron"] == pytest.approx(2.0)
        assert meal["portion_g"] == 350

    def test_editing_grams_recalculates_totals(self, client):
        meal = add_meal(
            client,
            items=[
                {"name": "Овсянка", "grams": 100, "per100": {"calories_kcal": 380, "protein_g": 13}}
            ],
        )
        updated = client.patch(
            f"/api/meals/{meal['id']}",
            json={
                "items": [
                    {"name": "Овсянка", "grams": 50, "per100": {"calories_kcal": 380, "protein_g": 13}}
                ]
            },
        ).json()
        assert updated["calories_kcal"] == pytest.approx(190)
        assert updated["protein_g"] == pytest.approx(6.5)

    def test_patch_updates_single_field(self, client):
        meal = add_meal(client)
        updated = client.patch(f"/api/meals/{meal['id']}", json={"name": "Поздний обед"}).json()
        assert updated["name"] == "Поздний обед"
        assert updated["calories_kcal"] == 640

    def test_move_meal_to_another_day(self, client):
        meal = add_meal(client)
        client.patch(f"/api/meals/{meal['id']}", json={"day": "2026-08-11"})
        assert client.get(f"/api/diary/{DAY}").json()["meals"] == []
        assert len(client.get("/api/diary/2026-08-11").json()["meals"]) == 1

    def test_delete_meal(self, client):
        meal = add_meal(client)
        assert client.delete(f"/api/meals/{meal['id']}").status_code == 204
        assert client.get(f"/api/meals/{meal['id']}").status_code == 404

    def test_other_users_meal_is_invisible(self, client):
        meal = add_meal(client)
        client.headers["X-Dev-User-Id"] = "999999"
        assert client.get(f"/api/meals/{meal['id']}").status_code == 404
        assert client.delete(f"/api/meals/{meal['id']}").status_code == 404

    def test_invalid_day_format_rejected(self, client):
        assert client.get("/api/diary/10-08-2026").status_code == 400

    def test_negative_calories_rejected(self, client):
        response = client.post(f"/api/diary/{DAY}/meals", json={"name": "X", "calories_kcal": -5})
        assert response.status_code == 422

    def test_save_as_product_creates_product(self, client):
        add_meal(client, name="Мой протеин", save_as_product=True)
        products = client.get("/api/products", params={"q": "протеин"}).json()
        assert len(products) == 1
        assert products[0]["name"] == "Мой протеин"

    def test_day_feed_orders_meals_by_time(self, client):
        add_meal(client, name="Ужин", eaten_at="20:05")
        add_meal(client, name="Завтрак", eaten_at="08:20")
        names = [m["name"] for m in client.get(f"/api/diary/{DAY}").json()["meals"]]
        assert names == ["Завтрак", "Ужин"]


class TestWorkouts:
    def test_kcal_computed_from_profile_weight(self, client):
        client.put("/api/profile", json={"weight_kg": 84})
        workout = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "swimming", "minutes": 60}
        ).json()
        assert workout["kcal"] == pytest.approx(7.0 * 84)
        assert workout["kind_name"] == "Плавание"

    def test_explicit_kcal_wins(self, client):
        workout = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "padel", "minutes": 75, "kcal": 590}
        ).json()
        assert workout["kcal"] == 590

    def test_workout_reduces_day_balance(self, client):
        add_meal(client, calories_kcal=1840)
        client.post(f"/api/diary/{DAY}/workouts", json={"kind": "swimming", "minutes": 60, "kcal": 520})
        totals = client.get(f"/api/diary/{DAY}").json()["totals"]
        assert totals["calories_burned"] == 520
        assert totals["calories_net"] == 1320

    def test_patch_minutes_recalculates_kcal(self, client):
        client.put("/api/profile", json={"weight_kg": 80})
        workout = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "cycling", "minutes": 60}
        ).json()
        updated = client.patch(f"/api/workouts/{workout['id']}", json={"minutes": 30}).json()
        assert updated["kcal"] == pytest.approx(workout["kcal"] / 2)

    def test_delete_workout(self, client):
        workout = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "gym", "minutes": 45}
        ).json()
        assert client.delete(f"/api/workouts/{workout['id']}").status_code == 204
        assert client.get(f"/api/workouts/{DAY}").json() == []

    def test_estimate_endpoint_matches_created_workout(self, client):
        client.put("/api/profile", json={"weight_kg": 84.2})
        estimate = client.get(
            "/api/workout-estimate", params={"kind": "swimming", "minutes": 45}
        ).json()
        created = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "swimming", "minutes": 45}
        ).json()
        assert estimate["kcal"] == round(created["kcal"])

    def test_zero_minutes_rejected(self, client):
        response = client.post(f"/api/diary/{DAY}/workouts", json={"kind": "gym", "minutes": 0})
        assert response.status_code == 422


class TestTemplates:
    def test_starter_templates_seeded_on_first_read(self, client):
        templates = client.get("/api/workout-templates").json()
        assert len(templates) == 6
        assert {t["name"] for t in templates} >= {"Плавание", "Футбол", "Падел"}

    def test_template_kcal_follows_weight(self, client):
        client.put("/api/profile", json={"weight_kg": 60})
        light = client.get("/api/workout-templates").json()[0]["kcal"]
        client.put("/api/profile", json={"weight_kg": 120})
        heavy = client.get("/api/workout-templates").json()[0]["kcal"]
        assert heavy == pytest.approx(light * 2, rel=0.02)

    def test_create_and_delete_template(self, client):
        created = client.post(
            "/api/workout-templates", json={"name": "Утренний бег", "kind": "running", "minutes": 25}
        ).json()
        assert created["kcal"] > 0
        assert client.delete(f"/api/workout-templates/{created['id']}").status_code == 204

    def test_workout_can_be_saved_as_template(self, client):
        client.get("/api/workout-templates")  # посеять стартовые
        client.post(
            f"/api/diary/{DAY}/workouts",
            json={"kind": "tennis", "minutes": 50, "save_as_template": True, "template_name": "Теннис вечером"},
        )
        names = [t["name"] for t in client.get("/api/workout-templates").json()]
        assert "Теннис вечером" in names


class TestSupplements:
    def test_crud(self, client):
        created = client.post(
            "/api/supplements",
            json={"name": "D3+K2", "nutrient_key": "vit_d", "dose": 2000, "unit": "МЕ", "when_label": "утром"},
        ).json()
        assert created["active"] is True
        client.patch(f"/api/supplements/{created['id']}", json={"dose": 1000})
        assert client.get("/api/supplements").json()[0]["dose"] == 1000
        assert client.delete(f"/api/supplements/{created['id']}").status_code == 204
        assert client.get("/api/supplements").json() == []

    def test_active_supplement_appears_in_day_feed(self, client):
        client.post(
            "/api/supplements",
            json={"name": "Магний", "nutrient_key": "magnesium", "dose": 200, "unit": "мг"},
        )
        assert len(client.get(f"/api/diary/{DAY}").json()["supplements"]) == 1

    def test_inactive_supplement_hidden_from_day_feed(self, client):
        created = client.post(
            "/api/supplements",
            json={"name": "Креатин", "nutrient_key": None, "dose": 5, "unit": "г"},
        ).json()
        client.patch(f"/api/supplements/{created['id']}", json={"active": False})
        assert client.get(f"/api/diary/{DAY}").json()["supplements"] == []


class TestReports:
    def test_day_report_shape(self, client):
        add_meal(client)
        report = client.get(f"/api/report/day/{DAY}").json()
        assert len(report["micros"]) == 25
        assert [m["key"] for m in report["macros"]] == ["calories", "protein", "fat", "carbs"]
        assert report["deficits"]

    def test_period_report_covers_seven_days(self, client):
        add_meal(client)
        report = client.get("/api/report/period", params={"range": "week", "end": DAY}).json()
        assert len(report["days"]) == 7
        assert report["days"][-1] == DAY
        assert report["logged_days"] == 1

    def test_month_report_covers_thirty_days(self, client):
        report = client.get("/api/report/period", params={"range": "month", "end": DAY}).json()
        assert len(report["days"]) == 30

    def test_progress_series_and_tiles(self, client):
        add_meal(client, calories_kcal=2000)
        client.post(f"/api/diary/{DAY}/workouts", json={"kind": "swimming", "minutes": 45})
        progress = client.get("/api/progress", params={"range": "week", "end": DAY}).json()
        assert len(progress["calories"]) == 7
        assert len(progress["tiles"]) == 4
        assert progress["burned_by_kind"][0]["name"] == "Плавание"

    def test_progress_on_empty_history(self, client):
        progress = client.get("/api/progress", params={"range": "week", "end": DAY}).json()
        assert progress["avg_calories"] == 0
        assert all(v == 0 for v in progress["calories"])

    def test_week_strip_returns_monday_to_sunday(self, client):
        strip = client.get(f"/api/diary/{DAY}/week").json()
        assert len(strip["days"]) == 7
        assert strip["days"][0]["day"] == "2026-08-10"  # 10 августа 2026 — понедельник


class TestProducts:
    def test_search_is_case_insensitive_for_cyrillic(self, client):
        client.post("/api/products", json={"name": "Протеиновый коктейль", "protein_g": 25})
        assert len(client.get("/api/products", params={"q": "протеин"}).json()) == 1

    def test_empty_query_returns_all(self, client):
        client.post("/api/products", json={"name": "Творог", "protein_g": 18})
        client.post("/api/products", json={"name": "Кефир", "protein_g": 3})
        assert len(client.get("/api/products").json()) == 2

    def test_product_keeps_micros(self, client):
        created = client.post(
            "/api/products",
            json={"name": "Печень", "protein_g": 20, "micros": {"iron": 6.9, "неизвестное": 1}},
        ).json()
        assert created["micros"] == {"iron": 6.9}


class TestAi:
    def test_parse_returns_503_without_api_key(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        response = client.post("/api/ai/parse", json={"text": "омлет из трёх яиц"})
        assert response.status_code == 503

    def test_oversized_image_rejected(self, client):
        response = client.post(
            "/api/ai/parse", json={"text": "", "image_base64": "x" * (13 * 1024 * 1024)}
        )
        assert response.status_code == 413


ITEMS = [
    {"name": "Яйцо", "grams": 100, "per100": {"calories_kcal": 155, "protein_g": 13, "iron": 1.2}},
    {"name": "Сыр", "grams": 50, "per100": {"calories_kcal": 350, "protein_g": 25, "calcium": 700}},
]


class TestMealPatchSemantics:
    def test_other_user_cannot_strip_items_via_delete(self, client):
        meal = add_meal(client, items=ITEMS)
        assert len(meal["items"]) == 2
        client.headers["X-Dev-User-Id"] = "999"
        assert client.delete(f"/api/meals/{meal['id']}").status_code == 404
        client.headers["X-Dev-User-Id"] = "424242"
        assert len(client.get(f"/api/meals/{meal['id']}").json()["items"]) == 2

    def test_empty_day_rejected(self, client):
        meal = add_meal(client)
        assert client.patch(f"/api/meals/{meal['id']}", json={"day": ""}).status_code == 422
        assert client.patch(f"/api/meals/{meal['id']}", json={"day": "2026-13-40"}).status_code == 400

    def test_empty_items_removes_composition_but_keeps_totals(self, client):
        meal = add_meal(client, items=ITEMS)
        updated = client.patch(f"/api/meals/{meal['id']}", json={"items": []}).json()
        assert updated["items"] == []
        assert updated["calories_kcal"] == pytest.approx(155 + 175)
        assert updated["portion_g"] == pytest.approx(150)

    def test_items_override_explicit_totals_like_on_create(self, client):
        meal = add_meal(client, items=ITEMS)
        updated = client.patch(
            f"/api/meals/{meal['id']}", json={"items": ITEMS, "calories_kcal": 1}
        ).json()
        assert updated["calories_kcal"] == pytest.approx(330)

    def test_null_clears_nullable_fields(self, client):
        meal = add_meal(client, ingredients="яйца, сыр", portion_g=150)
        updated = client.patch(
            f"/api/meals/{meal['id']}", json={"eaten_at": None, "ingredients": None}
        ).json()
        assert updated["eaten_at"] is None
        assert updated["ingredients"] is None

    def test_null_does_not_wipe_required_fields(self, client):
        meal = add_meal(client)
        updated = client.patch(f"/api/meals/{meal['id']}", json={"name": None}).json()
        assert updated["name"] == "Обед"

    def test_bad_time_rejected(self, client):
        meal = add_meal(client)
        assert client.patch(f"/api/meals/{meal['id']}", json={"eaten_at": "zzzzz"}).status_code == 422
        assert client.patch(f"/api/meals/{meal['id']}", json={"eaten_at": "9:30"}).status_code == 422
        assert client.patch(f"/api/meals/{meal['id']}", json={"eaten_at": "09:30"}).status_code == 200


class TestWorkoutPatchSemantics:
    def test_null_clears_note_and_time(self, client):
        created = client.post(
            f"/api/diary/{DAY}/workouts",
            json={"kind": "swimming", "minutes": 45, "note": "бассейн", "done_at": "07:30"},
        ).json()
        updated = client.patch(
            f"/api/workouts/{created['id']}", json={"note": None, "done_at": None}
        ).json()
        assert updated["note"] is None
        assert updated["done_at"] is None

    def test_bad_time_rejected(self, client):
        response = client.post(
            f"/api/diary/{DAY}/workouts", json={"kind": "swimming", "minutes": 45, "done_at": "25:99"}
        )
        assert response.status_code == 422


class TestSupplementUnits:
    def test_latin_units_are_normalized(self, client):
        created = client.post(
            "/api/supplements",
            json={"name": "Магний", "nutrient_key": "magnesium", "dose": 400, "unit": "mg"},
        ).json()
        assert created["unit"] == "мг"
        report = client.get(f"/api/report/day/{DAY}").json()
        magnesium = next(r for r in report["micros"] if r["key"] == "magnesium")
        assert magnesium["value"] == pytest.approx(400)

    def test_vitamin_d_in_iu_counts(self, client):
        client.post(
            "/api/supplements",
            json={"name": "D3", "nutrient_key": "vit_d", "dose": 2000, "unit": "IU"},
        )
        report = client.get(f"/api/report/day/{DAY}").json()
        vit_d = next(r for r in report["micros"] if r["key"] == "vit_d")
        assert vit_d["value"] == pytest.approx(2000)

    def test_unknown_unit_and_nutrient_rejected(self, client):
        base = {"name": "X", "dose": 1}
        assert client.post("/api/supplements", json={**base, "unit": "шт"}).status_code == 422
        assert (
            client.post("/api/supplements", json={**base, "unit": "мг", "nutrient_key": "unobtainium"}).status_code
            == 422
        )

    def test_nutrient_key_can_be_cleared(self, client):
        created = client.post(
            "/api/supplements",
            json={"name": "Магний", "nutrient_key": "magnesium", "dose": 400, "unit": "мг"},
        ).json()
        updated = client.patch(f"/api/supplements/{created['id']}", json={"nutrient_key": None}).json()
        assert updated["nutrient_key"] is None


class TestProgressDeltas:
    def test_no_previous_data_means_no_delta(self, client):
        add_meal(client)
        data = client.get(f"/api/progress?range=week&end={DAY}").json()
        tile = next(t for t in data["tiles"] if t["key"] == "avg_calories")
        assert tile["delta"] is None
        assert "нет данных" in tile["hint"]

    def test_delta_against_previous_week(self, client):
        client.post("/api/diary/2026-08-01/meals", json={"name": "Ранее", "calories_kcal": 400, "protein_g": 10})
        add_meal(client)
        data = client.get(f"/api/progress?range=week&end={DAY}").json()
        tile = next(t for t in data["tiles"] if t["key"] == "avg_calories")
        assert tile["delta"] == 240


class TestAiInput:
    def test_empty_text_is_client_error(self, client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert client.post("/api/ai/parse", json={"text": "   "}).status_code == 400

    def test_raw_base64_without_data_url_is_client_error(self, client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        response = client.post("/api/ai/parse", json={"text": "", "image_base64": "iVBORw0KGgo="})
        assert response.status_code == 400


class TestConcurrentFirstOpen:
    def test_parallel_first_requests_create_one_profile(self, client):
        """Первое открытие мини-аппа шлёт профиль, день и полосу недели разом."""
        import concurrent.futures as futures

        paths = [f"/api/diary/{DAY}", "/api/profile", f"/api/diary/{DAY}/week"] * 4
        with futures.ThreadPoolExecutor(max_workers=len(paths)) as pool:
            codes = [r.status_code for r in pool.map(lambda p: client.get(p), paths)]
        assert codes == [200] * len(paths)
