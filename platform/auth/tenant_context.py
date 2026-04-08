"""TenantContext dataclass - the canonical tenant-scoped request identity object (Issue #3).

This is the shared library that all assistant repos import.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional, List
from enum import Enum


class MemberRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"
    SERVICE = "service"  # machine-to-machine IAM service account


class PlanTier(str, Enum):
    FREE = "free"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


@dataclass(frozen=True)
class TenantContext:
    """Immutable request-scoped identity for a verified tenant principal.

    Injected into every protected FastAPI route via `Depends(get_tenant_context)`.
    Attached to `request.state.tenant` by the auth middleware.

    Fields
    ------
    tenant_id       : Top-level tenant UUID
    workspace_id    : Active workspace UUID
    user_id         : Google OIDC `sub` or IAM service account email
    role            : Workspace role resolved from the control plane
    plan            : Subscription plan tier
    quota_remaining : Estimated remaining daily requests (from Redis)
    scopes          : OAuth/IAM scopes granted
    is_service_account : True when authenticated via IAM workload identity
    """
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: str
    role: MemberRole = MemberRole.VIEWER
    plan: PlanTier = PlanTier.FREE
    quota_remaining: int = 0
    scopes: List[str] = field(default_factory=list)
    is_service_account: bool = False

    def has_role(self, minimum_role: MemberRole) -> bool:
        """Check if the principal meets a minimum role level."""
        role_order = [
            MemberRole.SERVICE,
            MemberRole.VIEWER,
            MemberRole.EDITOR,
            MemberRole.ADMIN,
            MemberRole.OWNER,
        ]
        current_idx = role_order.index(self.role)
        required_idx = role_order.index(minimum_role)
        return current_idx >= required_idx

    def require_role(self, minimum_role: MemberRole) -> None:
        """Raise PermissionError if role is insufficient."""
        if not self.has_role(minimum_role):
            raise PermissionError(
                f"Role '{self.role}' insufficient; requires '{minimum_role}'"
            )

    def to_log_dict(self) -> dict:
        """Return a safe dict for structured logging (no PII secrets)."""
        return {
            "tenant_id": str(self.tenant_id),
            "workspace_id": str(self.workspace_id),
            "user_id": self.user_id,
            "role": self.role.value,
            "plan": self.plan.value,
            "is_service_account": self.is_service_account,
        }
