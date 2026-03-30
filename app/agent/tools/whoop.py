"""WHOOP tools — доступ к данным WHOOP через агента."""

import logging
from datetime import timedelta

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.logs import SleepLog, RecoveryLog, WorkoutLog, CycleLog
from app.models.whoop import WhoopConnection

logger = logging.getLogger(__name__)


@function_tool
async def get_whoop_status() -> str:
    """Проверяет, подключён ли WHOOP и когда была последняя синхронизация.
    Вызывай, когда пользователь спрашивает о статусе WHOOP или хочет подключить."""
    user_id = get_user_id()

    async with async_session() as session:
        conn = (await session.execute(
            select(WhoopConnection).where(
                WhoopConnection.user_id == user_id,
                WhoopConnection.is_active.is_(True),
            ).order_by(WhoopConnection.created_at.desc()).limit(1)
        )).scalar_one_or_none()

    if not conn:
        return "WHOOP не подключён. Пользователь может подключить через /whoop в Telegram."

    last_refresh = conn.last_refresh_at.strftime("%Y-%m-%d %H:%M") if conn.last_refresh_at else "никогда"
    return f"WHOOP подключён.\nПоследнее обновление токена: {last_refresh}."


@function_tool
async def sync_whoop_now(days: int = 7) -> str:
    """Синхронизирует данные из WHOOP за последние N дней (сон, recovery, тренировки).
    Вызывай, когда пользователь просит обновить данные WHOOP или говорит 'синхронизируй'."""
    user_id = get_user_id()

    from app.whoop.sync import sync_whoop_data
    result = await sync_whoop_data(user_id, days=days)
    return result


@function_tool
async def get_latest_whoop_metrics(days: int = 3) -> str:
    """Возвращает последние данные с WHOOP: recovery score, HRV, пульс покоя, SpO2,
    сон (стадии), тренировки со strain.
    Вызывай, когда пользователь спрашивает о recovery, HRV, strain, пульсе покоя, данных с браслета."""
    user_id = get_user_id()
    since = today_msk() - timedelta(days=days)
    lines = []

    async with async_session() as session:
        # Recovery
        recoveries = (await session.execute(
            select(RecoveryLog)
            .where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date >= since,
                RecoveryLog.source == "whoop_api",
                RecoveryLog.deleted_at.is_(None),
            )
            .order_by(RecoveryLog.date.desc())
        )).scalars().all()

        # Day Strain (cycles)
        cycles = (await session.execute(
            select(CycleLog)
            .where(
                CycleLog.user_id == user_id,
                CycleLog.date >= since,
                CycleLog.source == "whoop_api",
                CycleLog.deleted_at.is_(None),
            )
            .order_by(CycleLog.date.desc())
        )).scalars().all()

        if cycles:
            lines.append("=== Day Strain (WHOOP) ===")
            for c in cycles:
                parts = [f"  {c.date}:"]
                if c.day_strain is not None:
                    zone = "тяжёлый" if c.day_strain >= 14 else "средний" if c.day_strain >= 8 else "лёгкий"
                    parts.append(f"strain={c.day_strain:.1f} ({zone})")
                if c.kilojoules is not None:
                    parts.append(f"{round(c.kilojoules / 4.184)}ккал")
                if c.avg_hr is not None:
                    parts.append(f"avg HR={c.avg_hr:.0f}")
                lines.append(" ".join(parts))

        if recoveries:
            lines.append("=== Recovery (WHOOP) ===")
            for r in recoveries:
                parts = [f"  {r.date}:"]
                if r.recovery_score is not None:
                    parts.append(f"recovery={r.recovery_score:.0f}%")
                if r.hrv_ms is not None:
                    parts.append(f"HRV={r.hrv_ms:.1f}ms")
                if r.resting_hr is not None:
                    parts.append(f"пульс покоя={r.resting_hr:.0f}")
                if r.spo2 is not None:
                    parts.append(f"SpO2={r.spo2:.1f}%")
                if r.skin_temp_celsius is not None:
                    parts.append(f"t кожи={r.skin_temp_celsius:.1f}°C")
                lines.append(" ".join(parts))

        # Сон (WHOOP)
        sleeps = (await session.execute(
            select(SleepLog)
            .where(
                SleepLog.user_id == user_id,
                SleepLog.date >= since,
                SleepLog.source == "whoop_api",
                SleepLog.deleted_at.is_(None),
            )
            .order_by(SleepLog.date.desc())
        )).scalars().all()

        if sleeps:
            lines.append("\n=== Сон (WHOOP) ===")
            for s in sleeps:
                parts = [f"  {s.date}:"]
                if s.duration_minutes is not None:
                    h, m = divmod(s.duration_minutes, 60)
                    parts.append(f"всего={h}ч{m:02d}м")
                if s.deep_sleep_minutes is not None:
                    parts.append(f"глубокий={s.deep_sleep_minutes}м")
                if s.rem_sleep_minutes is not None:
                    parts.append(f"REM={s.rem_sleep_minutes}м")
                if s.sleep_score is not None:
                    parts.append(f"score={s.sleep_score:.0f}%")
                lines.append(" ".join(parts))

        # Тренировки (WHOOP)
        workouts = (await session.execute(
            select(WorkoutLog)
            .where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.date >= since,
                WorkoutLog.source == "whoop_api",
                WorkoutLog.deleted_at.is_(None),
            )
            .order_by(WorkoutLog.date.desc())
        )).scalars().all()

        if workouts:
            lines.append("\n=== Тренировки (WHOOP) ===")
            for w in workouts:
                parts = [f"  {w.date}: {w.workout_type or 'unknown'}"]
                if w.duration_minutes is not None:
                    parts.append(f"{w.duration_minutes}мин")
                if w.strain is not None:
                    parts.append(f"strain={w.strain:.1f}")
                if w.avg_hr is not None:
                    parts.append(f"avg HR={w.avg_hr:.0f}")
                if w.calories_burned is not None:
                    parts.append(f"{w.calories_burned:.0f}ккал")
                lines.append(" ".join(parts))

    if not lines:
        return "Данных WHOOP нет. Возможно, нужно синхронизировать (sync_whoop_now) или подключить WHOOP."

    return "\n".join(lines)
