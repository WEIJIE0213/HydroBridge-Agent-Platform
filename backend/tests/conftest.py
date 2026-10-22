from __future__ import annotations

import os
from collections.abc import AsyncIterator

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./.test-hydrobridge.db"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-tests"
os.environ["REDIS_RUNTIME_URL"] = "redis://localhost:6399/0"
os.environ["REDIS_CACHE_URL"] = "redis://localhost:6399/1"
# Unit and API tests must never inherit live provider credentials from the
# developer's .env file. Provider integrations are exercised by explicit
# live-smoke scripts instead.
os.environ["LLM_API_KEY"] = ""
os.environ["CONTINUUM_LLM_API_KEY"] = ""
os.environ["CONTINUUM_LLM_MODEL"] = ""
os.environ["EMBEDDING_BACKEND"] = "hash"
os.environ["RETRIEVAL_BACKEND"] = "local"
os.environ["LLM_MODEL"] = ""
os.environ["TAVILY_API_KEY"] = ""

import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import import_all_models
from app.db.models.identity import Organization, Role, User, UserRole
from app.db.session import AsyncSessionFactory, engine
from app.main import app

import_all_models()


@pytest_asyncio.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def seeded_user(session: AsyncSession) -> User:
    organization = Organization(
        name="测试水利单位",
        slug="test-water-org",
        organization_type="water_authority",
    )
    role = Role(name="admin", description="测试管理员", is_system=True)
    session.add_all([organization, role])
    await session.flush()
    user = User(
        organization_id=organization.id,
        email="admin@example.com",
        display_name="测试管理员",
        password_hash=hash_password("SecurePass123!"),
        is_superuser=True,
    )
    session.add(user)
    await session.flush()
    session.add(UserRole(user_id=user.id, role_id=role.id))
    await session.commit()
    await session.refresh(user)
    return user


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as test_client:
            yield test_client


@pytest_asyncio.fixture
async def auth_headers(
    client: httpx.AsyncClient,
    seeded_user: User,
) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": seeded_user.email, "password": "SecurePass123!"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
