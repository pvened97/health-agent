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
from sqlalchemy import func

from app.models.logs import CycleLog, WorkoutLog, RecoveryLog, MealLog


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

# Таблица надбавки ккал для ручных тренировок (без WHOOP strain)
# Строки — длительность, столбцы — интенсивность
MANUAL_LOAD_BONUS = {
    #              low  moderate  high   max
    "short":    {"low":   0, "moderate":  50, "high":  80, "max": 100},   # < 30 мин
    "medium":   {"low":  50, "moderate": 100, "high": 150, "max": 200},   # 30–60 мин
    "long":     {"low":  80, "moderate": 150, "high": 220, "max": 280},   # 60–90 мин
    "extended": {"low": 100, "moderate": 200, "high": 280, "max": 350},   # 90+ мин
}


def _duration_bucket(minutes: int | None) -> str:
    """Определяет зону длительности."""
    if minutes is None or minutes < 30:
        return "short"
    elif minutes <= 60:
        return "medium"
    elif minutes <= 90:
        return "long"
    else:
        return "extended"


def _estimate_manual_load_bonus(workouts: list) -> tuple[int, str]:
    """Оценивает надбавку ккал по ручным тренировкам (без WHOOP).

    Берёт максимальный бонус из всех тренировок за день.
    Возвращает (бонус_ккал, описание).
    """
    if not workouts:
        return 0, "нет тренировок"

    best_bonus = 0
    best_desc = ""
    for w in workouts:
        intensity = (w.intensity or "moderate").lower()
        if intensity not in ("low", "moderate", "high", "max"):
            intensity = "moderate"
        bucket = _duration_bucket(w.duration_minutes)
        bonus = MANUAL_LOAD_BONUS[bucket].get(intensity, 100)
        if bonus > best_bonus:
            best_bonus = bonus
            dur_str = f"{w.duration_minutes} мин" if w.duration_minutes else "н/д"
            best_desc = f"{w.workout_type or 'тренировка'} ({dur_str}, {intensity})"
    return best_bonus, best_desc


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


async def compute_daily_targets(user_id: "uuid.UUID", calc_date: date) -> dict | None:
    """Вычисляет дневные цели по калориям и макросам. Возвращает dict или None если профиль не заполнен.

    Результат: {target_cal, target_prot, target_fat, target_carbs,
                bmr, base_maintenance, activity_mult, activity_level, load_adjustment,
                load_source, adjustment, goal_type, primary_goal, recovery_label, recovery_mod,
                day_strain, workout_mod, workout_type_name, used_manual_estimate,
                strain_bonus?, manual_bonus?, manual_desc?}
    """
    import uuid as _uuid  # noqa — for type hint only

    async with async_session() as session:
        stmt = select(UserProfile).where(
            UserProfile.user_id == user_id,
            UserProfile.deleted_at.is_(None),
        )
        rows = (await session.execute(stmt)).scalars().all()
        profile = {(r.category, r.key): r.value for r in rows}

    weight = profile.get(("anthropometry", "weight_kg"))
    height = profile.get(("anthropometry", "height_cm"))
    age = profile.get(("anthropometry", "age"))
    sex = profile.get(("anthropometry", "sex"))
    activity_level = profile.get(("lifestyle", "activity_level"))
    primary_goal = profile.get(("goals", "primary_goal"), "")

    if not all([weight, height, age, sex, activity_level]):
        return None

    weight_kg = float(weight)
    height_cm = float(height)
    age_years = int(age)
    activity_mult = ACTIVITY_LEVEL_MULTIPLIER.get(activity_level, 1.45)

    bmr = _calc_bmr(weight_kg, height_cm, age_years, sex)
    base_maintenance = bmr * activity_mult

    # --- Нагрузка ---
    day_strain = None
    workout_types = []

    async with async_session() as session:
        cycle = (await session.execute(
            select(CycleLog).where(
                CycleLog.user_id == user_id,
                CycleLog.date == calc_date,
                CycleLog.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

        if cycle and cycle.day_strain is not None:
            day_strain = cycle.day_strain

        workouts = (await session.execute(
            select(WorkoutLog).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date == calc_date,
                WorkoutLog.deleted_at.is_(None),
            )
        )).scalars().all()

        if workouts:
            workout_types = [w.workout_type for w in workouts if w.workout_type]
            if day_strain is None:
                workout_strains = [w.strain for w in workouts if w.strain is not None]
                if workout_strains:
                    day_strain = max(workout_strains)

        recovery = (await session.execute(
            select(RecoveryLog).where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date == calc_date,
                RecoveryLog.deleted_at.is_(None),
            ).order_by(RecoveryLog.created_at.desc())
        )).scalar_one_or_none()

    # Надбавка
    used_manual_estimate = False
    strain_bonus = 0
    manual_bonus = 0
    manual_desc = ""
    if day_strain is not None:
        strain_bonus = _get_strain_bonus(day_strain)
        workout_mod, workout_type_name = _get_workout_modifier(workout_types)
        load_adjustment = int(strain_bonus * workout_mod)
        load_source = f"WHOOP strain {day_strain:.1f}"
    elif workouts:
        manual_bonus, manual_desc = _estimate_manual_load_bonus(workouts)
        workout_mod, workout_type_name = _get_workout_modifier(workout_types)
        load_adjustment = int(manual_bonus * workout_mod)
        load_source = f"оценка: {manual_desc}"
        used_manual_estimate = True
    else:
        load_adjustment = 0
        workout_mod = NO_WORKOUT_MODIFIER
        workout_type_name = "нет тренировки"
        load_source = "нет тренировок"

    # Профицит / дефицит по цели
    recovery_score = recovery.recovery_score if recovery else None
    recovery_mod, recovery_label = _get_recovery_modifier(recovery_score)

    goal_lower = primary_goal.lower()
    if "набор" in goal_lower:
        goal_type = "bulk"
        goal_pct = 0.05  # +5%
        adjustment = int(base_maintenance * goal_pct * recovery_mod)
    elif "похуд" in goal_lower:
        goal_type = "cut"
        goal_pct = -0.15  # -15%
        # Плохое восстановление → мягче дефицит (ближе к 0)
        cut_recovery_mod = 2.0 - recovery_mod  # green→1.0, yellow→1.15, red→1.30
        adjustment = int(base_maintenance * goal_pct * cut_recovery_mod)
    elif "рекомпозиц" in goal_lower:
        goal_type = "recomp"
        goal_pct = -0.05  # -5%
        cut_recovery_mod = 2.0 - recovery_mod
        adjustment = int(base_maintenance * goal_pct * cut_recovery_mod)
    else:
        goal_type = "maintain"
        goal_pct = 0.0
        adjustment = 0

    target_cal = int(base_maintenance + load_adjustment + adjustment)
    target_prot = round(2.0 * weight_kg) if goal_type in ("bulk", "recomp") else round(1.8 * weight_kg)
    target_fat = round(target_cal * 0.25 / 9)
    target_carbs = round(max(target_cal - target_prot * 4 - target_fat * 9, 0) / 4)
    return {
        "target_cal": target_cal, "target_prot": target_prot,
        "target_fat": target_fat, "target_carbs": target_carbs,
        "bmr": bmr, "base_maintenance": base_maintenance,
        "activity_mult": activity_mult, "activity_level": activity_level,
        "load_adjustment": load_adjustment, "load_source": load_source,
        "adjustment": adjustment, "goal_type": goal_type,
        "primary_goal": primary_goal,
        "recovery_label": recovery_label, "recovery_mod": recovery_mod,
        "day_strain": day_strain, "workout_mod": workout_mod,
        "workout_type_name": workout_type_name,
        "used_manual_estimate": used_manual_estimate,
        "strain_bonus": strain_bonus,
        "manual_bonus": manual_bonus, "manual_desc": manual_desc,
        "goal_pct": goal_pct,
    }


@function_tool
async def calculate_daily_target(target_date: Optional[str] = None) -> str:
    """Рассчитывает дневную цель по калориям и макросам на основе профиля, WHOOP strain и recovery.
    Вызывай когда нужно показать или пересчитать дневную норму калорий.

    Args:
        target_date: Дата расчёта в формате YYYY-MM-DD (по умолчанию сегодня)
    """
    user_id = get_user_id()
    calc_date = date.fromisoformat(target_date) if target_date else today_msk()

    t = await compute_daily_targets(user_id, calc_date)
    if t is None:
        return "Невозможно рассчитать: профиль не заполнен."

    lines = [f"Дневная цель ({calc_date}):"]
    lines.append(f"")
    lines.append(f"  BMR (Mifflin–St Jeor): {int(t['bmr'])} ккал")
    lines.append(f"  Базовое поддержание (×{t['activity_mult']} {t['activity_level']}): {int(t['base_maintenance'])} ккал")

    if t["day_strain"] is not None:
        strain_zone = "тяжёлый" if t["day_strain"] >= 14 else "средний" if t["day_strain"] >= 8 else "лёгкий"
        lines.append(f"  Надбавка по нагрузке: +{t['strain_bonus']} × {t['workout_mod']} ({t['workout_type_name']}) = +{t['load_adjustment']} ккал  [strain {t['day_strain']:.1f}, {strain_zone}]")
    elif t["used_manual_estimate"]:
        lines.append(f"  Надбавка по нагрузке (оценка): +{t['manual_bonus']} × {t['workout_mod']} ({t['workout_type_name']}) = +{t['load_adjustment']} ккал  [{t['manual_desc']}]")
    else:
        lines.append(f"  Надбавка по нагрузке: +0 ккал (нет тренировок и данных WHOOP)")

    if t["goal_type"] == "bulk":
        lines.append(f"  Профицит на набор: {int(t['base_maintenance'])} × {t['goal_pct']:+.0%} × {t['recovery_mod']} (recovery {t['recovery_label']}) = {t['adjustment']:+d} ккал")
    elif t["goal_type"] == "cut":
        lines.append(f"  Дефицит на похудение: {int(t['base_maintenance'])} × {t['goal_pct']:+.0%} × recovery {t['recovery_label']} = {t['adjustment']:+d} ккал")
    elif t["goal_type"] == "recomp":
        lines.append(f"  Рекомпозиция: {int(t['base_maintenance'])} × {t['goal_pct']:+.0%} × recovery {t['recovery_label']} = {t['adjustment']:+d} ккал")
    else:
        lines.append(f"  Поддержание: без профицита/дефицита (цель: {t['primary_goal']})")

    lines.append(f"")
    lines.append(f"  ИТОГО: {t['target_cal']} ккал")
    lines.append(f"  Белок: {t['target_prot']} г ({t['target_prot'] * 4} ккал)")
    lines.append(f"  Жиры: {t['target_fat']} г ({t['target_fat'] * 9} ккал)")
    lines.append(f"  Углеводы: {t['target_carbs']} г ({max(t['target_cal'] - t['target_prot'] * 4 - t['target_fat'] * 9, 0)} ккал)")

    return "\n".join(lines)


@function_tool
async def get_nutrition_remaining(target_date: Optional[str] = None) -> str:
    """Показывает сколько ещё нужно съесть сегодня: цель, съедено, остаток по калориям и макросам.
    ОБЯЗАТЕЛЬНО вызывай после каждой записи еды или тренировки.

    Args:
        target_date: Дата в формате YYYY-MM-DD (по умолчанию сегодня)
    """
    user_id = get_user_id()
    calc_date = date.fromisoformat(target_date) if target_date else today_msk()

    t = await compute_daily_targets(user_id, calc_date)
    if t is None:
        return "Профиль не заполнен — невозможно рассчитать норму."

    # Съедено за день
    async with async_session() as session:
        eaten_row = (await session.execute(
            select(
                func.coalesce(func.sum(MealLog.calories), 0),
                func.coalesce(func.sum(MealLog.protein_g), 0),
                func.coalesce(func.sum(MealLog.fat_g), 0),
                func.coalesce(func.sum(MealLog.carbs_g), 0),
                func.count(MealLog.id),
            ).where(
                MealLog.user_id == user_id,
                MealLog.date == calc_date,
                MealLog.deleted_at.is_(None),
            )
        )).one()

    eaten_cal, eaten_prot, eaten_fat, eaten_carbs, meal_count = eaten_row

    rem_cal = t["target_cal"] - int(eaten_cal)
    rem_prot = t["target_prot"] - round(eaten_prot)
    rem_fat = t["target_fat"] - round(eaten_fat)
    rem_carbs = t["target_carbs"] - round(eaten_carbs)
    pct = int(eaten_cal / t["target_cal"] * 100) if t["target_cal"] else 0

    lines = [f"Баланс дня ({calc_date}):"]
    lines.append(f"  Цель: {t['target_cal']} ккал ({t['load_source']})")
    lines.append(f"  Съедено: {int(eaten_cal)} ккал ({meal_count} приёмов) — {pct}%")
    lines.append(f"  Осталось: {rem_cal} ккал")
    lines.append(f"")
    lines.append(f"  Макросы (съедено → цель → осталось):")
    lines.append(f"    Белок:    {round(eaten_prot)}г → {t['target_prot']}г → {rem_prot}г")
    lines.append(f"    Жиры:     {round(eaten_fat)}г → {t['target_fat']}г → {rem_fat}г")
    lines.append(f"    Углеводы: {round(eaten_carbs)}г → {t['target_carbs']}г → {rem_carbs}г")

    if rem_cal < 0:
        lines.append(f"")
        lines.append(f"  ⚠️ Перебор на {abs(rem_cal)} ккал")

    return "\n".join(lines)
