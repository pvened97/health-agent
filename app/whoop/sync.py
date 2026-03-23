"""Синхронизация данных из WHOOP v2 API в локальную БД."""

import logging
import uuid
from datetime import datetime, timedelta, timezone, date

from app.config import today_msk

from sqlalchemy import select

from app.database import async_session
from app.models.logs import SleepLog, RecoveryLog, WorkoutLog
from app.models.whoop import SyncEvent
from app.whoop.client import get_whoop_client

logger = logging.getLogger(__name__)


def _ms_to_minutes(ms: int | None) -> int | None:
    if ms is None:
        return None
    return round(ms / 60_000)


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def sync_whoop_data(user_id: uuid.UUID, days: int = 7) -> str:
    """Синхронизирует сон, recovery и тренировки из WHOOP за последние N дней."""
    client = await get_whoop_client(user_id)
    if not client:
        return "WHOOP не подключён."

    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    results = []

    # --- Сон ---
    try:
        sleep_records = await client.get_sleep(start_date=start, limit=25)
        sleep_synced = await _sync_sleep(user_id, sleep_records)
        results.append(f"Сон: {sleep_synced} записей")
    except Exception as e:
        logger.exception("WHOOP sleep sync error")
        results.append(f"Сон: ошибка ({e})")

    # --- Recovery ---
    try:
        recovery_records = await client.get_recovery(start_date=start, limit=25)
        recovery_synced = await _sync_recovery(user_id, recovery_records)
        results.append(f"Recovery: {recovery_synced} записей")
    except Exception as e:
        logger.exception("WHOOP recovery sync error")
        results.append(f"Recovery: ошибка ({e})")

    # --- Тренировки ---
    try:
        workouts = await client.get_workouts(start_date=start, limit=25)
        workout_synced = await _sync_workouts(user_id, workouts)
        results.append(f"Тренировки: {workout_synced} записей")
    except Exception as e:
        logger.exception("WHOOP workout sync error")
        results.append(f"Тренировки: ошибка ({e})")

    # Записываем sync event
    async with async_session() as session:
        synced_count = 0
        for r in results:
            if "ошибка" not in r:
                try:
                    synced_count += int(r.split(":")[1].strip().split()[0])
                except (IndexError, ValueError):
                    pass
        event = SyncEvent(
            user_id=user_id,
            sync_type="manual",
            data_type="all",
            status="completed",
            records_synced=synced_count,
            completed_at=datetime.now(timezone.utc),
        )
        session.add(event)
        await session.commit()

    return "Синхронизация завершена.\n" + "\n".join(results)


async def _sync_sleep(user_id: uuid.UUID, records: list[dict]) -> int:
    """Сохраняет записи сна из WHOOP."""
    synced = 0
    async with async_session() as session:
        for rec in records:
            if rec.get("nap"):
                continue
            if rec.get("score_state") != "SCORED":
                continue

            # v2 uses UUID string IDs
            external_id = f"whoop_sleep_{rec['id']}"

            existing = await session.execute(
                select(SleepLog).where(
                    SleepLog.external_id == external_id,
                    SleepLog.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                continue

            score = rec.get("score", {})
            stage = score.get("stage_summary", {})

            start_dt = _parse_iso(rec.get("start"))
            end_dt = _parse_iso(rec.get("end"))

            total_sleep_ms = (
                (stage.get("total_light_sleep_time_milli") or 0)
                + (stage.get("total_slow_wave_sleep_time_milli") or 0)
                + (stage.get("total_rem_sleep_time_milli") or 0)
            )

            # Дата сна = дата пробуждения (а не засыпания)
            from zoneinfo import ZoneInfo
            msk = ZoneInfo("Europe/Moscow")
            if end_dt:
                sleep_date = end_dt.astimezone(msk).date()
            elif start_dt:
                sleep_date = start_dt.astimezone(msk).date()
            else:
                sleep_date = today_msk()

            log = SleepLog(
                user_id=user_id,
                date=sleep_date,
                bed_time=start_dt,
                wake_time=end_dt,
                duration_minutes=_ms_to_minutes(total_sleep_ms) if total_sleep_ms else None,
                deep_sleep_minutes=_ms_to_minutes(stage.get("total_slow_wave_sleep_time_milli")),
                rem_sleep_minutes=_ms_to_minutes(stage.get("total_rem_sleep_time_milli")),
                light_sleep_minutes=_ms_to_minutes(stage.get("total_light_sleep_time_milli")),
                awake_minutes=_ms_to_minutes(stage.get("total_awake_time_milli")),
                sleep_score=score.get("sleep_performance_percentage"),
                source="whoop_api",
                external_id=external_id,
                last_synced_at=datetime.now(timezone.utc),
            )
            session.add(log)
            synced += 1

        await session.commit()
    return synced


async def _sync_recovery(user_id: uuid.UUID, records: list[dict]) -> int:
    """Сохраняет recovery данные из WHOOP."""
    synced = 0
    async with async_session() as session:
        for rec in records:
            if rec.get("score_state") != "SCORED":
                continue

            score = rec.get("score", {})
            if not score:
                continue

            # Recovery uses cycle_id as identifier
            external_id = f"whoop_recovery_{rec['cycle_id']}"

            existing = await session.execute(
                select(RecoveryLog).where(
                    RecoveryLog.external_id == external_id,
                    RecoveryLog.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                continue

            created = _parse_iso(rec.get("created_at"))
            from zoneinfo import ZoneInfo
            msk = ZoneInfo("Europe/Moscow")

            log = RecoveryLog(
                user_id=user_id,
                date=created.astimezone(msk).date() if created else today_msk(),
                recovery_score=score.get("recovery_score"),
                hrv_ms=score.get("hrv_rmssd_milli"),
                resting_hr=score.get("resting_heart_rate"),
                spo2=score.get("spo2_percentage"),
                skin_temp_celsius=score.get("skin_temp_celsius"),
                source="whoop_api",
                external_id=external_id,
                last_synced_at=datetime.now(timezone.utc),
            )
            session.add(log)
            synced += 1

        await session.commit()
    return synced


async def _sync_workouts(user_id: uuid.UUID, records: list[dict]) -> int:
    """Сохраняет тренировки из WHOOP."""
    synced = 0
    async with async_session() as session:
        for rec in records:
            if rec.get("score_state") != "SCORED":
                continue

            # v2 uses UUID string IDs
            external_id = f"whoop_workout_{rec['id']}"

            existing = await session.execute(
                select(WorkoutLog).where(
                    WorkoutLog.external_id == external_id,
                    WorkoutLog.deleted_at.is_(None),
                )
            )
            if existing.scalar_one_or_none():
                continue

            score = rec.get("score", {})
            start_dt = _parse_iso(rec.get("start"))
            end_dt = _parse_iso(rec.get("end"))

            duration = None
            if start_dt and end_dt:
                duration = round((end_dt - start_dt).total_seconds() / 60)

            strain = score.get("strain")
            intensity = None
            if strain is not None:
                if strain >= 14:
                    intensity = "high"
                elif strain >= 8:
                    intensity = "medium"
                else:
                    intensity = "low"

            from zoneinfo import ZoneInfo
            msk = ZoneInfo("Europe/Moscow")

            log = WorkoutLog(
                user_id=user_id,
                date=start_dt.astimezone(msk).date() if start_dt else today_msk(),
                started_at=start_dt,
                ended_at=end_dt,
                duration_minutes=duration,
                workout_type=rec.get("sport_name", "unknown"),
                intensity=intensity,
                avg_hr=score.get("average_heart_rate"),
                max_hr=score.get("max_heart_rate"),
                calories_burned=round(score["kilojoule"] / 4.184) if score.get("kilojoule") else None,
                strain=strain,
                source="whoop_api",
                external_id=external_id,
                last_synced_at=datetime.now(timezone.utc),
            )
            session.add(log)
            synced += 1

        await session.commit()
    return synced
