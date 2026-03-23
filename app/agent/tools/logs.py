from datetime import date, time, datetime
from typing import Optional

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.logs import SleepLog, MealLog, WorkoutLog, DailyNote


@function_tool
async def save_sleep_log(
    sleep_date: str,
    duration_minutes: Optional[int] = None,
    bed_time: Optional[str] = None,
    wake_time: Optional[str] = None,
    quality: Optional[str] = None,
    comment: Optional[str] = None,
) -> str:
    """Сохраняет запись о сне пользователя.

    Args:
        sleep_date: Дата сна в формате YYYY-MM-DD
        duration_minutes: Длительность сна в минутах
        bed_time: Время отхода ко сну в формате HH:MM
        wake_time: Время пробуждения в формате HH:MM
        quality: Качество сна: good, fair, poor
        comment: Комментарий пользователя
    """
    user_id = get_user_id()
    log = SleepLog(
        user_id=user_id,
        date=date.fromisoformat(sleep_date),
        duration_minutes=duration_minutes,
        quality=quality,
        comment=comment,
    )
    if bed_time:
        log.bed_time = datetime.fromisoformat(f"{sleep_date}T{bed_time}")
    if wake_time:
        log.wake_time = datetime.fromisoformat(f"{sleep_date}T{wake_time}")

    async with async_session() as session:
        session.add(log)
        await session.commit()

    parts = [f"Сон за {sleep_date} записан."]
    if duration_minutes:
        parts.append(f"Длительность: {duration_minutes} мин.")
    if quality:
        parts.append(f"Качество: {quality}.")
    return " ".join(parts)


@function_tool
async def save_meal_log(
    meal_date: str,
    description: str,
    meal_type: Optional[str] = None,
    meal_time: Optional[str] = None,
    calories: Optional[int] = None,
    protein_g: Optional[float] = None,
    carbs_g: Optional[float] = None,
    fat_g: Optional[float] = None,
    comment: Optional[str] = None,
) -> str:
    """Сохраняет запись о приёме пищи.

    Args:
        meal_date: Дата в формате YYYY-MM-DD
        description: Что съел пользователь
        meal_type: Тип приёма пищи: breakfast, lunch, dinner, snack
        meal_time: Время приёма пищи в формате HH:MM
        calories: Примерная калорийность
        protein_g: Белок в граммах
        carbs_g: Углеводы в граммах
        fat_g: Жиры в граммах
        comment: Комментарий
    """
    user_id = get_user_id()

    # Если время не указано — подставляем типичное по типу приёма пищи
    default_times = {
        "breakfast": "08:30",
        "lunch": "13:00",
        "dinner": "19:00",
        "snack": "15:30",
    }
    if not meal_time and meal_type:
        meal_time = default_times.get(meal_type)

    log = MealLog(
        user_id=user_id,
        date=date.fromisoformat(meal_date),
        time=time.fromisoformat(meal_time) if meal_time else None,
        meal_type=meal_type,
        description=description,
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        comment=comment,
    )

    async with async_session() as session:
        session.add(log)
        await session.commit()

    parts = [f"Приём пищи записан: {description}."]
    if calories:
        parts.append(f"~{calories} ккал.")
    if protein_g:
        parts.append(f"Белок: {protein_g}г.")
    return " ".join(parts)


@function_tool
async def save_workout_log(
    workout_date: str,
    workout_type: str,
    duration_minutes: Optional[int] = None,
    intensity: Optional[str] = None,
    description: Optional[str] = None,
    comment: Optional[str] = None,
) -> str:
    """Сохраняет запись о тренировке.

    Args:
        workout_date: Дата в формате YYYY-MM-DD
        workout_type: Тип: strength, cardio, flexibility, sport, mixed, other
        duration_minutes: Длительность в минутах
        intensity: Интенсивность: low, moderate, high, max
        description: Описание тренировки (упражнения, группы мышц)
        comment: Комментарий
    """
    user_id = get_user_id()
    log = WorkoutLog(
        user_id=user_id,
        date=date.fromisoformat(workout_date),
        duration_minutes=duration_minutes,
        workout_type=workout_type,
        intensity=intensity,
        description=description,
        comment=comment,
    )

    async with async_session() as session:
        session.add(log)
        await session.commit()

    parts = [f"Тренировка записана: {workout_type}."]
    if duration_minutes:
        parts.append(f"{duration_minutes} мин.")
    if intensity:
        parts.append(f"Интенсивность: {intensity}.")
    return " ".join(parts)


@function_tool
async def save_note(
    note_date: str,
    text: str,
    mood: Optional[str] = None,
    energy_level: Optional[int] = None,
    stress_level: Optional[int] = None,
) -> str:
    """Сохраняет заметку о самочувствии, настроении или свободный комментарий.

    Args:
        note_date: Дата в формате YYYY-MM-DD
        text: Текст заметки
        mood: Настроение: great, good, ok, bad, terrible
        energy_level: Уровень энергии от 1 до 10
        stress_level: Уровень стресса от 1 до 10
    """
    user_id = get_user_id()
    note = DailyNote(
        user_id=user_id,
        date=date.fromisoformat(note_date),
        text=text,
        mood=mood,
        energy_level=energy_level,
        stress_level=stress_level,
    )

    async with async_session() as session:
        session.add(note)
        await session.commit()

    return f"Заметка за {note_date} сохранена."


@function_tool
async def get_recent_logs(
    log_type: str,
    days: int = 7,
    specific_date: Optional[str] = None,
) -> str:
    """Получает записи из журнала по типу. Можно запросить за конкретную дату или за последние N дней.

    Args:
        log_type: Тип записей: sleep, meal, workout, note
        days: За сколько последних дней (по умолчанию 7). Игнорируется если указан specific_date.
        specific_date: Конкретная дата в формате YYYY-MM-DD. Если указана — возвращает записи только за этот день.
    """
    from datetime import timedelta

    user_id = get_user_id()

    model_map = {
        "sleep": SleepLog,
        "meal": MealLog,
        "workout": WorkoutLog,
        "note": DailyNote,
    }

    model = model_map.get(log_type)
    if not model:
        return f"Неизвестный тип записей: {log_type}. Доступные: sleep, meal, workout, note."

    if specific_date:
        target_date = date.fromisoformat(specific_date)
        date_filter = model.date == target_date
        period_desc = f"за {specific_date}"
    else:
        since = today_msk() - timedelta(days=days)
        date_filter = model.date >= since
        period_desc = f"за последние {days} дней"

    async with async_session() as session:
        stmt = (
            select(model)
            .where(
                model.user_id == user_id,
                date_filter,
                model.deleted_at.is_(None),
            )
            .order_by(model.date.desc(), model.created_at.desc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        return f"Нет записей типа '{log_type}' {period_desc}."

    lines = []
    for row in rows:
        if log_type == "sleep":
            dur = f", {row.duration_minutes} мин" if row.duration_minutes else ""
            qual = f", качество: {row.quality}" if row.quality else ""
            lines.append(f"  {row.date}: сон{dur}{qual}")
        elif log_type == "meal":
            cal = f", ~{row.calories} ккал" if row.calories else ""
            prot = f", белок {row.protein_g}г" if row.protein_g else ""
            lines.append(f"  {row.date} {row.meal_type or ''}: {row.description}{cal}{prot}")
        elif log_type == "workout":
            dur = f", {row.duration_minutes} мин" if row.duration_minutes else ""
            inten = f", {row.intensity}" if row.intensity else ""
            lines.append(f"  {row.date}: {row.workout_type}{dur}{inten}")
        elif log_type == "note":
            mood = f", настроение: {row.mood}" if row.mood else ""
            lines.append(f"  {row.date}: {row.text}{mood}")

    header = f"Записи '{log_type}' {period_desc} ({len(rows)} шт.):"
    return header + "\n" + "\n".join(lines)
