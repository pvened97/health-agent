from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "dev",
    pool_size=20,       # постоянных соединений в пуле
    max_overflow=20,    # дополнительных при пиковой нагрузке (итого макс 40)
    pool_timeout=30,    # секунд ждать свободное соединение, потом ошибка
    pool_recycle=1800,  # пересоздавать соединение каждые 30 мин (защита от разрывов)
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
