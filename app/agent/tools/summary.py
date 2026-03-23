import re
from datetime import date, timedelta

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select, func

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.logs import SleepLog, MealLog, WorkoutLog, DailyNote, RecoveryLog
from app.models.memory import UserProfile, DerivedRule


def _parse_goal(value: str) -> tuple[int | None, int | None]:
    """Парсит цель: '2500' → (2500, 2500), '110-150' → (110, 150)."""
    m = re.match(r"(\d+)\s*[-–—]\s*(\d+)", value)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d+)", value)
    if m:
        v = int(m.group(1))
        return v, v
    return None, None


def _goal_progress(actual: float, goal_min: int, goal_max: int) -> str:
    """Форматирует прогресс: '1800 ккал (цель: 2500, 72%)' или 'цель: 110-150г, 95%'."""
    if goal_min == goal_max:
        pct = int(actual / goal_max * 100)
        return f"цель: {goal_max}, {pct}%"
    else:
        mid = (goal_min + goal_max) / 2
        pct = int(actual / mid * 100)
        return f"цель: {goal_min}–{goal_max}, {pct}%"


@function_tool
async def get_daily_recommendation_context(target_date: str = "") -> str:
    """Собирает данные за конкретный день: питание (с прогрессом по целям), тренировки, сон, WHOOP recovery, профиль, наблюдения. Вызывай ПЕРЕД тем как давать рекомендацию, оценку дня или промежуточные итоги.

    Args:
        target_date: Дата в формате YYYY-MM-DD. Если не указана — используется сегодня.
    """
    user_id = get_user_id()
    today = today_msk()
    target = date.fromisoformat(target_date) if target_date else today
    target_label = "сегодня" if target == today else ("вчера" if target == today - timedelta(days=1) else str(target))
    sections = []

    async with async_session() as session:
        # --- Профиль (цель, ограничения) ---
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
            lines = []
            current_cat = None
            for row in profile_rows:
                if row.category != current_cat:
                    current_cat = row.category
                    lines.append(f"[{current_cat}]")
                lines.append(f"  {row.key}: {row.value}")
            sections.append("Профиль:\n" + "\n".join(lines))
        else:
            sections.append("Профиль: не заполнен.")

        # --- Сон за этот день ---
        sleep_stmt = (
            select(SleepLog)
            .where(
                SleepLog.user_id == user_id,
                SleepLog.date == target,
                SleepLog.deleted_at.is_(None),
            )
            .order_by(SleepLog.created_at.desc())
            .limit(1)
        )
        last_sleep = (await session.execute(sleep_stmt)).scalar_one_or_none()

        if last_sleep:
            parts = [f"Сон ({target_label}):"]
            if last_sleep.duration_minutes:
                h = last_sleep.duration_minutes / 60
                parts.append(f"  Длительность: {last_sleep.duration_minutes} мин ({h:.1f}ч)")
            if last_sleep.quality:
                parts.append(f"  Качество: {last_sleep.quality}")
            if last_sleep.bed_time:
                parts.append(f"  Лёг: {last_sleep.bed_time.strftime('%H:%M')}")
            if last_sleep.wake_time:
                parts.append(f"  Встал: {last_sleep.wake_time.strftime('%H:%M')}")
            sections.append("\n".join(parts))
        else:
            sections.append(f"Сон ({target_label}): нет данных.")

        # --- Средний сон за 7 дней ---
        week_ago = target - timedelta(days=7)
        avg_sleep_stmt = select(
            func.avg(SleepLog.duration_minutes),
            func.count(SleepLog.id),
        ).where(
            SleepLog.user_id == user_id,
            SleepLog.date >= week_ago,
            SleepLog.date <= target,
            SleepLog.deleted_at.is_(None),
            SleepLog.duration_minutes.isnot(None),
        )
        avg_sleep_row = (await session.execute(avg_sleep_stmt)).one()
        avg_sleep, sleep_count = avg_sleep_row

        if avg_sleep:
            sections.append(f"Средний сон за 7 дн.: {int(avg_sleep)} мин ({avg_sleep / 60:.1f}ч), записей: {sleep_count}")

        # --- Цели по питанию из профиля ---
        cal_min, cal_max = None, None
        prot_min, prot_max = None, None
        goals_stmt = select(UserProfile).where(
            UserProfile.user_id == user_id,
            UserProfile.category == "goals",
            UserProfile.key.in_(["daily_calories", "daily_protein_g"]),
            UserProfile.deleted_at.is_(None),
        )
        goals = (await session.execute(goals_stmt)).scalars().all()
        for g in goals:
            if g.key == "daily_calories":
                cal_min, cal_max = _parse_goal(g.value)
            elif g.key == "daily_protein_g":
                prot_min, prot_max = _parse_goal(g.value)

        # --- Питание за целевой день ---
        meals_stmt = select(
            func.count(MealLog.id),
            func.sum(MealLog.calories),
            func.sum(MealLog.protein_g),
            func.sum(MealLog.carbs_g),
            func.sum(MealLog.fat_g),
        ).where(
            MealLog.user_id == user_id,
            MealLog.date == target,
            MealLog.deleted_at.is_(None),
        )
        meal_row = (await session.execute(meals_stmt)).one()
        m_count, m_cal, m_prot, m_carbs, m_fat = meal_row

        if m_count:
            meal_parts = [f"Питание {target_label} ({m_count} приёмов):"]
            if m_cal:
                cal_str = f"  Калории: {int(m_cal)} ккал"
                if cal_min and cal_max:
                    cal_str += f" ({_goal_progress(m_cal, cal_min, cal_max)})"
                meal_parts.append(cal_str)
            if m_prot:
                prot_str = f"  Белок: {m_prot:.0f}г"
                if prot_min and prot_max:
                    prot_str += f" ({_goal_progress(m_prot, prot_min, prot_max)})"
                meal_parts.append(prot_str)
            if m_carbs:
                meal_parts.append(f"  Углеводы: {m_carbs:.0f}г")
            if m_fat:
                meal_parts.append(f"  Жиры: {m_fat:.0f}г")
            sections.append("\n".join(meal_parts))
        else:
            sections.append(f"Питание {target_label}: нет данных.")

        # --- Тренировки за целевой день ---
        workout_stmt = (
            select(WorkoutLog)
            .where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date == target,
                WorkoutLog.deleted_at.is_(None),
            )
            .order_by(WorkoutLog.created_at.desc())
        )
        workouts = (await session.execute(workout_stmt)).scalars().all()

        if workouts:
            w_parts = [f"Тренировки {target_label} ({len(workouts)} шт.):"]
            for w in workouts:
                line = f"  • {w.workout_type}"
                if w.intensity:
                    line += f", {w.intensity}"
                if w.duration_minutes:
                    line += f", {w.duration_minutes} мин"
                if w.description:
                    line += f" — {w.description}"
                w_parts.append(line)
            sections.append("\n".join(w_parts))
        else:
            sections.append(f"Тренировки {target_label}: нет записей.")

        # --- Recovery (WHOOP) за целевой день ---
        recovery_stmt = (
            select(RecoveryLog)
            .where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date == target,
                RecoveryLog.source == "whoop_api",
                RecoveryLog.deleted_at.is_(None),
            )
            .order_by(RecoveryLog.date.desc())
            .limit(1)
        )
        last_recovery = (await session.execute(recovery_stmt)).scalar_one_or_none()

        if last_recovery:
            zone = "🟢" if last_recovery.recovery_score >= 67 else "🟡" if last_recovery.recovery_score >= 34 else "🔴"
            rec_parts = [f"WHOOP Recovery ({target_label}):"]
            rec_parts.append(f"  {zone} Score: {last_recovery.recovery_score:.0f}%")
            if last_recovery.hrv_ms is not None:
                rec_parts.append(f"  HRV: {last_recovery.hrv_ms:.1f} ms")
            if last_recovery.resting_hr is not None:
                rec_parts.append(f"  Пульс покоя: {last_recovery.resting_hr:.0f}")
            if last_recovery.spo2 is not None:
                rec_parts.append(f"  SpO2: {last_recovery.spo2:.0f}%")
            sections.append("\n".join(rec_parts))
        else:
            sections.append(f"WHOOP Recovery ({target_label}): нет данных.")

        # --- Активные наблюдения ---
        rules_stmt = (
            select(DerivedRule)
            .where(
                DerivedRule.user_id == user_id,
                DerivedRule.deleted_at.is_(None),
                DerivedRule.confidence >= 0.5,
            )
            .order_by(DerivedRule.confidence.desc())
            .limit(5)
        )
        rules = (await session.execute(rules_stmt)).scalars().all()

        if rules:
            r_lines = ["Наблюдения:"]
            for r in rules:
                r_lines.append(f"  • [{r.confidence:.0%}] {r.rule}")
            sections.append("\n".join(r_lines))

    return "\n\n".join(sections)


@function_tool
async def get_week_summary(weeks_ago: int = 0) -> str:
    """Формирует сводку за неделю: сон, питание, тренировки, самочувствие, дельта с прошлой неделей.

    Args:
        weeks_ago: 0 = текущая неделя (пн–сегодня), 1 = прошлая неделя и т.д.
    """
    user_id = get_user_id()
    today = today_msk()

    # Определяем границы недели (пн-вс)
    current_monday = today - timedelta(days=today.weekday())
    start = current_monday - timedelta(weeks=weeks_ago)
    end = start + timedelta(days=6)
    if end > today:
        end = today

    # Предыдущая неделя для дельты
    prev_start = start - timedelta(days=7)
    prev_end = start - timedelta(days=1)

    period_label = f"{start} — {end}"
    sections = [f"Сводка за неделю ({period_label}):"]

    async with async_session() as session:
        # === СОН ===
        async def _sleep_stats(d_from: date, d_to: date):
            stmt = select(
                func.count(SleepLog.id),
                func.avg(SleepLog.duration_minutes),
                func.min(SleepLog.duration_minutes),
                func.max(SleepLog.duration_minutes),
            ).where(
                SleepLog.user_id == user_id,
                SleepLog.date >= d_from,
                SleepLog.date <= d_to,
                SleepLog.deleted_at.is_(None),
                SleepLog.duration_minutes.isnot(None),
            )
            return (await session.execute(stmt)).one()

        s_count, s_avg, s_min, s_max = await _sleep_stats(start, end)
        ps_count, ps_avg, _, _ = await _sleep_stats(prev_start, prev_end)

        if s_count:
            delta = ""
            if ps_avg and s_avg:
                diff = s_avg - ps_avg
                sign = "+" if diff > 0 else ""
                delta = f" (дельта: {sign}{int(diff)} мин)"
            sections.append(
                f"\nСон ({s_count} записей):"
                f"\n  Среднее: {int(s_avg)} мин ({s_avg / 60:.1f}ч){delta}"
                f"\n  Мин: {s_min} мин, Макс: {s_max} мин"
            )
        else:
            sections.append("\nСон: нет записей.")

        # === ПИТАНИЕ ===
        async def _meal_stats(d_from: date, d_to: date):
            stmt = select(
                func.count(MealLog.id),
                func.sum(MealLog.calories),
                func.sum(MealLog.protein_g),
                func.count(func.distinct(MealLog.date)),
            ).where(
                MealLog.user_id == user_id,
                MealLog.date >= d_from,
                MealLog.date <= d_to,
                MealLog.deleted_at.is_(None),
            )
            return (await session.execute(stmt)).one()

        m_count, m_cal, m_prot, m_days = await _meal_stats(start, end)
        pm_count, pm_cal, pm_prot, pm_days = await _meal_stats(prev_start, prev_end)

        if m_count:
            avg_cal = int(m_cal / m_days) if m_cal and m_days else None
            avg_prot = round(m_prot / m_days, 1) if m_prot and m_days else None

            cal_delta = ""
            if avg_cal and pm_cal and pm_days:
                prev_avg_cal = int(pm_cal / pm_days)
                diff = avg_cal - prev_avg_cal
                sign = "+" if diff > 0 else ""
                cal_delta = f" (дельта: {sign}{diff} ккал)"

            meal_lines = [f"\nПитание ({m_count} записей за {m_days} дн.):"]
            if avg_cal:
                meal_lines.append(f"  Среднее: ~{avg_cal} ккал/день{cal_delta}")
            if avg_prot:
                meal_lines.append(f"  Белок: ~{avg_prot}г/день")

            total_days = (end - start).days + 1
            missing_days = total_days - m_days
            if missing_days > 0:
                meal_lines.append(f"  Пропущено дней: {missing_days}")

            sections.append("\n".join(meal_lines))
        else:
            sections.append("\nПитание: нет записей.")

        # === ТРЕНИРОВКИ ===
        async def _workout_stats(d_from: date, d_to: date):
            stmt = select(
                func.count(WorkoutLog.id),
                func.sum(WorkoutLog.duration_minutes),
            ).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date >= d_from,
                WorkoutLog.date <= d_to,
                WorkoutLog.deleted_at.is_(None),
            )
            return (await session.execute(stmt)).one()

        w_count, w_total_min = await _workout_stats(start, end)
        pw_count, _ = await _workout_stats(prev_start, prev_end)

        if w_count:
            w_delta = ""
            if pw_count is not None:
                diff = w_count - pw_count
                if diff != 0:
                    sign = "+" if diff > 0 else ""
                    w_delta = f" (дельта: {sign}{diff})"

            # Типы тренировок
            types_stmt = select(
                WorkoutLog.workout_type,
                func.count(WorkoutLog.id),
            ).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date >= start,
                WorkoutLog.date <= end,
                WorkoutLog.deleted_at.is_(None),
            ).group_by(WorkoutLog.workout_type)
            type_rows = (await session.execute(types_stmt)).all()
            types_str = ", ".join(f"{t}: {c}" for t, c in type_rows)

            sections.append(
                f"\nТренировки: {w_count} шт.{w_delta}"
                f"\n  Общее время: {w_total_min or '?'} мин"
                f"\n  Типы: {types_str}"
            )
        else:
            sections.append("\nТренировки: нет записей.")

        # === САМОЧУВСТВИЕ ===
        note_stmt = select(
            func.count(DailyNote.id),
            func.avg(DailyNote.energy_level),
            func.avg(DailyNote.stress_level),
        ).where(
            DailyNote.user_id == user_id,
            DailyNote.date >= start,
            DailyNote.date <= end,
            DailyNote.deleted_at.is_(None),
        )
        n_count, n_energy, n_stress = (await session.execute(note_stmt)).one()

        if n_count:
            note_lines = [f"\nСамочувствие ({n_count} записей):"]
            if n_energy:
                note_lines.append(f"  Средняя энергия: {n_energy:.1f}/10")
            if n_stress:
                note_lines.append(f"  Средний стресс: {n_stress:.1f}/10")
            sections.append("\n".join(note_lines))

    return "\n".join(sections)
