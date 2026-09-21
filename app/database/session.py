from sqlalchemy.orm import DeclarativeBase

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

engine = create_async_engine(
    settings.sqlalchemy_database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


class Base(DeclarativeBase):
    def __repr__(self) -> str:
        values = []
        for column in self.__table__.primary_key.columns:
            values.append(f"{column.key}={getattr(self, column.key, None)!r}")
        identity = ", ".join(values) or "no-primary-key"
        return f"<{type(self).__name__} {identity}>"
