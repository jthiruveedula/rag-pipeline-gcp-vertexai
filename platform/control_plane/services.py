"""CRUD service layer for the multi-tenant control plane (Issue #2)."""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone, date
from typing import Optional, List, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from .models import (
    Tenant, Workspace, WorkspaceMembership, QuotaPolicy,
    ApiCredential, AuditEvent, TenantStatus, AuditAction, MemberRole
)
from .schemas import (
    TenantCreate, TenantUpdate, WorkspaceCreate, MemberAdd, QuotaUpdate
)


class TenantService:
    """Business logic for tenant lifecycle management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_tenant(self, payload: TenantCreate, actor_id: str) -> Tuple[Tenant, Workspace]:
        """Create a tenant and provision a default workspace atomically."""
        tenant = Tenant(
            name=payload.name,
            slug=payload.slug,
            contact_email=payload.contact_email,
            plan=payload.plan,
            region=payload.region,
            metadata_=payload.metadata,
            status=TenantStatus.ACTIVE,
        )
        self.db.add(tenant)
        await self.db.flush()  # get tenant.id

        # Provision default workspace
        default_ws = Workspace(
            tenant_id=tenant.id,
            name="Default",
            slug="default",
        )
        self.db.add(default_ws)
        await self.db.flush()

        # Provision default quota policy
        quota = QuotaPolicy(
            workspace_id=default_ws.id,
            tenant_id=tenant.id,
        )
        self.db.add(quota)

        # Audit trail
        self.db.add(AuditEvent(
            tenant_id=tenant.id,
            workspace_id=default_ws.id,
            actor_id=actor_id,
            action=AuditAction.CREATE,
            resource_type="tenant",
            resource_id=str(tenant.id),
        ))

        await self.db.commit()
        await self.db.refresh(tenant)
        await self.db.refresh(default_ws)
        return tenant, default_ws

    async def get_tenant(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        result = await self.db.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    async def list_tenants(self, page: int = 1, page_size: int = 20) -> Tuple[List[Tenant], int]:
        offset = (page - 1) * page_size
        result = await self.db.execute(
            select(Tenant).order_by(Tenant.created_at.desc()).offset(offset).limit(page_size)
        )
        total_result = await self.db.execute(select(func.count()).select_from(Tenant))
        return result.scalars().all(), total_result.scalar()

    async def update_tenant(self, tenant_id: uuid.UUID, payload: TenantUpdate, actor_id: str) -> Optional[Tenant]:
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return None
        if payload.name is not None:
            tenant.name = payload.name
        if payload.plan is not None:
            tenant.plan = payload.plan
        if payload.status is not None:
            tenant.status = payload.status
        self.db.add(AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=AuditAction.UPDATE,
            resource_type="tenant",
            resource_id=str(tenant_id),
        ))
        await self.db.commit()
        await self.db.refresh(tenant)
        return tenant


class WorkspaceService:
    """Business logic for workspace and membership management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_workspace(self, tenant_id: uuid.UUID, payload: WorkspaceCreate, actor_id: str) -> Workspace:
        workspace = Workspace(
            tenant_id=tenant_id,
            name=payload.name,
            slug=payload.slug,
            assistant_type=payload.assistant_type,
            corpus_id=payload.corpus_id,
            metadata_=payload.metadata,
        )
        self.db.add(workspace)
        await self.db.flush()
        # Create default quota
        self.db.add(QuotaPolicy(workspace_id=workspace.id, tenant_id=tenant_id))
        self.db.add(AuditEvent(
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            actor_id=actor_id,
            action=AuditAction.CREATE,
            resource_type="workspace",
            resource_id=str(workspace.id),
        ))
        await self.db.commit()
        await self.db.refresh(workspace)
        return workspace

    async def add_member(self, workspace_id: uuid.UUID, payload: MemberAdd, actor_id: str) -> WorkspaceMembership:
        """Add a member; raises IntegrityError on duplicate (409)."""
        membership = WorkspaceMembership(
            workspace_id=workspace_id,
            user_id=payload.user_id,
            role=payload.role,
            invited_by=actor_id,
        )
        self.db.add(membership)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise
        await self.db.refresh(membership)
        return membership

    async def get_workspace(self, workspace_id: uuid.UUID) -> Optional[Workspace]:
        result = await self.db.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        return result.scalar_one_or_none()

    async def list_workspaces(self, tenant_id: uuid.UUID) -> List[Workspace]:
        result = await self.db.execute(
            select(Workspace).where(Workspace.tenant_id == tenant_id)
        )
        return result.scalars().all()


class QuotaService:
    """Real-time quota enforcement with Redis hot-path caching."""

    def __init__(self, db: AsyncSession, redis=None):
        self.db = db
        self.redis = redis

    async def get_quota_usage(self, workspace_id: uuid.UUID) -> dict:
        """Return policy limits + today's Redis counters."""
        result = await self.db.execute(
            select(QuotaPolicy).where(QuotaPolicy.workspace_id == workspace_id)
        )
        policy = result.scalar_one_or_none()
        if not policy:
            return {}

        today = date.today().isoformat()
        requests_today = 0
        tokens_today = 0

        if self.redis:
            req_key = f"quota:{workspace_id}:{today}:requests"
            tok_key = f"quota:{workspace_id}:{today}:tokens"
            requests_today = int(await self.redis.get(req_key) or 0)
            tokens_today = int(await self.redis.get(tok_key) or 0)

        return {
            "workspace_id": workspace_id,
            "daily_requests_limit": policy.daily_requests,
            "daily_tokens_limit": policy.daily_tokens,
            "daily_ingestion_bytes_limit": policy.daily_ingestion_bytes,
            "requests_today": requests_today,
            "tokens_today": tokens_today,
            "ingestion_bytes_today": 0,
            "requests_remaining": max(0, policy.daily_requests - requests_today),
            "tokens_remaining": max(0, policy.daily_tokens - tokens_today),
        }

    async def increment_usage(self, workspace_id: uuid.UUID, requests: int = 0, tokens: int = 0) -> None:
        """Atomically increment usage counters in Redis."""
        if not self.redis:
            return
        today = date.today().isoformat()
        pipe = self.redis.pipeline()
        if requests:
            key = f"quota:{workspace_id}:{today}:requests"
            pipe.incrby(key, requests)
            pipe.expire(key, 86400 * 2)  # 2 days TTL
        if tokens:
            key = f"quota:{workspace_id}:{today}:tokens"
            pipe.incrby(key, tokens)
            pipe.expire(key, 86400 * 2)
        await pipe.execute()
