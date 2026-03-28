# Health Agent

Персональный ИИ-ассистент по питанию, тренировкам, сну и восстановлению. Работает через Telegram, использует данные WHOOP и ручные логи для рекомендаций.

Это не чат-бот — это агентное приложение: LLM рассуждает, вызывает инструменты, читает и пишет в БД. Telegram — канал общения, PostgreSQL — источник истины.

## Что умеет

- Записывать сон, еду, тренировки, заметки (текстом или фото еды)
- Синхронизировать данные WHOOP (сон, recovery, тренировки) через OAuth + webhooks
- Давать рекомендации на основе всех данных: логи + WHOOP + профиль + правила
- Отправлять вечерний итог дня, недельный обзор, streak-уведомления
- Искать блюда по каталогу доставки (если загружен)
- Вести профиль пользователя (цели, ограничения, антропометрия)
- Формировать гипотезы (derived rules) на основе накопленных данных

## Стек

| Компонент | Технология |
|-----------|-----------|
| API | FastAPI + uvicorn |
| LLM | OpenAI GPT-5.4 через [Agents SDK](https://github.com/openai/openai-agents-python) |
| БД | PostgreSQL 16 + SQLAlchemy 2.0 (async) |
| Миграции | Alembic |
| Telegram | python-telegram-bot 21 (polling / webhook) |
| Фоновые задачи | APScheduler |
| WHOOP | OAuth 2.0 + REST API v2 + webhooks |

## Быстрый старт

### Требования

- Python 3.12+
- Docker (для PostgreSQL)

### 1. Клонировать и установить зависимости

```bash
git clone <repo-url>
cd health-agent
pip install -r requirements-dev.txt   # production + тесты
```

### 2. Настроить переменные окружения

```bash
cp .env.example .env
```

Обязательные переменные:

| Переменная | Описание |
|---|---|
| `OPENAI_API_KEY` | Ключ [OpenAI API](https://platform.openai.com/api-keys) |
| `TELEGRAM_BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `ALLOWED_TELEGRAM_USER_IDS` | Telegram ID через запятую (узнать свой: [@userinfobot](https://t.me/userinfobot)) |

Опциональные:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://healthagent:healthagent@localhost:5432/healthagent` | Подключение к PostgreSQL |
| `WHOOP_CLIENT_ID` | — | Client ID из [WHOOP Developer Portal](https://developer-dashboard.whoop.com/) |
| `WHOOP_CLIENT_SECRET` | — | Client Secret оттуда же |
| `WHOOP_REDIRECT_URI` | `http://localhost:8000/whoop/callback` | Callback URL для OAuth |
| `TIMEZONE` | `Europe/Moscow` | Часовой пояс для дат и расписания |
| `APP_ENV` | `dev` | `dev` — polling, `prod` — webhook |
| `TELEGRAM_WEBHOOK_URL` | — | Публичный URL для webhook-режима (только prod) |

### 3. Поднять PostgreSQL

```bash
docker-compose up -d postgres
```

> `docker-compose up -d` без указания сервиса поднимет и контейнер приложения. Для локальной разработки достаточно только postgres.

### 4. Применить миграции

```bash
alembic upgrade head
```

### 5. Запустить

```bash
python -m app.main
```

В dev-режиме бот работает через polling (не нужен публичный IP). Для production — задать `TELEGRAM_WEBHOOK_URL` и `APP_ENV=prod`, запускать за reverse proxy с SSL.

## Архитектура

```
Telegram ──► FastAPI ──► OpenAI Agent (GPT-5.4)
                │              │
                │              ├── function tools
                │              │     ├── save/get/delete логи
                │              │     ├── профиль и память
                │              │     ├── агрегаты и рекомендации
                │              │     ├── каталог еды
                │              │     └── WHOOP статус и синхронизация
                │              │
                ▼              ▼
           PostgreSQL ◄── единственный источник истины
                ▲
                │
WHOOP API ──────┘  (OAuth + webhooks + scheduled sync)
```

### Память агента (4 слоя)

1. **Profile** — устойчивые факты (цель, ограничения, предпочтения). Меняются только с подтверждением.
2. **Current State** — агрегаты (средний сон за неделю, калории за день, последний recovery).
3. **Event Log** — сырые записи (сон, еда, тренировки, заметки).
4. **Derived Rules** — гипотезы с confidence (например, «кофе после 16:00 ухудшает сон»).

### Фоновые задачи (APScheduler)

| Задача | Расписание | Что делает |
|--------|-----------|-----------|
| Refresh WHOOP tokens | Каждый час | Обновляет токены, истекающие в ближайшие 2 часа |
| Nightly WHOOP sync | 03:00 | Досинхронизирует пропущенные webhooks за 2 дня |
| Evening summary | 22:00 | Итог дня: калории, белок vs цель, тренировки |
| Weekly streak check | Пн 10:00 | Поздравление если логировал еду каждый день |
| Sleep trend | Пн 10:05 | Сравнение сна: эта неделя vs прошлая |
| Weekly summary | Вс 20:00 | Детальный недельный обзор через агента |

> Все времена — в часовом поясе из `TIMEZONE`.

## WHOOP интеграция

Для подключения нужно создать приложение в [WHOOP Developer Portal](https://developer-dashboard.whoop.com/) и прописать `WHOOP_CLIENT_ID`, `WHOOP_CLIENT_SECRET`, `WHOOP_REDIRECT_URI` в `.env`.

1. Отправь `/whoop` боту — получишь ссылку авторизации
2. Авторизуй приложение на сайте WHOOP
3. После callback автоматически синхронизируются последние 7 дней
4. Далее данные приходят через webhooks в реальном времени + nightly sync как страховка

Webhooks обрабатывают: `sleep.updated`, `recovery.updated`, `workout.updated`, `*.deleted`.

Без WHOOP приложение работает полноценно — просто данные вводятся вручную.

## Каталог еды

Если используешь доставку еды, можно загрузить меню:

```bash
python scripts/load_menu.py <user_id> '[{"date":"2026-03-17","meal_number":1,"name":"Каша рисовая","calories":353,"protein_g":9,"fat_g":11,"carbs_g":55,"source":"level_kitchen"}]'
```

Агент будет искать блюда по каталогу и использовать точные макросы.

## HTTP-эндпоинты

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Health check |
| POST | `/telegram/webhook` | Telegram updates (prod) |
| POST | `/whoop/webhook` | WHOOP events (signature verified) |
| GET | `/whoop/auth` | Редирект на WHOOP OAuth |
| GET | `/whoop/callback` | OAuth callback |

## Тесты

```bash
pytest tests/
```

Тесты используют in-memory SQLite — PostgreSQL не нужен.

- **test_unit.py** — парсинг целей, markdown→HTML, роутер моделей, подпись WHOOP, хелперы синхронизации, валидаторы
- **test_db.py** — CRUD логов, soft delete, resurrect при синхронизации, профиль, quality rules
- **test_api.py** — health check, WHOOP webhook/OAuth endpoints

## Структура проекта

```
├── app/
│   ├── main.py              # FastAPI + lifespan (бот, scheduler, endpoints)
│   ├── config.py            # Настройки из .env
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models/              # ORM-модели (users, logs, memory, whoop, agent, catalog)
│   ├── agent/
│   │   ├── agent.py         # Определение агента + system prompt
│   │   ├── router.py        # Роутер моделей (strong/fast)
│   │   ├── context.py       # Инъекция контекста в system prompt
│   │   └── tools/           # Function tools агента
│   ├── telegram/
│   │   ├── bot.py           # Инициализация бота
│   │   ├── handlers.py      # Обработка сообщений и фото
│   │   └── user_service.py  # Создание/поиск пользователя по Telegram ID
│   ├── whoop/
│   │   ├── oauth.py         # OAuth 2.0 flow
│   │   ├── client.py        # WHOOP API клиент
│   │   ├── sync.py          # Синхронизация данных
│   │   └── webhook.py       # Обработка WHOOP webhooks
│   ├── scheduler/
│   │   └── jobs.py          # Фоновые задачи
│   └── quality/
│       └── rules.py         # Детерминированные правила валидации
├── alembic/                 # Миграции БД
├── scripts/
│   └── load_menu.py         # Загрузка каталога еды
├── tests/
├── docker-compose.yml
├── requirements.txt
└── .env.example
```
