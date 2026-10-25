from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.db import get_db
from app.core.redis import create_arq_pool, create_cache_client
from app.main import app

test_engine = create_async_engine(
    str(settings.DATABASE_URL),
    poolclass=NullPool,
)
TestSession = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture(scope="session", autouse=True)
async def _setup_db():
    """Schema must exist before tests. `make test` runs `alembic upgrade head` first.

    We deliberately DO NOT call create_all/drop_all:
    - create_all would create schema without populating alembic_version
    - drop_all would drop tables while alembic_version still claims they exist,
      causing every subsequent `alembic downgrade` to fail.
    """
    async with test_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='users'"
            )
        )
        if result.scalar() is None:
            pytest.fail(
                "Schema missing. Run `alembic upgrade head` before `pytest` "
                "(or just `make test` — it runs migrations for you)."
            )
    yield
    await test_engine.dispose()


@pytest.fixture(autouse=True)
async def _clean():
    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, refresh_tokens, venues, halls, hall_seats, "
                "events, showtimes, price_categories, showtime_seats, orders, "
                "order_items, seat_holds, tickets, payments, payment_events, "
                "idempotency_keys, outbox RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(autouse=True)
async def _redis_state():
    app.state.arq = await create_arq_pool()
    app.state.cache = create_cache_client()
    await app.state.cache.flushdb()
    try:
        yield
    finally:
        await app.state.cache.aclose()
        await app.state.arq.aclose()


async def _override_get_db() -> AsyncIterator[AsyncSession]:
    async with TestSession() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with TestSession() as session:
        yield session