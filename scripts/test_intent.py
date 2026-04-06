"""Быстрая проверка classify_intent на живых фразах.

Запуск:
    python3 -m scripts.test_intent
    python3 -m scripts.test_intent "съел бургер и побегал"
"""

import sys

from app.agent.router import classify_intent

EXAMPLES = [
    # food_text
    "Съел омлет с сыром",
    "На завтрак овсянка и кофе",
    "Обед: борщ, хлеб, компот",
    "Выпил протеин",
    # food_photo
    # (has_image=True, текст не важен)
    # workout
    "Побегал 5 км",
    "Силовая 60 минут",
    "Сходил в зал, жим и присед",
    # body_state
    "Спал 7 часов",
    "Вешу 75.5",
    "Голова болит с утра",
    # advice
    "Что поесть на ужин?",
    "Итог дня",
    "Как улучшить сон?",
    "Какая моя норма калорий?",
    # mixed → general
    "Съел омлет и побегал 5 км",
    "Спал 6 часов, на завтрак яйца",
    # general (ничего не совпало)
    "Привет",
    "Удали последнюю запись",
    "Покажи что я ел вчера",
]


def main() -> None:
    if len(sys.argv) > 1:
        phrase = " ".join(sys.argv[1:])
        intent = classify_intent(phrase)
        print(f"  {intent:<15} ← {phrase}")
        return

    print("=" * 60)
    print("Intent classifier test")
    print("=" * 60)
    for phrase in EXAMPLES:
        intent = classify_intent(phrase)
        print(f"  {intent:<15} ← {phrase}")

    print()
    print(f"  {'food_photo':<15} ← [любое фото] (has_image=True)")
    print()
    print("Интерактивный режим (Ctrl+C для выхода):")
    print()
    try:
        while True:
            phrase = input("  > ")
            if not phrase.strip():
                continue
            intent = classify_intent(phrase.strip())
            print(f"  → {intent}")
            print()
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
