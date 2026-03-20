# Health Agent

Персональный цифровой ассистент по питанию, тренировкам, сну и восстановлению. Агентное приложение (не чат-бот) с собственным state layer.

## Stack

- **Language:** Python 3.12+
- **Framework:** FastAPI
- **Database:** PostgreSQL (Docker)
- **LLM:** OpenAI GPT-5.4 via OpenAI Agents SDK
- **Telegram:** python-telegram-bot или aiogram 3 (webhook в prod, polling в dev)
- **Background tasks:** решение принято в пользу APScheduler для MVP (без Redis/Celery)
- **WHOOP:** OAuth 2.0 интеграция (будет добавлена позже)

## Architecture

Это НЕ чат-бот с памятью в переписке. Это агентное приложение:
- OpenAI Agents SDK = reasoning + orchestration + tool calling
- Telegram = канал взаимодействия
- WHOOP = источник объективных данных
- PostgreSQL = единственный источник истины
- Рекомендации = данные WHOOP + ручные логи + агрегаты/правила + LLM-интерпретация

## Project Structure

```
Health Agent/
├── CLAUDE.md
├── .env.example
├── .env                    # NOT in git
├── docker-compose.yml      # PostgreSQL
├── requirements.txt
├── alembic.ini
├── alembic/                # DB migrations
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI entrypoint
│   ├── config.py           # Settings from env
│   ├── database.py         # SQLAlchemy engine, session
│   ├── models/             # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── logs.py         # sleep, meal, workout, notes
│   │   ├── memory.py       # profile, derived_rules, memory_notes
│   │   ├── whoop.py        # whoop connections, sync events
│   │   └── agent.py        # agent_runs, tool_calls
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent.py        # OpenAI Agent definition
│   │   ├── tools/          # Function tools
│   │   │   ├── __init__.py
│   │   │   ├── profile.py  # get/update profile
│   │   │   ├── logs.py     # save/get sleep, meal, workout
│   │   │   ├── memory.py   # memory management
│   │   │   ├── state.py    # current state, aggregates
│   │   │   ├── summary.py  # daily/weekly summaries
│   │   │   └── whoop.py    # whoop data access
│   │   ├── prompts.py      # System instructions builder
│   │   └── context.py      # Selective memory injection
│   ├── telegram/
│   │   ├── __init__.py
│   │   ├── bot.py          # Bot setup
│   │   ├── handlers.py     # Message handlers
│   │   └── webhook.py      # Webhook endpoint for FastAPI
│   ├── whoop/
│   │   ├── __init__.py
│   │   ├── oauth.py        # OAuth flow
│   │   ├── client.py       # WHOOP API client
│   │   ├── sync.py         # Sync logic
│   │   └── webhook.py      # WHOOP webhook handler
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py         # Daily/weekly tasks, token refresh
│   └── quality/
│       ├── __init__.py
│       └── rules.py        # Deterministic validation rules
├── tests/
│   ├── conftest.py
│   ├── test_tools/
│   ├── test_memory/
│   ├── test_telegram/
│   └── test_whoop/
└── scripts/
    └── seed.py             # Initial data seeding
```

## Key Conventions

- Все API-ключи и токены в `.env`, никогда не в коде
- Каждая запись в БД имеет `source`: `user_manual`, `whoop_api`, `agent_inferred`, `system_aggregated`
- Inferred записи имеют `confidence`, `status`, `confirmation_flag`
- Soft delete для логов и памяти
- Idempotent обработка Telegram updates и WHOOP webhooks (по external_id)
- Tools — единственный способ агента получить данные и выполнить действия
- Memory injection — выборочная, не полная загрузка истории
- Детерминированные расчёты — в коде, LLM — интерпретация и язык

## Memory Model (4 layers)

1. **Profile** — устойчивые факты (цель, ограничения, предпочтения). Меняются только с подтверждением.
2. **Current State** — агрегаты (текущий вес, средний сон за неделю, последний recovery).
3. **Event Log** — сырые записи (сон, еда, тренировки, заметки).
4. **Derived Rules** — гипотезы с confidence (влияние кофе на сон и т.п.).

Two-phase memory: сначала запись → потом consolidation в устойчивую память.

## Agent Behavioral Rules

- Опирается на structured context из tools, не на предположения
- Не делает долговременных выводов после одного события
- Различает: факт, наблюдение, гипотезу, рекомендацию
- Признаёт недостаточность данных
- Объясняет, почему дал совет
- Сжатый понятный стиль
- Не формулирует советы как медицинский диагноз
- Disclaimer при высокорисковых рекомендациях

## Quality Rules (deterministic, in code)

- Нет recovery за сегодня → не делать уверенных выводов о нагрузке
- Сон < порога 3 дня подряд → усилить осторожность в рекомендациях
- Смена цели → пересчитать недельный анализ с новой даты
- Конфликт manual vs WHOOP данных → приоритет по правилам

## DB Entities

users, telegram_accounts, whoop_connections, user_profile, sleep_logs, recovery_logs, workout_logs, meal_logs, body_metrics, daily_notes, memory_notes, derived_rules, weekly_summaries, agent_runs, tool_calls, sync_events

## Commands

```bash
# Dev setup
docker-compose up -d          # PostgreSQL
pip install -r requirements.txt
alembic upgrade head           # Run migrations
python -m app.main             # Start server (polling mode)

# Migrations
alembic revision --autogenerate -m "description"
alembic upgrade head

# Tests
pytest tests/
```

## User

- Один пользователь в MVP
- Модель GPT-5.4
- Telegram как основной канал
- WHOOP интеграция будет добавлена на этапе 3
