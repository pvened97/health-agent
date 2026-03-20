"""Скрипт для загрузки меню доставки в meal_catalog.

Использование:
    python scripts/load_menu.py

Данные передаются как JSON — Claude Code парсит скриншоты и вызывает этот скрипт.
"""

import asyncio
import json
import sys
import uuid
from datetime import date

from app.database import async_session
from app.models.user import User  # noqa: F401
from app.models.catalog import MealCatalog
from sqlalchemy import select


async def load_menu(user_id: str, meals: list[dict]) -> None:
    """Загружает блюда в meal_catalog.

    meals: [
        {
            "date": "2026-03-17",
            "meal_number": 1,
            "name": "Каша рисовая с тыквенным чатни",
            "calories": 353,
            "protein_g": 9,
            "fat_g": 11,
            "carbs_g": 55,
            "source": "level_kitchen"
        },
        ...
    ]
    """
    uid = uuid.UUID(user_id)

    async with async_session() as session:
        # Собираем даты для очистки старых записей
        dates = set(date.fromisoformat(m["date"]) for m in meals)

        for d in dates:
            # Удаляем старые записи на эту дату (перезаписываем)
            stmt = (
                select(MealCatalog)
                .where(
                    MealCatalog.user_id == uid,
                    MealCatalog.date == d,
                    MealCatalog.deleted_at.is_(None),
                )
            )
            existing = (await session.execute(stmt)).scalars().all()
            for row in existing:
                await session.delete(row)

        # Вставляем новые
        for m in meals:
            entry = MealCatalog(
                user_id=uid,
                date=date.fromisoformat(m["date"]),
                meal_number=m["meal_number"],
                name=m["name"],
                calories=m.get("calories"),
                protein_g=m.get("protein_g"),
                fat_g=m.get("fat_g"),
                carbs_g=m.get("carbs_g"),
                source=m.get("source", "delivery_menu"),
            )
            session.add(entry)

        await session.commit()

    print(f"Загружено {len(meals)} блюд на {len(dates)} дней.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/load_menu.py <user_id> '<json_meals>'")
        sys.exit(1)

    user_id = sys.argv[1]
    meals = json.loads(sys.argv[2])
    asyncio.run(load_menu(user_id, meals))
