"""SQLAlchemy ORM models for the multi-tenant control plane (Issue #2)."""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column, String, DateTime, Enum, ForeignKey, BigInteger,
    Boolean, JSON, Text, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class TenantStatus(str, PyEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"
    DELETED = "deleted"


class PlanTier(str, PyEnum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class WorkspaceStatus(str, PyEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MemberRole(str, PyEnum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class AuditAction(str, PyEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    QUOTA_EXCEEDED = "quota_exceeded"


class Tenant(Base):
    """Top-level organizational unit in the multi-tenant hierarchy."""
    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    plan = Column(Enum(PlanTier), nullable=False, default=PlanTier.FREE)
    region = Column(String(50), nullable=False, default="us-central1")
    status = Column(Enum(TenantStatus), nullable=False, default=TenantStatus.PENDING)
    contact_email = Column(String(255), nullable=False)
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    workspaces = relationship("Workspace", back_populates="tenant", cascade="all, delete-orphan")
    quota_policies = relationship("QuotaPolicy", back_populates="tenant")
    audit_events = relationship("AuditEvent", back_populates="tenant")

    def __repr__(self):
        return f"<Tenant id={self.id} name={self.name} plan={self.plan}>"


class Workspace(Base):
    """A logical namespace scoped to a tenant."""
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False)
    status = Column(Enum(WorkspaceStatus), nullable=False, default=WorkspaceStatus.ACTIVE)
    corpus_id = Column(String(255), nullable=True)  # Vertex AI / BQ corpus reference
    assistant_type = Column(String(50), nullable=True)  # gdrive | policy | intraday
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
        Index("ix_workspace_tenant_status", "tenant_id", "status"),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="workspaces")
    memberships = relationship("WorkspaceMembership", back_populates="workspace", cascade="all, delete-orphan")
    quota_policy = relationship("QuotaPolicy", back_populates="workspace", uselist=False)
    api_credentials = relationship("ApiCredential", back_populates="workspace", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Workspace id={self.id} name={self.name} tenant_id={self.tenant_id}>"


class WorkspaceMembership(Base):
    """Maps a user to a workspace with an assigned role."""
    __tablename__ = "workspace_memberships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(255), nullable=False, index=True)  # Google sub / IAM principal
    role = Column(Enum(MemberRole), nullable=False, default=MemberRole.VIEWER)
    invited_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
    )

    # Relationships
    workspace = relationship("Workspace", back_populates="memberships")

    def __repr__(self):
        return f"<WorkspaceMembership workspace_id={self.workspace_id} user_id={self.user_id} role={self.role}>"


class QuotaPolicy(Base):
    """Per-workspace usage limits for requests, tokens, and ingestion."""
    __tablename__ = "quota_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    daily_requests = Column(BigInteger, nullable=False, default=1000)
    daily_tokens = Column(BigInteger, nullable=False, default=500_000)
    daily_ingestion_bytes = Column(BigInteger, nullable=False, default=100 * 1024 * 1024)  # 100 MB
    monthly_requests = Column(BigInteger, nullable=False, default=30_000)
    monthly_tokens = Column(BigInteger, nullable=False, default=15_000_000)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    workspace = relationship("Workspace", back_populates="quota_policy")
    tenant = relationship("Tenant", back_populates="quota_policies")


class ApiCredential(Base):
    """API key credentials scoped to a workspace."""
    __tablename__ = "api_credentials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    key_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 of the raw key
    key_prefix = Column(String(12), nullable=False)  # first 12 chars for display
    label = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_api_credential_workspace_active", "workspace_id", "is_active"),
    )

    # Relationships
    workspace = relationship("Workspace", back_populates="api_credentials")


class AuditEvent(Base):
    """Immutable audit log for all create/update/delete operations."""
    __tablename__ = "audit_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    workspace_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    actor_id = Column(String(255), nullable=False)
    action = Column(Enum(AuditAction), nullable=False)
    resource_type = Column(String(100), nullable=False)  # e.g. tenant, workspace
    resource_id = Column(String(255), nullable=False)
    payload_hash = Column(String(64), nullable=True)  # SHA-256 of the request body
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="audit_events")

    def __repr__(self):
        return f"<AuditEvent actor={self.actor_id} action={self.action} resource={self.resource_type}/{self.resource_id}>"
