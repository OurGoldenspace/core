from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from src.config import get_settings
from src.database import get_session_factory, init_db, reset_engine
from src.seed import seed


@pytest.fixture
async def db_url(tmp_path, monkeypatch) -> AsyncIterator[str]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'workcore-test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LLM_PROVIDER", "policy")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    await reset_engine()
    await init_db()
    yield url
    await reset_engine()


@pytest.fixture
async def seeded_session(db_url):
    factory = get_session_factory()
    async with factory() as session:
        await seed(session, get_settings())
        await session.commit()
        yield session


@pytest.fixture
async def client(db_url) -> AsyncIterator[AsyncClient]:
    factory = get_session_factory()
    async with factory() as session:
        await seed(session, get_settings())
        await session.commit()

    from src.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
