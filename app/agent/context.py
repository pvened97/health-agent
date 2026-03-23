"""Selective memory injection — подгружает релевантный контекст из БД перед запросом к агенту."""

import uuid
from datetime import date, timedelta

from app.config import today_msk

from sqlalchemy import select, func

from app.database import async_session
from app.models.memory import UserProfile, DerivedRule
from app.models.logs import SleepLog, MealLog, WorkoutLog, DailyNote, RecoveryLog
from app.quality.rules import check_all_rules


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

        # --- Питание за сегодня ---
        today = today_msk()
        meals_stmt = select(
            func.count(MealLog.id),
            func.sum(MealLog.calories),
            func.sum(MealLog.protein_g),
        ).where(
            MealLog.user_id == user_id,
            MealLog.date == today,
            MealLog.deleted_at.is_(None),
        )
        meal_row = (await session.execute(meals_stmt)).one()
        meal_count, total_cal, total_prot = meal_row

        if meal_count:
            meal_info = f"Питание сегодня: {meal_count} приёмов"
            if total_cal:
                meal_info += f", ~{int(total_cal)} ккал"
            if total_prot:
                meal_info += f", белок ~{total_prot:.0f}г"
            sections.append(meal_info)

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

    if not sections:
        return ""

    return "\n\n".join(sections)
