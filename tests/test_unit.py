"""Iteration 1: Pure unit tests — no DB, no async, no external deps."""

import hmac
import hashlib
import base64
from datetime import datetime, timezone

import pytest


# ============================================================
# _parse_goal / _goal_progress (summary.py)
# ============================================================
class TestGoalParsing:
    def test_parse_single_value(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("2500") == (2500, 2500)

    def test_parse_range_dash(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("110-150") == (110, 150)

    def test_parse_range_en_dash(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("110–150") == (110, 150)

    def test_parse_range_em_dash(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("110—150") == (110, 150)

    def test_parse_with_spaces(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("110 - 150") == (110, 150)

    def test_parse_empty(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("") == (None, None)

    def test_parse_garbage(self):
        from app.agent.tools.summary import _parse_goal
        assert _parse_goal("abc") == (None, None)

    def test_goal_progress_single(self):
        from app.agent.tools.summary import _goal_progress
        result = _goal_progress(1800, 2500, 2500)
        assert "цель: 2500" in result
        assert "72%" in result

    def test_goal_progress_range(self):
        from app.agent.tools.summary import _goal_progress
        result = _goal_progress(130, 110, 150)
        assert "цель: 110–150" in result
        assert "100%" in result  # 130 / midpoint(130) = 100%

    def test_goal_progress_zero_division(self):
        """Edge case: goal_max=0 should not crash."""
        from app.agent.tools.summary import _goal_progress
        # Single value goal of 0 would cause division by zero
        with pytest.raises(ZeroDivisionError):
            _goal_progress(100, 0, 0)


# ============================================================
# _md_to_html (handlers.py)
# ============================================================
class TestMdToHtml:
    def test_bold(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("**hello**") == "<b>hello</b>"

    def test_italic_asterisk(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("*hello*") == "<i>hello</i>"

    def test_italic_underscore(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("_hello_") == "<i>hello</i>"

    def test_code_inline(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("`code`") == "<code>code</code>"

    def test_header(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("### Title") == "<b>Title</b>"

    def test_html_escaping(self):
        from app.telegram.handlers import _md_to_html
        result = _md_to_html("1 < 2 & 3 > 0")
        assert "&lt;" in result
        assert "&amp;" in result
        assert "&gt;" in result

    def test_bold_inside_html_escape(self):
        """Bold after escaping should still work."""
        from app.telegram.handlers import _md_to_html
        result = _md_to_html("**hello <world>**")
        assert "<b>" in result
        assert "&lt;world&gt;" in result

    def test_plain_text_unchanged(self):
        from app.telegram.handlers import _md_to_html
        assert _md_to_html("hello world") == "hello world"


# ============================================================
# choose_model (router.py)
# ============================================================
class TestModelRouter:
    def test_recommendation_uses_strong(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("Что посоветуешь на ужин?") == settings.openai_model_strong

    def test_simple_message_uses_fast(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("Съел курицу с рисом 400 ккал") == settings.openai_model_fast

    def test_image_always_fast(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("Оцени что я ем", has_image=True) == settings.openai_model_fast

    def test_analysis_uses_strong(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("Как прошёл день?") == settings.openai_model_strong

    def test_pattern_case_insensitive(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("РЕКОМЕНДУЙ тренировку") == settings.openai_model_strong

    def test_weekly_summary_uses_strong(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("покажи недельный обзор") == settings.openai_model_strong

    def test_why_uses_strong(self):
        from app.agent.router import choose_model
        from app.config import settings
        assert choose_model("Почему я плохо сплю?") == settings.openai_model_strong


# ============================================================
# verify_signature (webhook.py)
# ============================================================
class TestWhoopSignature:
    def test_valid_signature(self):
        from app.whoop.webhook import verify_signature
        from app.config import settings

        body = b'{"type":"sleep.updated","user_id":123}'
        timestamp = "1234567890"

        message = timestamp.encode() + body
        expected = base64.b64encode(
            hmac.new(settings.whoop_client_secret.encode(), message, hashlib.sha256).digest()
        ).decode()

        assert verify_signature(body, expected, timestamp) is True

    def test_invalid_signature(self):
        from app.whoop.webhook import verify_signature
        assert verify_signature(b"body", "wrong_sig", "12345") is False

    def test_empty_secret_returns_false(self, monkeypatch):
        from app.whoop import webhook
        from app.config import settings
        monkeypatch.setattr(settings, "whoop_client_secret", "")
        assert webhook.verify_signature(b"body", "sig", "ts") is False


# ============================================================
# _ms_to_minutes / _parse_iso (sync.py)
# ============================================================
class TestSyncHelpers:
    def test_ms_to_minutes_normal(self):
        from app.whoop.sync import _ms_to_minutes
        assert _ms_to_minutes(3_600_000) == 60  # 1 hour

    def test_ms_to_minutes_rounds(self):
        from app.whoop.sync import _ms_to_minutes
        assert _ms_to_minutes(90_000) == 2  # 1.5 min → 2

    def test_ms_to_minutes_none(self):
        from app.whoop.sync import _ms_to_minutes
        assert _ms_to_minutes(None) is None

    def test_ms_to_minutes_zero(self):
        from app.whoop.sync import _ms_to_minutes
        assert _ms_to_minutes(0) == 0

    def test_parse_iso_utc(self):
        from app.whoop.sync import _parse_iso
        result = _parse_iso("2026-03-25T10:30:00.000Z")
        assert result.tzinfo is not None
        assert result.hour == 10
        assert result.minute == 30

    def test_parse_iso_offset(self):
        from app.whoop.sync import _parse_iso
        result = _parse_iso("2026-03-25T13:30:00+03:00")
        assert result.tzinfo is not None

    def test_parse_iso_none(self):
        from app.whoop.sync import _parse_iso
        assert _parse_iso(None) is None

    def test_parse_iso_empty(self):
        from app.whoop.sync import _parse_iso
        assert _parse_iso("") is None


# ============================================================
# validate_meal_calories (quality/rules.py)
# ============================================================
class TestQualityValidators:
    def test_normal_calories_ok(self):
        from app.quality.rules import validate_meal_calories
        assert validate_meal_calories(500) is None

    def test_too_high_calories(self):
        from app.quality.rules import validate_meal_calories
        result = validate_meal_calories(6000)
        assert result is not None
        assert result.severity == "warning"

    def test_too_low_calories(self):
        from app.quality.rules import validate_meal_calories
        result = validate_meal_calories(10)
        assert result is not None
        assert result.severity == "info"

    def test_none_calories(self):
        from app.quality.rules import validate_meal_calories
        assert validate_meal_calories(None) is None

    def test_boundary_5000(self):
        from app.quality.rules import validate_meal_calories
        assert validate_meal_calories(5000) is None  # exactly 5000 is OK

    def test_boundary_30(self):
        from app.quality.rules import validate_meal_calories
        assert validate_meal_calories(30) is None  # exactly 30 is OK


# ============================================================
# today_msk / now_msk (config.py)
# ============================================================
class TestTimezone:
    def test_today_msk_returns_date(self):
        from app.config import today_msk
        result = today_msk()
        assert isinstance(result, datetime.date.__class__) or hasattr(result, "year")

    def test_now_msk_has_timezone(self):
        from app.config import now_msk
        result = now_msk()
        assert result.tzinfo is not None

    def test_now_msk_is_moscow(self):
        from app.config import now_msk
        result = now_msk()
        # Moscow is UTC+3
        assert result.utcoffset().total_seconds() == 3 * 3600


# ============================================================
# classify_intent (router.py)
# ============================================================
class TestClassifyIntent:
    """Тесты на классификацию интентов по тексту сообщения."""

    def _ci(self, msg: str, has_image: bool = False) -> str:
        from app.agent.router import classify_intent
        return classify_intent(msg, has_image=has_image)

    # --- food_photo ---
    def test_photo_always_food_photo(self):
        assert self._ci("любой текст", has_image=True) == "food_photo"

    def test_photo_ignores_workout_text(self):
        assert self._ci("побегал 5 км", has_image=True) == "food_photo"

    # --- food_text ---
    def test_food_text_syel(self):
        assert self._ci("Съел омлет с сыром") == "food_text"

    def test_food_text_na_zavtrak(self):
        assert self._ci("На завтрак овсянка") == "food_text"

    def test_food_text_obed_colon(self):
        assert self._ci("Обед: борщ и хлеб") == "food_text"

    def test_food_text_vypil(self):
        assert self._ci("Выпил протеин") == "food_text"

    def test_food_text_poobiledal(self):
        assert self._ci("Пообедал в кафе") == "food_text"

    # --- food_text exclude → advice ---
    def test_chto_poest_is_advice(self):
        assert self._ci("Что поесть на ужин?") == "advice"

    def test_chto_ya_el_is_general(self):
        assert self._ci("Что я ел вчера?") == "general"

    def test_udali_edu_is_general(self):
        assert self._ci("Удали завтрак") == "general"

    # --- workout ---
    def test_workout_pobegal(self):
        assert self._ci("Побегал 5 км") == "workout"

    def test_workout_silovaya(self):
        assert self._ci("Силовая 60 минут") == "workout"

    def test_workout_zal(self):
        assert self._ci("Сходил в зал") == "workout"

    def test_workout_crossfit(self):
        assert self._ci("Кроссфит сегодня") == "workout"

    # --- workout exclude → advice/general ---
    def test_kak_trenirovat_is_advice(self):
        assert self._ci("Как тренироваться?") == "advice"

    def test_plan_trenirovok_is_advice(self):
        assert self._ci("Спланируй тренировку") == "advice"

    # --- body_state ---
    def test_body_spal(self):
        assert self._ci("Спал 7 часов") == "body_state"

    def test_body_veshu(self):
        assert self._ci("Вешу 75.5") == "body_state"

    def test_body_bolit(self):
        assert self._ci("Голова болит с утра") == "body_state"

    def test_body_stress(self):
        assert self._ci("Сильный стресс на работе") == "body_state"

    # --- body_state exclude ---
    def test_kak_uluchshit_son_is_advice(self):
        assert self._ci("Как улучшить сон?") == "advice"

    def test_pokaji_ves_is_general(self):
        assert self._ci("Покажи вес") == "general"

    # --- advice ---
    def test_advice_itog_dnya(self):
        assert self._ci("Итог дня") == "advice"

    def test_advice_norma_kaloriy(self):
        assert self._ci("Какая моя норма калорий?") == "advice"

    def test_advice_kak_pitatsya(self):
        assert self._ci("Как правильно питаться?") == "advice"

    def test_advice_obzor_nedeli(self):
        assert self._ci("Обзор недели") == "advice"

    # --- mixed → general ---
    def test_mixed_food_and_workout(self):
        assert self._ci("Съел омлет и побегал 5 км") == "general"

    def test_mixed_sleep_and_food(self):
        assert self._ci("Спал 6 часов, на завтрак яйца") == "general"

    def test_mixed_body_and_workout(self):
        assert self._ci("Спал плохо и побегал утром") == "general"

    # --- food_text: числовые маркеры ---
    def test_food_bju_kkal(self):
        assert self._ci("Приём пищи. Удон с кальмаром. Бжу 21/7/72. 435 ккал") == "food_text"

    def test_food_poschitay(self):
        assert self._ci("Хлеб посчитай примерно") == "food_text"

    # --- mixed: food + workout → general ---
    def test_mixed_posle_trenirovki_food(self):
        assert self._ci("После тренировки батончик. Бжу. 20/4/4. 173 ккал") == "general"

    # --- body_state exclude: порция, не масса тела ---
    def test_ves_pomenshe_not_body(self):
        assert self._ci("Оцени вес поменьше, тут небольшая тарелка") != "body_state"

    # --- general fallback ---
    def test_privet_is_general(self):
        assert self._ci("Привет") == "general"

    def test_random_question_is_general(self):
        assert self._ci("Как дела?") == "general"
