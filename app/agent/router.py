"""Роутер: выбирает модель (strong/fast) по содержимому сообщения."""

import logging

from app.config import settings

logger = logging.getLogger(__name__)

# Паттерны, при которых используется strong-модель
STRONG_PATTERNS: list[str] = [
    # Рекомендации и советы
    "рекоменд",
    "посоветуй",
    "совет",
    "что делать",
    "что есть",
    "что поесть",
    "что пить",
    "как трениро",
    "как питать",
    "как восстан",
    "как улучши",
    "стоит ли",
    "можно ли",
    # Планирование
    "спланируй",
    "план на",
    "распиши",
    "составь",
    # Аналитика и обзоры
    "обзор",
    "итог",
    "анализ",
    "недел",
    "тренд",
    "динамик",
    "сравни",
    # Объяснения
    "почему",
    "объясни",
    "на основании",
    "откуда вывод",
]


def choose_model(message: str, has_image: bool = False) -> str:
    """Возвращает имя модели для данного сообщения.

    - Фото еды → fast (распознавание + запись)
    - Совпадение с STRONG_PATTERNS → strong
    - Всё остальное → fast
    """
    if has_image:
        return settings.openai_model_fast

    lower = message.lower()
    for pattern in STRONG_PATTERNS:
        if pattern in lower:
            logger.info("Router → strong (pattern: %r)", pattern)
            return settings.openai_model_strong

    logger.info("Router → fast")
    return settings.openai_model_fast
