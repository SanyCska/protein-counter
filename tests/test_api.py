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

    def test_vitamin_d_in_mcg_converts_to_iu(self, client):
        # С этикеток витамин D чаще идёт в мкг; в отчёте он живёт в МЕ, 1 мкг = 40 МЕ.
        client.post(
            "/api/supplements",
            json={"name": "D3", "nutrient_key": "vit_d", "dose": 7, "unit": "мкг"},
        )
        report = client.get(f"/api/report/day/{DAY}").json()
        vit_d = next(r for r in report["micros"] if r["key"] == "vit_d")
        assert vit_d["value"] == pytest.approx(280)

    def test_vitamin_a_in_mcg_is_taken_as_is(self, client):
        client.post(
            "/api/supplements",
            json={"name": "Витамин A", "nutrient_key": "vit_a", "dose": 2800, "unit": "мкг"},
        )
        report = client.get(f"/api/report/day/{DAY}").json()
        vit_a = next(r for r in report["micros"] if r["key"] == "vit_a")
        assert vit_a["value"] == pytest.approx(2800)

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


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = type("Msg", (), {"content": content})()


class FakeCompletions:
    """Минимальный двойник OpenAI: отдаёт заранее заданные ответы по очереди."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][-1]["content"])
        reply = self.replies.pop(0) if self.replies else self.replies_default
        if isinstance(reply, Exception):
            raise reply
        return type("Resp", (), {"choices": [FakeChoice(reply)]})()

    replies_default = "{}"


class FakeOpenAI:
    def __init__(self, replies: list[str]) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(replies)})()

    @property
    def prompts(self) -> list[str]:
        return self.chat.completions.prompts


PRODUCT_REPLY = (
    '{"confidence": "medium", "comment": "по составу творога",'
    ' "per100": {"calcium": 120, "b12": 0.4, "fiber_g": 0.5, "выдумка": 5}}'
)


@pytest.fixture
def fake_ai(monkeypatch):
    """Подменяет клиента OpenAI, чтобы тесты не ходили в сеть."""
    created: list[FakeOpenAI] = []

    def factory(replies: list[str]) -> FakeOpenAI:
        fake = FakeOpenAI(replies)
        created.append(fake)
        monkeypatch.setattr("api.ai.client", lambda: fake)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        return fake

    return factory


class TestCaloriesOverride:
    def test_manual_norm_is_returned_and_marked(self, client):
        auto = client.get("/api/profile").json()["norms"]
        assert auto["calories_source"] == "computed"

        norms = client.put("/api/profile", json={"calories_override": 2400}).json()["norms"]
        assert norms["calories"] == 2400
        assert norms["calories_source"] == "manual"
        assert norms["calories_computed"] == auto["calories"]

    def test_manual_norm_drives_day_totals(self, client):
        client.put("/api/profile", json={"calories_override": 2000})
        add_meal(client, calories_kcal=500, protein_g=10)
        totals = client.get(f"/api/diary/{DAY}").json()["totals"]
        assert totals["calories_remaining"] == 1500

    def test_null_returns_to_formula(self, client):
        client.put("/api/profile", json={"calories_override": 2400})
        profile = client.put("/api/profile", json={"calories_override": None}).json()
        assert profile["calories_override"] is None
        assert profile["norms"]["calories_source"] == "computed"

    def test_absurd_values_rejected(self, client):
        assert client.put("/api/profile", json={"calories_override": 100}).status_code == 422
        assert client.put("/api/profile", json={"calories_override": 99999}).status_code == 422

    def test_weight_change_keeps_manual_norm(self, client):
        client.put("/api/profile", json={"calories_override": 2400})
        norms = client.put("/api/profile", json={"weight_kg": 90}).json()["norms"]
        assert norms["calories"] == 2400


class TestProductMicroEstimate:
    def test_estimate_before_saving_scales_to_portion(self, client, fake_ai):
        fake = fake_ai([PRODUCT_REPLY])
        result = client.post(
            "/api/ai/product",
            json={"name": "Творог 5%", "portion_g": 200, "calories_kcal": 240, "protein_g": 34},
        ).json()
        assert result["per100"]["calcium"] == 120
        assert result["micros"]["calcium"] == 240
        assert result["fiber_g"] == 1
        assert "выдумка" not in result["micros"]
        assert "Творог 5%" in fake.prompts[0] and "240" in fake.prompts[0]

    def test_estimate_without_portion_assumes_100g(self, client, fake_ai):
        fake_ai([PRODUCT_REPLY])
        result = client.post("/api/ai/product", json={"name": "Творог"}).json()
        assert result["portion_g"] == 100
        assert result["micros"]["calcium"] == 120

    def test_estimate_saves_micros_to_existing_product(self, client, fake_ai):
        fake_ai([PRODUCT_REPLY])
        product = client.post(
            "/api/products", json={"name": "Творог", "protein_g": 34, "portion_g": 200}
        ).json()
        assert product["micros"] == {}
        updated = client.post(f"/api/products/{product['id']}/estimate").json()
        assert updated["micros"]["calcium"] == 240
        assert updated["fiber_g"] == 1
        assert client.get("/api/products").json()[0]["micros"]["calcium"] == 240

    def test_estimate_does_not_touch_known_fiber(self, client, fake_ai):
        fake_ai([PRODUCT_REPLY])
        product = client.post(
            "/api/products", json={"name": "Творог", "protein_g": 34, "fiber_g": 7}
        ).json()
        assert client.post(f"/api/products/{product['id']}/estimate").json()["fiber_g"] == 7

    def test_estimate_of_missing_product_is_404(self, client, fake_ai):
        fake_ai([PRODUCT_REPLY])
        assert client.post("/api/products/999/estimate").status_code == 404

    def test_estimate_without_key_is_503(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        product = client.post("/api/products", json={"name": "Творог", "protein_g": 34}).json()
        assert client.post(f"/api/products/{product['id']}/estimate").status_code == 503


class TestProductsBatchEstimate:
    def test_only_products_without_micros_are_estimated(self, client, fake_ai):
        fake = fake_ai([PRODUCT_REPLY, PRODUCT_REPLY])
        client.post("/api/products", json={"name": "Творог", "protein_g": 34})
        client.post("/api/products", json={"name": "Кефир", "protein_g": 3})
        client.post(
            "/api/products", json={"name": "Печень", "protein_g": 20, "micros": {"iron": 6.9}}
        )

        result = client.post("/api/products/estimate").json()
        assert len(result["updated"]) == 2
        assert result["failed"] == 0
        assert result["remaining"] == 0
        assert len(fake.prompts) == 2
        assert {p["name"] for p in result["updated"]} == {"Творог", "Кефир"}

    def test_broken_answer_for_one_product_does_not_break_batch(self, client, fake_ai):
        fake_ai(["не json", PRODUCT_REPLY])
        client.post("/api/products", json={"name": "Творог", "protein_g": 34})
        client.post("/api/products", json={"name": "Кефир", "protein_g": 3})

        result = client.post("/api/products/estimate").json()
        assert result["failed"] == 1
        assert len(result["updated"]) == 1
        assert result["updated"][0]["name"] == "Кефир"
        # Творог остался без состава и попадёт в следующий запуск.
        assert result["remaining"] == 1
        assert result["error"] is None

    def test_nothing_to_do_is_not_an_error(self, client, fake_ai):
        fake_ai([])
        result = client.post("/api/products/estimate").json()
        assert result == {"updated": [], "failed": 0, "remaining": 0, "error": None}

    def test_without_key_batch_is_503(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        client.post("/api/products", json={"name": "Творог", "protein_g": 34})
        assert client.post("/api/products/estimate").status_code == 503


class TestProductPatch:
    def test_micros_can_be_edited_by_hand(self, client):
        product = client.post("/api/products", json={"name": "Творог", "protein_g": 34}).json()
        updated = client.patch(
            f"/api/products/{product['id']}", json={"micros": {"calcium": 300, "чушь": 1}}
        ).json()
        assert updated["micros"] == {"calcium": 300}

    def test_other_users_product_is_invisible(self, client):
        product = client.post("/api/products", json={"name": "Творог", "protein_g": 34}).json()
        client.headers["X-Dev-User-Id"] = "999"
        assert client.patch(f"/api/products/{product['id']}", json={"name": "Чужое"}).status_code == 404
        assert client.post(f"/api/products/{product['id']}/estimate").status_code == 404


class TestProductDeduplication:
    def test_same_name_updates_instead_of_duplicating(self, client):
        client.post(
            "/api/products",
            json={"name": "Творог 5%", "protein_g": 34, "calories_kcal": 240, "micros": {"calcium": 240}},
        )
        client.post(
            "/api/products",
            json={"name": "творог 5%", "protein_g": 36, "calories_kcal": 250},
        )
        products = client.get("/api/products").json()
        assert len(products) == 1
        assert products[0]["protein_g"] == 36
        assert products[0]["calories_kcal"] == 250
        # Состав, добытый раньше, ручная перезапись не стирает.
        assert products[0]["micros"] == {"calcium": 240}

    def test_new_micros_replace_old_ones(self, client):
        client.post(
            "/api/products", json={"name": "Творог", "protein_g": 34, "micros": {"calcium": 240}}
        )
        client.post(
            "/api/products", json={"name": "Творог", "protein_g": 34, "micros": {"calcium": 300}}
        )
        assert client.get("/api/products").json()[0]["micros"] == {"calcium": 300}

    def test_saving_a_meal_twice_keeps_one_product(self, client):
        for _ in range(3):
            add_meal(client, name="Овсянка", save_as_product=True)
        assert len(client.get("/api/products").json()) == 1


PHOTO = "data:image/jpeg;base64,/9j/test"

LABEL_REPLY = (
    '{"name": "Творожок ванильный", "portion": 200, "portion_unit": "г",'
    ' "confidence": "high", "comment": "таблица читается",'
    ' "per100": {"calories_kcal": 120, "protein_g": 8, "fat_g": 3, "carbs_g": 14,'
    ' "fiber_g": 0.5, "calcium": 110, "выдумка": 5}}'
)

SUPPLEMENT_REPLY = (
    '{"name": "Мультивитамины", "serving": 2, "serving_unit": "капсула",'
    ' "when_label": "утром", "confidence": "medium",'
    ' "comment": "состав с банки", "items": ['
    '{"name": "Витамин D3", "nutrient_key": "vit_d", "dose": 2000, "unit": "IU"},'
    '{"name": "Магний", "nutrient_key": "magnesium", "dose": 400, "unit": "mg"},'
    '{"name": "Коллаген", "nutrient_key": null, "dose": 5, "unit": "g"}]}'
)


class TestLabelPhoto:
    def test_label_scales_per100_to_portion(self, client, fake_ai):
        fake_ai([LABEL_REPLY])
        result = client.post("/api/ai/label", json={"image_base64": PHOTO}).json()
        assert result["name"] == "Творожок ванильный"
        assert result["portion_g"] == 200
        assert result["portion_unit"] == "г"
        assert result["per100"]["calories_kcal"] == 120
        assert result["calories_kcal"] == 240
        assert result["protein_g"] == 16
        assert result["micros"]["calcium"] == 220
        assert "выдумка" not in result["micros"]

    def test_fiber_is_kept_in_both_places(self, client, fake_ai):
        fake_ai([LABEL_REPLY])
        result = client.post("/api/ai/label", json={"image_base64": PHOTO}).json()
        assert result["fiber_g"] == 1
        assert result["micros"]["fiber"] == 1

    def test_drink_label_keeps_millilitres(self, client, fake_ai):
        fake_ai(['{"name": "Молоко", "portion": 250, "portion_unit": "ml",'
                 ' "per100": {"calories_kcal": 60, "protein_g": 3}}'])
        result = client.post("/api/ai/label", json={"image_base64": PHOTO}).json()
        assert result["portion_unit"] == "мл"
        assert result["calories_kcal"] == 150

    def test_label_without_portion_falls_back_to_100(self, client, fake_ai):
        fake_ai(['{"name": "Овсянка", "portion": null, "per100": {"calories_kcal": 370}}'])
        result = client.post("/api/ai/label", json={"image_base64": PHOTO}).json()
        assert result["portion_g"] is None
        assert result["calories_kcal"] == 370
        assert result["portion_unit"] == "г"

    def test_photo_is_sent_to_the_model(self, client, fake_ai):
        fake = fake_ai([LABEL_REPLY])
        client.post("/api/ai/label", json={"image_base64": PHOTO, "text": "творожок"})
        content = fake.prompts[0]
        assert content[0]["text"] == "творожок"
        assert content[1]["image_url"]["url"] == PHOTO

    def test_raw_base64_without_data_url_is_client_error(self, client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert client.post("/api/ai/label", json={"image_base64": "iVBOR"}).status_code == 400

    def test_photo_is_required(self, client):
        assert client.post("/api/ai/label", json={"text": "творог"}).status_code == 422

    def test_oversized_image_rejected(self, client):
        response = client.post(
            "/api/ai/label", json={"image_base64": "x" * (13 * 1024 * 1024)}
        )
        assert response.status_code == 413

    def test_without_key_is_503(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert client.post("/api/ai/label", json={"image_base64": PHOTO}).status_code == 503


class TestSupplementLabelPhoto:
    def test_all_substances_are_returned_with_normalized_units(self, client, fake_ai):
        fake_ai([SUPPLEMENT_REPLY])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        assert result["name"] == "Мультивитамины"
        assert result["when_label"] == "утром"
        assert [i["unit"] for i in result["items"]] == ["МЕ", "мг", "г"]
        assert [i["dose"] for i in result["items"]] == [2000, 400, 5]

    def test_label_serving_is_returned_for_rescaling(self, client, fake_ai):
        fake_ai([SUPPLEMENT_REPLY])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        # Дозы отданы как на банке — на две капсулы; пересчёт под свой приём делает клиент.
        assert result["serving"] == 2
        assert result["serving_unit"] == "капсула"
        assert result["items"][1]["dose"] == 400

    def test_missing_serving_means_one_unit(self, client, fake_ai):
        fake_ai(['{"name": "X", "items": [{"name": "Цинк", "dose": 15, "unit": "мг"}]}'])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        assert result["serving"] == 1
        assert result["serving_unit"] == "порция"

    def test_invented_serving_unit_falls_back_to_portion(self, client, fake_ai):
        fake_ai(['{"name": "X", "serving": 3, "serving_unit": "софтгель",'
                 ' "items": [{"name": "Цинк", "dose": 15, "unit": "мг"}]}'])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        assert result["serving"] == 3
        assert result["serving_unit"] == "порция"

    def test_absurd_serving_is_capped(self, client, fake_ai):
        fake_ai(['{"name": "X", "serving": 900, "serving_unit": "капсула",'
                 ' "items": [{"name": "Цинк", "dose": 15, "unit": "мг"}]}'])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        assert result["serving"] == 20

    def test_substance_outside_catalog_is_kept_without_key(self, client, fake_ai):
        fake_ai([SUPPLEMENT_REPLY])
        result = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()
        collagen = result["items"][2]
        assert collagen["name"] == "Коллаген"
        assert collagen["nutrient_key"] is None

    def test_unknown_key_and_zero_dose_are_dropped(self, client, fake_ai):
        fake_ai(['{"name": "X", "items": ['
                 '{"name": "Юникорний", "nutrient_key": "unobtainium", "dose": 5, "unit": "мг"},'
                 '{"name": "Пустышка", "dose": 0, "unit": "мг"},'
                 '{"name": "Без имени", "dose": 5, "unit": "мг"}]}'])
        items = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()["items"]
        assert [i["name"] for i in items] == ["Юникорний", "Без имени"]
        assert items[0]["nutrient_key"] is None

    def test_garbage_in_nutrient_key_does_not_break_parsing(self, client, fake_ai):
        fake_ai(['{"name": "X", "items": ['
                 '{"name": "Цинк", "nutrient_key": {"key": "zinc"}, "dose": 15, "unit": "мг"}]}'])
        items = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()["items"]
        assert items == [{"name": "Цинк", "nutrient_key": None, "dose": 15, "unit": "мг"}]

    def test_unreadable_unit_falls_back_to_mg(self, client, fake_ai):
        fake_ai('{"name": "X", "items": [{"name": "Цинк", "dose": 15, "unit": "капсул"}]}'.split("\n"))
        items = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO}).json()["items"]
        assert items[0]["unit"] == "мг"

    def test_without_key_is_503(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        response = client.post("/api/ai/supplement-label", json={"image_base64": PHOTO})
        assert response.status_code == 503


def add_jar(client, name="Мультивитамины", items=None):
    payload = {
        "name": name,
        "items": items
        or [
            {"name": "D3", "nutrient_key": "vit_d", "dose": 2000, "unit": "IU"},
            {"name": "Магний", "nutrient_key": "magnesium", "dose": 400, "unit": "mg"},
        ],
    }
    response = client.post("/api/supplements/bulk", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


class TestSupplementsBulk:
    def test_whole_label_is_saved_at_once(self, client):
        created = add_jar(client)
        assert [s["unit"] for s in created] == ["МЕ", "мг"]
        assert len(client.get("/api/supplements").json()) == 2

    def test_all_substances_share_the_name_user_typed(self, client):
        created = add_jar(client, name="Мультивитамины Solgar")
        assert {s["group_name"] for s in created} == {"Мультивитамины Solgar"}
        # Название вещества остаётся своим — оно нужно и в списке банки, и в отчёте.
        assert [s["name"] for s in created] == ["D3", "Магний"]

    def test_name_in_items_does_not_split_the_jar(self, client):
        created = add_jar(
            client,
            name="Мультивитамины",
            items=[
                {"name": "D3", "dose": 2000, "unit": "МЕ", "group_name": "Другая банка"},
                {"name": "Магний", "dose": 400, "unit": "мг"},
            ],
        )
        assert {s["group_name"] for s in created} == {"Мультивитамины"}

    def test_single_supplement_has_no_group(self, client):
        created = client.post(
            "/api/supplements", json={"name": "Магний", "dose": 400, "unit": "мг"}
        ).json()
        assert created["group_name"] is None

    def test_saved_substances_count_in_the_day_report(self, client):
        add_jar(client)
        report = client.get(f"/api/report/day/{DAY}").json()
        magnesium = next(r for r in report["micros"] if r["key"] == "magnesium")
        vit_d = next(r for r in report["micros"] if r["key"] == "vit_d")
        assert magnesium["value"] == pytest.approx(400)
        assert vit_d["value"] == pytest.approx(2000)

    def test_third_of_a_tablet_counts_as_a_third(self, client):
        """Этикетка Opti-Men считает дозы на три таблетки, а пьют одну."""
        created = client.post(
            "/api/supplements/bulk",
            json={
                "name": "Оптимен",
                "label_serving": 3,
                "taken_serving": 1,
                "items": [
                    {"name": "Витамин A", "nutrient_key": "vit_a", "dose": 2800, "unit": "мкг"},
                    {"name": "Цинк", "nutrient_key": "zinc", "dose": 15, "unit": "мг"},
                ],
            },
        ).json()
        # Доза остаётся как на этикетке, а принимается треть.
        assert [s["dose"] for s in created] == [2800, 15]
        assert created[0]["effective_dose"] == pytest.approx(933.3333, abs=0.001)

        report = client.get(f"/api/report/day/{DAY}").json()
        vit_a = next(r for r in report["micros"] if r["key"] == "vit_a")
        zinc = next(r for r in report["micros"] if r["key"] == "zinc")
        assert vit_a["value"] == pytest.approx(933.333, abs=0.01)
        assert zinc["value"] == pytest.approx(5)

    def test_whole_serving_is_the_default(self, client):
        created = add_jar(client)
        assert created[0]["label_serving"] == 1
        assert created[0]["taken_serving"] == 1
        assert created[0]["effective_dose"] == created[0]["dose"]

    def test_taken_serving_can_be_changed_later(self, client):
        created = add_jar(client)
        updated = client.patch(
            f"/api/supplements/{created[0]['id']}", json={"label_serving": 3, "taken_serving": 1}
        ).json()
        assert updated["effective_dose"] == pytest.approx(updated["dose"] / 3)

    def test_absurd_serving_rejected(self, client):
        response = client.post(
            "/api/supplements",
            json={"name": "X", "dose": 1, "unit": "мг", "taken_serving": 0},
        )
        assert response.status_code == 422

    def test_jar_is_deleted_as_a_whole(self, client):
        created = add_jar(client)
        ids = [s["id"] for s in created]
        assert client.post("/api/supplements/bulk-delete", json={"ids": ids}).status_code == 204
        assert client.get("/api/supplements").json() == []

    def test_other_users_jar_is_not_deleted(self, client, user_id):
        created = add_jar(client)
        ids = [s["id"] for s in created]
        client.headers["X-Dev-User-Id"] = "999"
        assert client.post("/api/supplements/bulk-delete", json={"ids": ids}).status_code == 404
        client.headers["X-Dev-User-Id"] = str(user_id)
        assert len(client.get("/api/supplements").json()) == 2

    def test_name_is_required(self, client):
        response = client.post(
            "/api/supplements/bulk",
            json={"items": [{"name": "Магний", "dose": 400, "unit": "мг"}]},
        )
        assert response.status_code == 422

    def test_empty_list_rejected(self, client):
        assert (
            client.post("/api/supplements/bulk", json={"name": "X", "items": []}).status_code
            == 422
        )

    def test_one_bad_item_rejects_the_whole_batch(self, client):
        response = client.post(
            "/api/supplements/bulk",
            json={
                "name": "Банка",
                "items": [
                    {"name": "Магний", "dose": 400, "unit": "мг"},
                    {"name": "Ерунда", "dose": 1, "unit": "шт"},
                ],
            },
        )
        assert response.status_code == 422
        assert client.get("/api/supplements").json() == []


class TestPortionUnits:
    def test_meal_keeps_millilitres(self, client):
        meal = add_meal(client, name="Кефир", portion_g=250, portion_unit="мл")
        assert meal["portion_unit"] == "мл"
        assert client.get(f"/api/meals/{meal['id']}").json()["portion_unit"] == "мл"

    def test_grams_are_the_default(self, client):
        assert add_meal(client)["portion_unit"] == "г"
        product = client.post("/api/products", json={"name": "Творог", "protein_g": 34}).json()
        assert product["portion_unit"] == "г"

    def test_latin_unit_is_normalized(self, client):
        assert add_meal(client, portion_g=250, portion_unit="ml")["portion_unit"] == "мл"
        product = client.post(
            "/api/products", json={"name": "Сок", "protein_g": 0, "portion_unit": "ml"}
        ).json()
        assert product["portion_unit"] == "мл"

    def test_unknown_unit_rejected(self, client):
        response = client.post(
            f"/api/diary/{DAY}/meals", json={"name": "X", "protein_g": 1, "portion_unit": "шт"}
        )
        assert response.status_code == 422

    def test_unit_travels_into_the_saved_product(self, client):
        add_meal(client, name="Кефир", portion_g=250, portion_unit="мл", save_as_product=True)
        assert client.get("/api/products").json()[0]["portion_unit"] == "мл"

    def test_unit_can_be_patched(self, client):
        meal = add_meal(client, name="Кефир", portion_g=250)
        assert client.patch(f"/api/meals/{meal['id']}", json={"portion_unit": "мл"}).json()[
            "portion_unit"
        ] == "мл"
