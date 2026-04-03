# Health Agent — Архитектура приложения

## Часть 1. Бизнес-логика

### Что это

Персональный ассистент по здоровью в Telegram. Ведёт учёт питания, тренировок, сна и самочувствия. Считает дневную норму калорий и макросов на основе профиля пользователя и объективных данных с WHOOP. Даёт рекомендации по питанию, нагрузке и восстановлению.

### Пользовательские сценарии

#### 1. Онбординг

Новый пользователь отправляет `/start`. Бот проверяет, все ли обязательные поля профиля заполнены.

Обязательные поля:
- Пол, возраст, рост, вес
- Цель: набор массы / похудение / поддержание формы / рекомпозиция
- Уровень бытовой активности: low / moderate / high / very_high

Бот извлекает данные из свободного текста ("мужчина, 28 лет, 180/75, хочу набрать, офис"). Каждый факт сохраняется сразу. Пока профиль не заполнен — бот не записывает еду/сон/тренировки, а просит завершить настройку.

После заполнения всех полей: итоговая сводка → автоматический расчёт дневной нормы.

#### 2. Запись питания (текст)

Пользователь пишет: "съел омлет с сыром и кофе с молоком".

Цепочка:
1. Роутер определяет интент `food_text` по паттерну "съел".
2. Агент получает специализированный промпт (FOOD_TEXT_PROMPT) и 5 tools.
3. Для каждого продукта агент ищет БЖУ в FatSecret API (сначала по-русски, потом по-английски).
4. Если продукт не найден — оценивает самостоятельно.
5. Каждый продукт → отдельная запись `save_meal_log` с 4 нутриентами.
6. Если пользователь указал точные цифры ("вафли БЖУ 15/12/35 339 ккал") — используются как есть.
7. После записи: вызов `get_nutrition_remaining` → пользователь видит остаток дня.

Каталог доставки: если пользователь заказывает еду из сервиса, меню загружено в каталог с точными БЖУ. Агент ищет блюдо через `search_meal_catalog`.

#### 3. Запись питания (фото)

Пользователь отправляет фото еды.

Цепочка:
1. Роутер: есть изображение → `food_photo`.
2. Агент получает FOOD_PHOTO_PROMPT и 4 tools.
3. Пошаговый анализ: определить блюда → найти БЖУ в справочнике → оценить порцию по референсным объектам (вилка, тарелка ~25 см, рука) → рассчитать → записать.
4. Обязательный вывод уровня уверенности: "Уверен" / "Скорее уверен" / "Не уверен".
5. При низкой уверенности — конкретный совет (сфоткать ближе, положить рядом ложку, подписать).

#### 4. Запись тренировки

Пользователь: "побегал 5 км" или "силовая 60 мин".

Цепочка:
1. Роутер: паттерн "побегал" → `workout`.
2. Агент получает WORKOUT_PROMPT и 4 tools.
3. Заполняет intensity (low/moderate/high/max) и duration_minutes. Если не указано — оценивает из контекста.
4. Проверяет recovery через WHOOP: красная зона → предупреждение, зелёная → "можно нагружать".
5. После записи: `get_nutrition_remaining` → норма калорий пересчитана с учётом тренировки.

#### 5. Запись сна, веса, самочувствия

Пользователь: "спал 7 часов", "вешу 75.5", "голова болит".

Цепочка:
1. Роутер: паттерн → `body_state`.
2. Сон: извлечь длительность/время → `save_sleep_log`. Если есть WHOOP — сравнить субъективный и объективный сон.
3. Вес: `save_body_metric` → показать тренд за неделю. Если изменение ±2 кг — обновить профиль.
4. Самочувствие: `save_note`. Тревожные симптомы → дисклеймер "обратись к врачу".
5. Несколько событий в одном сообщении — обрабатываются.

#### 6. Смешанные сообщения

"Съел омлет и побегал 5 км" — содержит и еду, и тренировку.

Роутер обнаруживает матчи из разных категорий → отправляет в `general` со всеми 22 tools. Оба события обрабатываются.

#### 7. Рекомендации и аналитика

Пользователь: "что поесть на ужин?", "итог дня", "как прошла неделя".

Цепочка:
1. Роутер: паттерн → `advice`.
2. Агент ОБЯЗАН вызвать `get_daily_recommendation_context` перед ответом — берёт реальные данные.
3. Для недельного обзора — `get_week_summary`.
4. WHOOP-данные (recovery, HRV, strain) используются для объективной оценки.
5. Ответ — конкретный и actionable: "тебе осталось 40г белка — это куриная грудка 150г".

#### 8. Управление записями

"Удали последний приём пищи", "покажи что я ел вчера" — идёт в `general`.

Исправление: найти через `get_recent_logs` → удалить `delete_log` → создать новую.

#### 9. WHOOP-интеграция

- `/whoop` — подключение через OAuth 2.0 или ручная синхронизация.
- Данные: recovery (HRV, пульс покоя, SpO2), strain, стадии сна.
- Используются для: расчёта нормы калорий, рекомендаций по нагрузке, сравнения субъективного сна с объективным.

#### 10. Проактивные сообщения (по расписанию)

| Джоб | Время | Что делает |
|------|-------|------------|
| evening_summary | 22:00 Мск | Итог дня: питание/тренировки/баланс. Только если есть записи о еде. |
| weekly_summary | Вс 20:00 | Обзор недели с дельтой к прошлой. Только если ≥3 дней с данными. |
| weekly_streak_check | Пн 10:00 | Поздравление за 7 дней подряд с записями. |
| sleep_trend_check | Пн 10:00 | Тренд сна: средняя длительность за неделю vs позапрошлая. |
| nightly_whoop_sync | 03:00 | Подстраховка: синхронизация WHOOP за 2 дня. |
| refresh_whoop_tokens | Каждый час | Обновление OAuth-токенов WHOOP. |

### Расчёт калорий (4-блочная модель)

```
Дневная цель = BMR × коэффициент активности
             + надбавка по нагрузке (WHOOP strain или оценка)
             + профицит/дефицит по цели × модификатор recovery
```

1. **BMR** — Mifflin–St Jeor: `10 × вес + 6.25 × рост - 5 × возраст + (5 для М / -161 для Ж)`.
2. **Коэффициент активности** — бытовая активность: low (1.35), moderate (1.45), high (1.55), very_high (1.60).
3. **Надбавка по нагрузке**:
   - Если есть WHOOP strain: фиксированный бонус по зоне strain × модификатор типа тренировки (силовая 1.0, кардио 0.75, йога 0.40).
   - Если нет WHOOP: таблица "длительность × интенсивность" → бонус × модификатор типа.
4. **Профицит/дефицит**:
   - Набор массы: +5% от базы × recovery modifier.
   - Похудение: -15% от базы. Плохой recovery → мягче дефицит.
   - Рекомпозиция: -5%.
   - Поддержание: 0%.

Recovery modifier: зелёная зона (≥67%) → 1.0, жёлтая (34-66%) → 0.85, красная (<34%) → 0.70.

Макросы: белок 2.0 г/кг (набор/рекомпозиция) или 1.8 г/кг (остальные), жиры 25% от ккал, углеводы — остаток.

### Quality rules (детерминированные)

Работают ДО вызова LLM, результат вшивается в system prompt:

- Сон < 6ч три дня подряд → "Восстановление замедлено, только лёгкая нагрузка" (critical).
- Мало данных за неделю (<5 записей) → "Рекомендации приблизительные" (info).
- Тяжёлая тренировка вчера → "Минимум 48ч для тех же мышц" (warning).
- Калорийность одного приёма >5000 ккал → "Вероятная ошибка ввода" (warning).
- Калорийность <30 ккал → "Подозрительно мало" (info).

### Команды

| Команда | Описание |
|---------|----------|
| /start | Онбординг или приветствие |
| /help | Справка |
| /whoop | Подключение/синхронизация WHOOP |
| /costs | Статистика расходов на OpenAI API |

### Ограничения и известные gaps

- Короткие сообщения без глагола ("банан", "кофе", "творог 200г") — не попадают в специализированный food_text промпт, обрабатываются через general.
- Просто "итог" без "дня"/"недели" уходит в general, а не в advice.
- Один пользователь в MVP (whitelist по Telegram ID).
- Бот не поддерживает несколько фото в одном сообщении.

---

## Часть 2. Техническая архитектура

### Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12+ |
| Web-фреймворк | FastAPI |
| LLM | OpenAI GPT-5.4 / GPT-5.4-mini через OpenAI Agents SDK |
| Telegram | python-telegram-bot (polling в dev, webhook в prod) |
| БД | PostgreSQL 16 (asyncpg + SQLAlchemy async) |
| Миграции | Alembic |
| Scheduler | APScheduler (AsyncIOScheduler, CronTrigger) |
| WHOOP | OAuth 2.0 + REST API + webhooks |
| Справочник БЖУ | FatSecret Platform API (OAuth 1.0 HMAC-SHA1) |

### Структура проекта

```
app/
├── main.py                 # FastAPI + lifespan (бот, scheduler, endpoints)
├── config.py               # Pydantic Settings из .env
├── database.py             # SQLAlchemy async engine + sessionmaker
│
├── agent/
│   ├── agent.py            # Промпты, tools списки, INTENT_CONFIG, run_agent()
│   ├── router.py           # classify_intent() + choose_model()
│   ├── context.py          # build_user_context() — инъекция контекста в system prompt
│   └── tools/              # Function tools для OpenAI Agents SDK
│       ├── _context.py     # ContextVar<user_id> для передачи user_id в tools
│       ├── profile.py      # get_user_profile
│       ├── logs.py         # save_sleep/meal/workout_log, save_note, get_recent_logs, delete_log
│       ├── memory.py       # update_user_profile, delete_memory_item, save_derived_rule
│       ├── state.py        # get_current_state (агрегаты)
│       ├── summary.py      # get_daily_recommendation_context, get_week_summary
│       ├── catalog.py      # search_meal_catalog (каталог доставки)
│       ├── body.py         # save_body_metric, get_weight_history
│       ├── calorie_calc.py # calculate_daily_target, get_nutrition_remaining, compute_daily_targets
│       ├── food_db.py      # lookup_food_nutrition (FatSecret API)
│       └── whoop.py        # get_whoop_status, sync_whoop_now, get_latest_whoop_metrics
│
├── models/                 # SQLAlchemy ORM
│   ├── user.py             # User, TelegramAccount
│   ├── logs.py             # SleepLog, MealLog, WorkoutLog, DailyNote, RecoveryLog, CycleLog
│   ├── memory.py           # UserProfile, DerivedRule, MemoryNote
│   ├── whoop.py            # WhoopConnection, SyncEvent
│   └── agent.py            # AgentRun, ToolCall
│
├── telegram/
│   ├── bot.py              # Bot setup (polling/webhook)
│   ├── handlers.py         # handle_message, handle_photo, handle_start/help/costs/whoop
│   ├── user_service.py     # get_or_create_user
│   └── webhook.py          # Webhook endpoint helpers
│
├── whoop/
│   ├── oauth.py            # OAuth flow (authorization URL, exchange, refresh)
│   ├── client.py           # WHOOP REST API client
│   ├── sync.py             # sync_whoop_data (recovery, cycles, workouts, sleep)
│   └── webhook.py          # handle_webhook, verify_signature
│
├── scheduler/
│   └── jobs.py             # evening_summary, weekly_summary, streak, sleep_trend, whoop sync/refresh
│
└── quality/
    └── rules.py            # Детерминированные quality rules (sleep streak, low data, heavy workout)
```

### Поток обработки сообщения

```
Telegram update
  → handlers.py: handle_message() / handle_photo()
    → _ensure_allowed_user()         # whitelist проверка
    → run_agent(message, user_id, image_url?)
      │
      ├─ get_missing_profile_fields()  # онбординг?
      ├─ build_user_context()          # контекст из БД → инъекция в system prompt
      │   ├─ quality rules (детерминированные предупреждения)
      │   ├─ профиль пользователя
      │   ├─ последний сон
      │   ├─ баланс питания за сегодня
      │   ├─ WHOOP recovery
      │   └─ активные derived rules
      │
      ├─ classify_intent()             # food_text / food_photo / workout / body_state / advice / general
      │   └─ при смешанных интентах → general
      ├─ INTENT_CONFIG[intent]         # → (prompt, tools)
      ├─ choose_model()               # fast (mini) или strong по паттернам
      │
      ├─ agent.clone(instructions, tools, model)
      ├─ Runner.run(agent, history)    # OpenAI Agents SDK
      │   └─ tool calls → tool results → final output
      │
      ├─ AgentRun + ToolCall → БД     # логирование: intent, model, tokens, duration
      └─ _conversation_history         # in-memory, 5 последних сообщений
```

### Система интентов

| Intent | Промпт | Tools | Триггер |
|--------|--------|-------|---------|
| `food_photo` | FOOD_PHOTO_PROMPT | 4 | Есть изображение |
| `food_text` | FOOD_TEXT_PROMPT | 5 | "съел", "поел", "на завтрак", "обед:" |
| `workout` | WORKOUT_PROMPT | 4 | "побегал", "силовая", "в зале" |
| `body_state` | BODY_STATE_PROMPT | 8 | "спал", "вешу", "болит", "устал" |
| `advice` | ADVICE_PROMPT | 11 | "посоветуй", "итог дня", "что поесть" |
| `general` | GENERAL_PROMPT | 22 | Всё остальное + смешанные сообщения |
| (onboarding) | ONBOARDING_PROMPT | 3 | Профиль не заполнен |

Exclude-паттерны: вопросы ("что поесть?") блокируют только свою категорию записи, не весь pipeline. "Что поесть" → не food_text → проходит дальше → advice.

### Роутер моделей

- Фото → fast (GPT-5.4-mini) — распознавание + запись.
- Совпадение с STRONG_PATTERNS ("посоветуй", "итог", "почему", "анализ") → strong (GPT-5.4).
- Всё остальное → fast.

Интент и модель выбираются НЕЗАВИСИМО. Можно получить: `intent=food_text` + `model=strong` (если текст содержит "посоветуй").

### Контекст-инъекция (build_user_context)

Перед каждым вызовом LLM в system prompt добавляется блок `--- Контекст пользователя ---` с:

1. **Quality warnings** — детерминированные ограничения (из `quality/rules.py`).
2. **Профиль** — все подтверждённые факты (антропометрия, цели, lifestyle).
3. **Незаполненные поля** — для онбординг-промпта.
4. **Последний сон** — за вчера/сегодня.
5. **Баланс питания** — съедено vs цель (ккал, белок, %).
6. **WHOOP recovery** — score, HRV, пульс покоя с цветовой зоной.
7. **Derived rules** — наблюдения с confidence ≥ 0.6 (макс. 3).

### Модель данных

Ключевые таблицы:

```
users
  └─ telegram_accounts (1:1)
  └─ user_profile (key-value: category/key/value, confirmed)
  └─ derived_rules (rule, confidence, evidence)
  └─ meal_logs (date, meal_type, name, calories, protein_g, fat_g, carbs_g, source)
  └─ sleep_logs (date, duration_minutes, quality, bed_time, wake_time, source)
  └─ workout_logs (date, workout_type, duration_minutes, intensity, strain, source)
  └─ daily_notes (date, text)
  └─ recovery_logs (date, recovery_score, hrv_ms, resting_hr, spo2, source)
  └─ cycle_logs (date, day_strain, source)
  └─ body_metrics (date, metric_type, value, unit)
  └─ whoop_connections (access_token, refresh_token, is_active)
  └─ agent_runs (trigger, intent, model, input/output, tokens, duration_ms, error)
       └─ tool_calls (tool_name, arguments, result, error)
```

Принципы:
- Soft delete (`deleted_at`) для всех логов и памяти.
- `source`: `user_manual`, `whoop_api`, `agent_inferred`, `system_aggregated`.
- Inferred записи имеют `confidence`, `status`, `confirmation_flag`.
- Idempotent обработка по `external_id` (WHOOP webhooks, Telegram updates).

### Memory model (4 слоя)

1. **Profile** — устойчивые факты (цель, аллергии, предпочтения). Меняются только с подтверждением.
2. **Current State** — агрегаты (текущий вес, средний сон за неделю, последний recovery).
3. **Event Log** — сырые записи (сон, еда, тренировки, заметки).
4. **Derived Rules** — гипотезы с confidence ("кофе после 18:00 ухудшает сон", confidence 0.7).

### FatSecret API

OAuth 1.0 HMAC-SHA1 (consumer-only, без user token). Поиск продуктов → парсинг описания "Per 100g - Calories: 250kcal | Fat: 10g | Carbs: 30g | Protein: 15g".

Стратегия: русский запрос → английский fallback → оценка моделью.

### Endpoints

| Метод | URL | Описание |
|-------|-----|----------|
| GET | /health | Healthcheck |
| POST | /telegram/webhook | Telegram webhook (prod) |
| POST | /whoop/webhook | WHOOP webhook (данные обновлены) |
| GET | /whoop/callback | OAuth callback от WHOOP |

### Логирование

- Dev: текстовый формат `%(asctime)s [%(levelname)s] %(name)s: %(message)s`.
- Prod: structured JSON (`ts`, `level`, `logger`, `msg`, `exception`).
- Каждый AgentRun записывается в БД: intent, model, tokens (input/output), duration_ms, error.
- Каждый ToolCall записывается: tool_name, arguments (JSON), result (JSON), error.

### Conversation history

In-memory dict `user_id → list`. Хранится 5 последних сообщений пользователя с ответами. Не персистится между рестартами. История передаётся в `Runner.run()` для контекста диалога.

Интент определяется заново для каждого сообщения — промпт и tools могут меняться между сообщениями одного диалога. История при этом сохраняется.

### Scheduler

APScheduler AsyncIOScheduler с CronTrigger. Все джобы используют `intent_override="advice"` при вызове `run_agent` — bypass роутера, гарантированный advice-промпт.

Джобы с проактивными сообщениями (evening_summary, weekly_summary) содержат guard-условия: не отправляются если нет данных за период.

### Развёртывание

```bash
# Dev
docker compose up -d postgres    # PostgreSQL
alembic upgrade head              # Миграции
python -m app.main                # Polling mode, hot reload

# Prod
docker compose up -d              # PostgreSQL + app
# Telegram webhook: TELEGRAM_WEBHOOK_URL в .env
# WHOOP webhook: настроить в WHOOP Developer Dashboard
```

Переменные окружения: `.env` (не в git). Ключевые: `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `ALLOWED_TELEGRAM_USER_IDS`, `WHOOP_CLIENT_ID/SECRET`, `FATSECRET_CONSUMER_KEY/SECRET`.
