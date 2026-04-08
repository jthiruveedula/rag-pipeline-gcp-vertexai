"""Token verification for JWT (OIDC) and API key auth against control plane DB.

Supports:
- Google-issued OIDC JWTs verified via google-auth library
- Opaque API keys looked up in Redis cache → control plane DB
Results are cached in Redis for 60 s to reduce latency.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

import redis
from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from platform.auth.tenant_context import MemberRole, PlanTier, TenantContext

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis client (lazy singleton)
# ---------------------------------------------------------------------------
_redis_client: Optional[redis.Redis] = None
_CACHE_TTL = int(os.getenv("AUTH_CACHE_TTL_SECONDS", "60"))
_EXPECTED_AUDIENCE = os.getenv("JWT_AUDIENCE", "")


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(token: str) -> str:
    """SHA-256 fingerprint so we never store raw tokens in Redis."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"auth:token:{digest}"


def _read_cache(token: str) -> Optional[TenantContext]:
    try:
        raw = _get_redis().get(_cache_key(token))
        if raw:
            data = json.loads(raw)
            return TenantContext(**data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis read error: %s", exc)
    return None


def _write_cache(token: str, ctx: TenantContext) -> None:
    try:
        payload = json.dumps(
            {
                "tenant_id": str(ctx.tenant_id),
                "workspace_id": str(ctx.workspace_id),
                "user_id": ctx.user_id,
                "role": ctx.role.value,
                "plan": ctx.plan.value,
                "quota_remaining": ctx.quota_remaining,
                "scopes": ctx.scopes,
                "is_service_account": ctx.is_service_account,
            }
        )
        _get_redis().setex(_cache_key(token), _CACHE_TTL, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis write error: %s", exc)


# ---------------------------------------------------------------------------
# OIDC JWT verification
# ---------------------------------------------------------------------------

def _verify_oidc_jwt(token: str) -> TenantContext:
    """Verify a Google-issued OIDC token and extract tenant claims."""
    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=_EXPECTED_AUDIENCE or None,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT: {exc}",
        ) from exc

    # Custom claims injected by the control-plane token issuer
    tenant_id = claims.get("tenant_id") or claims.get("sub", "")
    workspace_id = claims.get("workspace_id", tenant_id)
    role_str = claims.get("role", MemberRole.VIEWER.value)
    plan_str = claims.get("plan", PlanTier.FREE.value)
    scopes = claims.get("scopes", [])
    is_sa = claims.get("is_service_account", False)

    try:
        role = MemberRole(role_str)
    except ValueError:
        role = MemberRole.VIEWER

    try:
        plan = PlanTier(plan_str)
    except ValueError:
        plan = PlanTier.FREE

    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=claims.get("email", claims.get("sub", "")),
        role=role,
        plan=plan,
        quota_remaining=claims.get("quota_remaining", 0),
        scopes=scopes,
        is_service_account=is_sa,
    )


# ---------------------------------------------------------------------------
# API-key verification (stub — replace with real DB lookup)
# ---------------------------------------------------------------------------

def _verify_api_key(api_key: str) -> TenantContext:  # noqa: ARG001
    """Look up an opaque API key in the control plane DB.

    This is a stub implementation.  Replace with a real asyncpg / SQLAlchemy
    query against the control_plane.api_keys table.
    """
    # TODO: query control plane DB
    # row = db.execute("SELECT * FROM api_keys WHERE key_hash = $1", hash(api_key))
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API key verification not yet implemented.",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def verify_token(token: str) -> TenantContext:
    """Validate a bearer token and return a TenantContext.

    Strategy:
    1. Check Redis cache.
    2. If token looks like a JWT (three dot-separated parts) → OIDC path.
    3. Otherwise treat as opaque API key.

    Raises ``fastapi.HTTPException`` (401) on failure.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token.",
        )

    cached = _read_cache(token)
    if cached is not None:
        return cached

    parts = token.split(".")
    if len(parts) == 3:  # noqa: PLR2004 — JWT structure heuristic
        ctx = _verify_oidc_jwt(token)
    else:
        ctx = _verify_api_key(token)

    _write_cache(token, ctx)
    return ctx


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

from fastapi import Depends, Request  # noqa: E402 — avoid circular at module top
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_tenant_context(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    request: Request = None,
) -> TenantContext:
    """FastAPI dependency — inject on every protected route.

    Usage::

        @app.get("/protected")
        async def handler(ctx: TenantContext = Depends(get_tenant_context)):
            ...
    """
    token: Optional[str] = None

    if credentials is not None:
        token = credentials.credentials
    elif request is not None:
        # Fallback: check X-API-Key header
        token = request.headers.get("X-API-Key")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(token)

