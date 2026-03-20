"""Детерминированные quality rules — проверки, которые перестраховывают модель.

Работают ДО вызова LLM: результат вшивается в system prompt как ограничения.
"""

import uuid
from datetime import date, timedelta
from dataclasses import dataclass

from sqlalchemy import select, func

from app.database import async_session
from app.models.logs import SleepLog, MealLog, WorkoutLog


@dataclass
class QualityWarning:
    severity: str  # "info", "warning", "critical"
    message: str


async def check_all_rules(user_id: uuid.UUID) -> list[QualityWarning]:
    """Запускает все quality rules и возвращает список предупреждений."""
    warnings: list[QualityWarning] = []

    async with async_session() as session:
        today = date.today()

        # --- Правило 1: сон < 6ч три дня подряд ---
        three_days_ago = today - timedelta(days=3)
        sleep_stmt = (
            select(SleepLog.date, SleepLog.duration_minutes)
            .where(
                SleepLog.user_id == user_id,
                SleepLog.date >= three_days_ago,
                SleepLog.deleted_at.is_(None),
                SleepLog.duration_minutes.isnot(None),
            )
            .order_by(SleepLog.date.desc())
        )
        sleep_rows = (await session.execute(sleep_stmt)).all()

        if len(sleep_rows) >= 3:
            short_sleep_streak = all(
                row.duration_minutes < 360 for row in sleep_rows[:3]  # < 6 часов
            )
            if short_sleep_streak:
                warnings.append(QualityWarning(
                    severity="critical",
                    message="ВНИМАНИЕ: сон менее 6 часов 3 дня подряд. "
                            "Восстановление значительно замедлено. "
                            "Рекомендуй только лёгкую нагрузку или отдых.",
                ))

        # --- Правило 2: мало данных для рекомендации ---
        week_ago = today - timedelta(days=7)

        log_count_stmt = select(func.count()).select_from(SleepLog).where(
            SleepLog.user_id == user_id,
            SleepLog.date >= week_ago,
            SleepLog.deleted_at.is_(None),
        )
        sleep_count = (await session.execute(log_count_stmt)).scalar() or 0

        meal_count_stmt = select(func.count()).select_from(MealLog).where(
            MealLog.user_id == user_id,
            MealLog.date >= week_ago,
            MealLog.deleted_at.is_(None),
        )
        meal_count = (await session.execute(meal_count_stmt)).scalar() or 0

        total_logs = sleep_count + meal_count
        if total_logs < 5:
            warnings.append(QualityWarning(
                severity="info",
                message=f"Мало данных за последнюю неделю ({total_logs} записей). "
                        "Рекомендации будут приблизительными — предупреди об этом.",
            ))

        # --- Правило 3: тяжёлая тренировка вчера ---
        yesterday = today - timedelta(days=1)
        heavy_stmt = (
            select(WorkoutLog)
            .where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date == yesterday,
                WorkoutLog.intensity == "high",
                WorkoutLog.deleted_at.is_(None),
            )
        )
        heavy_yesterday = (await session.execute(heavy_stmt)).scalars().first()

        if heavy_yesterday:
            warnings.append(QualityWarning(
                severity="warning",
                message=f"Вчера была тяжёлая тренировка ({heavy_yesterday.workout_type}). "
                        "Учитывай необходимость восстановления (минимум 48ч для тех же мышц).",
            ))

    return warnings


def validate_meal_calories(calories: int | None) -> QualityWarning | None:
    """Проверяет калорийность одного приёма пищи на адекватность."""
    if calories is None:
        return None
    if calories > 5000:
        return QualityWarning(
            severity="warning",
            message=f"Калорийность {calories} ккал за один приём — вероятная ошибка ввода. Уточни у пользователя.",
        )
    if calories < 30:
        return QualityWarning(
            severity="info",
            message=f"Калорийность {calories} ккал — подозрительно мало для приёма пищи.",
        )
    return None
