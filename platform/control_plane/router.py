"""FastAPI router for /platform/v1 REST endpoints (Issue #2)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .services import TenantService, WorkspaceService, QuotaService
from .schemas import (
    TenantCreate, TenantResponse, TenantUpdate,
    WorkspaceCreate, WorkspaceResponse,
    MemberAdd, MemberResponse,
    QuotaResponse, QuotaUpdate,
    PaginatedResponse,
)

router = APIRouter(prefix="/platform/v1", tags=["platform"])


def _actor(request: Request) -> str:
    """Extract actor ID from request state (injected by auth middleware)."""
    return getattr(getattr(request.state, "tenant", None), "user_id", "anonymous")


# ─────────────────── Tenant Endpoints ───────────────────────────────────

@router.post("/tenants", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Provision a new tenant and its default workspace. Returns tenantId + defaultWorkspaceId."""
    svc = TenantService(db)
    try:
        tenant, default_ws = await svc.create_tenant(payload, actor_id=_actor(request))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Tenant slug already exists")
    return {
        "tenantId": str(tenant.id),
        "defaultWorkspaceId": str(default_ws.id),
        "status": tenant.status.value,
    }


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    tenant = await svc.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    svc = TenantService(db)
    tenant = await svc.update_tenant(tenant_id, payload, actor_id=_actor(request))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


# ─────────────────── Workspace Endpoints ─────────────────────────────────

@router.post("/tenants/{tenant_id}/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    tenant_id: uuid.UUID,
    payload: WorkspaceCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    svc = WorkspaceService(db)
    try:
        ws = await svc.create_workspace(tenant_id, payload, actor_id=_actor(request))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Workspace slug already exists in tenant")
    return ws


@router.get("/tenants/{tenant_id}/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    svc = WorkspaceService(db)
    return await svc.list_workspaces(tenant_id)


# ─────────────────── Membership Endpoints ────────────────────────────────

@router.post("/workspaces/{workspace_id}/members", response_model=MemberResponse, status_code=201)
async def add_member(
    workspace_id: uuid.UUID,
    payload: MemberAdd,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a user to a workspace. Returns 409 if already a member."""
    svc = WorkspaceService(db)
    try:
        membership = await svc.add_member(workspace_id, payload, actor_id=_actor(request))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="User is already a member of this workspace")
    return membership


# ─────────────────── Quota Endpoints ─────────────────────────────────────

@router.get("/workspaces/{workspace_id}/quotas", response_model=QuotaResponse)
async def get_quota(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return real-time today's usage vs. limits from Redis + Cloud SQL."""
    svc = QuotaService(db)
    usage = await svc.get_quota_usage(workspace_id)
    if not usage:
        raise HTTPException(status_code=404, detail="Quota policy not found for workspace")
    return usage
