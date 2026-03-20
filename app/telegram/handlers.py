import asyncio
import base64
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from app.agent.agent import run_agent
from app.config import settings
from app.telegram.user_service import get_or_create_user

logger = logging.getLogger(__name__)


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
    if telegram_user.id != settings.allowed_telegram_user_id:
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

    await update.message.reply_text(response)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    if update.effective_user.id != settings.allowed_telegram_user_id:
        return

    await update.message.reply_text(
        "Привет! Я твой персональный ассистент по здоровью.\n\n"
        "Могу помочь с питанием, тренировками, сном и восстановлением.\n"
        "Просто напиши мне или отправь фото еды — и я запишу.\n\n"
        "Команды:\n"
        "/whoop — подключить или синхронизировать WHOOP"
    )


async def handle_whoop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /whoop — подключение и синхронизация WHOOP."""
    if update.effective_user.id != settings.allowed_telegram_user_id:
        return

    user = await _ensure_allowed_user(update)
    if not user:
        return

    from sqlalchemy import select
    from app.database import async_session
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
