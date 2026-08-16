# Nutrition Mini App — дизайн бэкенда и фронтенда

Дата: 2026-08-16. Статус: реализуется автономно (пользователь попросил сделать без промежуточных согласований).

## Что строим

Telegram Mini App поверх существующего бота `protein_counter`. Бот остаётся точкой быстрого ввода
(текст/фото → ИИ разбирает КБЖУ), мини-апп даёт полный контроль: правка блюд, нормы, микронутриенты,
физнагрузка, прогресс, рекомендации.

Два репозитория:

- `protein_counter` — бот + **новый HTTP API** (`api/`), общая SQLite-база.
- `protein_web` — фронтенд мини-аппа (Vite + React + TS).

## Ключевые решения

**Одна база на бота и API.** Бот уже пишет в `data/protein.sqlite3` через `storage.py`. API работает
с тем же файлом и расширяет схему аддитивными миграциями (`ALTER TABLE ADD COLUMN`, новые таблицы),
чтобы старый код бота продолжал работать без изменений. Альтернатива (отдельная БД + синхронизация)
даёт лишнюю сложность без выгоды при одном пользователе-владельце.

**Аутентификация — Telegram `initData`.** Фронт шлёт заголовок `Authorization: tma <initData>`;
сервер валидирует HMAC-SHA256 с ключом `HMAC("WebAppData", bot_token)` и проверяет `auth_date`
(TTL 24 ч). `user_id` берётся только из проверенной подписи, никогда из тела запроса. В деве
(`APP_ENV=dev`) допускается заголовок `X-Dev-User-Id`.

**Нутриенты — единый словарь.** 25 микронутриентов из макета плюс клетчатка живут в
`api/nutrition/catalog.py`: ключ, русское имя, единица, RDA (с разбивкой по полу). Всё — граммы/мг/мкг/МЕ
в фиксированных единицах, храним в JSON-колонке `micros_json` на блюде и на продукте.

**Микронутриенты блюда даёт ИИ.** Расширяем промпт: одним вызовом получаем КБЖУ + разбивку по
ингредиентам + микронутриенты. Данные, введённые вручную, микронутриентов не имеют (нули) — в отчёте
это честно видно как «покрытие данными N%».

**Расход на нагрузке — MET × вес × часы.** MET-таблица в `api/nutrition/workouts.py`. Шаблоны
нагрузок хранят тип и длительность, ккал пересчитывается от текущего веса пользователя.

**Норма — Mifflin–St Jeor.** BMR × коэффициент активности, затем ±% под цель (снижение −15%,
поддержание 0, набор +12%). Белок 1.8 г/кг (снижение 2.0), жиры 0.9 г/кг, углеводы — остаток.
Нагрузка из дневника прибавляется к норме дня отдельной строкой («Норма + нагрузка»), а не размывается
в коэффициент активности — так пользователь видит, что реально потратил.

## Схема БД (дополнения)

Существующие таблицы `protein_entries`, `saved_products` сохраняются. Добавляем:

```
protein_entries  += fat_g, carbs_g, fiber_g REAL
                 += micros_json TEXT           -- {"iron": 3.1, ...}
                 += portion_g REAL
                 += meal_type TEXT             -- breakfast|lunch|dinner|snack|other
                 += eaten_at TEXT              -- ISO time "12:40"
                 += updated_at TEXT

meal_items       -- ингредиенты блюда для степперов граммовки
  id, entry_id FK, name, grams REAL, per100_json TEXT, position INT

saved_products   += fat_g, carbs_g, fiber_g, micros_json, per_100g INT

user_profile     -- 1 строка на пользователя
  user_id PK, sex, age, height_cm, weight_kg, activity, body_fat_pct,
  goal, tz, updated_at

workouts
  id, user_id, day, kind, minutes, kcal REAL, note, done_at, template_id, created_at

workout_templates
  id, user_id, name, kind, minutes, icon, created_at

supplements
  id, user_id, name, nutrient_key, dose REAL, unit, when_label,
  frequency, active INT, created_at
```

Миграции идемпотентны, выполняются при старте API (`api/db.py::migrate`).

## API

Все ручки под `/api`, ответы camelCase не используем — snake_case как в Python, фронт типизирован.

```
GET    /api/health

GET    /api/profile                     -> профиль + рассчитанная норма
PUT    /api/profile

GET    /api/diary/{day}                 -> лента дня: блюда, нагрузки, добавки + итоги
POST   /api/diary/{day}/meals           -> добавить блюдо (ручное / из продукта / из ИИ-разбора)
PATCH  /api/meals/{id}                  -> правка (граммовка, КБЖУ, время, тип)
DELETE /api/meals/{id}
GET    /api/meals/{id}                  -> деталь с ингредиентами и микро

POST   /api/ai/parse                    -> {text, image_base64?} -> разбор в позиции с КБЖУ и микро

GET    /api/products?q=                 -> поиск сохранённых продуктов
POST   /api/products                    -> сохранить своё блюдо
DELETE /api/products/{id}

GET    /api/workouts/{day}
POST   /api/diary/{day}/workouts
PATCH  /api/workouts/{id}
DELETE /api/workouts/{id}
GET    /api/workout-templates
POST   /api/workout-templates
DELETE /api/workout-templates/{id}
GET    /api/workout-kinds               -> справочник видов + MET

GET    /api/supplements
POST   /api/supplements
PATCH  /api/supplements/{id}
DELETE /api/supplements/{id}

GET    /api/report/day/{day}            -> КБЖУ + 25 микро против нормы + советы
GET    /api/report/period?range=week|month&end=YYYY-MM-DD
GET    /api/progress?range=week|month&end=YYYY-MM-DD  -> ряды для 4 графиков + плитки
```

Рекомендации «что добрать» — детерминированные: берём нутриенты с покрытием < 80 %, сортируем по
дефициту, к каждому подставляем список продуктов-источников из `api/nutrition/sources.py`.
ИИ здесь не нужен, ответ должен быть быстрым и воспроизводимым.

## Фронтенд

Vite + React 19 + TypeScript, `@tanstack/react-query` для серверного состояния, `zustand` для UI-состояния
(активный таб, открытый шит), `@telegram-apps/sdk` для BackButton/тем/haptics, `@phosphor-icons/react`.

Токены Nocturne переносим в `src/styles/tokens.css`; графики рисуем руками на SVG/div — макет
описывает их попиксельно, библиотека графиков только помешает.

Структура:

```
src/
  api/          client.ts, types.ts, hooks.ts
  app/          App.tsx, Shell.tsx, TabBar.tsx, Header.tsx
  screens/      Diary/ Report/ Progress/ Profile/
  sheets/       AddMeal/ MealDetail/ Workout/ Supplement/
  components/   Card, Segment, Stepper, Bar, Ring, Sheet, Chip, ...
  styles/       tokens.css, base.css
```

Экраны и шиты воспроизводят `design_handoff_nutrition_miniapp/README.md` — он является источником
истины по вёрстке. Обязательно добавляем то, чего в прототипе нет: скелетоны загрузки, пустой день,
ошибки ИИ-разбора, валидацию ручного ввода, `:focus-visible`.

## Запуск

Локально: `uvicorn api.main:app --reload --port 8000` и `npm run dev` (Vite проксирует `/api`).
В проде: тот же образ бота получает второй процесс/сервис `api` в `docker-compose.yml`, общий volume
с базой; фронт собирается статикой и раздаётся через `--app-static` или отдельный статик-хостинг.

## Что осознанно не делаем

- Внешняя база продуктов (USDA/FatSecret) — поиск идёт по своим сохранённым продуктам. Точка
  расширения оставлена в `api/routers/products.py`.
- Голосовой ввод в шите добавления — кнопка есть в макете, но без бэкенда распознавания она бы
  вводила в заблуждение; кнопка скрыта до реализации.
- Мультипользовательские фичи, шаринг, экспорт.
