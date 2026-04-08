"""Starlette/FastAPI middleware that resolves tenant context for every request.

The middleware:
1. Extracts the bearer token or X-API-Key header.
2. Calls verify_token() (with Redis cache).
3. Attaches the resulting TenantContext to request.state.tenant.
4. Emits a structured Cloud Audit Log entry via Python logging.

Routes listed in EXEMPT_PATHS bypass auth (health checks, metrics).
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.status import HTTP_401_UNAUTHORIZED

from platform.auth.verify_token import verify_token

logger = logging.getLogger(__name__)

# Paths that do not require authentication
DEFAULT_EXEMPT_PATHS: tuple[str, ...] = (
    "/health",
    "/healthz",
    "/ready",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
)


class TenantAuthMiddleware(BaseHTTPMiddleware):
    """Shared authentication + tenant-resolution middleware.

    Args:
        app: The ASGI application to wrap.
        exempt_paths: Sequence of path prefixes that skip auth.
            Defaults to ``DEFAULT_EXEMPT_PATHS``.
    """

    def __init__(
        self,
        app,
        exempt_paths: Sequence[str] = DEFAULT_EXEMPT_PATHS,
    ) -> None:
        super().__init__(app)
        self._exempt = tuple(exempt_paths)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # ------------------------------------------------------------------
        # 1. Skip auth for exempt paths
        # ------------------------------------------------------------------
        path = request.url.path
        if any(path.startswith(p) for p in self._exempt):
            return await call_next(request)

        # ------------------------------------------------------------------
        # 2. Extract token
        # ------------------------------------------------------------------
        token: str | None = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("X-API-Key")

        if not token:
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # ------------------------------------------------------------------
        # 3. Verify token and attach TenantContext
        # ------------------------------------------------------------------
        start = time.perf_counter()
        try:
            ctx = verify_token(token)
        except Exception as exc:  # noqa: BLE001
            _audit_log(
                request=request,
                outcome="DENIED",
                reason=str(exc),
                latency_ms=_ms(start),
            )
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.tenant = ctx
        latency = _ms(start)

        # ------------------------------------------------------------------
        # 4. Emit Cloud Audit Log (structured JSON via logger)
        # ------------------------------------------------------------------
        _audit_log(
            request=request,
            outcome="ALLOWED",
            reason=None,
            latency_ms=latency,
            tenant_log=ctx.to_log_dict(),
        )

        response = await call_next(request)
        return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _audit_log(
    *,
    request: Request,
    outcome: str,
    reason: str | None,
    latency_ms: float,
    tenant_log: dict | None = None,
) -> None:
    """Emit a structured audit log entry compatible with Cloud Logging."""
    entry: dict = {
        "httpRequest": {
            "requestMethod": request.method,
            "requestUrl": str(request.url),
            "remoteIp": request.client.host if request.client else "",
        },
        "auth": {
            "outcome": outcome,
            "latency_ms": latency_ms,
        },
    }
    if reason:
        entry["auth"]["reason"] = reason
    if tenant_log:
        entry["tenant"] = tenant_log

    if outcome == "ALLOWED":
        logger.info("cloud_audit_log", extra={"json_fields": entry})
    else:
        logger.warning("cloud_audit_log", extra={"json_fields": entry})

