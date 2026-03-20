import logging

from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters

from app.config import settings
from app.telegram.handlers import handle_message, handle_photo, handle_start, handle_whoop

logger = logging.getLogger(__name__)


def create_bot_app():
    """Создаёт Telegram Application для polling mode."""
    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("whoop", handle_whoop))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    return app


async def start_polling():
    """Запускает бота в polling mode (для dev)."""
    app = create_bot_app()
    logger.info("Starting Telegram bot in polling mode...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    return app


async def stop_polling(app):
    """Останавливает polling."""
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
