"""Pydantic v2 request/response schemas for the Platform Control Plane REST API."""

import uuid
from datetime import datetime
from typing import Optional, List, Any, Dict

from pydantic import BaseModel, EmailStr, Field, field_validator

from .models import TenantStatus, PlanTier, WorkspaceStatus, MemberRole


# ── Tenant Schemas ────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r'^[a-z0-9-]+$')
    contact_email: str
    plan: PlanTier = PlanTier.FREE
    region: str = "us-central1"
    metadata: Optional[Dict[str, Any]] = None


class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: PlanTier
    region: str
    status: TenantStatus
    contact_email: str
    created_at: datetime
    default_workspace_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    plan: Optional[PlanTier] = None
    status: Optional[TenantStatus] = None


# ── Workspace Schemas ─────────────────────────────────────────────────────────

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r'^[a-z0-9-]+$')
    assistant_type: Optional[str] = None
    corpus_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    status: WorkspaceStatus
    assistant_type: Optional[str]
    corpus_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Membership Schemas ────────────────────────────────────────────────────────

class MemberAdd(BaseModel):
    user_id: str = Field(..., description="Google sub / IAM principal")
    role: MemberRole = MemberRole.VIEWER


class MemberResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: str
    role: MemberRole
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Quota Schemas ─────────────────────────────────────────────────────────────

class QuotaResponse(BaseModel):
    workspace_id: uuid.UUID
    daily_requests_limit: int
    daily_tokens_limit: int
    daily_ingestion_bytes_limit: int
    requests_today: int = 0
    tokens_today: int = 0
    ingestion_bytes_today: int = 0
    requests_remaining: int = 0
    tokens_remaining: int = 0

    model_config = {"from_attributes": True}


class QuotaUpdate(BaseModel):
    daily_requests: Optional[int] = Field(None, ge=1)
    daily_tokens: Optional[int] = Field(None, ge=1)
    daily_ingestion_bytes: Optional[int] = Field(None, ge=1)


# ── Audit Schemas ─────────────────────────────────────────────────────────────

class AuditEventResponse(BaseModel):
    id: uuid.UUID
    tenant_id: Optional[uuid.UUID]
    workspace_id: Optional[uuid.UUID]
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Generic ───────────────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
