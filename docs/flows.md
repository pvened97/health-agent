# Health Agent — Бизнес-схемы

## 1. Точка входа: маршрутизация сообщения

```mermaid
flowchart TD
    MSG["📱 Сообщение в Telegram"] --> ACL{"Пользователь\nв whitelist?"}
    ACL -->|Нет| IGNORE["⛔ Игнор"]
    ACL -->|Да| PROFILE{"Профиль\nзаполнен?"}

    PROFILE -->|Нет| ONBOARD["→ Онбординг"]
    PROFILE -->|Да| INTENT["Классификация интента\n(pattern matching)"]

    INTENT --> PHOTO{"Есть фото?"}
    PHOTO -->|Да| FP["🍽 food_photo\n4 tools"]

    PHOTO -->|Нет| MATCH["Проверка паттернов\nпо категориям"]
    MATCH --> MULTI{"Матч из\n2+ категорий?"}
    MULTI -->|Да| GEN["📦 general\n22 tools — обрабатывает всё"]

    MULTI -->|Нет| SINGLE{"Какая категория?"}
    SINGLE -->|"съел, поел,\nна завтрак"| FT["🍽 food_text\n5 tools"]
    SINGLE -->|"побегал,\nсиловая"| WK["🏋️ workout\n4 tools"]
    SINGLE -->|"спал, вешу,\nболит"| BS["😴 body_state\n8 tools"]
    SINGLE -->|Нет матча| ADV_CHECK{"Advice-паттерн?\nпосоветуй, итог,\nчто поесть"}
    ADV_CHECK -->|Да| ADV["💡 advice\n11 tools, read-only"]
    ADV_CHECK -->|Нет| GEN

    style ONBOARD fill:#FFF3E0
    style FP fill:#E8F5E9
    style FT fill:#E8F5E9
    style WK fill:#E3F2FD
    style BS fill:#F3E5F5
    style ADV fill:#FFF9C4
    style GEN fill:#ECEFF1
```

## 2. Онбординг

```mermaid
flowchart TD
    START["/start"] --> CHECK{"Все 6 полей\nзаполнены?"}

    CHECK -->|Да| HELLO["Приветствие\n+ справка\n+ фото-советы"]

    CHECK -->|Нет| ASK["Бот объясняет что нужно:\nпол, возраст, рост, вес,\nцель, активность"]

    ASK --> INPUT["Пользователь пишет\nсвободным текстом"]
    INPUT --> EXTRACT["LLM извлекает факты\nиз текста"]
    EXTRACT --> SAVE["Каждый факт сохраняется\nсразу в user_profile"]

    SAVE --> FOOD_ATTEMPT{"Пользователь\nпро еду/сон?"}
    FOOD_ATTEMPT -->|Да| DEFER["«Запомню, но сначала\nдавай закончим настройку»"]
    DEFER --> REMAIN

    FOOD_ATTEMPT -->|Нет| REMAIN{"Остались\nнезаполненные?"}
    REMAIN -->|Да| ASK_MORE["Спросить оставшиеся\n(естественно, не списком)"]
    ASK_MORE --> INPUT

    REMAIN -->|Нет| CALC["Автоматический расчёт\nдневной нормы калорий\nпо формуле Mifflin–St Jeor"]
    CALC --> SUMMARY["Итоговая сводка профиля\n+ объяснение как работает бот"]

    style CHECK fill:#FFF3E0
    style CALC fill:#E8F5E9
    style SUMMARY fill:#E8F5E9
```

## 3. Запись питания

```mermaid
flowchart TD
    subgraph photo ["📷 Фото еды"]
        P_IN["Пользователь\nотправляет фото"] --> P_DETECT["LLM определяет\nвсе блюда на фото"]
        P_DETECT --> P_REF["Оценка порции\nпо референсу:\nвилка, тарелка ~25 см,\nладонь, стакан"]
        P_REF --> P_DB["Поиск БЖУ\nв FatSecret API\n(рус → англ → оценка LLM)"]
        P_DB --> P_CALC["Данные справочника\n× оценённый вес порции"]
        P_CALC --> P_CONF{"Уровень\nуверенности?"}
        P_CONF -->|"Уверен"| P_SAVE
        P_CONF -->|"Скорее уверен /\nНе уверен"| P_TIP["Совет:\n«положи вилку рядом»\n«сфоткай ближе»\n«подпиши что это»"]
        P_TIP --> P_SAVE["Запись каждого продукта\nотдельным save_meal_log\n(ккал, Б, Ж, У)"]
    end

    subgraph text ["✏️ Текст о еде"]
        T_IN["«Съел омлет с сыром\nи кофе с молоком»"] --> T_EXACT{"Указаны\nточные БЖУ?"}
        T_EXACT -->|"Да\n(«вафли 339 ккал\nБЖУ 15/12/35»)"| T_USE["Использовать\nкак есть"]

        T_EXACT -->|Нет| T_CATALOG["Поиск в каталоге\nдоставки"]
        T_CATALOG --> T_FOUND{"Найдено?"}
        T_FOUND -->|Да| T_CATALOG_DATA["Точные данные\nиз каталога"]
        T_FOUND -->|Нет| T_FATSECRET["Поиск в FatSecret API\nсначала рус, потом англ"]
        T_FATSECRET --> T_API_FOUND{"Найдено?"}
        T_API_FOUND -->|Да| T_API_DATA["Данные справочника\n× оценка порции"]
        T_API_FOUND -->|Нет| T_LLM["LLM оценивает\nсамостоятельно"]

        T_USE --> T_SAVE
        T_CATALOG_DATA --> T_SAVE
        T_API_DATA --> T_SAVE
        T_LLM --> T_SAVE["Запись каждого продукта\nотдельным save_meal_log"]
    end

    subgraph delivery ["🚚 Каталог доставки"]
        D_IN["«Съел обед»\nбез деталей"] --> D_SEARCH["search_meal_catalog\nна сегодня"]
        D_SEARCH --> D_MATCH["Точные ккал и БЖУ\nиз загруженного меню"]
        D_MATCH --> D_SAVE["save_meal_log\nс данными каталога"]
    end

    P_SAVE --> BALANCE
    T_SAVE --> BALANCE
    D_SAVE --> BALANCE
    BALANCE["📊 get_nutrition_remaining\n→ остаток дня:\nккал, белок + совет что доесть"]

    style photo fill:#E8F5E9
    style text fill:#E8F5E9
    style delivery fill:#E8F5E9
    style BALANCE fill:#FFF9C4
```

## 4. Запись тренировки

```mermaid
flowchart TD
    IN["«Побегал 5 км»\n«Силовая 60 мин»\n«Кроссфит»"] --> PARSE["LLM определяет:\n— тип тренировки\n— intensity: low/moderate/high/max\n— duration_minutes"]

    PARSE --> EXPLICIT{"Пользователь\nуказал длительность\nи интенсивность?"}
    EXPLICIT -->|Да| SAVE
    EXPLICIT -->|Нет| ESTIMATE["LLM оценивает из контекста:\n«побегал» → moderate 30 мин\n«тяжёлая силовая 1.5ч» → high 90 мин\n«лёгкая йога» → low 45 мин"]
    ESTIMATE --> SAVE["save_workout_log"]

    SAVE --> WHOOP{"WHOOP\nподключён?"}
    WHOOP -->|Да| RECOVERY["Запрос recovery\nс WHOOP"]
    RECOVERY --> ZONE{"Recovery\nзона?"}
    ZONE -->|"🔴 < 33%"| WARN_RED["⚠️ «Recovery низкий —\nлёгкая нагрузка или отдых»"]
    ZONE -->|"🟡 34-66%"| WARN_YELLOW["«Умеренная нагрузка ок,\nбез максимумов»"]
    ZONE -->|"🟢 67%+"| OK_GREEN["«Можно нагружать»"]

    WHOOP -->|Нет| SKIP["Без комментария\nо recovery"]

    WARN_RED --> RECALC
    WARN_YELLOW --> RECALC
    OK_GREEN --> RECALC
    SKIP --> RECALC

    RECALC["📊 Пересчёт нормы калорий\nс учётом тренировки\n(+50–350 ккал в зависимости\nот intensity × duration × тип)"]

    RECALC --> SHOW["Показать новую цель\nи сколько осталось съесть"]

    style IN fill:#E3F2FD
    style RECALC fill:#FFF9C4
    style WARN_RED fill:#FFCDD2
    style WARN_YELLOW fill:#FFF9C4
    style OK_GREEN fill:#C8E6C9
```

## 5. Запись сна, веса, самочувствия

```mermaid
flowchart TD
    IN["Сообщение пользователя"] --> TYPE{"Что в\nсообщении?"}

    TYPE -->|"«Спал 7 часов»\n«Лёг в 23:30,\nвстал в 7:00»"| SLEEP
    TYPE -->|"«Вешу 75.5»\n«Утренний вес 74.8»"| WEIGHT
    TYPE -->|"«Голова болит»\n«Энергия 7/10»\n«Устал»"| NOTE
    TYPE -->|"Несколько событий\n(«спал 7ч, вес 75,\nголова болит»)"| MULTI["3 отдельных записи"]

    subgraph sleep ["😴 Сон"]
        SLEEP["Извлечь:\nдлительность, время\nотбоя и подъёма"] --> SLEEP_SAVE["save_sleep_log"]
        SLEEP_SAVE --> WHOOP_SLEEP{"WHOOP\nподключён?"}
        WHOOP_SLEEP -->|Да| COMPARE["Сравнение:\nсубъективный vs объективный\n«По WHOOP: 6ч 45мин,\n1ч 20мин REM, Recovery 72%»"]
        WHOOP_SLEEP -->|Нет| CONFIRM_SLEEP["Подтверждение записи"]
    end

    subgraph weight ["⚖️ Вес"]
        WEIGHT["save_body_metric"] --> DELTA{"Изменение\n±2 кг от\nпрофиля?"}
        DELTA -->|Да| UPDATE_PROFILE["Обновить вес в профиле\n→ пересчёт нормы калорий"]
        DELTA -->|Нет| TREND
        UPDATE_PROFILE --> TREND["get_weight_history\n→ тренд за неделю:\n↑ / ↓ / стабильно"]
    end

    subgraph note ["📝 Самочувствие"]
        NOTE["save_note"] --> ALARM{"Тревожные\nсимптомы?\n(сильная боль,\nтемпература,\nдавление)"}
        ALARM -->|Да| DOCTOR["⚠️ «Если симптомы\nсохраняются —\nобратись к врачу»"]
        ALARM -->|Нет| CONFIRM_NOTE["Подтверждение записи"]
    end

    MULTI --> sleep
    MULTI --> weight
    MULTI --> note

    style sleep fill:#F3E5F5
    style weight fill:#F3E5F5
    style note fill:#F3E5F5
```

## 6. Рекомендации и аналитика

```mermaid
flowchart TD
    IN["«Что поесть на ужин?»\n«Итог дня»\n«Как прошла неделя?»\n«Какая моя норма калорий?»"] --> COLLECT["Сбор данных\n(ОБЯЗАТЕЛЬНО перед ответом)"]

    COLLECT --> DAY{"Запрос\nпро день?"}
    DAY -->|Да| CTX["get_daily_recommendation_context\n— питание, тренировки,\nсон, recovery за день"]
    DAY -->|Нет| WEEK{"Запрос\nпро неделю?"}
    WEEK -->|Да| WEEK_CTX["get_week_summary\n+ сравнение с прошлой"]
    WEEK -->|Нет| CALC_CTX["calculate_daily_target\n— расчёт нормы"]

    CTX --> WHOOP_CHECK
    WEEK_CTX --> WHOOP_CHECK
    CALC_CTX --> WHOOP_CHECK

    WHOOP_CHECK{"WHOOP\nданные есть?"} -->|Да| WHOOP_DATA["+ recovery, HRV,\nstrain, объективный сон"]
    WHOOP_CHECK -->|Нет| NO_WHOOP["Работает с тем что есть"]

    WHOOP_DATA --> QUALITY
    NO_WHOOP --> QUALITY

    QUALITY{"Quality rules\nв контексте?"}
    QUALITY -->|"⚠️ Ограничения\n(недосып 3 дня,\nтяжёлая тренировка\nвчера)"| STRICT["Следовать\nограничениям строго"]
    QUALITY -->|Нет| NORMAL["Обычный ответ"]

    STRICT --> ANSWER
    NORMAL --> ANSWER

    ANSWER["Конкретный, actionable ответ:\n«Осталось 40г белка —\nэто куриная грудка 150г\nили творог 200г»"]

    LOW_DATA["Мало данных (<5 записей\nза неделю)"] -.->|"Предупреждение\nв контексте"| ANSWER
    LOW_DATA -.-> APPROX["«Рекомендация\nприблизительная»"]

    style IN fill:#FFF9C4
    style ANSWER fill:#E8F5E9
    style STRICT fill:#FFCDD2
```

## 7. Расчёт дневной нормы калорий

```mermaid
flowchart TD
    BMR["<b>1. BMR</b>\nMifflin–St Jeor:\n10×вес + 6.25×рост\n− 5×возраст ± пол"]

    BMR --> MAINTENANCE["<b>2. Базовое поддержание</b>\nBMR × коэффициент активности\nlow 1.35 / moderate 1.45\nhigh 1.55 / very_high 1.60"]

    MAINTENANCE --> LOAD{"Источник\nданных о нагрузке?"}

    LOAD -->|"WHOOP\n(strain есть)"| WHOOP_BONUS["<b>3a. Надбавка по WHOOP</b>\nstrain ≥18 → +320 ккал\nstrain ≥14 → +220\nstrain ≥10 → +120\n× модификатор типа:\nсиловая 1.0 / кардио 0.75\nйога 0.40"]

    LOAD -->|"Ручной ввод\n(без WHOOP)"| MANUAL_BONUS["<b>3b. Оценка по тренировке</b>\nТаблица: длительность × intensity\nshort/medium/long/extended\n× low/moderate/high/max\n→ +0 до +350 ккал\n× модификатор типа"]

    LOAD -->|"Нет тренировок"| NO_LOAD["+0 ккал"]

    WHOOP_BONUS --> GOAL
    MANUAL_BONUS --> GOAL
    NO_LOAD --> GOAL

    GOAL{"<b>4. Цель</b>"}
    GOAL -->|Набор массы| BULK["+5% от базы\n× recovery modifier"]
    GOAL -->|Похудение| CUT["−15% от базы\nплохой recovery → мягче дефицит"]
    GOAL -->|Рекомпозиция| RECOMP["−5% от базы"]
    GOAL -->|Поддержание| MAINTAIN["±0%"]

    BULK --> RECOVERY_MOD
    CUT --> RECOVERY_MOD
    RECOMP --> RECOVERY_MOD
    MAINTAIN --> TOTAL

    RECOVERY_MOD["<b>Recovery modifier</b>\n🟢 ≥67% → 1.0\n🟡 34-66% → 0.85\n🔴 <34% → 0.70"]
    RECOVERY_MOD --> TOTAL

    TOTAL["<b>ИТОГО</b>\nБаза + надбавка + профицит/дефицит\n\nМакросы:\nБелок: 2.0 г/кг (набор) или 1.8 г/кг\nЖиры: 25% от ккал\nУглеводы: остаток"]

    style BMR fill:#E3F2FD
    style MAINTENANCE fill:#E3F2FD
    style TOTAL fill:#E8F5E9
    style RECOVERY_MOD fill:#FFF9C4
```

## 8. Проактивные сообщения (по расписанию)

```mermaid
flowchart TD
    subgraph daily ["Ежедневно"]
        EVE["🌙 22:00 Мск\nВечерний итог"] --> EVE_CHECK{"Есть записи\nо еде за сегодня?"}
        EVE_CHECK -->|Нет| EVE_SKIP["Не отправлять"]
        EVE_CHECK -->|Да| EVE_SEND["Агент с intent=advice:\n🍽 Питание: факт / цель / %\n🏋️ Тренировки\n⚡️ Краткий вывод"]
    end

    subgraph weekly ["Еженедельно"]
        SUN["📊 Вс 20:00\nНедельный обзор"] --> SUN_CHECK{"≥3 дней\nс данными?"}
        SUN_CHECK -->|Нет| SUN_SKIP["Не отправлять"]
        SUN_CHECK -->|Да| SUN_SEND["Агент с intent=advice:\nСон, питание, тренировки,\nrecovery — с дельтой\nк прошлой неделе"]

        MON_STREAK["🔥 Пн 10:00\nStreak check"] --> STREAK_CHECK{"7 дней подряд\nс записями о еде?"}
        STREAK_CHECK -->|Да| STREAK_SEND["«7 дней подряд! 🔥\nТак держать!»"]
        STREAK_CHECK -->|Нет| STREAK_SKIP["Не отправлять"]

        MON_SLEEP["😴 Пн 10:00\nТренд сна"] --> SLEEP_CHECK{"≥3 записей\nза каждую\nиз 2 недель?"}
        SLEEP_CHECK -->|Нет| SLEEP_SKIP["Не отправлять"]
        SLEEP_CHECK -->|Да| SLEEP_COMPARE["Сравнить средний сон\nза 2 недели"]
        SLEEP_COMPARE --> SLEEP_DIFF{"|Δ| > 10 мин?"}
        SLEEP_DIFF -->|Нет| SLEEP_SKIP2["Не отправлять\n(незначительно)"]
        SLEEP_DIFF -->|Да| SLEEP_SEND["📈/📉 Тренд сна:\n~7.2ч → ~6.8ч\n(−24 мин)"]
    end

    subgraph whoop_bg ["WHOOP (фон)"]
        REFRESH["🔑 Каждый час\nОбновление токенов"] --> REFRESH_CHECK{"Токен истекает\nв ближайшие 2ч?"}
        REFRESH_CHECK -->|Да| REFRESH_DO["Refresh OAuth token"]
        REFRESH_CHECK -->|Нет| REFRESH_SKIP["Пропуск"]

        SYNC["🔄 03:00 Мск\nНочная синхронизация"] --> SYNC_DO["Синхронизировать\nWHOOP за 2 дня\n(подстраховка webhooks)"]
    end

    style daily fill:#FFF3E0
    style weekly fill:#E3F2FD
    style whoop_bg fill:#F3E5F5
```

## 9. WHOOP-интеграция

```mermaid
flowchart TD
    CMD["/whoop в Telegram"] --> CONNECTED{"WHOOP\nуже подключён?"}

    CONNECTED -->|Нет| AUTH["Бот отправляет\nссылку OAuth 2.0"]
    AUTH --> BROWSER["Пользователь\nавторизуется в браузере"]
    BROWSER --> CALLBACK["WHOOP callback\n→ /whoop/callback"]
    CALLBACK --> TOKENS["Сохранение\naccess + refresh token"]
    TOKENS --> INITIAL_SYNC["Синхронизация\nза 7 дней"]
    INITIAL_SYNC --> DONE["✅ WHOOP подключён"]

    CONNECTED -->|Да| MANUAL_SYNC["Синхронизация\nза 7 дней"]
    MANUAL_SYNC --> RESULT["Результат:\nN recovery, N тренировок,\nN циклов синхронизировано"]

    subgraph data_flow ["Поток данных WHOOP"]
        direction LR
        WEBHOOK["WHOOP webhook\n(при обновлении)"] --> PROCESS["Обработка:\nrecovery, sleep,\nworkout, cycle"]
        PROCESS --> DB["Запись в БД:\nrecovery_logs,\nsleep_logs,\nworkout_logs,\ncycle_logs"]
        DB --> CONTEXT["Инъекция в контекст\nагента при каждом\nзапросе пользователя"]
    end

    style DONE fill:#E8F5E9
    style data_flow fill:#F3E5F5
```

## 10. Управление записями

```mermaid
flowchart TD
    IN["«Удали последний\nприём пищи»\n«Покажи что я ел вчера»\n«Исправь — было не 200г,\nа 150г»"] --> INTENT["→ general\n(22 tools)"]

    INTENT --> ACTION{"Действие?"}

    ACTION -->|Просмотр| VIEW["get_recent_logs\n(specific_date для\nконкретного дня)"]
    VIEW --> SHOW["Показать записи\nпользователю"]

    ACTION -->|Удаление| FIND["get_recent_logs\n→ найти ID записи"]
    FIND --> DELETE["delete_log(id)\nsoft delete"]
    DELETE --> CONFIRM_DEL["Подтверждение удаления"]

    ACTION -->|Исправление| FIND2["get_recent_logs\n→ найти ID"]
    FIND2 --> DELETE2["delete_log(id)"]
    DELETE2 --> CREATE["Создать новую\nправильную запись"]
    CREATE --> BALANCE["Показать\nобновлённый баланс"]

    style INTENT fill:#ECEFF1
```
