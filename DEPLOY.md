# Деплой (Docker + GitHub Actions)

После пуша в ветку `main` workflow **Deploy** собирает образ на сервере и перезапускает контейнер. Ручной запуск: **Actions → Deploy → Run workflow**.

## Сервер

- Ubuntu (или другой Linux) с **Docker** и плагином **Docker Compose v2** — команда **`docker compose`** (два слова). Старый бинарник **`docker-compose`** (с дефисом) не используется.
- Открытый SSH (по умолчанию порт **22**).
- Пользователь с правом запускать `docker` (часто в группе `docker`).

Первый раз создай каталог деплоя (или он создастся сам из workflow через `mkdir -p`).

На сервер добавь **публичный** SSH-ключ, для **приватного** ключа из секрета `SSH_PRIVATE_KEY` (см. ниже).

## Секреты в GitHub (Settings → Secrets and variables → Actions)

### Обязательные

| Секрет | Описание |
|--------|----------|
| `SSH_HOST` | IP или домен сервера (например `203.0.113.10` или `bot.example.com`) |
| `SSH_USER` | Пользователь SSH (например `deploy` или `ubuntu`) |
| `SSH_PRIVATE_KEY` | Содержимое **приватного** ключа OpenSSH (весь файл `id_ed25519`, включая `BEGIN` / `END`) |
| `DEPLOY_PATH` | Абсолютный путь на сервере без завершающего `/` (например `/opt/protein-counter`) |
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |

### Рекомендуемые

| Секрет | Описание |
|--------|----------|
| `OPENAI_API_KEY` | Ключ OpenAI для режима «Посчитать с ИИ». Можно оставить пустым, если ИИ не нужен |

### Опциональные

| Секрет | Описание |
|--------|----------|
| `OPENAI_MODEL` | Модель (по умолчанию в коде `gpt-4o-mini`) |
| `TZ` | Часовой пояс для «сегодня» (IANA, например `Europe/Moscow`) |
| `SSH_PORT` | Порт SSH, если не **22**. Если секрет не задан, используется **22** |

## Локальный запуск в Docker

```bash
cp env.example .env
# заполни TELEGRAM_BOT_TOKEN и при необходимости OPENAI_API_KEY
docker compose up -d --build
```

База SQLite хранится в Docker volume `bot-data`.

## Ручной деплой

В репозитории: **Actions** → выбери workflow **Deploy** → **Run workflow** → ветка `main` → **Run workflow**.
