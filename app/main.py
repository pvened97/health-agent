import asyncio
import logging
import json
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.telegram.bot import start_polling, stop_polling, start_webhook, stop_webhook


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    if settings.app_env == "dev":
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    else:
        handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=settings.log_level, handlers=[handler])


setup_logging()
logger = logging.getLogger(__name__)

_bot_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bot_app

    logger.info("Starting Health Agent (env=%s)...", settings.app_env)

    if settings.app_env == "dev":
        _bot_app = await start_polling()
    else:
        _bot_app = await start_webhook()

    yield

    # Shutdown
    if _bot_app:
        if settings.app_env == "dev":
            await stop_polling(_bot_app)
        else:
            await stop_webhook(_bot_app)
    logger.info("Health Agent stopped.")


app = FastAPI(title="Health Agent", lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Принимает updates от Telegram в webhook mode."""
    from telegram import Update
    data = await request.json()
    update = Update.de_json(data, _bot_app.bot)
    await _bot_app.process_update(update)
    return {"ok": True}


@app.get("/whoop/auth")
async def whoop_auth():
    """Редиректит на WHOOP OAuth авторизацию."""
    from app.whoop.oauth import get_authorization_url
    from fastapi.responses import RedirectResponse
    return RedirectResponse(get_authorization_url())


@app.get("/whoop/callback")
async def whoop_callback(request: Request):
    """Callback от WHOOP после авторизации."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", "")
        return HTMLResponse(f"<h1>Ошибка WHOOP OAuth</h1><p>{error}: {desc}</p>", status_code=400)
    if not code:
        return HTMLResponse("<h1>Ошибка: код авторизации не получен</h1>", status_code=400)

    try:
        from app.whoop.oauth import exchange_code_for_tokens
        from app.whoop.sync import sync_whoop_data
        from sqlalchemy import select
        from app.database import async_session
        from app.models.user import User

        # Берём единственного пользователя (MVP)
        async with async_session() as session:
            user = (await session.execute(select(User))).scalar_one()

        await exchange_code_for_tokens(code, user.id)

        # Сразу синхронизируем данные за последние 7 дней
        result = await sync_whoop_data(user.id, days=7)
        logger.info("WHOOP connected and synced: %s", result)

        return HTMLResponse(
            "<h1>WHOOP подключён!</h1>"
            f"<p>{result.replace(chr(10), '<br>')}</p>"
            "<p>Можешь закрыть эту страницу и вернуться в Telegram.</p>"
        )
    except Exception as e:
        logger.exception("WHOOP callback error")
        return HTMLResponse(f"<h1>Ошибка подключения WHOOP</h1><p>{e}</p>", status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
