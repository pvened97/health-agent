from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-5.4"
    openai_model_strong: str = "gpt-5.4"
    openai_model_fast: str = "gpt-5.4-mini"

    # Telegram
    telegram_bot_token: str
    telegram_webhook_url: str = ""  # e.g. https://your-domain/telegram/webhook

    # Database
    database_url: str = "postgresql+asyncpg://healthagent:healthagent@localhost:5432/healthagent"

    # Access (comma-separated Telegram user IDs)
    allowed_telegram_user_ids: str

    # WHOOP
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8000/whoop/callback"

    # App
    app_env: str = "dev"
    log_level: str = "INFO"
    timezone: str = "Europe/Moscow"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def allowed_user_ids_set(self) -> set[int]:
        """Парсит ALLOWED_TELEGRAM_USER_IDS в set[int]."""
        return {
            int(uid.strip())
            for uid in self.allowed_telegram_user_ids.split(",")
            if uid.strip().isdigit()
        }


settings = Settings()

_tz = ZoneInfo(settings.timezone)


def now_msk() -> datetime:
    """Текущее время в московском часовом поясе."""
    return datetime.now(_tz)


def today_msk() -> date:
    """Сегодняшняя дата по Москве."""
    return now_msk().date()
