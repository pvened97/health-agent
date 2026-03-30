"""WHOOP OAuth 2.0 flow — авторизация и обмен токенов."""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.whoop import WhoopConnection

logger = logging.getLogger(__name__)

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = "read:profile read:body_measurement read:cycles read:recovery read:sleep read:workout offline"


_pending_states: dict[str, str] = {}


def get_authorization_url(user_id: str = "") -> str:
    """Возвращает URL для авторизации пользователя в WHOOP."""
    state = secrets.token_urlsafe(32)
    _pending_states[state] = str(user_id) if user_id else ""
    params = {
        "client_id": settings.whoop_client_id,
        "redirect_uri": settings.whoop_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def validate_state(state: str) -> str | None:
    """Проверяет и удаляет state из pending. Возвращает user_id или None."""
    return _pending_states.pop(state, None)


async def exchange_code_for_tokens(code: str, user_id) -> WhoopConnection:
    """Обменивает authorization code на access/refresh токены."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.whoop_redirect_uri,
                "client_id": settings.whoop_client_id,
                "client_secret": settings.whoop_client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

    # Получаем WHOOP user_id из профиля
    whoop_user_id = None
    try:
        async with httpx.AsyncClient() as client:
            profile_resp = await client.get(
                "https://api.prod.whoop.com/developer/v2/user/profile/basic",
                headers={"Authorization": f"Bearer {data['access_token']}"},
                timeout=10,
            )
            if profile_resp.status_code == 200:
                whoop_user_id = str(profile_resp.json().get("user_id", ""))
    except Exception:
        logger.warning("Could not fetch WHOOP user_id from profile")

    async with async_session() as session:
        # Ищем существующее подключение (берём последнее, дубли удаляем)
        stmt = select(WhoopConnection).where(
            WhoopConnection.user_id == user_id
        ).order_by(WhoopConnection.created_at.desc())
        all_conns = (await session.execute(stmt)).scalars().all()
        conn = all_conns[0] if all_conns else None
        for dup in all_conns[1:]:
            await session.delete(dup)

        if conn:
            conn.access_token = data["access_token"]
            conn.refresh_token = data["refresh_token"]
            conn.token_expires_at = expires_at
            conn.scopes = data.get("scope", SCOPES)
            conn.is_active = True
            conn.last_refresh_at = datetime.now(timezone.utc)
            if whoop_user_id:
                conn.whoop_user_id = whoop_user_id
        else:
            conn = WhoopConnection(
                user_id=user_id,
                whoop_user_id=whoop_user_id,
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                token_expires_at=expires_at,
                scopes=data.get("scope", SCOPES),
                is_active=True,
            )
            session.add(conn)

        await session.commit()
        await session.refresh(conn)

    logger.info("WHOOP tokens saved for user %s", user_id)
    return conn


async def refresh_access_token(conn: WhoopConnection) -> WhoopConnection:
    """Обновляет access token через refresh token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": conn.refresh_token,
                "client_id": settings.whoop_client_id,
                "client_secret": settings.whoop_client_secret,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

    async with async_session() as session:
        stmt = select(WhoopConnection).where(WhoopConnection.id == conn.id)
        conn = (await session.execute(stmt)).scalar_one()
        conn.access_token = data["access_token"]
        conn.refresh_token = data["refresh_token"]
        conn.token_expires_at = expires_at
        conn.last_refresh_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(conn)

    logger.info("WHOOP token refreshed for connection %s", conn.id)
    return conn
