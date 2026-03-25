import json
import logging
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime

from agents import Agent, Runner, function_tool
from agents.items import ToolCallItem, ToolCallOutputItem

from app.config import settings
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
)
from app.agent.tools.state import get_current_state
from app.agent.tools.summary import get_daily_recommendation_context, get_week_summary
from app.agent.tools.catalog import search_meal_catalog
from app.agent.tools.whoop import get_whoop_status, sync_whoop_now, get_latest_whoop_metrics
from app.agent.context import build_user_context
from app.agent.router import choose_model

logger = logging.getLogger(__name__)

MAX_HISTORY_ITEMS = 5

# Краткосрочная история диалога: user_id → list of input items
_conversation_history: dict[str, list] = defaultdict(list)


@function_tool
def get_today_date() -> str:
    """Возвращает сегодняшнюю дату в формате YYYY-MM-DD. Вызывай перед сохранением записей, чтобы знать текущую дату."""
    from app.config import today_msk
    return today_msk().isoformat()


BASE_SYSTEM_PROMPT = """Ты — персональный ассистент: нутрициолог, тренер и специалист по восстановлению в одном.

Всегда учитывай ВСЮ картину: питание + нагрузка + сон + recovery связаны. Не давай советов в отрыве от остальных факторов.

Правила:
- НИКОГДА не выдумывай данные. Если пользователь спрашивает о прошлых записях — ОБЯЗАТЕЛЬНО вызови get_recent_logs. Если записей нет — так и скажи. Не генерируй вымышленные данные.
- Данные ТОЛЬКО через tools. Перед записью вызови get_today_date().
- Когда пользователь спрашивает про конкретный день («за сегодня», «за вчера», «за 2026-03-20») — ВСЕГДА передавай specific_date в get_recent_logs. НЕ используй days для запроса за конкретный день.
- Несколько событий в одном сообщении — отдельный tool call на каждое.
- Еда → если пользователь указал калории/БЖУ (например, с упаковки или сервиса доставки) — используй его цифры как есть, не пересчитывай. Если не указал — оцени примерно. Время приёма пищи необязательно — не переспрашивай, если пользователь не указал.
- Фото еды → определи блюда на изображении, оцени порцию, калории и БЖУ, запиши через save_meal_log. Если подпись к фото есть — учитывай её.
- Тренировка → определи тип и интенсивность.
- Сон → извлеки длительность и время.
- Исправление записей: если пользователь говорит что запись неправильная — СНАЧАЛА вызови get_recent_logs чтобы найти ID, затем удали старую через delete_log, затем создай новую правильную. НЕ просто добавляй новую поверх старой.
- Перед рекомендацией — прочитай профиль и последние данные через tools.
- Отвечай кратко. Мало данных — скажи прямо. Тревожный паттерн — обрати внимание.
- Ты НЕ врач. Без диагнозов, без назначений. Вне компетенции → к специалисту.

Цели по питанию:
- Когда пользователь называет цель по калориям или белку — ОБЯЗАТЕЛЬНО сохрани через update_user_profile(category="goals", key="daily_calories", value="2500") и/или update_user_profile(category="goals", key="daily_protein_g", value="150"). Диапазон тоже допустим: value="110-150".
- Используй эти цели при оценке питания: показывай не просто итого, а процент от цели.

Память:
- Когда пользователь сообщает устойчивый факт о себе (цель, вес, аллергия, предпочтения, режим) — сохрани через update_user_profile.
- Когда замечаешь паттерн в данных (кофе ухудшает сон, тренировки по утрам эффективнее) — сохрани через save_derived_rule с обоснованием.
- Не сохраняй одноразовые факты (сегодня голова болит) в профиль — это для save_note.
- Перед рекомендацией вызови get_current_state чтобы видеть агрегаты.

Рекомендации (СТРОГО):
- Если пользователь просит СОВЕТ, РЕКОМЕНДАЦИЮ, ОЦЕНКУ ДНЯ или спрашивает «как тренироваться», «что есть», «что посоветуешь», «как спланировать день», «промежуточные итоги» — ты ОБЯЗАН сначала вызвать get_daily_recommendation_context. БЕЗ этого вызова НЕ ДАВАЙ рекомендаций.
- ВАЖНО: передавай в get_daily_recommendation_context правильную дату. Если пользователь спрашивает про сегодня — не передавай target_date (по умолчанию сегодня). Если про вчера — передай вчерашнюю дату. Если про конкретный день — передай эту дату.
- Для недельного обзора — вызови get_week_summary. Можно сравнить с прошлой неделей (weeks_ago=1).
- Если в контексте есть ОГРАНИЧЕНИЯ (⚠️) — следуй им строго, они основаны на данных.
- Если пользователь спрашивает «почему?» после рекомендации — покажи конкретные цифры из данных.
- Мало данных → скажи прямо, что рекомендация приблизительная.
- Для простых действий (запись еды, сна, тренировки, ответ на вопрос о записях) — вызывать get_daily_recommendation_context НЕ нужно.

Каталог доставки:
- Пользователь заказывает еду из сервиса доставки. Меню загружено в каталог с точными калориями и БЖУ.
- Когда пользователь спрашивает про еду на ЛЮБУЮ дату (сегодня, завтра, через N дней, «что у меня по еде») — СНАЧАЛА вызови search_meal_catalog на эту дату. Каталог содержит запланированное меню из доставки.
- Когда пользователь пишет название блюда (например «съел сырники», «ежики», «фриттата») — вызови search_meal_catalog с query=название, чтобы найти точные данные.
- Когда пользователь говорит «съел обед/завтрак/ужин» без деталей — вызови search_meal_catalog на сегодня.
- Если блюдо найдено в каталоге — используй ТОЧНЫЕ калории и БЖУ из каталога при записи через save_meal_log. Не оценивай сам.
- Если каталог пуст или блюдо не найдено — оцени сам как обычно и предупреди что это приблизительно.
- get_recent_logs показывает что УЖЕ записано. search_meal_catalog показывает что ЗАПЛАНИРОВАНО из доставки. Не путай.

WHOOP:
- У пользователя может быть подключён WHOOP — браслет, который отслеживает сон (стадии), recovery (HRV, пульс покоя, SpO2), тренировки (strain, HR).
- Когда пользователь спрашивает о recovery, HRV, strain, пульсе покоя, данных с браслета — вызови get_latest_whoop_metrics.
- Данные WHOOP = объективные метрики. Используй их вместе с субъективными данными (заметки, самочувствие) для более точных рекомендаций.
- Recovery < 33% = красная зона (лёгкая нагрузка или отдых). 34-66% = жёлтая (умеренная). 67%+ = зелёная (можно нагружать).
- Strain: <8 = лёгкий день, 8-14 = средняя нагрузка, 14+ = тяжёлый день.
- Если пользователь просит синхронизировать WHOOP — вызови sync_whoop_now.
- Для проверки подключения — get_whoop_status.
"""

health_agent = Agent(
    name="Health Agent",
    instructions=BASE_SYSTEM_PROMPT,
    model=settings.openai_model,
    tools=[
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
    ],
)


def _trim_history(history: list, max_items: int = MAX_HISTORY_ITEMS) -> None:
    """Оставляет только последние max_items пользовательских сообщений и ответов."""
    # Считаем user messages с конца, удаляем лишнее с начала
    user_count = sum(1 for item in history if isinstance(item, dict) and item.get("role") == "user")
    while user_count > max_items and history:
        removed = history.pop(0)
        if isinstance(removed, dict) and removed.get("role") == "user":
            user_count -= 1


async def run_agent(
    user_message: str,
    user_id: uuid.UUID | None = None,
    image_url: str | None = None,
    trigger: str = "telegram",
) -> str:
    """Запускает агента и возвращает текстовый ответ."""
    start_time = time.monotonic()

    if user_id:
        set_user_id(user_id)

    # Selective memory injection — подгружаем контекст из БД
    dynamic_context = ""
    if user_id:
        dynamic_context = await build_user_context(user_id)

    instructions = BASE_SYSTEM_PROMPT
    if dynamic_context:
        instructions += f"\n\n--- Контекст пользователя ---\n{dynamic_context}\n---"

    # Выбор модели через роутер
    model_used = choose_model(user_message, has_image=bool(image_url))

    # Создаём агента с динамическим промптом
    agent = health_agent.clone(instructions=instructions, model=model_used)

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

    # Запускаем агента с полной историей
    error_text = None
    result = None
    try:
        result = await Runner.run(agent, history)
    except Exception as e:
        error_text = f"{type(e).__name__}: {e}"
        logger.exception("Agent run failed")

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

    if user_id:
        try:
            async with async_session() as session:
                agent_run = AgentRun(
                    user_id=user_id,
                    trigger=trigger,
                    input_text=user_message,
                    output_text=output_text,
                    model=model_used,
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

    # Если агент упал — возвращаем дружелюбное сообщение
    if error_text:
        return "Произошла ошибка при обработке запроса. Попробуй ещё раз через пару секунд."

    # Сохраняем ответ агента в историю
    new_items = result.to_input_list()
    _conversation_history[history_key] = new_items
    _trim_history(_conversation_history[history_key])

    return result.final_output
