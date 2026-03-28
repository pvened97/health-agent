"""Shared test fixtures: in-memory async SQLite database, test user, helpers."""

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.database import Base

# Import ALL models so Base.metadata knows about every table
import app.models.user  # noqa: F401
import app.models.logs  # noqa: F401
import app.models.memory  # noqa: F401
import app.models.whoop  # noqa: F401
import app.models.agent  # noqa: F401
import app.models.catalog  # noqa: F401


# ---------------------------------------------------------------------------
# Event loop
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# In-memory async SQLite engine (no PostgreSQL needed for tests)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture()
async def engine():
    from sqlalchemy import JSON, event
    from sqlalchemy.dialects.postgresql import JSONB

    eng = create_async_engine("sqlite+aiosqlite://", echo=False)

    # SQLite doesn't support JSONB — map it to JSON for tests
    @event.listens_for(eng.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        pass  # placeholder for future pragmas

    # Replace PostgreSQL-specific types with SQLite-compatible ones
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID
    from sqlalchemy import Uuid

    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            elif isinstance(col.type, PG_UUID):
                col.type = Uuid()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture()
async def session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as sess:
        yield sess


# ---------------------------------------------------------------------------
# Patch app.database.async_session to use test DB (NOT autouse — only for DB tests)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture()
async def patch_db(engine, monkeypatch):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    # Patch async_session in app.database AND in every module that already imported it
    _modules_using_session = [
        "app.database",
        "app.whoop.sync",
        "app.whoop.webhook",
        "app.whoop.client",
        "app.whoop.oauth",
        "app.quality.rules",
        "app.agent.tools.logs",
        "app.agent.tools.memory",
        "app.agent.tools.state",
        "app.agent.tools.summary",
        "app.agent.tools.catalog",
        "app.agent.tools.profile",
        "app.agent.tools.whoop",
        "app.agent.tools.body",
        "app.agent.context",
        "app.agent.agent",
        "app.telegram.handlers",
        "app.telegram.user_service",
        "app.scheduler.jobs",
        "app.main",
    ]
    for mod in _modules_using_session:
        try:
            monkeypatch.setattr(f"{mod}.async_session", factory)
        except AttributeError:
            pass  # module may not have been imported yet
    yield


# ---------------------------------------------------------------------------
# Test user
# ---------------------------------------------------------------------------
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture()
async def user_id(patch_db, session):
    """Creates a test user and returns their UUID."""
    from app.models.user import User
    user = User(id=TEST_USER_ID, display_name="Test User")
    session.add(user)
    await session.commit()
    return TEST_USER_ID


# ---------------------------------------------------------------------------
# Set context var so tools can call get_user_id()
# ---------------------------------------------------------------------------
@pytest.fixture()
def set_user_ctx(user_id):
    from app.agent.tools._context import set_user_id
    set_user_id(user_id)
    return user_id
