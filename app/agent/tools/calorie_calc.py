"""Калькулятор дневной нормы калорий и макросов.

Формула: BMR (Mifflin–St Jeor) × коэффициент активности
         + надбавка по Day Strain × модификатор типа нагрузки
         + профицит на набор × модификатор Recovery.
"""

from datetime import date, timedelta
from typing import Optional

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.agent.context import ACTIVITY_LEVEL_MULTIPLIER
from app.database import async_session
from app.models.memory import UserProfile
from app.models.logs import CycleLog, WorkoutLog, RecoveryLog


# --- Константы ---

STRAIN_BONUS = [
    (18, 320),
    (14, 220),
    (10, 120),
    (0, 0),
]

WORKOUT_TYPE_MODIFIER = {
    # Силовые
    "strength training": 1.0,
    "weightlifting": 1.0,
    "functional fitness": 1.0,
    "powerlifting": 1.0,
    "bodybuilding": 1.0,
    # Смешанные
    "crossfit": 0.85,
    "hiit": 0.85,
    "boxing": 0.85,
    "martial arts": 0.85,
    "mma": 0.85,
    "circuit training": 0.85,
    # Кардио
    "running": 0.75,
    "cycling": 0.75,
    "swimming": 0.75,
    "rowing": 0.75,
    "walking": 0.75,
    "hiking": 0.75,
    "elliptical": 0.75,
    "stairmaster": 0.75,
    # Лёгкие
    "yoga": 0.40,
    "stretching": 0.40,
    "pilates": 0.40,
    "meditation": 0.40,
}

# Ручные типы тренировок → модификатор
MANUAL_TYPE_MODIFIER = {
    "strength": 1.0,
    "cardio": 0.75,
    "flexibility": 0.40,
    "sport": 0.85,
    "mixed": 0.85,
    "other": 0.75,
}

NO_WORKOUT_MODIFIER = 0.25


def _calc_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """BMR по Mifflin–St Jeor."""
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age
    if sex == "M":
        bmr += 5
    else:
        bmr -= 161
    return bmr


def _get_strain_bonus(day_strain: float) -> int:
    """Надбавка ккал по зоне Day Strain."""
    for threshold, bonus in STRAIN_BONUS:
        if day_strain >= threshold:
            return bonus
    return 0


def _get_workout_modifier(workout_types: list[str]) -> tuple[float, str]:
    """Определяет максимальный модификатор из списка тренировок за день.
    Возвращает (модификатор, название типа)."""
    if not workout_types:
        return NO_WORKOUT_MODIFIER, "нет тренировки"

    best_mod = 0.0
    best_name = ""
    for wt in workout_types:
        wt_lower = wt.lower().strip()
        # Сначала ищем в WHOOP типах
        mod = WORKOUT_TYPE_MODIFIER.get(wt_lower)
        if mod is None:
            # Затем в ручных типах
            mod = MANUAL_TYPE_MODIFIER.get(wt_lower, 0.75)
        if mod > best_mod:
            best_mod = mod
            best_name = wt
    return best_mod, best_name


def _get_recovery_modifier(recovery_score: float | None) -> tuple[float, str]:
    """Модификатор профицита по Recovery zone."""
    if recovery_score is None:
        return 1.0, "нет данных (по умолчанию 1.0)"
    if recovery_score >= 67:
        return 1.0, f"🟢 {recovery_score:.0f}%"
    elif recovery_score >= 34:
        return 0.85, f"🟡 {recovery_score:.0f}%"
    else:
        return 0.70, f"🔴 {recovery_score:.0f}%"


@function_tool
async def calculate_daily_target(target_date: Optional[str] = None) -> str:
    """Рассчитывает дневную цель по калориям и макросам на основе профиля, WHOOP strain и recovery.
    Вызывай когда нужно показать или пересчитать дневную норму калорий.

    Args:
        target_date: Дата расчёта в формате YYYY-MM-DD (по умолчанию сегодня)
    """
    user_id = get_user_id()
    calc_date = date.fromisoformat(target_date) if target_date else today_msk()

    # --- Загружаем профиль ---
    async with async_session() as session:
        stmt = select(UserProfile).where(
            UserProfile.user_id == user_id,
            UserProfile.deleted_at.is_(None),
        )
        rows = (await session.execute(stmt)).scalars().all()
        profile = {(r.category, r.key): r.value for r in rows}

    # Проверяем обязательные поля
    weight = profile.get(("anthropometry", "weight_kg"))
    height = profile.get(("anthropometry", "height_cm"))
    age = profile.get(("anthropometry", "age"))
    sex = profile.get(("anthropometry", "sex"))
    activity_level = profile.get(("lifestyle", "activity_level"))
    primary_goal = profile.get(("goals", "primary_goal"), "")

    missing = []
    if not weight:
        missing.append("вес")
    if not height:
        missing.append("рост")
    if not age:
        missing.append("возраст")
    if not sex:
        missing.append("пол")
    if not activity_level:
        missing.append("уровень активности")

    if missing:
        return f"Невозможно рассчитать: не заполнены поля профиля: {', '.join(missing)}."

    weight_kg = float(weight)
    height_cm = float(height)
    age_years = int(age)
    activity_mult = ACTIVITY_LEVEL_MULTIPLIER.get(activity_level, 1.45)

    # --- Блок 1: Базовое поддержание ---
    bmr = _calc_bmr(weight_kg, height_cm, age_years, sex)
    base_maintenance = bmr * activity_mult

    # --- Блок 2: Поправка на нагрузку ---
    day_strain = None
    workout_types = []

    async with async_session() as session:
        # Day Strain из CycleLog
        cycle = (await session.execute(
            select(CycleLog).where(
                CycleLog.user_id == user_id,
                CycleLog.date == calc_date,
                CycleLog.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

        if cycle and cycle.day_strain is not None:
            day_strain = cycle.day_strain

        # Тренировки за день (для типа нагрузки)
        workouts = (await session.execute(
            select(WorkoutLog).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date == calc_date,
                WorkoutLog.deleted_at.is_(None),
            )
        )).scalars().all()

        if workouts:
            workout_types = [w.workout_type for w in workouts if w.workout_type]

            # Если нет CycleLog, fallback: максимальный strain из тренировок
            if day_strain is None:
                workout_strains = [w.strain for w in workouts if w.strain is not None]
                if workout_strains:
                    day_strain = max(workout_strains)

        # Recovery за день
        recovery = (await session.execute(
            select(RecoveryLog).where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date == calc_date,
                RecoveryLog.deleted_at.is_(None),
            ).order_by(RecoveryLog.created_at.desc())
        )).scalar_one_or_none()

    strain_bonus = _get_strain_bonus(day_strain) if day_strain is not None else 0
    workout_mod, workout_type_name = _get_workout_modifier(workout_types)
    load_adjustment = int(strain_bonus * workout_mod)

    # --- Блок 3: Профицит на набор ---
    recovery_score = recovery.recovery_score if recovery else None
    recovery_mod, recovery_label = _get_recovery_modifier(recovery_score)

    # Профицит только при цели "набор массы"
    is_bulk = "набор" in primary_goal.lower()
    surplus_pct = 0.05 if is_bulk else 0.0
    surplus = int(base_maintenance * surplus_pct * recovery_mod) if is_bulk else 0

    # --- Итого ---
    target_calories = int(base_maintenance + load_adjustment + surplus)

    # --- Макросы ---
    protein_g = round(2.0 * weight_kg)
    fat_g = round(target_calories * 0.25 / 9)
    protein_cal = protein_g * 4
    fat_cal = fat_g * 9
    carbs_cal = target_calories - protein_cal - fat_cal
    carbs_g = round(max(carbs_cal, 0) / 4)
    fiber_g = 30 if target_calories >= 2500 else 25

    # --- Формируем ответ ---
    lines = [f"Дневная цель ({calc_date}):"]
    lines.append(f"")
    lines.append(f"  BMR (Mifflin–St Jeor): {int(bmr)} ккал")
    lines.append(f"  Базовое поддержание (×{activity_mult} {activity_level}): {int(base_maintenance)} ккал")

    if day_strain is not None:
        strain_zone = "тяжёлый" if day_strain >= 14 else "средний" if day_strain >= 8 else "лёгкий"
        lines.append(f"  Надбавка по нагрузке: +{strain_bonus} × {workout_mod} ({workout_type_name}) = +{load_adjustment} ккал  [strain {day_strain:.1f}, {strain_zone}]")
    else:
        lines.append(f"  Надбавка по нагрузке: +0 ккал (нет данных о strain)")

    if is_bulk:
        lines.append(f"  Профицит на набор: {int(base_maintenance)} × {surplus_pct:.0%} × {recovery_mod} (recovery {recovery_label}) = +{surplus} ккал")
    else:
        lines.append(f"  Профицит: 0 (цель: {primary_goal})")

    lines.append(f"")
    lines.append(f"  ИТОГО: {target_calories} ккал")
    lines.append(f"  Белок: {protein_g} г ({protein_cal} ккал)")
    lines.append(f"  Жиры: {fat_g} г ({fat_cal} ккал)")
    lines.append(f"  Углеводы: {carbs_g} г ({carbs_cal} ккал)")
    lines.append(f"  Клетчатка: {fiber_g} г")

    return "\n".join(lines)
