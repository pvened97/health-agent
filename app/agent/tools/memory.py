from datetime import datetime
from typing import Optional

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.memory import UserProfile, DerivedRule, MemoryNote


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


@function_tool
async def save_memory(
    content: str,
    category: str = "general",
) -> str:
    """Сохраняет заметку в долгосрочную память. Используй когда пользователь просит запомнить что-то:
    предпочтения, ограничения, привычки, аллергии, любимые блюда, или когда ты сам замечаешь важное.

    Примеры: «запомни, что я не ем глютен», «я обычно тренируюсь по утрам», «у меня аллергия на арахис».

    Args:
        content: Что запомнить (краткая формулировка факта)
        category: Категория: food, training, health, sleep, general
    """
    user_id = get_user_id()

    # Проверяем дубликат
    async with async_session() as session:
        existing = (await session.execute(
            select(MemoryNote).where(
                MemoryNote.user_id == user_id,
                MemoryNote.content == content,
                MemoryNote.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

        if existing:
            existing.occurrences += 1
            await session.commit()
            return f"Это уже запомнено (упомянуто {existing.occurrences} раз): {content}"

        note = MemoryNote(
            user_id=user_id,
            content=content,
            category=category,
            source="user_manual",
            status="active",
        )
        session.add(note)
        await session.commit()

    return f"Запомнил: {content}"


@function_tool
async def get_memories() -> str:
    """Показывает все сохранённые заметки из долгосрочной памяти.
    Вызывай когда пользователь спрашивает «что ты запомнил?», «что ты знаешь обо мне?»."""
    user_id = get_user_id()

    async with async_session() as session:
        notes = (await session.execute(
            select(MemoryNote).where(
                MemoryNote.user_id == user_id,
                MemoryNote.deleted_at.is_(None),
            ).order_by(MemoryNote.category, MemoryNote.created_at)
        )).scalars().all()

    if not notes:
        return "Долгосрочная память пуста. Скажи «запомни, что ...» чтобы я начал запоминать."

    lines = ["Моя память о тебе:"]
    current_cat = None
    for note in notes:
        if note.category != current_cat:
            current_cat = note.category
            lines.append(f"\n[{current_cat}]")
        times = f" (x{note.occurrences})" if note.occurrences > 1 else ""
        lines.append(f"  • {note.content}{times}")

    return "\n".join(lines)


@function_tool
async def delete_memory(content_fragment: str) -> str:
    """Удаляет заметку из долгосрочной памяти. Используй когда пользователь говорит «забудь, что ...» или «удали из памяти».

    Args:
        content_fragment: Часть текста заметки для поиска
    """
    user_id = get_user_id()

    async with async_session() as session:
        notes = (await session.execute(
            select(MemoryNote).where(
                MemoryNote.user_id == user_id,
                MemoryNote.content.ilike(f"%{content_fragment}%"),
                MemoryNote.deleted_at.is_(None),
            )
        )).scalars().all()

        if not notes:
            return f"Не нашёл заметку с «{content_fragment}» в памяти."

        for note in notes:
            note.deleted_at = datetime.now()
        await session.commit()

    if len(notes) == 1:
        return f"Забыл: {notes[0].content}"
    return f"Удалено {len(notes)} заметок по запросу «{content_fragment}»."
