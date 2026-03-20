from agents import function_tool

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.memory import UserProfile

from sqlalchemy import select


@function_tool
async def get_user_profile() -> str:
    """Получает все сохранённые факты из профиля пользователя: цель, ограничения, предпочтения, антропометрию и другие устойчивые данные."""
    user_id = get_user_id()
    async with async_session() as session:
        stmt = (
            select(UserProfile)
            .where(UserProfile.user_id == user_id, UserProfile.deleted_at.is_(None))
            .order_by(UserProfile.category, UserProfile.key)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return "Профиль пуст. Пользователь ещё не сообщал информации о себе."

    lines = []
    current_category = None
    for row in rows:
        if row.category != current_category:
            current_category = row.category
            lines.append(f"\n[{current_category}]")
        confirmed = "" if row.confirmed else " (не подтверждено)"
        lines.append(f"  {row.key}: {row.value}{confirmed}")

    return "\n".join(lines)
