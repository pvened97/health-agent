"""Selective memory injection — подгружает релевантный контекст из БД перед запросом к агенту."""

import uuid
from datetime import date, timedelta

from app.config import today_msk

from sqlalchemy import select, func

from app.database import async_session
from app.models.memory import UserProfile, DerivedRule, MemoryNote
from app.models.logs import SleepLog, MealLog, WorkoutLog, DailyNote, RecoveryLog
from app.quality.rules import check_all_rules

# Поля, которые должны быть заполнены для завершения онбординга
REQUIRED_PROFILE_FIELDS = [
    ("personal", "first_name", "Имя"),
    ("anthropometry", "sex", "Пол (М/Ж)"),
    ("anthropometry", "age", "Возраст"),
    ("anthropometry", "weight_kg", "Вес (кг)"),
    ("anthropometry", "height_cm", "Рост (см)"),
    ("goals", "primary_goal", "Основная цель (набор массы / похудение / поддержание формы / рекомпозиция)"),
    ("lifestyle", "activity_level", "Уровень бытовой активности (low / moderate / high / very_high)"),
]

# Маппинг уровня активности → коэффициент для BMR
ACTIVITY_LEVEL_MULTIPLIER = {
    "low": 1.35,
    "moderate": 1.45,
    "high": 1.55,
    "very_high": 1.60,
}


async def get_missing_profile_fields(user_id: uuid.UUID) -> list[str]:
    """Возвращает список описаний незаполненных обязательных полей профиля."""
    async with async_session() as session:
        stmt = select(UserProfile.category, UserProfile.key).where(
            UserProfile.user_id == user_id,
            UserProfile.deleted_at.is_(None),
        )
        rows = (await session.execute(stmt)).all()
        existing = {(r[0], r[1]) for r in rows}

    return [
        desc for cat, key, desc in REQUIRED_PROFILE_FIELDS
        if (cat, key) not in existing
    ]


async def get_user_first_name(user_id: uuid.UUID) -> str | None:
    """Возвращает имя пользователя из профиля или None."""
    async with async_session() as session:
        row = (await session.execute(
            select(UserProfile.value).where(
                UserProfile.user_id == user_id,
                UserProfile.category == "personal",
                UserProfile.key == "first_name",
                UserProfile.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
    return row


async def build_user_context(user_id: uuid.UUID) -> str:
    """Собирает краткий контекст о пользователе для инъекции в system prompt.

    Включает:
    - Quality warnings (детерминированные правила)
    - Профиль (все подтверждённые факты)
    - Последний сон (если за вчера/сегодня)
    - Сводка по питанию за сегодня
    - Активные наблюдения (derived rules с высокой уверенностью)
    """
    sections = []

    # Quality rules — детерминированные предупреждения
    warnings = await check_all_rules(user_id)
    if warnings:
        warn_lines = ["⚠️ ОГРАНИЧЕНИЯ (обязательно учитывай):"]
        for w in warnings:
            prefix = "🔴" if w.severity == "critical" else "🟡" if w.severity == "warning" else "ℹ️"
            warn_lines.append(f"  {prefix} {w.message}")
        sections.append("\n".join(warn_lines))

    async with async_session() as session:
        # --- Профиль ---
        profile_stmt = (
            select(UserProfile)
            .where(
                UserProfile.user_id == user_id,
                UserProfile.deleted_at.is_(None),
                UserProfile.confirmed.is_(True),
            )
            .order_by(UserProfile.category, UserProfile.key)
        )
        profile_rows = (await session.execute(profile_stmt)).scalars().all()

        if profile_rows:
            profile_lines = []
            current_cat = None
            for row in profile_rows:
                if row.category != current_cat:
                    current_cat = row.category
                    profile_lines.append(f"[{current_cat}]")
                profile_lines.append(f"  {row.key}: {row.value}")
            sections.append("Профиль пользователя:\n" + "\n".join(profile_lines))

        # --- Онбординг: список незаполненных полей (для онбординг-промпта) ---
        existing_keys = {(r.category, r.key) for r in profile_rows}
        missing = [
            desc for cat, key, desc in REQUIRED_PROFILE_FIELDS
            if (cat, key) not in existing_keys
        ]
        if missing:
            missing_list = "\n".join(f"  - {m}" for m in missing)
            sections.insert(0, f"Ещё не заполнено:\n{missing_list}")

        # --- Последний сон ---
        yesterday = today_msk() - timedelta(days=1)
        sleep_stmt = (
            select(SleepLog)
            .where(
                SleepLog.user_id == user_id,
                SleepLog.date >= yesterday,
                SleepLog.deleted_at.is_(None),
            )
            .order_by(SleepLog.date.desc())
            .limit(1)
        )
        last_sleep = (await session.execute(sleep_stmt)).scalar_one_or_none()

        if last_sleep:
            sleep_parts = [f"Последний сон ({last_sleep.date}):"]
            if last_sleep.duration_minutes:
                sleep_parts.append(f"  Длительность: {last_sleep.duration_minutes} мин ({last_sleep.duration_minutes / 60:.1f} ч)")
            if last_sleep.quality:
                sleep_parts.append(f"  Качество: {last_sleep.quality}")
            sections.append("\n".join(sleep_parts))

        # --- Баланс питания за сегодня ---
        today = today_msk()
        meals_stmt = select(
            func.count(MealLog.id),
            func.coalesce(func.sum(MealLog.calories), 0),
            func.coalesce(func.sum(MealLog.protein_g), 0),
        ).where(
            MealLog.user_id == user_id,
            MealLog.date == today,
            MealLog.deleted_at.is_(None),
        )
        meal_row = (await session.execute(meals_stmt)).one()
        meal_count, total_cal, total_prot = meal_row

    # Вычисляем дневную норму (lazy import — avoid circular)
    from app.agent.tools.calorie_calc import compute_daily_targets
    targets = await compute_daily_targets(user_id, today)

    if targets and meal_count:
        pct = int(total_cal / targets["target_cal"] * 100) if targets["target_cal"] else 0
        rem_cal = targets["target_cal"] - int(total_cal)
        rem_prot = targets["target_prot"] - round(total_prot)
        bal_parts = [f"Питание сегодня ({meal_count} приёмов):"]
        bal_parts.append(f"  Калории: {int(total_cal)} / {targets['target_cal']} ккал ({pct}%), осталось {rem_cal}")
        bal_parts.append(f"  Белок: {round(total_prot)}г / {targets['target_prot']}г, осталось {rem_prot}г")
        sections.append("\n".join(bal_parts))
    elif targets and not meal_count:
        sections.append(f"Питание сегодня: нет записей. Цель: {targets['target_cal']} ккал, белок {targets['target_prot']}г")
    elif meal_count:
        meal_info = f"Питание сегодня: ~{int(total_cal)} ккал, белок ~{round(total_prot)}г (норма не рассчитана)"
        sections.append(meal_info)

    async with async_session() as session:

        # --- Последний recovery (WHOOP) ---
        recovery_stmt = (
            select(RecoveryLog)
            .where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date >= yesterday,
                RecoveryLog.source == "whoop_api",
                RecoveryLog.deleted_at.is_(None),
            )
            .order_by(RecoveryLog.date.desc())
            .limit(1)
        )
        last_recovery = (await session.execute(recovery_stmt)).scalar_one_or_none()

        if last_recovery:
            rec_parts = [f"WHOOP Recovery ({last_recovery.date}):"]
            if last_recovery.recovery_score is not None:
                zone = "🟢" if last_recovery.recovery_score >= 67 else "🟡" if last_recovery.recovery_score >= 34 else "🔴"
                rec_parts.append(f"  {zone} Score: {last_recovery.recovery_score:.0f}%")
            if last_recovery.hrv_ms is not None:
                rec_parts.append(f"  HRV: {last_recovery.hrv_ms:.1f} ms")
            if last_recovery.resting_hr is not None:
                rec_parts.append(f"  Пульс покоя: {last_recovery.resting_hr:.0f}")
            sections.append("\n".join(rec_parts))

        # --- Активные наблюдения (confidence >= 0.6) ---
        rules_stmt = (
            select(DerivedRule)
            .where(
                DerivedRule.user_id == user_id,
                DerivedRule.deleted_at.is_(None),
                DerivedRule.confidence >= 0.6,
            )
            .order_by(DerivedRule.confidence.desc())
            .limit(3)
        )
        rules = (await session.execute(rules_stmt)).scalars().all()

        if rules:
            rules_lines = ["Наблюдения:"]
            for r in rules:
                rules_lines.append(f"  • {r.rule} (уверенность: {r.confidence:.0%})")
            sections.append("\n".join(rules_lines))

        # --- Долгосрочная память (заметки пользователя) ---
        memory_stmt = (
            select(MemoryNote)
            .where(
                MemoryNote.user_id == user_id,
                MemoryNote.deleted_at.is_(None),
            )
            .order_by(MemoryNote.category, MemoryNote.created_at)
            .limit(20)
        )
        memories = (await session.execute(memory_stmt)).scalars().all()

        if memories:
            mem_lines = ["Память (пользователь просил запомнить):"]
            for m in memories:
                mem_lines.append(f"  • [{m.category}] {m.content}")
            sections.append("\n".join(mem_lines))

    if not sections:
        return ""

    return "\n\n".join(sections)
