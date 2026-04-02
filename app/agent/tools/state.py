from datetime import date, timedelta

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select, func

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.logs import SleepLog, MealLog, WorkoutLog, DailyNote
from app.models.memory import DerivedRule


@function_tool
async def get_current_state(days: int = 7) -> str:
    """Получает агрегированное текущее состояние пользователя за последние N дней: средний сон, калории, тренировки, настроение. Вызывай перед рекомендациями.

    Args:
        days: За сколько последних дней агрегировать (по умолчанию 7)
    """
    user_id = get_user_id()
    since = today_msk() - timedelta(days=days)
    parts = [f"Агрегаты за последние {days} дней:"]

    async with async_session() as session:
        # --- Сон ---
        sleep_stmt = select(
            func.count(SleepLog.id),
            func.avg(SleepLog.duration_minutes),
            func.min(SleepLog.duration_minutes),
            func.max(SleepLog.duration_minutes),
        ).where(
            SleepLog.user_id == user_id,
            SleepLog.date >= since,
            SleepLog.deleted_at.is_(None),
        )
        sleep_row = (await session.execute(sleep_stmt)).one()
        sleep_count, sleep_avg, sleep_min, sleep_max = sleep_row

        if sleep_count:
            parts.append(
                f"\nСон ({sleep_count} записей):"
                f"\n  Средняя длительность: {int(sleep_avg)} мин ({sleep_avg / 60:.1f} ч)"
                f"\n  Мин: {sleep_min} мин, Макс: {sleep_max} мин"
            )
        else:
            parts.append("\nСон: нет записей.")

        # --- Питание ---
        meal_stmt = select(
            func.count(MealLog.id),
            func.sum(MealLog.calories),
            func.sum(MealLog.protein_g),
            func.sum(MealLog.carbs_g),
            func.sum(MealLog.fat_g),
        ).where(
            MealLog.user_id == user_id,
            MealLog.date >= since,
            MealLog.deleted_at.is_(None),
        )
        meal_row = (await session.execute(meal_stmt)).one()
        meal_count, total_cal, total_prot, total_carbs, total_fat = meal_row

        if meal_count:
            # Количество уникальных дней с записями
            days_stmt = select(func.count(func.distinct(MealLog.date))).where(
                MealLog.user_id == user_id,
                MealLog.date >= since,
                MealLog.deleted_at.is_(None),
            )
            meal_days = (await session.execute(days_stmt)).scalar() or 1

            avg_cal = int(total_cal / meal_days) if total_cal else None
            avg_prot = round(total_prot / meal_days, 1) if total_prot else None
            avg_fat = round(total_fat / meal_days, 1) if total_fat else None
            avg_carbs = round(total_carbs / meal_days, 1) if total_carbs else None
            parts.append(f"\nПитание (за {meal_days} дн.):")
            if avg_cal:
                parts.append(f"  Среднее: ~{avg_cal} ккал/день")
            if avg_prot:
                parts.append(f"  Белок: ~{avg_prot} г/день")
            if avg_fat:
                parts.append(f"  Жиры: ~{avg_fat} г/день")
            if avg_carbs:
                parts.append(f"  Углеводы: ~{avg_carbs} г/день")
        else:
            parts.append("\nПитание: нет записей.")

        # --- Тренировки ---
        workout_stmt = select(
            func.count(WorkoutLog.id),
            func.sum(WorkoutLog.duration_minutes),
        ).where(
            WorkoutLog.user_id == user_id,
            WorkoutLog.date >= since,
            WorkoutLog.deleted_at.is_(None),
        )
        workout_row = (await session.execute(workout_stmt)).one()
        workout_count, total_workout_min = workout_row

        if workout_count:
            parts.append(
                f"\nТренировки ({workout_count} шт.):"
                f"\n  Общее время: {total_workout_min or '?'} мин"
            )
        else:
            parts.append("\nТренировки: нет записей.")

        # --- Заметки / настроение ---
        note_stmt = select(
            func.count(DailyNote.id),
            func.avg(DailyNote.energy_level),
            func.avg(DailyNote.stress_level),
        ).where(
            DailyNote.user_id == user_id,
            DailyNote.date >= since,
            DailyNote.deleted_at.is_(None),
        )
        note_row = (await session.execute(note_stmt)).one()
        note_count, avg_energy, avg_stress = note_row

        if note_count:
            parts.append(f"\nЗаметки ({note_count} шт.):")
            if avg_energy:
                parts.append(f"  Средняя энергия: {avg_energy:.1f}/10")
            if avg_stress:
                parts.append(f"  Средний стресс: {avg_stress:.1f}/10")
        else:
            parts.append("\nЗаметки: нет записей.")

        # --- Активные наблюдения (derived rules) ---
        rules_stmt = (
            select(DerivedRule)
            .where(
                DerivedRule.user_id == user_id,
                DerivedRule.deleted_at.is_(None),
            )
            .order_by(DerivedRule.confidence.desc())
            .limit(5)
        )
        rules_result = await session.execute(rules_stmt)
        rules = rules_result.scalars().all()

        if rules:
            parts.append(f"\nАктивные наблюдения ({len(rules)}):")
            for r in rules:
                parts.append(f"  [{r.confidence:.1f}] {r.rule}")

    return "\n".join(parts)
