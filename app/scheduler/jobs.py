"""Фоновые задачи: token refresh, nightly sync, проактивные сообщения."""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, func
from telegram.constants import ParseMode

from app.config import today_msk
from app.database import async_session
from app.models.user import User, TelegramAccount
from app.models.logs import SleepLog, MealLog, WorkoutLog
from app.models.whoop import WhoopConnection
from app.telegram.handlers import _md_to_html

logger = logging.getLogger(__name__)


async def _send_html(bot, chat_id: int, text: str) -> None:
    """Отправляет сообщение в Telegram с HTML-форматированием, fallback на plain text."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_md_to_html(text),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await bot.send_message(chat_id=chat_id, text=text)


async def _get_users_with_telegram() -> list[tuple]:
    """Возвращает пары (User, TelegramAccount) для всех пользователей с привязанным Telegram."""
    async with async_session() as session:
        stmt = (
            select(User, TelegramAccount)
            .join(TelegramAccount, TelegramAccount.user_id == User.id)
        )
        return (await session.execute(stmt)).all()


async def refresh_whoop_tokens():
    """Обновляет WHOOP токены, которые истекают в ближайший час."""
    from app.whoop.oauth import refresh_access_token

    async with async_session() as session:
        stmt = select(WhoopConnection).where(WhoopConnection.is_active.is_(True))
        connections = (await session.execute(stmt)).scalars().all()

    refreshed = 0
    for conn in connections:
        if not conn.token_expires_at:
            continue
        # Обновляем если истекает в ближайшие 2 часа
        remaining = (conn.token_expires_at - datetime.now(timezone.utc)).total_seconds()
        if remaining < 7200:
            try:
                await refresh_access_token(conn)
                refreshed += 1
                logger.info("Refreshed WHOOP token for user %s", conn.user_id)
            except Exception:
                logger.exception("Failed to refresh WHOOP token for user %s", conn.user_id)

    if refreshed:
        logger.info("WHOOP token refresh: %d tokens refreshed", refreshed)


async def nightly_whoop_sync():
    """Ночная синхронизация WHOOP — подстраховка на случай пропущенных webhooks."""
    from app.whoop.sync import sync_whoop_data

    async with async_session() as session:
        stmt = select(WhoopConnection).where(WhoopConnection.is_active.is_(True))
        connections = (await session.execute(stmt)).scalars().all()

    for conn in connections:
        try:
            result = await sync_whoop_data(conn.user_id, days=2)
            logger.info("Nightly sync for user %s: %s", conn.user_id, result)
        except Exception:
            logger.exception("Nightly sync failed for user %s", conn.user_id)


async def morning_checkin(bot):
    """Утренний check-in: recovery + рекомендация на день."""
    from app.agent.agent import run_agent
    from app.whoop.sync import sync_whoop_data

    for user, tg_account in await _get_users_with_telegram():
        if not tg_account.chat_id:
            continue
        # Обновляем WHOOP перед утренней сводкой (свежий recovery, сон)
        try:
            await sync_whoop_data(user.id, days=1)
        except Exception:
            logger.warning("WHOOP sync before morning check-in failed for user %s", user.id)
        try:
            response = await run_agent(
                "Дай краткую утреннюю сводку: мой recovery, как я спал, и рекомендацию на день. Коротко, 3-5 предложений.",
                user_id=user.id,
                trigger="scheduler",
                intent_override="advice",
            )
            await _send_html(bot, tg_account.chat_id, response)
            logger.info("Morning check-in sent to user %s", user.id)
        except Exception:
            logger.exception("Morning check-in failed for user %s", user.id)


async def evening_summary(bot):
    """Вечерний итог дня: тренировки и питание за сегодня."""
    from app.agent.agent import run_agent
    from app.whoop.sync import sync_whoop_data

    today = today_msk()

    for user, tg_account in await _get_users_with_telegram():
        if not tg_account.chat_id:
            continue

        # Обновляем WHOOP данные перед итогом (strain растёт в течение дня)
        try:
            await sync_whoop_data(user.id, days=1)
        except Exception:
            logger.warning("WHOOP sync before evening summary failed for user %s", user.id)

        # Не отправляем если за сегодня нет ни одной записи о еде
        async with async_session() as session:
            meal_count = (await session.execute(
                select(func.count(MealLog.id)).where(
                    MealLog.user_id == user.id,
                    MealLog.date == today,
                    MealLog.deleted_at.is_(None),
                )
            )).scalar() or 0

        if meal_count == 0:
            logger.info("Skipping evening summary for user %s: no meals today", user.id)
            continue

        try:
            response = await run_agent(
                "Дай краткий итог дня. Вызови get_nutrition_remaining чтобы увидеть баланс калорий и макросов. "
                "Оформи каждый раздел отдельным абзацем с эмодзи:\n"
                "🍽 Питание: сколько съедено / цель / процент\n"
                "🏋️ Тренировки (если были): упомяни что норма учитывает нагрузку\n"
                "⚡️ Краткий вывод. Коротко, 3-5 предложений.",
                user_id=user.id,
                trigger="scheduler",
                intent_override="advice",
            )
            await _send_html(bot, tg_account.chat_id, response)
            logger.info("Evening summary sent to user %s", user.id)
        except Exception:
            logger.exception("Evening summary failed for user %s", user.id)


async def weekly_streak_check(bot):
    """Проверяет недельный streak — 7 дней подряд с записями."""
    today = today_msk()
    week = [today - timedelta(days=i) for i in range(1, 8)]  # последние 7 дней

    async with async_session() as session:
        for user, tg_account in await _get_users_with_telegram():
            if not tg_account.chat_id:
                continue

            # Считаем дни с записями о еде (ручной ввод, не автосинхронизация)
            stmt_days = select(func.distinct(MealLog.date)).where(
                MealLog.user_id == user.id,
                MealLog.date.in_(week),
                MealLog.deleted_at.is_(None),
            )
            logged_days = (await session.execute(stmt_days)).scalars().all()

            if len(logged_days) >= 7:
                try:
                    await bot.send_message(
                        chat_id=tg_account.chat_id,
                        text="<b>7 дней подряд с записями!</b> 🔥\n\n"
                             "Ты вёл логи каждый день на прошлой неделе. "
                             "Стабильность — ключ к результату. Так держать!",
                        parse_mode=ParseMode.HTML,
                    )
                    logger.info("Weekly streak notification sent to user %s", user.id)
                except Exception:
                    logger.exception("Weekly streak notification failed for user %s", user.id)


async def sleep_trend_check(bot):
    """Сравнивает средний сон за прошлую неделю с позапрошлой."""
    today = today_msk()
    # Прошлая неделя: пн–вс
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_sunday = last_monday + timedelta(days=6)
    # Позапрошлая неделя
    prev_monday = last_monday - timedelta(days=7)
    prev_sunday = last_monday - timedelta(days=1)

    async with async_session() as session:
        for user, tg_account in await _get_users_with_telegram():
            if not tg_account.chat_id:
                continue

            async def _avg_sleep(user_id, d_from: date, d_to: date) -> tuple[float | None, int]:
                s = select(
                    func.avg(SleepLog.duration_minutes),
                    func.count(SleepLog.id),
                ).where(
                    SleepLog.user_id == user_id,
                    SleepLog.date >= d_from,
                    SleepLog.date <= d_to,
                    SleepLog.deleted_at.is_(None),
                    SleepLog.duration_minutes.isnot(None),
                )
                row = (await session.execute(s)).one()
                return row[0], row[1]

            last_avg, last_count = await _avg_sleep(user.id, last_monday, last_sunday)
            prev_avg, prev_count = await _avg_sleep(user.id, prev_monday, prev_sunday)

            # Нужно минимум 3 записи за каждую неделю для сравнения
            if not last_avg or not prev_avg or last_count < 3 or prev_count < 3:
                continue

            diff = last_avg - prev_avg  # в минутах
            if abs(diff) < 10:
                continue  # незначительная разница

            last_h = last_avg / 60
            prev_h = prev_avg / 60
            abs_diff = abs(int(diff))

            if diff > 0:
                emoji = "📈"
                direction = f"на {abs_diff} мин больше"
            else:
                emoji = "📉"
                direction = f"на {abs_diff} мин меньше"

            text = (
                f"{emoji} <b>Тренд сна за неделю</b>\n\n"
                f"Прошлая неделя: ~{last_h:.1f}ч в среднем\n"
                f"Позапрошлая: ~{prev_h:.1f}ч\n\n"
                f"Спал {direction}."
            )

            if diff < -20:
                text += " Стоит обратить внимание — недосып накапливается."

            try:
                await bot.send_message(
                    chat_id=tg_account.chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
                logger.info("Sleep trend notification sent to user %s", user.id)
            except Exception:
                logger.exception("Sleep trend notification failed for user %s", user.id)


async def weekly_summary(bot):
    """Воскресный недельный обзор. Отправляется только если за неделю есть минимум 3 дня с данными."""
    from app.agent.agent import run_agent

    today = today_msk()
    week_start = today - timedelta(days=today.weekday())
    MIN_DAYS = 3

    for user, tg_account in await _get_users_with_telegram():
        if not tg_account.chat_id:
            continue

        # Проверяем достаточность данных: минимум 3 дня с записями о еде ИЛИ тренировках
        async with async_session() as session:
            meal_days = (await session.execute(
                select(func.count(func.distinct(MealLog.date))).where(
                    MealLog.user_id == user.id,
                    MealLog.date >= week_start,
                    MealLog.date <= today,
                    MealLog.deleted_at.is_(None),
                )
            )).scalar() or 0

            workout_days = (await session.execute(
                select(func.count(func.distinct(WorkoutLog.date))).where(
                    WorkoutLog.user_id == user.id,
                    WorkoutLog.date >= week_start,
                    WorkoutLog.date <= today,
                    WorkoutLog.deleted_at.is_(None),
                )
            )).scalar() or 0

        if max(meal_days, workout_days) < MIN_DAYS:
            logger.info(
                "Skipping weekly summary for user %s: only %d meal days, %d workout days",
                user.id, meal_days, workout_days,
            )
            continue

        try:
            response = await run_agent(
                "Дай подробный обзор за эту неделю. Вызови get_week_summary для текущей и прошлой недели. "
                "Оформи каждый раздел отдельным абзацем с эмодзи:\n"
                "😴 Сон: средняя длительность, дельта с прошлой неделей\n"
                "🍽 Питание: среднее ккал и белок в день, сравни с рассчитанной нормой (факт vs цель, %), дельта\n"
                "🏋️ Тренировки: количество, общее время, дельта\n"
                "💚 Recovery: средний score и HRV, дельта\n"
                "⚡️ В конце — краткий вывод: что улучшилось, что ухудшилось, на что обратить внимание.",
                user_id=user.id,
                trigger="scheduler",
                intent_override="advice",
            )
            await _send_html(bot, tg_account.chat_id, response)
            logger.info("Weekly summary sent to user %s", user.id)
        except Exception:
            logger.exception("Weekly summary failed for user %s", user.id)
