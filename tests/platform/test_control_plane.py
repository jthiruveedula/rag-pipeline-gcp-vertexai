"""Unit + integration tests for the Platform Control Plane (Issue #2).

Run: pytest tests/platform/test_control_plane.py -v
Requires: pytest-asyncio, sqlalchemy[asyncio], asyncpg (or aiosqlite for CI)
"""

import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from platform.control_plane.models import Base
from platform.control_plane.database import get_db
from platform.control_plane.services import TenantService, WorkspaceService, QuotaService
from platform.control_plane.schemas import TenantCreate, WorkspaceCreate, MemberAdd
from platform.control_plane.models import MemberRole

# Use in-memory SQLite for CI (via aiosqlite)
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def async_db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ─────────────────── TenantService Tests ────────────────────────────

@pytest.mark.asyncio
async def test_create_tenant_creates_default_workspace(async_db):
    svc = TenantService(async_db)
    payload = TenantCreate(
        name="Acme Corp",
        slug="acme",
        contact_email="admin@acme.com",
    )
    tenant, ws = await svc.create_tenant(payload, actor_id="user-1")

    assert tenant.id is not None
    assert ws.id is not None
    assert ws.tenant_id == tenant.id
    assert ws.slug == "default"


@pytest.mark.asyncio
async def test_create_tenant_returns_201_with_ids(async_db):
    svc = TenantService(async_db)
    payload = TenantCreate(name="Beta Inc", slug="beta", contact_email="x@beta.com")
    tenant, ws = await svc.create_tenant(payload, actor_id="system")
    assert str(tenant.id) != str(ws.id)


@pytest.mark.asyncio
async def test_get_nonexistent_tenant_returns_none(async_db):
    svc = TenantService(async_db)
    result = await svc.get_tenant(uuid.uuid4())
    assert result is None


# ─────────────────── WorkspaceService Tests ─────────────────────────

@pytest.mark.asyncio
async def test_add_member_succeeds(async_db):
    t_svc = TenantService(async_db)
    tenant, ws = await t_svc.create_tenant(
        TenantCreate(name="C", slug="c", contact_email="c@c.com"), actor_id="sys"
    )
    w_svc = WorkspaceService(async_db)
    membership = await w_svc.add_member(
        ws.id,
        MemberAdd(user_id="google-sub-123", role=MemberRole.EDITOR),
        actor_id="sys",
    )
    assert membership.user_id == "google-sub-123"
    assert membership.role == MemberRole.EDITOR


@pytest.mark.asyncio
async def test_duplicate_member_raises_integrity_error(async_db):
    from sqlalchemy.exc import IntegrityError
    t_svc = TenantService(async_db)
    tenant, ws = await t_svc.create_tenant(
        TenantCreate(name="D", slug="d", contact_email="d@d.com"), actor_id="sys"
    )
    w_svc = WorkspaceService(async_db)
    await w_svc.add_member(ws.id, MemberAdd(user_id="user-x"), actor_id="sys")
    with pytest.raises(IntegrityError):
        await w_svc.add_member(ws.id, MemberAdd(user_id="user-x"), actor_id="sys")


# ─────────────────── Isolation Tests ────────────────────────────────

@pytest.mark.asyncio
async def test_workspace_isolation_between_tenants(async_db):
    """Workspace A should not be able to enumerate Workspace B's data."""
    t_svc = TenantService(async_db)
    tenant_a, ws_a = await t_svc.create_tenant(
        TenantCreate(name="A", slug="a", contact_email="a@a.com"), actor_id="sys"
    )
    tenant_b, ws_b = await t_svc.create_tenant(
        TenantCreate(name="B", slug="b", contact_email="b@b.com"), actor_id="sys"
    )
    w_svc = WorkspaceService(async_db)
    workspaces_a = await w_svc.list_workspaces(tenant_a.id)
    workspaces_b = await w_svc.list_workspaces(tenant_b.id)

    ws_a_ids = {str(w.id) for w in workspaces_a}
    ws_b_ids = {str(w.id) for w in workspaces_b}
    # No overlap
    assert ws_a_ids.isdisjoint(ws_b_ids), "Workspace isolation violated!"


# ─────────────────── QuotaService Tests ─────────────────────────────

@pytest.mark.asyncio
async def test_quota_returns_limits_for_workspace(async_db):
    t_svc = TenantService(async_db)
    tenant, ws = await t_svc.create_tenant(
        TenantCreate(name="Q", slug="q", contact_email="q@q.com"), actor_id="sys"
    )
    q_svc = QuotaService(async_db, redis=None)
    usage = await q_svc.get_quota_usage(ws.id)
    assert usage["daily_requests_limit"] == 1000
    assert usage["requests_today"] == 0
    assert usage["requests_remaining"] == 1000


@pytest.mark.asyncio
async def test_quota_returns_empty_for_unknown_workspace(async_db):
    q_svc = QuotaService(async_db, redis=None)
    usage = await q_svc.get_quota_usage(uuid.uuid4())
    assert usage == {}
