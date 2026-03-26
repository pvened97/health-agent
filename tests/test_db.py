"""Iteration 2: Database-backed tests — sync, soft-delete, resurrect, quality rules, logs."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from tests.conftest import TEST_USER_ID


# ============================================================
# Save & read logs directly via ORM (tools use same DB layer)
# ============================================================
class TestSaveLogs:
    @pytest.mark.asyncio
    async def test_save_sleep_log(self, user_id, session):
        from app.models.logs import SleepLog
        log = SleepLog(
            user_id=user_id, date=date(2026, 3, 25),
            duration_minutes=480, quality="good",
        )
        session.add(log)
        await session.commit()

        result = (await session.execute(
            select(SleepLog).where(SleepLog.user_id == user_id)
        )).scalar_one()
        assert result.duration_minutes == 480
        assert result.quality == "good"

    @pytest.mark.asyncio
    async def test_save_meal_log(self, user_id, session):
        from app.models.logs import MealLog
        log = MealLog(
            user_id=user_id, date=date(2026, 3, 25),
            description="Chicken breast", calories=300, protein_g=50.0,
        )
        session.add(log)
        await session.commit()

        result = (await session.execute(
            select(MealLog).where(MealLog.user_id == user_id)
        )).scalar_one()
        assert result.calories == 300
        assert result.protein_g == 50.0

    @pytest.mark.asyncio
    async def test_save_workout_log(self, user_id, session):
        from app.models.logs import WorkoutLog
        log = WorkoutLog(
            user_id=user_id, date=date(2026, 3, 25),
            workout_type="strength", duration_minutes=60, intensity="high",
        )
        session.add(log)
        await session.commit()

        result = (await session.execute(
            select(WorkoutLog).where(WorkoutLog.user_id == user_id)
        )).scalar_one()
        assert result.workout_type == "strength"
        assert result.intensity == "high"

    @pytest.mark.asyncio
    async def test_save_note(self, user_id, session):
        from app.models.logs import DailyNote
        note = DailyNote(
            user_id=user_id, date=date(2026, 3, 25),
            text="Feeling great", energy_level=8,
        )
        session.add(note)
        await session.commit()

        result = (await session.execute(
            select(DailyNote).where(DailyNote.user_id == user_id)
        )).scalar_one()
        assert result.energy_level == 8


# ============================================================
# Soft delete — deleted_at excludes from active queries
# ============================================================
class TestSoftDelete:
    @pytest.mark.asyncio
    async def test_soft_deleted_meal_not_in_active_query(self, user_id, session):
        from app.models.logs import MealLog
        log = MealLog(
            user_id=user_id, date=date(2026, 3, 25),
            description="Deleted meal", calories=100,
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.commit()

        result = (await session.execute(
            select(MealLog).where(
                MealLog.user_id == user_id,
                MealLog.deleted_at.is_(None),
            )
        )).scalars().all()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_soft_deleted_still_exists_without_filter(self, user_id, session):
        from app.models.logs import MealLog
        log = MealLog(
            user_id=user_id, date=date(2026, 3, 25),
            description="Deleted meal", calories=100,
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.commit()

        result = (await session.execute(
            select(MealLog).where(MealLog.user_id == user_id)
        )).scalars().all()
        assert len(result) == 1


# ============================================================
# WHOOP sync — resurrect pattern
# ============================================================
class TestSyncResurrect:
    @pytest.mark.asyncio
    async def test_sleep_sync_creates_record(self, user_id, session):
        from app.whoop.sync import _sync_sleep
        records = [{
            "id": "test-sleep-001",
            "nap": False,
            "score_state": "SCORED",
            "start": "2026-03-24T21:00:00.000Z",
            "end": "2026-03-25T05:00:00.000Z",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 7_200_000,
                    "total_rem_sleep_time_milli": 5_400_000,
                    "total_awake_time_milli": 1_800_000,
                },
                "sleep_performance_percentage": 92.0,
            },
        }]
        synced = await _sync_sleep(user_id, records)
        assert synced == 1

        from app.models.logs import SleepLog
        log = (await session.execute(
            select(SleepLog).where(SleepLog.external_id == "whoop_sleep_test-sleep-001")
        )).scalar_one()
        assert log.duration_minutes == 480  # 8h from total_in_bed_time
        assert log.deleted_at is None

    @pytest.mark.asyncio
    async def test_sleep_sync_skips_existing_active(self, user_id):
        from app.whoop.sync import _sync_sleep
        records = [{
            "id": "test-sleep-002",
            "nap": False,
            "score_state": "SCORED",
            "start": "2026-03-24T21:00:00.000Z",
            "end": "2026-03-25T05:00:00.000Z",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 7_200_000,
                    "total_rem_sleep_time_milli": 5_400_000,
                    "total_awake_time_milli": 1_800_000,
                },
                "sleep_performance_percentage": 90.0,
            },
        }]
        await _sync_sleep(user_id, records)
        synced2 = await _sync_sleep(user_id, records)
        assert synced2 == 0

    @pytest.mark.asyncio
    async def test_sleep_sync_resurrects_soft_deleted(self, user_id, session):
        from app.models.logs import SleepLog
        from app.whoop.sync import _sync_sleep

        log = SleepLog(
            user_id=user_id, date=date(2026, 3, 25),
            duration_minutes=300, source="whoop_api",
            external_id="whoop_sleep_test-sleep-003",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.commit()
        original_id = log.id

        records = [{
            "id": "test-sleep-003",
            "nap": False,
            "score_state": "SCORED",
            "start": "2026-03-24T22:00:00.000Z",
            "end": "2026-03-25T06:00:00.000Z",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 7_200_000,
                    "total_rem_sleep_time_milli": 5_400_000,
                    "total_awake_time_milli": 1_800_000,
                },
                "sleep_performance_percentage": 95.0,
            },
        }]
        synced = await _sync_sleep(user_id, records)
        assert synced == 1

        # Expire cached state so we re-read from DB
        session.expire_all()
        resurrected = (await session.execute(
            select(SleepLog).where(SleepLog.external_id == "whoop_sleep_test-sleep-003")
        )).scalar_one()
        assert resurrected.id == original_id
        assert resurrected.deleted_at is None
        assert resurrected.duration_minutes == 480

    @pytest.mark.asyncio
    async def test_sleep_duration_uses_in_bed_time(self, user_id, session):
        """duration_minutes should be total_in_bed_time, not sum of sleep stages."""
        from app.whoop.sync import _sync_sleep
        records = [{
            "id": "test-sleep-duration",
            "nap": False,
            "score_state": "SCORED",
            "start": "2026-03-24T23:00:00.000Z",
            "end": "2026-03-25T07:00:00.000Z",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,  # 480 min
                    "total_light_sleep_time_milli": 10_800_000,  # 180 min
                    "total_slow_wave_sleep_time_milli": 5_400_000,  # 90 min
                    "total_rem_sleep_time_milli": 3_600_000,  # 60 min
                    "total_awake_time_milli": 9_000_000,  # 150 min awake!
                },
                "sleep_performance_percentage": 60.0,
            },
        }]
        await _sync_sleep(user_id, records)

        from app.models.logs import SleepLog
        log = (await session.execute(
            select(SleepLog).where(SleepLog.external_id == "whoop_sleep_test-sleep-duration")
        )).scalar_one()
        # Should be 480 (in-bed), NOT 330 (light+deep+rem)
        assert log.duration_minutes == 480

    @pytest.mark.asyncio
    async def test_sleep_date_uses_wake_time_msk(self, user_id, session):
        """Sleep date should be based on wake-up time in Moscow timezone."""
        from app.whoop.sync import _sync_sleep
        # Sleep starts Mar 24 at 20:00 UTC (23:00 MSK) → wake Mar 25 at 04:00 UTC (07:00 MSK)
        records = [{
            "id": "test-sleep-date",
            "nap": False,
            "score_state": "SCORED",
            "start": "2026-03-24T20:00:00.000Z",
            "end": "2026-03-25T04:00:00.000Z",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,
                    "total_light_sleep_time_milli": 14_400_000,
                    "total_slow_wave_sleep_time_milli": 7_200_000,
                    "total_rem_sleep_time_milli": 5_400_000,
                    "total_awake_time_milli": 1_800_000,
                },
                "sleep_performance_percentage": 90.0,
            },
        }]
        await _sync_sleep(user_id, records)

        from app.models.logs import SleepLog
        log = (await session.execute(
            select(SleepLog).where(SleepLog.external_id == "whoop_sleep_test-sleep-date")
        )).scalar_one()
        # Wake at 04:00 UTC = 07:00 MSK on Mar 25
        assert log.date == date(2026, 3, 25)

    @pytest.mark.asyncio
    async def test_recovery_sync_resurrects(self, user_id, session):
        from app.models.logs import RecoveryLog
        from app.whoop.sync import _sync_recovery

        log = RecoveryLog(
            user_id=user_id, date=date(2026, 3, 24),
            recovery_score=50.0, source="whoop_api",
            external_id="whoop_recovery_999",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.commit()

        records = [{
            "cycle_id": 999,
            "score_state": "SCORED",
            "created_at": "2026-03-25T06:00:00.000Z",
            "score": {
                "recovery_score": 72.0,
                "hrv_rmssd_milli": 85.5,
                "resting_heart_rate": 52.0,
                "spo2_percentage": 97.0,
                "skin_temp_celsius": 35.1,
            },
        }]
        synced = await _sync_recovery(user_id, records)
        assert synced == 1

        session.expire_all()
        resurrected = (await session.execute(
            select(RecoveryLog).where(RecoveryLog.external_id == "whoop_recovery_999")
        )).scalar_one()
        assert resurrected.deleted_at is None
        assert resurrected.recovery_score == 72.0

    @pytest.mark.asyncio
    async def test_workout_sync_resurrects(self, user_id, session):
        from app.models.logs import WorkoutLog
        from app.whoop.sync import _sync_workouts

        log = WorkoutLog(
            user_id=user_id, date=date(2026, 3, 24),
            workout_type="yoga", source="whoop_api",
            external_id="whoop_workout_test-w-001",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(log)
        await session.commit()

        records = [{
            "id": "test-w-001",
            "score_state": "SCORED",
            "sport_name": "Strength Training",
            "start": "2026-03-25T10:00:00.000Z",
            "end": "2026-03-25T11:00:00.000Z",
            "score": {
                "strain": 12.5,
                "average_heart_rate": 135,
                "max_heart_rate": 170,
                "kilojoule": 1500,
            },
        }]
        synced = await _sync_workouts(user_id, records)
        assert synced == 1

        session.expire_all()
        resurrected = (await session.execute(
            select(WorkoutLog).where(WorkoutLog.external_id == "whoop_workout_test-w-001")
        )).scalar_one()
        assert resurrected.deleted_at is None
        assert resurrected.workout_type == "Strength Training"
        assert resurrected.strain == 12.5

    @pytest.mark.asyncio
    async def test_sync_skips_naps(self, user_id):
        from app.whoop.sync import _sync_sleep
        records = [{
            "id": "test-nap",
            "nap": True,
            "score_state": "SCORED",
            "start": "2026-03-25T12:00:00.000Z",
            "end": "2026-03-25T12:30:00.000Z",
            "score": {"stage_summary": {}, "sleep_performance_percentage": 50.0},
        }]
        synced = await _sync_sleep(user_id, records)
        assert synced == 0

    @pytest.mark.asyncio
    async def test_sync_skips_unscored(self, user_id):
        from app.whoop.sync import _sync_sleep
        records = [{
            "id": "test-pending",
            "nap": False,
            "score_state": "PENDING_SCORE",
            "start": "2026-03-24T21:00:00.000Z",
            "end": "2026-03-25T05:00:00.000Z",
            "score": {},
        }]
        synced = await _sync_sleep(user_id, records)
        assert synced == 0


# ============================================================
# Profile via ORM
# ============================================================
class TestProfile:
    @pytest.mark.asyncio
    async def test_save_and_read_profile(self, user_id, session):
        from app.models.memory import UserProfile
        profile = UserProfile(
            user_id=user_id, category="goals",
            key="daily_calories", value="2500", confirmed=True,
        )
        session.add(profile)
        await session.commit()

        result = (await session.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
                UserProfile.deleted_at.is_(None),
            )
        )).scalar_one()
        assert result.value == "2500"

    @pytest.mark.asyncio
    async def test_soft_deleted_profile_excluded(self, user_id, session):
        from app.models.memory import UserProfile
        profile = UserProfile(
            user_id=user_id, category="restrictions",
            key="allergy", value="nuts", confirmed=True,
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(profile)
        await session.commit()

        result = (await session.execute(
            select(UserProfile).where(
                UserProfile.user_id == user_id,
                UserProfile.deleted_at.is_(None),
            )
        )).scalars().all()
        assert len(result) == 0


# ============================================================
# Quality rules (async, needs DB)
# ============================================================
class TestQualityRules:
    @pytest.mark.asyncio
    async def test_short_sleep_streak_warning(self, user_id, session, monkeypatch):
        from app.models.logs import SleepLog
        from app.quality.rules import check_all_rules

        monkeypatch.setattr("app.quality.rules.today_msk", lambda: date(2026, 3, 25))

        for days_ago in range(3):
            session.add(SleepLog(
                user_id=user_id,
                date=date(2026, 3, 25) - timedelta(days=days_ago),
                duration_minutes=300,
            ))
        await session.commit()

        warnings = await check_all_rules(user_id)
        critical = [w for w in warnings if w.severity == "critical"]
        assert len(critical) >= 1
        assert "менее 6 часов" in critical[0].message

    @pytest.mark.asyncio
    async def test_no_warning_with_good_sleep(self, user_id, session, monkeypatch):
        from app.models.logs import SleepLog
        from app.quality.rules import check_all_rules

        monkeypatch.setattr("app.quality.rules.today_msk", lambda: date(2026, 3, 25))

        for days_ago in range(3):
            session.add(SleepLog(
                user_id=user_id,
                date=date(2026, 3, 25) - timedelta(days=days_ago),
                duration_minutes=480,
            ))
        # Add enough meals to avoid "low data" warning
        from app.models.logs import MealLog
        for days_ago in range(5):
            session.add(MealLog(
                user_id=user_id,
                date=date(2026, 3, 25) - timedelta(days=days_ago),
                description="Lunch", calories=500,
            ))
        await session.commit()

        warnings = await check_all_rules(user_id)
        critical = [w for w in warnings if w.severity == "critical"]
        assert len(critical) == 0

    @pytest.mark.asyncio
    async def test_low_data_warning(self, user_id, session, monkeypatch):
        from app.quality.rules import check_all_rules
        monkeypatch.setattr("app.quality.rules.today_msk", lambda: date(2026, 3, 25))

        warnings = await check_all_rules(user_id)
        info = [w for w in warnings if w.severity == "info"]
        assert len(info) >= 1
        assert "Мало данных" in info[0].message

    @pytest.mark.asyncio
    async def test_heavy_workout_warning(self, user_id, session, monkeypatch):
        from app.models.logs import WorkoutLog, MealLog, SleepLog
        from app.quality.rules import check_all_rules

        monkeypatch.setattr("app.quality.rules.today_msk", lambda: date(2026, 3, 25))

        # Heavy workout yesterday
        session.add(WorkoutLog(
            user_id=user_id, date=date(2026, 3, 24),
            workout_type="strength", intensity="high",
        ))
        # Enough data to avoid low data warning
        for days_ago in range(5):
            session.add(SleepLog(
                user_id=user_id,
                date=date(2026, 3, 25) - timedelta(days=days_ago),
                duration_minutes=480,
            ))
            session.add(MealLog(
                user_id=user_id,
                date=date(2026, 3, 25) - timedelta(days=days_ago),
                description="Meal", calories=500,
            ))
        await session.commit()

        warnings = await check_all_rules(user_id)
        workout_warnings = [w for w in warnings if w.severity == "warning"]
        assert len(workout_warnings) >= 1
        assert "тяжёлая тренировка" in workout_warnings[0].message
