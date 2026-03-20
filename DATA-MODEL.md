# Data Model — Health Agent

## Общие правила

- Все таблицы: `id` (UUID, PK), `created_at` (timestamptz), `updated_at` (timestamptz)
- Логи и память: `deleted_at` (timestamptz, nullable) — soft delete
- Записи с внешним источником: `external_id` (varchar, nullable, unique per source), `last_synced_at` (timestamptz)
- Enum `source`: `user_manual`, `whoop_api`, `agent_inferred`, `system_aggregated`
- Inferred-записи: `confidence` (float 0-1), `status` (enum: pending/confirmed/rejected/expired), `confirmed_at` (timestamptz)

---

## users

Корневая сущность. Один пользователь в MVP, но проектируем под multi-user.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| display_name | varchar(255) | Имя для обращения |
| timezone | varchar(50) | Таймзона пользователя (default: UTC) |
| is_active | bool | default true |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

## telegram_accounts

Связь Telegram ↔ internal user. 1:1 в MVP.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| telegram_user_id | bigint UNIQUE | Telegram user ID |
| telegram_username | varchar(255) | @username, nullable |
| chat_id | bigint | ID чата для отправки сообщений |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

## user_profile

Устойчивые факты о пользователе. Key-value подход для гибкости.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| category | varchar(50) | goals, restrictions, preferences, anthropometry, lifestyle |
| key | varchar(100) | Название факта: weight, goal, allergy, wake_time... |
| value | text | Значение |
| source | source_enum | Кто записал |
| confirmed | bool | Подтверждён пользователем |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | Soft delete |

UNIQUE(user_id, category, key) WHERE deleted_at IS NULL

---

## sleep_logs

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | Дата сна (ночь на эту дату) |
| bed_time | timestamptz | Время отхода ко сну, nullable |
| wake_time | timestamptz | Время подъёма, nullable |
| duration_minutes | int | Общая длительность, nullable |
| quality | varchar(20) | good/fair/poor, nullable |
| deep_sleep_minutes | int | nullable, из WHOOP |
| rem_sleep_minutes | int | nullable, из WHOOP |
| light_sleep_minutes | int | nullable, из WHOOP |
| awake_minutes | int | nullable, из WHOOP |
| sleep_score | float | nullable, из WHOOP (0-100) |
| comment | text | Комментарий пользователя |
| source | source_enum | |
| external_id | varchar(100) | WHOOP sleep ID |
| last_synced_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## recovery_logs

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | |
| recovery_score | float | 0-100, из WHOOP |
| hrv_ms | float | HRV в миллисекундах |
| resting_hr | float | Пульс покоя |
| spo2 | float | nullable, % кислорода |
| skin_temp_celsius | float | nullable |
| comment | text | |
| source | source_enum | |
| external_id | varchar(100) | WHOOP recovery ID |
| last_synced_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## workout_logs

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | |
| started_at | timestamptz | nullable |
| ended_at | timestamptz | nullable |
| duration_minutes | int | |
| workout_type | varchar(50) | strength, cardio, flexibility, sport, mixed, other |
| intensity | varchar(20) | low, moderate, high, max |
| avg_hr | float | nullable, из WHOOP |
| max_hr | float | nullable |
| calories_burned | float | nullable |
| strain | float | nullable, WHOOP strain 0-21 |
| description | text | Описание от пользователя |
| comment | text | |
| source | source_enum | |
| external_id | varchar(100) | WHOOP workout ID |
| last_synced_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## meal_logs

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | |
| time | time | nullable |
| meal_type | varchar(20) | breakfast, lunch, dinner, snack, other |
| description | text | Что съел (свободный текст) |
| calories | int | nullable, приблизительно |
| protein_g | float | nullable |
| carbs_g | float | nullable |
| fat_g | float | nullable |
| fiber_g | float | nullable |
| quality | varchar(20) | good/fair/poor, nullable — оценка качества питания |
| comment | text | |
| source | source_enum | Всегда user_manual или agent_inferred |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## body_metrics

Точечные измерения тела.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | |
| weight_kg | float | nullable |
| body_fat_pct | float | nullable |
| muscle_mass_kg | float | nullable |
| waist_cm | float | nullable |
| comment | text | |
| source | source_enum | |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## daily_notes

Свободные заметки, самочувствие, настроение.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| date | date | |
| mood | varchar(20) | great/good/ok/bad/terrible, nullable |
| energy_level | int | 1-10, nullable |
| stress_level | int | 1-10, nullable |
| text | text | Свободный текст |
| source | source_enum | |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## meal_catalog

Каталог блюд из внешних сервисов (Level Kitchen и др.). Парсится автоматически раз в неделю.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| name | varchar(255) | Название блюда |
| calories | int | |
| protein_g | float | |
| carbs_g | float | |
| fat_g | float | |
| meal_type | varchar(20) | breakfast, lunch, dinner, snack |
| provider | varchar(50) | level_kitchen и др. |
| source_url | text | nullable, откуда спарсено |
| week_start | date | Неделя, к которой относится блюдо |
| parsed_at | timestamptz | Когда спарсено |
| created_at | timestamptz | |

---

## memory_notes

Промежуточная память. Кандидаты на перенос в профиль или derived_rules.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| content | text | Содержание заметки |
| category | varchar(50) | observation, pattern, preference, habit |
| occurrences | int | Сколько раз наблюдалось (default 1) |
| status | varchar(20) | pending, promoted, dismissed |
| promoted_to | varchar(20) | profile / derived_rule, nullable |
| source | source_enum | agent_inferred |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## derived_rules

Гипотезы и паттерны, выведенные агентом.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| rule | text | Формулировка правила |
| evidence | text | На чём основано |
| confidence | float | 0.0 - 1.0 |
| status | varchar(20) | pending, confirmed, rejected, expired |
| confirmed_at | timestamptz | nullable |
| last_validated_at | timestamptz | Когда последний раз проверялось |
| source | source_enum | agent_inferred |
| created_at | timestamptz | |
| updated_at | timestamptz | |
| deleted_at | timestamptz | |

---

## weekly_summaries

Сгенерированные недельные отчёты.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| week_start | date | Понедельник недели |
| week_end | date | Воскресенье |
| summary_text | text | Текст сводки |
| metrics_json | jsonb | Агрегированные метрики |
| created_at | timestamptz | |

---

## whoop_connections

OAuth-подключения к WHOOP.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users UNIQUE | |
| whoop_user_id | varchar(100) | ID в WHOOP |
| access_token | text | Зашифрован |
| refresh_token | text | Зашифрован |
| token_expires_at | timestamptz | |
| scopes | text | Granted scopes |
| is_active | bool | default true |
| last_refresh_at | timestamptz | |
| created_at | timestamptz | |
| updated_at | timestamptz | |

---

## sync_events

Журнал синхронизаций с WHOOP.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| sync_type | varchar(20) | periodic, webhook, manual |
| data_type | varchar(20) | sleep, recovery, workout, cycle |
| status | varchar(20) | started, success, failed |
| records_synced | int | Количество записей |
| error_message | text | nullable |
| started_at | timestamptz | |
| completed_at | timestamptz | nullable |

---

## agent_runs

Каждый вызов агента.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| trigger | varchar(20) | user_message, scheduler, webhook |
| input_text | text | Входное сообщение |
| output_text | text | Ответ агента |
| model | varchar(50) | Использованная модель |
| tokens_input | int | |
| tokens_output | int | |
| duration_ms | int | |
| error | text | nullable |
| trace_id | varchar(100) | OpenAI trace ID, nullable |
| created_at | timestamptz | |

---

## tool_calls

Журнал вызовов tools внутри agent run.

| Поле | Тип | Описание |
|------|-----|----------|
| id | UUID PK | |
| agent_run_id | UUID FK → agent_runs | |
| tool_name | varchar(100) | |
| arguments | jsonb | Входные аргументы |
| result | jsonb | Результат |
| duration_ms | int | |
| error | text | nullable |
| created_at | timestamptz | |

---

## Диаграмма связей

```
users (1) ──→ (1) telegram_accounts
users (1) ──→ (1) whoop_connections
users (1) ──→ (N) user_profile
users (1) ──→ (N) sleep_logs
users (1) ──→ (N) recovery_logs
users (1) ──→ (N) workout_logs
users (1) ──→ (N) meal_logs
users (1) ──→ (N) body_metrics
users (1) ──→ (N) daily_notes
users (1) ──→ (N) memory_notes
users (1) ──→ (N) derived_rules
users (1) ──→ (N) weekly_summaries
users (1) ──→ (N) agent_runs
users (1) ──→ (N) sync_events
agent_runs (1) ──→ (N) tool_calls
```
