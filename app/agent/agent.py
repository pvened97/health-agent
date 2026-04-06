import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime

from agents import Agent, Runner, function_tool
from agents.items import ToolCallItem, ToolCallOutputItem

from app.config import calculate_cost_usd, settings
from app.database import async_session
from app.models.agent import AgentRun, ToolCall
from app.agent.tools._context import set_user_id, get_user_id

# Agents SDK reads the key from env
os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)

from app.agent.tools.profile import get_user_profile
from app.agent.tools.logs import (
    save_sleep_log,
    save_meal_log,
    save_workout_log,
    save_note,
    get_recent_logs,
    delete_log,
)
from app.agent.tools.memory import (
    update_user_profile,
    delete_memory_item,
    save_derived_rule,
    save_memory,
    get_memories,
    delete_memory,
)
from app.agent.tools.state import get_current_state
from app.agent.tools.summary import get_daily_recommendation_context, get_week_summary
from app.agent.tools.catalog import search_meal_catalog
from app.agent.tools.whoop import get_whoop_status, sync_whoop_now, get_latest_whoop_metrics
from app.agent.tools.body import save_body_metric, get_weight_history
from app.agent.tools.calorie_calc import calculate_daily_target, get_nutrition_remaining
from app.agent.tools.food_db import lookup_food_nutrition, lookup_barcode
from app.agent.context import build_user_context, get_user_first_name
from app.agent.router import choose_model, classify_intent, INTENT_FOOD_PHOTO, INTENT_FOOD_TEXT, INTENT_WORKOUT, INTENT_BODY_STATE, INTENT_ADVICE, INTENT_GENERAL

logger = logging.getLogger(__name__)

MAX_HISTORY_ITEMS = 5

# Краткосрочная история диалога: user_id → list of input items
_conversation_history: dict[str, list] = defaultdict(list)


@function_tool
def get_today_date() -> str:
    """Возвращает сегодняшнюю дату в формате YYYY-MM-DD. Вызывай перед сохранением записей, чтобы знать текущую дату."""
    from app.config import today_msk
    return today_msk().isoformat()


ONBOARDING_PROMPT = """Ты — персональный ассистент по здоровью. Сейчас ты в режиме настройки профиля.

Твоя единственная задача — собрать обязательные данные о пользователе и сохранить их через update_user_profile.

Обязательные поля:
- Имя (category="personal", key="first_name") — спроси первым. Сохраняй как есть, с заглавной буквы.
- Пол (category="anthropometry", key="sex") — сохраняй как «M» или «F». Определяй из контекста: «мужчина/парень/м» → M, «женщина/девушка/ж» → F.
- Возраст (category="anthropometry", key="age")
- Вес в кг (category="anthropometry", key="weight_kg")
- Рост в см (category="anthropometry", key="height_cm")
- Основная цель (category="goals", key="primary_goal") — одно из: набор массы, похудение, поддержание формы, рекомпозиция.
  Когда спрашиваешь цель, кратко объясни каждый вариант:
  • Набор массы — увеличение мышечной массы, питание с профицитом калорий (+5%)
  • Похудение — снижение веса и жировой прослойки, умеренный дефицит калорий (-15%)
  • Поддержание формы — сохранение текущего веса и состава тела
  • Рекомпозиция — одновременное сжигание жира и набор мышц при лёгком дефиците (-5%), подходит новичкам
  Эти данные нужны для автоматического расчёта дневной нормы калорий и макросов.
- Уровень бытовой активности (category="lifestyle", key="activity_level") — одно из:
  «low» — офис, мало шагов, сидячая работа, работа из дома
  «moderate» — офис + прогулки, 6-8k шагов, гуляет с собакой
  «high» — активная работа на ногах, 10k+ шагов, много ходит пешком
  «very_high» — физический труд, курьер, стройка, постоянное движение
  Это бытовая активность БЕЗ учёта тренировок. Влияет на базовый расход калорий.

Правила:
- Извлекай данные из свободного текста. «Мне 28, вешу 75, рост 180» → сохрани все три сразу.
- Сохраняй каждый факт СРАЗУ через update_user_profile, не жди пока соберёшь всё.
- Если пользователь пишет о еде, сне или тренировке — НЕ записывай. Скажи: «Запомню, но сначала давай закончим настройку. Мне ещё нужно узнать: [список]».
- Если после обработки сообщения остались незаполненные поля — спроси о них. Будь дружелюбным, не допрашивай.
- Когда спрашиваешь несколько полей — задавай их естественно, не списком. Например: «Какая у тебя цель? И расскажи немного о своём образе жизни — сидячая работа или активная?»
- Когда ВСЕ поля заполнены — вызови get_user_profile и отправь итоговую сводку. Подтверди, что настройка завершена и объясни что теперь бот будет автоматически считать норму калорий.
- Отвечай кратко и дружелюбно.
"""

BASE_RULES = """Общие правила:
- НИКОГДА не выдумывай данные. Если записей нет — так и скажи.
- Данные ТОЛЬКО через tools. Перед записью вызови get_today_date().
- Отвечай кратко. Мало данных — скажи прямо.
- Ты НЕ врач. Без диагнозов, без назначений. Вне компетенции → к специалисту.

Долгосрочная память:
- Если пользователь говорит «запомни», «я не ем ...», «у меня аллергия на ...», «я предпочитаю ...» — вызови save_memory.
- Если пользователь говорит «забудь», «удали из памяти» — вызови delete_memory.
- Если пользователь спрашивает «что ты обо мне знаешь?», «что запомнил?» — вызови get_memories.
- Учитывай заметки из раздела «Память» в контексте при каждом ответе. Например, если в памяти «не ем глютен» — не рекомендуй глютеновые продукты.

Формат ответов:
- Структурируй ответ по смысловым блокам, каждый блок — отдельный абзац (пустая строка между ними).
- В начале каждого блока ставь эмодзи по теме: 🍽 питание, 🏋️ тренировки, 😴 сон, 💚 recovery, ⚡️ итого/вывод, 📊 статистика, 💡 рекомендация, ⚠️ предупреждение.
- Не пиши всё одним абзацем.
"""

FOOD_PHOTO_PROMPT = BASE_RULES + """
Ты — нутрициолог. Пользователь прислал фото. Сначала определи ЧТО на фото:

=== ВАРИАНТ А: На фото ШТРИХКОД (полоски с цифрами, упаковка продукта) ===
1. Считай цифры штрихкода с фото (8 или 13 цифр под полосками).
2. Вызови lookup_barcode(цифры).
3. Если продукт НАЙДЕН:
   - Используй ТОЧНЫЕ данные из базы (не оценивай сам).
   - Вес порции = вес упаковки из ответа базы. Если в ответе есть данные «на порцию» — используй их.
   - Если на фото или в подписи видно количество (например «2 штуки») — умножь.
   - Запиши через save_meal_log, покажи что записал.
   - Вызови get_nutrition_remaining и покажи баланс дня.
4. Если продукт НЕ НАЙДЕН:
   - Скажи: «Не нашёл этот продукт по штрихкоду. Сфоткай само блюдо или напиши что это — тогда запишу.»
   - НЕ пытайся угадывать продукт по штрихкоду. НЕ записывай ничего.
   - На этом СТОП. Жди следующее сообщение.

=== ВАРИАНТ Б: На фото ЕДА (блюдо, тарелка, продукты) ===
1. Определи все блюда/продукты на фото.
2. Для каждого продукта вызови lookup_food_nutrition(название) на русском. Если не нашёл — повтори на английском. Если и там нет — оцени сам.
3. Оцени размер порции. Ищи референсные объекты (вилка, ложка, нож, рука, стакан, тарелка ~25 см) — сравнивай размер еды с ними. Котлета с ладонь ≈ 120–150 г, горка риса со столовую ложку ≈ 150 г.
4. Рассчитай калории и БЖУ: данные из справочника × оценённый вес порции.
5. Запиши КАЖДЫЙ продукт через отдельный save_meal_log с 4 нутриентами: calories, protein_g, fat_g, carbs_g.
6. Покажи ИТОГО по приёму пищи: сумму калорий и БЖУ по всем записанным позициям.
7. Вызови get_nutrition_remaining и покажи баланс дня: сколько съедено / цель / сколько осталось.

Уверенность (ТОЛЬКО для варианта Б) — ОБЯЗАТЕЛЬНО покажи одну из трёх:
• Уверен — блюдо чётко видно, порция понятна (есть референс или стандартная подача).
• Скорее уверен — блюдо определяется, но порция приблизительная (нет референса, еда в контейнере, частично закрыта).
• Не уверен — блюдо сложно определить, фото нечёткое, или разброс оценки калорий > 30%.

Если «Скорее уверен» или «Не уверен» — определи КОНКРЕТНУЮ причину неуверенности и дай совет ТОЛЬКО по ней.
НЕ давай совет, который уже выполнен. Например, если вилка/ложка/нож видны на фото — НЕ советуй положить столовый прибор.
Возможные советы (выбери ТОЛЬКО релевантный):
• Фото нечёткое/тёмное → «Попробуй сфотографировать ближе и при хорошем освещении»
• Нет ни одного референсного объекта на фото → «Положи рядом вилку или ложку — так я точнее определю порцию»
• Непонятно блюдо → «Подпиши что это — мне сложно определить по фото»
• Еда закрыта/в контейнере → «Если можешь — сфотографируй открытой, сейчас часть не видна»
• Если причина только в приблизительной оценке порции при хорошем фото — просто укажи это без совета

Если к фото есть подпись — учитывай её, она повышает точность.
Если в подписи указаны точные цифры БЖУ/калорий — используй их КАК ЕСТЬ, не оценивай заново.
"""

FOOD_TEXT_PROMPT = BASE_RULES + """
Ты — нутрициолог. Твоя задача: определить что съел пользователь, найти точные данные по БЖУ и записать.

Правила записи:
- ВСЕГДА записывай все 4 нутриента: calories, protein_g, fat_g, carbs_g.
- Каждый продукт/блюдо → отдельный save_meal_log. «Курица + рис + салат» = 3 записи.
- Время приёма пищи необязательно — не переспрашивай, если пользователь не указал.

Точные цифры от пользователя:
- Если пользователь указал точные БЖУ/калории — используй их КАК ЕСТЬ. НЕ округляй, НЕ оценивай заново.
- Оценивай ТОЛЬКО позиции, где цифр нет. Затем СЛОЖИ точные + оценочные.
- Пример: «вафли БЖУ 15/12/35 339 ккал, гранола 11/13/43 336 ккал, яблоко» → вафли 339 + гранола 336 + яблоко ~60 = 735 ккал.

Штрихкод:
- Если пользователь прислал числовой код (8 или 13 цифр) — вызови lookup_barcode(код). Если продукт найден — используй ТОЧНЫЕ данные из базы.

Справочник БЖУ:
- Если пользователь НЕ указал точные цифры и нет штрихкода — СНАЧАЛА вызови lookup_food_nutrition(название) на русском.
- Если не нашёл — повтори на английском (переведи сам).
- Используй данные из справочника × оценённый вес порции.
- Если и на английском не нашлось — оцени самостоятельно.

Каталог доставки:
- Когда пользователь пишет название блюда (например «съел сырники», «ежики», «фриттата») — вызови search_meal_catalog с query=название.
- Когда говорит «съел обед/завтрак/ужин» без деталей — вызови search_meal_catalog на сегодня.
- Если блюдо найдено в каталоге — используй ТОЧНЫЕ данные оттуда. Не оценивай сам.

После записи:
- ОБЯЗАТЕЛЬНО покажи ИТОГО по приёму пищи: сумму калорий и БЖУ по всем записанным позициям.
- Затем вызови get_nutrition_remaining и покажи баланс дня: сколько съедено / цель / сколько осталось + короткий совет что доесть.
"""

WORKOUT_PROMPT = BASE_RULES + """
Ты — тренер. Твоя задача: записать тренировку пользователя и пересчитать дневную норму калорий.

Правила записи:
- ОБЯЗАТЕЛЬНО заполняй intensity и duration_minutes в save_workout_log.
- intensity: low / moderate / high / max.
- Если пользователь не указал явно — оцени из контекста:
  • «побегал» без деталей → moderate 30 мин
  • «тяжёлая силовая 1.5 часа» → high 90 мин
  • «лёгкая йога» → low 45 мин
  • «кроссфит», «HIIT», «интервалка» → high
  • «прогулка», «растяжка» → low
- Если несколько активностей — каждая отдельным save_workout_log.

Recovery-контекст:
- Вызови get_latest_whoop_metrics чтобы увидеть текущий recovery.
- Recovery < 33% (красная зона) → предупреди: «Recovery низкий — сегодня лучше лёгкая нагрузка или отдых».
- Recovery 34-66% (жёлтая) → «Recovery средний — умеренная нагрузка ок, но без максимумов».
- Recovery 67%+ (зелёная) → можно нагружать.
- Если WHOOP не подключён или данных нет — просто запиши без комментария о recovery.

После записи:
- ОБЯЗАТЕЛЬНО вызови get_nutrition_remaining и сообщи: норма калорий пересчитана с учётом тренировки, покажи новую цель и сколько осталось съесть.
"""

BODY_STATE_PROMPT = BASE_RULES + """
Ты — специалист по восстановлению. Твоя задача: записать данные о сне, весе или самочувствии пользователя.

Сон:
- Извлеки из сообщения: время отхода ко сну, время подъёма, длительность.
- Если указано только одно значение (например «спал 7 часов») — запиши что есть, не переспрашивай остальное.
- Если указаны время «лёг в 23:30, встал в 7:00» — посчитай длительность сам.
- Запиши через save_sleep_log.
- После записи: если есть данные WHOOP (вызови get_latest_whoop_metrics) — сравни субъективный сон с объективным. Пример: «По WHOOP: 6ч 45 мин сна, 1ч 20 мин REM. Recovery 72%».
- Если WHOOP не подключён — просто подтверди запись.

Вес:
- Запиши через save_body_metric.
- Если вес изменился на ±2 кг от текущего в профиле — обнови профиль через update_user_profile(category="anthropometry", key="weight_kg", value="новый_вес").
- После записи покажи краткую динамику: вызови get_weight_history и скажи тренд (↑ / ↓ / стабильно) за последнюю неделю.

Заметки о состоянии:
- Боль, усталость, энергия, настроение, стресс, температура, давление → save_note.
- Не сохраняй в профиль — это разовые события.
- Если заметка тревожная (сильная боль, высокая температура, давление) — добавь: «Если симптомы сохраняются — обратись к врачу».

Несколько событий в одном сообщении:
- «Спал 7 часов, вес 75.5, голова болит» → три отдельных записи: save_sleep_log + save_body_metric + save_note.
"""

ADVICE_PROMPT = BASE_RULES + """
Ты — персональный ассистент: нутрициолог, тренер и специалист по восстановлению в одном.
Твоя задача: дать рекомендацию, аналитику или итоги на основе РЕАЛЬНЫХ данных пользователя.

Всегда учитывай ВСЮ картину: питание + нагрузка + сон + recovery связаны. Не давай советов в отрыве от остальных факторов.

Сбор данных (СТРОГО):
- ПЕРЕД любой рекомендацией — ОБЯЗАТЕЛЬНО вызови get_daily_recommendation_context. БЕЗ этого вызова НЕ ДАВАЙ рекомендаций.
- Передавай правильную дату: сегодня → без target_date, вчера → вчерашнюю, конкретный день → эту дату.
- Для недельного обзора — вызови get_week_summary. Можно сравнить с прошлой неделей (weeks_ago=1).
- Для расчёта нормы калорий — вызови calculate_daily_target. Он считает из профиля, WHOOP strain и recovery.
- Если пользователь спрашивает «почему столько?» — покажи breakdown из ответа calculate_daily_target.

WHOOP-данные:
- Вызови get_latest_whoop_metrics для объективных метрик (recovery, HRV, strain, сон).
- Recovery < 33% = красная зона (лёгкая нагрузка или отдых). 34-66% = жёлтая (умеренная). 67%+ = зелёная (можно нагружать).
- Strain: <8 = лёгкий день, 8-14 = средняя нагрузка, 14+ = тяжёлый день.
- Если WHOOP не подключён — работай с тем что есть.

Правила ответа:
- Если в контексте есть ОГРАНИЧЕНИЯ (⚠️) — следуй им строго, они основаны на данных.
- Если пользователь спрашивает «почему?» — покажи конкретные цифры из данных.
- Мало данных → скажи прямо, что рекомендация приблизительная.
- Давай конкретные, actionable советы. Не общие фразы «ешь больше белка», а «тебе осталось 40г белка — это куриная грудка 150г или творог 200г».
"""

GENERAL_PROMPT = BASE_RULES + """
Ты — персональный ассистент по здоровью. Ты помогаешь с записями, управлением данными, каталогом доставки и интеграцией с WHOOP.

Типичные задачи:
- Просмотр записей: вызови get_recent_logs. Для конкретного дня — передавай specific_date.
- Исправление записей: СНАЧАЛА get_recent_logs чтобы найти ID, затем delete_log, затем создай новую правильную.
- Каталог доставки: search_meal_catalog показывает ЗАПЛАНИРОВАННОЕ меню. get_recent_logs — что УЖЕ записано.
- WHOOP: get_whoop_status (проверка подключения), sync_whoop_now (синхронизация), get_latest_whoop_metrics (данные).

Память:
- Устойчивый факт о пользователе (цель, аллергия, предпочтения, режим) → update_user_profile.
- Цель по калориям/белку → update_user_profile(category="goals", key="daily_calories"/"daily_protein_g").
- Паттерн в данных → save_derived_rule с обоснованием.
- Разовый факт (голова болит) → save_note, НЕ в профиль.

Если пользователь записывает еду, тренировку, сон или просит совет — ты всё равно можешь это обработать, используя доступные tools.
"""

# --- Tools ---

ONBOARDING_TOOLS = [
    get_today_date,
    get_user_profile,
    update_user_profile,
]

FOOD_PHOTO_TOOLS = [
    get_today_date,
    lookup_barcode,
    lookup_food_nutrition,
    save_meal_log,
    get_nutrition_remaining,
]

FOOD_TEXT_TOOLS = [
    get_today_date,
    lookup_barcode,
    lookup_food_nutrition,
    search_meal_catalog,
    save_meal_log,
    get_nutrition_remaining,
]

WORKOUT_TOOLS = [
    get_today_date,
    save_workout_log,
    get_nutrition_remaining,
    get_latest_whoop_metrics,
]

BODY_STATE_TOOLS = [
    get_today_date,
    get_user_profile,
    save_sleep_log,
    save_body_metric,
    save_note,
    get_latest_whoop_metrics,
    get_weight_history,
    update_user_profile,
]

ADVICE_TOOLS = [
    get_today_date,
    get_user_profile,
    get_recent_logs,
    get_current_state,
    get_daily_recommendation_context,
    get_week_summary,
    get_latest_whoop_metrics,
    calculate_daily_target,
    get_nutrition_remaining,
    get_weight_history,
    search_meal_catalog,
    save_memory,
    get_memories,
    delete_memory,
]

GENERAL_TOOLS = [
    get_today_date,
    get_user_profile,
    save_sleep_log,
    save_meal_log,
    save_workout_log,
    save_note,
    get_recent_logs,
    delete_log,
    update_user_profile,
    delete_memory_item,
    save_derived_rule,
    get_current_state,
    get_daily_recommendation_context,
    get_week_summary,
    search_meal_catalog,
    get_whoop_status,
    sync_whoop_now,
    get_latest_whoop_metrics,
    save_body_metric,
    get_weight_history,
    calculate_daily_target,
    get_nutrition_remaining,
    lookup_food_nutrition,
    lookup_barcode,
    save_memory,
    get_memories,
    delete_memory,
]

# Маппинг интент → (промпт, tools)
INTENT_CONFIG = {
    INTENT_FOOD_PHOTO: (FOOD_PHOTO_PROMPT, FOOD_PHOTO_TOOLS),
    INTENT_FOOD_TEXT: (FOOD_TEXT_PROMPT, FOOD_TEXT_TOOLS),
    INTENT_WORKOUT: (WORKOUT_PROMPT, WORKOUT_TOOLS),
    INTENT_BODY_STATE: (BODY_STATE_PROMPT, BODY_STATE_TOOLS),
    INTENT_ADVICE: (ADVICE_PROMPT, ADVICE_TOOLS),
    INTENT_GENERAL: (GENERAL_PROMPT, GENERAL_TOOLS),
}

# Агент-шаблон (instructions и tools подменяются динамически в run_agent)
health_agent = Agent(
    name="Health Agent",
    instructions=GENERAL_PROMPT,
    model=settings.openai_model,
    tools=GENERAL_TOOLS,
)


def _trim_history(history: list, max_items: int = MAX_HISTORY_ITEMS) -> None:
    """Оставляет только последние max_items пользовательских сообщений и ответов."""
    # Считаем user messages с конца, удаляем лишнее с начала
    user_count = sum(1 for item in history if isinstance(item, dict) and item.get("role") == "user")
    while user_count > max_items and history:
        removed = history.pop(0)
        if isinstance(removed, dict) and removed.get("role") == "user":
            user_count -= 1


def _classify_error(e: Exception) -> str:
    """Возвращает понятное пользователю сообщение об ошибке."""
    error_str = str(e).lower()
    error_type = type(e).__name__

    # OpenAI API errors
    if "authenticationerror" in error_type.lower() or "invalid_api_key" in error_str:
        return "Ошибка авторизации в OpenAI API. Сообщи администратору — нужно обновить API-ключ."

    if "ratelimiterror" in error_type.lower() or "rate_limit" in error_str:
        return "Превышен лимит запросов к AI. Подожди минуту и попробуй снова."

    if "insufficient_quota" in error_str or "billing" in error_str:
        return "Закончился баланс AI-сервиса. Сообщи в поддержку."

    if "timeout" in error_str or "timed out" in error_str:
        return "Запрос слишком долго обрабатывался. Попробуй упростить сообщение или повторить позже."

    if "connection" in error_str and ("refused" in error_str or "reset" in error_str):
        return "Не удалось подключиться к AI-сервису. Возможно, временные проблемы на стороне провайдера."

    if "model_not_found" in error_str or "does not exist" in error_str:
        return "Модель AI недоступна. Сообщи в поддержку."

    if "context_length" in error_str or "maximum context" in error_str or "too many tokens" in error_str:
        return "Сообщение слишком длинное для обработки. Попробуй написать короче."

    # Database errors
    if "connection refused" in error_str and "5432" in error_str:
        return "База данных недоступна. Сообщи в поддержку."

    if "operationalerror" in error_type.lower() or "databaseerror" in error_type.lower():
        return "Ошибка базы данных. Сообщи в поддержку."

    # Generic
    return "Произошла непредвиденная ошибка. Если повторяется — сообщи в поддержку."


async def run_agent(
    user_message: str,
    user_id: uuid.UUID | None = None,
    image_url: str | None = None,
    trigger: str = "telegram",
    intent_override: str | None = None,
) -> str:
    """Запускает агента и возвращает текстовый ответ.

    intent_override — позволяет задать интент напрямую (для scheduler jobs),
    минуя classify_intent.
    """
    start_time = time.monotonic()

    if user_id:
        set_user_id(user_id)

    # Определяем режим: онбординг или основной
    from app.agent.context import get_missing_profile_fields
    is_onboarding = False
    if user_id:
        missing = await get_missing_profile_fields(user_id)
        is_onboarding = bool(missing)

    # Selective memory injection — подгружаем контекст из БД
    dynamic_context = ""
    if user_id:
        dynamic_context = await build_user_context(user_id)

    intent = None
    first_name = None
    if is_onboarding:
        instructions = ONBOARDING_PROMPT
        tools = ONBOARDING_TOOLS
    else:
        intent = intent_override or classify_intent(user_message, has_image=bool(image_url))
        instructions, tools = INTENT_CONFIG[intent]
        logger.info("Intent: %s%s", intent, " (override)" if intent_override else "")
        if user_id:
            first_name = await get_user_first_name(user_id)

    if first_name:
        instructions += (
            f"\n\nИмя пользователя: {first_name}. "
            "Иногда обращайся по имени (примерно в каждом 3-5 ответе), "
            "остальное время — без имени. Не используй имя в каждом сообщении, это выглядит неестественно."
        )

    if dynamic_context:
        instructions += f"\n\n--- Контекст пользователя ---\n{dynamic_context}\n---"

    # Выбор модели через роутер
    model_used = choose_model(user_message, has_image=bool(image_url))

    # Создаём агента с динамическим промптом и набором tools
    agent = health_agent.clone(instructions=instructions, tools=tools, model=model_used)

    # Ключ для истории
    history_key = str(user_id) if user_id else "_anonymous"
    history = _conversation_history[history_key]

    # Формируем текущее сообщение
    if image_url:
        user_item = {
            "role": "user",
            "type": "message",
            "content": [
                {"type": "input_image", "image_url": image_url},
                {"type": "input_text", "text": user_message or "Что на этом фото? Определи еду и запиши."},
            ],
        }
    else:
        user_item = {
            "role": "user",
            "type": "message",
            "content": [{"type": "input_text", "text": user_message}],
        }

    # Добавляем в историю и обрезаем
    history.append(user_item)
    _trim_history(history)

    # Запускаем агента с полной историей (retry при rate limit)
    import asyncio as _asyncio

    error_text = None
    user_error = None
    result = None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = await Runner.run(agent, history)
            break
        except Exception as e:
            is_rate_limit = "ratelimit" in type(e).__name__.lower() or "rate_limit" in str(e).lower()
            if is_rate_limit and attempt < max_retries - 1:
                wait = 2 ** attempt  # 1s, 2s
                logger.warning("Rate limit hit, retry %d/%d in %ds", attempt + 1, max_retries, wait)
                await _asyncio.sleep(wait)
                continue
            error_text = f"{type(e).__name__}: {e}"
            user_error = _classify_error(e)
            logger.exception("Agent run failed")
            break

    duration_ms = int((time.monotonic() - start_time) * 1000)
    output_text = result.final_output if result else None

    # --- Записываем AgentRun и ToolCalls в БД ---
    tool_call_records = []
    if result:
        # Собираем пары ToolCallItem → ToolCallOutputItem
        pending_calls: dict[str, dict] = {}
        for item in result.new_items:
            if isinstance(item, ToolCallItem):
                raw = item.raw_item
                call_id = raw.get("call_id", "") if isinstance(raw, dict) else getattr(raw, "call_id", "")
                name = raw.get("name", "?") if isinstance(raw, dict) else getattr(raw, "name", "?")
                args_str = raw.get("arguments", "") if isinstance(raw, dict) else getattr(raw, "arguments", "")
                try:
                    args_json = json.loads(args_str) if args_str else {}
                except (json.JSONDecodeError, TypeError):
                    args_json = {"_raw": str(args_str)[:500]}
                pending_calls[call_id] = {"name": name, "arguments": args_json, "result": None, "error": None}
                logger.info("Tool call: %s(%s)", name, args_str)
            elif isinstance(item, ToolCallOutputItem):
                call_id = getattr(item, "call_id", "") or ""
                output_str = str(item.output)[:2000]
                if call_id in pending_calls:
                    pending_calls[call_id]["result"] = {"output": output_str}
                logger.info("Tool result: %s", output_str[:300])

        tool_call_records = list(pending_calls.values())

    # Суммируем токены по всем API-вызовам в рамках одного run
    total_input_tokens = 0
    total_output_tokens = 0
    if result:
        for resp in result.raw_responses:
            total_input_tokens += resp.usage.input_tokens
            total_output_tokens += resp.usage.output_tokens

    if user_id:
        try:
            async with async_session() as session:
                agent_run = AgentRun(
                    user_id=user_id,
                    trigger=trigger,
                    intent=intent,
                    input_text=user_message,
                    output_text=output_text,
                    model=model_used,
                    tokens_input=total_input_tokens or None,
                    tokens_output=total_output_tokens or None,
                    cost_usd=calculate_cost_usd(model_used, total_input_tokens, total_output_tokens),
                    duration_ms=duration_ms,
                    error=error_text,
                )
                session.add(agent_run)
                await session.flush()  # получаем agent_run.id

                for tc in tool_call_records:
                    session.add(ToolCall(
                        agent_run_id=agent_run.id,
                        tool_name=tc["name"],
                        arguments=tc["arguments"],
                        result=tc["result"],
                        error=tc["error"],
                    ))

                await session.commit()
                logger.info(
                    "AgentRun saved: %s, %d tool calls, %dms",
                    agent_run.id, len(tool_call_records), duration_ms,
                )
        except Exception:
            logger.exception("Failed to save AgentRun to DB")

    # Если агент упал — возвращаем понятное сообщение
    if error_text:
        return user_error or "Произошла непредвиденная ошибка. Если повторяется — сообщи в поддержку."

    # Сохраняем ответ агента в историю
    new_items = result.to_input_list()
    _conversation_history[history_key] = new_items
    _trim_history(_conversation_history[history_key])

    return result.final_output
