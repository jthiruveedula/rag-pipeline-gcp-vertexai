"""platform.auth – shared JWT/IAM authentication package.

Public API::

    from platform.auth import TenantContext, get_tenant_context, TenantAuthMiddleware
"""
from platform.auth.tenant_context import MemberRole, PlanTier, TenantContext
from platform.auth.verify_token import get_tenant_context, verify_token
from platform.auth.middleware import TenantAuthMiddleware, DEFAULT_EXEMPT_PATHS

__all__ = [
    "MemberRole",
    "PlanTier",
    "TenantContext",
    "verify_token",
    "get_tenant_context",
    "TenantAuthMiddleware",
    "DEFAULT_EXEMPT_PATHS",
]

