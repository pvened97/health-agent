import logging

from sqlalchemy import select

from app.database import async_session
from app.models.user import User, TelegramAccount

logger = logging.getLogger(__name__)


async def get_or_create_user(
    telegram_user_id: int,
    chat_id: int,
    username: str | None = None,
    display_name: str | None = None,
) -> User:
    """Находит или создаёт пользователя по Telegram ID."""
    async with async_session() as session:
        stmt = select(TelegramAccount).where(TelegramAccount.telegram_user_id == telegram_user_id)
        result = await session.execute(stmt)
        tg_account = result.scalar_one_or_none()

        if tg_account:
            # Update chat_id and username if changed
            tg_account.chat_id = chat_id
            tg_account.telegram_username = username
            await session.commit()

            user_stmt = select(User).where(User.id == tg_account.user_id)
            user_result = await session.execute(user_stmt)
            return user_result.scalar_one()

        # Create new user + telegram account
        user = User(display_name=display_name)
        session.add(user)
        await session.flush()

        tg_account = TelegramAccount(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            telegram_username=username,
            chat_id=chat_id,
        )
        session.add(tg_account)
        await session.commit()

        logger.info("Created new user %s for telegram_user_id %s", user.id, telegram_user_id)
        return user
