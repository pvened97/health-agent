"""WHOOP API v2 client — получение данных сна, recovery, тренировок."""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.database import async_session
from app.models.whoop import WhoopConnection
from app.whoop.oauth import refresh_access_token

logger = logging.getLogger(__name__)

BASE_URL = "https://api.prod.whoop.com/developer/v2"


class WhoopClient:
    def __init__(self, connection: WhoopConnection):
        self._conn = connection

    async def _ensure_valid_token(self) -> str:
        """Проверяет и обновляет токен при необходимости."""
        if self._conn.token_expires_at and self._conn.token_expires_at < datetime.now(timezone.utc) + timedelta(minutes=5):
            logger.info("WHOOP token expiring soon, refreshing...")
            self._conn = await refresh_access_token(self._conn)
        return self._conn.access_token

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """GET-запрос к WHOOP API с автоматическим refresh."""
        token = await self._ensure_valid_token()

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=30,
            )

            if resp.status_code == 401:
                logger.warning("WHOOP 401, attempting token refresh")
                self._conn = await refresh_access_token(self._conn)
                resp = await client.get(
                    f"{BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {self._conn.access_token}"},
                    params=params,
                    timeout=30,
                )

            resp.raise_for_status()
            return resp.json()

    # --- Profile ---

    async def get_profile(self) -> dict:
        """Получает профиль пользователя WHOOP."""
        return await self._get("/user/profile/basic")

    async def get_body_measurement(self) -> dict:
        """Получает антропометрию из WHOOP."""
        return await self._get("/user/measurement/body")

    # --- Cycles ---

    async def get_cycles(self, start_date: str | None = None, end_date: str | None = None, limit: int = 25) -> list[dict]:
        """Получает cycles (strain, kilojoule, HR).
        Даты в формате YYYY-MM-DDTHH:MM:SS.000Z"""
        params = {"limit": limit}
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        data = await self._get("/cycle", params)
        return data.get("records", [])

    # --- Recovery ---

    async def get_recovery(self, start_date: str | None = None, end_date: str | None = None, limit: int = 25) -> list[dict]:
        """Получает recovery данные (score, HRV, resting HR, SpO2).
        Даты в формате YYYY-MM-DDTHH:MM:SS.000Z"""
        params = {"limit": limit}
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        data = await self._get("/recovery", params)
        return data.get("records", [])

    # --- Sleep ---

    async def get_sleep(self, start_date: str | None = None, end_date: str | None = None, limit: int = 25) -> list[dict]:
        """Получает записи сна (стадии, длительность, score).
        Даты в формате YYYY-MM-DDTHH:MM:SS.000Z"""
        params = {"limit": limit}
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        data = await self._get("/activity/sleep", params)
        return data.get("records", [])

    # --- Workouts ---

    async def get_workouts(self, start_date: str | None = None, end_date: str | None = None, limit: int = 25) -> list[dict]:
        """Получает записи тренировок (тип, strain, HR, калории).
        Даты в формате YYYY-MM-DDTHH:MM:SS.000Z"""
        params = {"limit": limit}
        if start_date:
            params["start"] = start_date
        if end_date:
            params["end"] = end_date
        data = await self._get("/activity/workout", params)
        return data.get("records", [])


    async def get_sleep_by_id(self, sleep_id: str) -> dict:
        """Получает одну запись сна по ID."""
        return await self._get(f"/activity/sleep/{sleep_id}")

    async def get_workout_by_id(self, workout_id: str) -> dict:
        """Получает одну тренировку по ID."""
        return await self._get(f"/activity/workout/{workout_id}")

    async def get_recovery_by_cycle_id(self, cycle_id: str) -> dict:
        """Получает recovery по cycle ID."""
        return await self._get(f"/recovery/{cycle_id}")


async def get_whoop_client(user_id) -> WhoopClient | None:
    """Создаёт WhoopClient для пользователя, если есть активное подключение."""
    async with async_session() as session:
        stmt = select(WhoopConnection).where(
            WhoopConnection.user_id == user_id,
            WhoopConnection.is_active.is_(True),
        ).order_by(WhoopConnection.created_at.desc()).limit(1)
        conn = (await session.execute(stmt)).scalar_one_or_none()

    if not conn:
        return None

    return WhoopClient(conn)


async def get_whoop_client_by_whoop_user_id(whoop_user_id: int) -> tuple[WhoopClient, WhoopConnection] | tuple[None, None]:
    """Находит WhoopClient по WHOOP user_id (из webhook payload)."""
    async with async_session() as session:
        stmt = select(WhoopConnection).where(
            WhoopConnection.whoop_user_id == str(whoop_user_id),
            WhoopConnection.is_active.is_(True),
        ).order_by(WhoopConnection.created_at.desc()).limit(1)
        conn = (await session.execute(stmt)).scalar_one_or_none()

    if not conn:
        return None, None

    return WhoopClient(conn), conn
