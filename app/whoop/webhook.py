"""WHOOP webhook handler — приём реалтайм-событий от WHOOP."""

import base64
import hashlib
import hmac
import logging

from app.config import settings
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
            # For recovery, id is the sleep UUID — fetch recovery by cycle
            record = await client.get_recovery_by_cycle_id(object_id)
            synced = await _sync_recovery(user_id, [record])
            logger.info("WHOOP webhook synced recovery: %d records", synced)

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
