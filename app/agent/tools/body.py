from datetime import date
from typing import Optional

from app.config import today_msk

from agents import function_tool
from sqlalchemy import select, func

from app.agent.tools._context import get_user_id
from app.database import async_session
from app.models.logs import BodyMetric


@function_tool
async def save_body_metric(
    weight_kg: float,
    body_fat_pct: Optional[float] = None,
    comment: Optional[str] = None,
) -> str:
    """Сохраняет измерение веса (и опционально % жира). Если за сегодня уже есть запись — обновляет её.

    Args:
        weight_kg: Вес в килограммах (например 75.5)
        body_fat_pct: Процент жира (опционально, например 15.0)
        comment: Комментарий (опционально)
    """
    user_id = get_user_id()
    today = today_msk()

    async with async_session() as session:
        # Проверяем, есть ли уже запись за сегодня
        stmt = select(BodyMetric).where(
            BodyMetric.user_id == user_id,
            BodyMetric.date == today,
            BodyMetric.deleted_at.is_(None),
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()

        if existing:
            existing.weight_kg = weight_kg
            if body_fat_pct is not None:
                existing.body_fat_pct = body_fat_pct
            if comment:
                existing.comment = comment
            await session.commit()
            action = "обновлён"
        else:
            metric = BodyMetric(
                user_id=user_id,
                date=today,
                weight_kg=weight_kg,
                body_fat_pct=body_fat_pct,
                comment=comment,
            )
            session.add(metric)
            await session.commit()
            action = "записан"

    parts = [f"Вес {weight_kg} кг {action} ({today})."]
    if body_fat_pct is not None:
        parts.append(f"Жир: {body_fat_pct}%.")
    return " ".join(parts)


@function_tool
async def get_weight_history(days: int = 30) -> str:
    """Возвращает историю веса за последние N дней и скользящую среднюю за 7 дней.

    Args:
        days: За сколько последних дней показать (по умолчанию 30)
    """
    from datetime import timedelta

    user_id = get_user_id()
    since = today_msk() - timedelta(days=days)

    async with async_session() as session:
        stmt = (
            select(BodyMetric)
            .where(
                BodyMetric.user_id == user_id,
                BodyMetric.date >= since,
                BodyMetric.deleted_at.is_(None),
                BodyMetric.weight_kg.isnot(None),
            )
            .order_by(BodyMetric.date.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        return f"Нет записей веса за последние {days} дней."

    lines = [f"История веса за последние {days} дней ({len(rows)} записей):"]

    prev_weight = None
    weights_for_avg = []

    for row in rows:
        weights_for_avg.append(row.weight_kg)

        # Скользящая средняя за последние 7 записей
        recent = weights_for_avg[-7:]
        ma7 = sum(recent) / len(recent)

        # Дельта с прошлым измерением
        if prev_weight is not None:
            delta = row.weight_kg - prev_weight
            delta_str = f" ({delta:+.1f})"
        else:
            delta_str = ""

        fat_str = f", жир {row.body_fat_pct}%" if row.body_fat_pct else ""
        lines.append(f"  {row.date}: {row.weight_kg} кг{delta_str}{fat_str}  [MA7: {ma7:.1f}]")
        prev_weight = row.weight_kg

    # Итого
    if len(rows) >= 2:
        total_delta = rows[-1].weight_kg - rows[0].weight_kg
        lines.append(f"\nИзменение за период: {total_delta:+.1f} кг ({rows[0].date} → {rows[-1].date})")

    return "\n".join(lines)
