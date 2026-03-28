import asyncio
import base64
import logging
import re

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from app.agent.agent import run_agent
from app.config import settings
from app.telegram.user_service import get_or_create_user

logger = logging.getLogger(__name__)


def _md_to_html(text: str) -> str:
    """Конвертирует базовый Markdown в Telegram HTML."""
    # Экранируем HTML-спецсимволы (кроме тех, что мы сами создадим)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # **bold** → <b>bold</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # *italic* → <i>italic</i>
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    # _italic_ → <i>italic</i> (но не внутри слов)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", text)
    # `code` → <code>code</code>
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # ### Header → <b>Header</b>
    text = re.sub(r"^#{1,3}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    return text


async def _send_typing_while(chat_id: int, bot, task: asyncio.Task) -> None:
    """Отправляет 'typing...' каждые 4 секунды, пока task не завершится."""
    while not task.done():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        await asyncio.sleep(4)


async def _ensure_allowed_user(update: Update):
    """Проверяет доступ и возвращает user или None."""
    telegram_user = update.effective_user
    if telegram_user.id not in settings.allowed_user_ids_set:
        return None

    return await get_or_create_user(
        telegram_user_id=telegram_user.id,
        chat_id=update.effective_chat.id,
        username=telegram_user.username,
        display_name=telegram_user.full_name,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик входящих текстовых сообщений."""
    if not update.message or not update.message.text:
        return

    user = await _ensure_allowed_user(update)
    if not user:
        return

    user_text = update.message.text
    logger.info("Message from %s: %s", update.effective_user.id, user_text[:100])

    agent_task = asyncio.create_task(run_agent(user_text, user_id=user.id))
    typing_task = asyncio.create_task(
        _send_typing_while(update.effective_chat.id, context.bot, agent_task)
    )

    try:
        response = await agent_task
    except Exception as e:
        logger.exception("Agent error for user %s", update.effective_user.id)
        error_name = type(e).__name__
        error_msg = str(e)[:200]
        response = f"Ошибка: {error_name}\n{error_msg}"
    finally:
        typing_task.cancel()

    try:
        await update.message.reply_text(_md_to_html(response), parse_mode=ParseMode.HTML)
    except Exception:
        # Fallback без форматирования если HTML невалидный
        await update.message.reply_text(response)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик входящих фотографий."""
    if not update.message or not update.message.photo:
        return

    user = await _ensure_allowed_user(update)
    if not user:
        return

    caption = update.message.caption or ""
    logger.info("Photo from %s, caption: %s", update.effective_user.id, caption[:100])

    # Берём фото в наилучшем качестве (последний элемент — максимальный размер)
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    # Скачиваем в память и кодируем в base64
    photo_bytes = await file.download_as_bytearray()
    b64 = base64.b64encode(photo_bytes).decode("utf-8")
    image_url = f"data:image/jpeg;base64,{b64}"

    agent_task = asyncio.create_task(run_agent(caption, user_id=user.id, image_url=image_url))
    typing_task = asyncio.create_task(
        _send_typing_while(update.effective_chat.id, context.bot, agent_task)
    )

    try:
        response = await agent_task
    except Exception as e:
        logger.exception("Agent error (photo) for user %s", update.effective_user.id)
        error_name = type(e).__name__
        error_msg = str(e)[:200]
        response = f"Ошибка (фото): {error_name}\n{error_msg}"
    finally:
        typing_task.cancel()

    try:
        await update.message.reply_text(_md_to_html(response), parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(response)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start — онбординг или приветствие."""
    user = await _ensure_allowed_user(update)
    if not user:
        return

    from app.agent.context import get_missing_profile_fields
    from app.agent.agent import _conversation_history

    missing = await get_missing_profile_fields(user.id)

    if not missing:
        # Профиль уже заполнен — короткое приветствие
        await update.message.reply_text(
            "С возвращением! Просто напиши — я на связи.\n\n"
            "Команды:\n"
            "/help — что я умею\n"
            "/whoop — подключить или синхронизировать WHOOP\n"
            "/costs — расходы на API"
        )
        return

    # Новый пользователь — системная плашка + запуск онбординга через агента
    await update.message.reply_text(
        "Привет! Я — персональный ассистент по здоровью.\n\n"
        "Что я умею:\n"
        "- Записывать питание, сон и тренировки (текстом или фото еды)\n"
        "- Давать рекомендации по питанию, нагрузке и восстановлению\n"
        "- Показывать аналитику: дневные итоги, недельные обзоры, тренды\n"
        "- Синхронизировать данные с WHOOP\n\n"
        "Каждый вечер пришлю итог дня с прогрессом по твоим целям."
    )

    # Очищаем историю для чистого онбординга
    history_key = str(user.id)
    _conversation_history.pop(history_key, None)

    # Запускаем агента с онбординг-запросом
    onboarding_prompt = (
        "Пользователь только что запустил бота. Расскажи ему в свободной форме, "
        "какие данные тебе нужны для персонализации, и попроси написать о себе. "
        "Предложи типичные цели: набор массы, похудение, поддержание формы, рекомпозиция. "
        "Попроси указать возраст, рост, вес и цели по калориям/белку. "
        "Будь дружелюбным и кратким."
    )

    agent_task = asyncio.create_task(run_agent(onboarding_prompt, user_id=user.id, trigger="onboarding"))
    typing_task = asyncio.create_task(
        _send_typing_while(update.effective_chat.id, context.bot, agent_task)
    )

    try:
        response = await agent_task
    except Exception as e:
        logger.exception("Onboarding agent error for user %s", update.effective_user.id)
        response = "Расскажи о себе в свободной форме: цель, возраст, рост, вес, цели по калориям и белку."
    finally:
        typing_task.cancel()

    try:
        await update.message.reply_text(_md_to_html(response), parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(response)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    user = await _ensure_allowed_user(update)
    if not user:
        return

    await update.message.reply_text(
        "Что я понимаю:\n\n"
        "Еда — «съел омлет с сыром», «обед: паста 400г», или отправь фото еды\n"
        "Сон — «лёг в 23:30, встал в 7:00», «спал 7 часов»\n"
        "Тренировка — «силовая 60 мин», «побегал 5 км»\n"
        "Заметка — «болит голова», «энергия 7/10»\n\n"
        "Можно спрашивать:\n"
        "— «что я ел сегодня?»\n"
        "— «итог дня» / «итог недели»\n"
        "— «что посоветуешь на ужин?»\n"
        "— «как спланировать тренировку?»\n"
        "— «удали последний приём пищи»\n\n"
        "Команды:\n"
        "/whoop — подключить или синхронизировать WHOOP\n"
        "/costs — расходы на API\n"
        "/help — эта справка"
    )


async def handle_costs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /costs — статистика расходов на API."""
    user = await _ensure_allowed_user(update)
    if not user:
        return

    from sqlalchemy import select, func, case
    from app.database import async_session
    from app.models.agent import AgentRun

    # Цены за 1M токенов (USD) — обновлять при смене модели
    PRICES = {
        "gpt-5.4":      {"input": 2.00, "output": 8.00},
        "gpt-5.4-mini": {"input": 0.30, "output": 1.20},
    }
    DEFAULT_PRICE = {"input": 2.00, "output": 8.00}

    async with async_session() as session:
        stmt = (
            select(
                AgentRun.model,
                func.sum(AgentRun.tokens_input).label("input"),
                func.sum(AgentRun.tokens_output).label("output"),
                func.count(AgentRun.id).label("runs"),
            )
            .where(AgentRun.user_id == user.id)
            .group_by(AgentRun.model)
        )
        rows = (await session.execute(stmt)).all()

    if not rows or all(r.input is None for r in rows):
        await update.message.reply_text("Пока нет данных о расходах.")
        return

    lines = ["Расходы на OpenAI API:\n"]
    total_cost = 0.0
    total_input = 0
    total_output = 0

    for row in rows:
        if not row.input and not row.output:
            continue
        inp = row.input or 0
        out = row.output or 0
        prices = PRICES.get(row.model, DEFAULT_PRICE)
        cost = (inp / 1_000_000) * prices["input"] + (out / 1_000_000) * prices["output"]
        total_cost += cost
        total_input += inp
        total_output += out
        lines.append(
            f"{row.model}: {row.runs} запросов, "
            f"{inp:,} in / {out:,} out — ${cost:.4f}"
        )

    lines.append(f"\nИтого: {total_input:,} in / {total_output:,} out — ${total_cost:.4f}")

    await update.message.reply_text("\n".join(lines))


async def handle_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /whoop — подключение и синхронизация WHOOP."""
    user = await _ensure_allowed_user(update)
    if not user:
        return

    from sqlalchemy import select
    from app.database import async_session  # lazy import — avoid circular dependency with bot.py
    from app.models.whoop import WhoopConnection

    async with async_session() as session:
        conn = (await session.execute(
            select(WhoopConnection).where(
                WhoopConnection.user_id == user.id,
                WhoopConnection.is_active.is_(True),
            )
        )).scalar_one_or_none()

    if conn:
        # Уже подключён — предлагаем синхронизировать
        await update.message.reply_text(
            "WHOOP подключён ✅\n\n"
            "Синхронизирую данные за последние 7 дней..."
        )
        try:
            from app.whoop.sync import sync_whoop_data
            result = await sync_whoop_data(user.id, days=7)
            await update.message.reply_text(result)
        except Exception as e:
            logger.exception("WHOOP sync error")
            await update.message.reply_text(f"Ошибка синхронизации: {e}")
    else:
        # Не подключён — даём ссылку на авторизацию
        from app.whoop.oauth import get_authorization_url
        auth_url = get_authorization_url()
        await update.message.reply_text(
            "WHOOP не подключён.\n\n"
            f"Для подключения перейди по ссылке:\n{auth_url}\n\n"
            "После авторизации данные синхронизируются автоматически."
        )
