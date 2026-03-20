from datetime import date, timedelta

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.catalog import MealCatalog


@function_tool
async def search_meal_catalog(meal_date: str | None = None, query: str | None = None) -> str:
    """Ищет блюда в каталоге доставки (Level Kitchen / Grow Food и др.).

    Используй ПЕРЕД записью еды, если пользователь упоминает доставку или блюдо похоже на позицию из каталога.
    Если meal_date не указан — ищет на сегодня.

    Args:
        meal_date: Дата в формате YYYY-MM-DD (по умолчанию сегодня)
        query: Поиск по названию блюда (необязательно)
    """
    user_id = get_user_id()
    target_date = date.fromisoformat(meal_date) if meal_date else date.today()

    async with async_session() as session:
        stmt = (
            select(MealCatalog)
            .where(
                MealCatalog.user_id == user_id,
                MealCatalog.date == target_date,
                MealCatalog.deleted_at.is_(None),
            )
            .order_by(MealCatalog.meal_number)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return f"Каталог на {target_date}: пусто. Блюда из доставки не загружены на эту дату."

    # Фильтр по названию если есть query
    if query:
        query_lower = query.lower()
        rows = [r for r in rows if query_lower in r.name.lower()]
        if not rows:
            return f"Блюдо «{query}» не найдено в каталоге на {target_date}."

    lines = [f"Каталог на {target_date} ({len(rows)} блюд):"]
    for r in rows:
        lines.append(
            f"  #{r.meal_number}. {r.name} — {r.calories} ккал, "
            f"{r.protein_g}Б / {r.fat_g}Ж / {r.carbs_g}У"
        )

    total_cal = sum(r.calories for r in rows if r.calories)
    lines.append(f"\nИтого: {total_cal} ккал")

    return "\n".join(lines)
