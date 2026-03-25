"""WHOOP webhook handler — приём реалтайм-событий от WHOOP."""

import base64
import hashlib
import hmac
import logging

from sqlalchemy import select
from telegram import Bot
from telegram.constants import ParseMode

from app.config import settings, today_msk
from app.database import async_session
from app.models.agent import AgentRun
from app.models.logs import SleepLog, RecoveryLog
from app.models.user import TelegramAccount
from app.telegram.handlers import _md_to_html
from app.whoop.client import get_whoop_client_by_whoop_user_id
from app.whoop.sync import _sync_sleep, _sync_recovery, _sync_workouts

logger = logging.getLogger(__name__)


def verify_signature(raw_body: bytes, signature: str, timestamp: str) -> bool:
    """Проверяет подпись WHOOP webhook."""
    if not settings.whoop_client_secret:
        return False
    message = timestamp.encode() + raw_body
    expected = base64.b64encode(
        hmac.new(
            settings.whoop_client_secret.encode(),
            message,
            hashlib.sha256,
        ).digest()
    ).decode()
    return hmac.compare_digest(expected, signature)


async def _trigger_morning_checkin(user_id: int) -> None:
    """Отправляет утреннюю сводку после получения recovery данных."""
    today = today_msk()

    async with async_session() as session:
        # Проверяем: есть ли сон и recovery за сегодня
        sleep = (await session.execute(
            select(SleepLog.id).where(
                SleepLog.user_id == user_id,
                SleepLog.date == today,
                SleepLog.source == "whoop_api",
                SleepLog.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

        recovery = (await session.execute(
            select(RecoveryLog.id).where(
                RecoveryLog.user_id == user_id,
                RecoveryLog.date == today,
                RecoveryLog.source == "whoop_api",
                RecoveryLog.deleted_at.is_(None),
            )
        )).scalar_one_or_none()

        if not sleep or not recovery:
            logger.info("Morning checkin skipped: sleep=%s, recovery=%s", bool(sleep), bool(recovery))
            return

        # Проверяем что сводку сегодня ещё не отправляли (по agent_runs)
        already_sent = (await session.execute(
            select(AgentRun.id).where(
                AgentRun.user_id == user_id,
                AgentRun.trigger == "recovery_webhook",
                AgentRun.created_at >= today.isoformat(),
            ).limit(1)
        )).scalar_one_or_none()

        if already_sent:
            logger.info("Morning checkin already sent today for user %s", user_id)
            return

        # Получаем chat_id
        tg = (await session.execute(
            select(TelegramAccount).where(TelegramAccount.user_id == user_id)
        )).scalar_one_or_none()

    if not tg or not tg.chat_id:
        return

    try:
        from app.agent.agent import run_agent
        response = await run_agent(
            "Дай краткую утреннюю сводку: мой recovery, как я спал, и рекомендацию на день. Коротко, 3-5 предложений.",
            user_id=user_id,
            trigger="recovery_webhook",
        )

        bot = Bot(token=settings.telegram_bot_token)
        try:
            await bot.send_message(
                chat_id=tg.chat_id,
                text=_md_to_html(response),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await bot.send_message(chat_id=tg.chat_id, text=response)

        logger.info("Morning checkin sent to user %s (triggered by recovery webhook)", user_id)
    except Exception:
        logger.exception("Morning checkin failed for user %s", user_id)


async def handle_webhook(payload: dict) -> str:
    """Обрабатывает входящий webhook от WHOOP.

    Payload: {"user_id": 10129, "id": "uuid", "type": "recovery.updated", "trace_id": "..."}
    """
    event_type = payload.get("type", "")
    whoop_user_id = payload.get("user_id")
    object_id = str(payload.get("id", ""))
    trace_id = payload.get("trace_id", "")

    logger.info("WHOOP webhook: type=%s, user=%s, id=%s, trace=%s", event_type, whoop_user_id, object_id, trace_id)

    if not whoop_user_id:
        return "missing user_id"

    # Находим нашего пользователя по WHOOP user_id
    client, conn = await get_whoop_client_by_whoop_user_id(whoop_user_id)
    if not client or not conn:
        logger.warning("WHOOP webhook: no connection for whoop_user_id=%s", whoop_user_id)
        return "user not found"

    user_id = conn.user_id

    try:
        if event_type == "sleep.updated":
            record = await client.get_sleep_by_id(object_id)
            synced = await _sync_sleep(user_id, [record])
            logger.info("WHOOP webhook synced sleep: %d records", synced)

        elif event_type == "recovery.updated":
            # Webhook id = sleep UUID, но recovery API требует cycle_id.
            # Берём последний recovery через list endpoint.
            records = await client.get_recovery(limit=1)
            if records:
                synced = await _sync_recovery(user_id, records)
                logger.info("WHOOP webhook synced recovery: %d records", synced)
            else:
                synced = 0
                logger.warning("WHOOP webhook: no recovery records returned")

            # Триггерим утреннюю сводку — recovery пришёл, значит данные готовы
            if synced:
                await _trigger_morning_checkin(user_id)

        elif event_type == "workout.updated":
            record = await client.get_workout_by_id(object_id)
            synced = await _sync_workouts(user_id, [record])
            logger.info("WHOOP webhook synced workout: %d records", synced)

        elif event_type.endswith(".deleted"):
            logger.info("WHOOP webhook: delete event ignored (soft delete not implemented)")

        else:
            logger.warning("WHOOP webhook: unknown event type %s", event_type)

    except Exception:
        logger.exception("WHOOP webhook processing error for %s", event_type)

    return "ok"
