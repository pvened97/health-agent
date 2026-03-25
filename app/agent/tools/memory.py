from datetime import datetime
from typing import Optional

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.memory import UserProfile, DerivedRule


@function_tool
async def update_user_profile(
    category: str,
    key: str,
    value: str,
) -> str:
    """Сохраняет или обновляет факт в профиле пользователя. Используй для устойчивых данных: цель, вес, возраст, ограничения, предпочтения.

    Args:
        category: Категория факта: goals, restrictions, preferences, anthropometry, lifestyle
        key: Название факта (например: weight_kg, primary_goal, allergy, wake_time)
        value: Значение факта
    """
    user_id = get_user_id()

    async with async_session() as session:
        # Ищем существующий факт
        stmt = select(UserProfile).where(
            UserProfile.user_id == user_id,
            UserProfile.category == category,
            UserProfile.key == key,
            UserProfile.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            old_value = existing.value
            existing.value = value
            existing.confirmed = True
            await session.commit()
            return f"Профиль обновлён: {key} изменён с '{old_value}' на '{value}'."
        else:
            profile_entry = UserProfile(
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                confirmed=True,
            )
            session.add(profile_entry)
            await session.commit()
            return f"Профиль обновлён: {key} = '{value}' добавлен в [{category}]."


@function_tool
async def delete_memory_item(
    item_type: str,
    key: str,
) -> str:
    """Удаляет факт из профиля или наблюдение из памяти.

    Args:
        item_type: Тип записи: profile или derived_rule
        key: Для profile — ключ факта (например: allergy). Для derived_rule — текст правила или его начало.
    """
    user_id = get_user_id()

    async with async_session() as session:
        if item_type == "profile":
            stmt = select(UserProfile).where(
                UserProfile.user_id == user_id,
                UserProfile.key == key,
                UserProfile.deleted_at.is_(None),
            )
            result = await session.execute(stmt)
            item = result.scalar_one_or_none()
            if not item:
                return f"Факт '{key}' не найден в профиле."
            item.deleted_at = datetime.now()
            await session.commit()
            return f"Факт '{key}' удалён из профиля."

        elif item_type == "derived_rule":
            stmt = select(DerivedRule).where(
                DerivedRule.user_id == user_id,
                DerivedRule.rule.ilike(f"%{key}%"),
                DerivedRule.deleted_at.is_(None),
            )
            result = await session.execute(stmt)
            item = result.scalar_one_or_none()
            if not item:
                return f"Наблюдение с '{key}' не найдено."
            item.deleted_at = datetime.now()
            await session.commit()
            return f"Наблюдение удалено."

    return f"Неизвестный тип: {item_type}. Используй profile или derived_rule."


@function_tool
async def save_derived_rule(
    rule: str,
    evidence: str,
    confidence: float = 0.5,
) -> str:
    """Сохраняет наблюдение или гипотезу, выведенную из данных пользователя. Используй когда замечаешь паттерн (например: поздний кофе ухудшает сон).

    Args:
        rule: Формулировка наблюдения
        evidence: На чём основано (какие данные привели к выводу)
        confidence: Уверенность от 0.0 до 1.0 (по умолчанию 0.5)
    """
    user_id = get_user_id()

    derived = DerivedRule(
        user_id=user_id,
        rule=rule,
        evidence=evidence,
        confidence=confidence,
    )

    async with async_session() as session:
        session.add(derived)
        await session.commit()

    return f"Наблюдение сохранено (confidence: {confidence}): {rule}"
