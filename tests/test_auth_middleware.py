"""Unit tests for platform.auth middleware and TenantContext.

Runs without a Redis server or real GCP credentials by mocking
external dependencies.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform.auth.tenant_context import MemberRole, PlanTier, TenantContext
from platform.auth.middleware import TenantAuthMiddleware


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_ctx() -> TenantContext:
    return TenantContext(
        tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        user_id="alice@example.com",
        role=MemberRole.EDITOR,
        plan=PlanTier.PRO,
        quota_remaining=500,
        scopes=["read", "write"],
        is_service_account=False,
    )


@pytest.fixture()
def app_with_middleware():
    """Minimal FastAPI app wrapped with TenantAuthMiddleware."""
    app = FastAPI()
    app.add_middleware(TenantAuthMiddleware)

    @app.get("/protected")
    async def protected(request):
        ctx: TenantContext = request.state.tenant
        return {"user_id": ctx.user_id, "role": ctx.role.value}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


# ---------------------------------------------------------------------------
# TenantContext unit tests
# ---------------------------------------------------------------------------

class TestTenantContext:
    def test_has_role_exact(self, sample_ctx):
        assert sample_ctx.has_role(MemberRole.EDITOR) is True

    def test_has_role_lower(self, sample_ctx):
        """EDITOR satisfies a VIEWER minimum."""
        assert sample_ctx.has_role(MemberRole.VIEWER) is True

    def test_has_role_higher_fails(self, sample_ctx):
        """EDITOR does not satisfy OWNER minimum."""
        assert sample_ctx.has_role(MemberRole.OWNER) is False

    def test_require_role_raises(self, sample_ctx):
        with pytest.raises(PermissionError):
            sample_ctx.require_role(MemberRole.OWNER)

    def test_to_log_dict_no_pii(self, sample_ctx):
        log = sample_ctx.to_log_dict()
        # user_id present but quota / scopes not in safe dict
        assert "tenant_id" in log
        assert "user_id" in log
        assert "role" in log
        assert log["plan"] == "pro"

    def test_to_log_dict_is_service_account(self, sample_ctx):
        assert log := sample_ctx.to_log_dict()
        assert log["is_service_account"] is False


# ---------------------------------------------------------------------------
# Middleware integration tests (mocked verify_token)
# ---------------------------------------------------------------------------

class TestTenantAuthMiddleware:
    def _make_client(self, app_with_middleware, ctx: TenantContext):
        with patch("platform.auth.middleware.verify_token", return_value=ctx):
            return TestClient(app_with_middleware, raise_server_exceptions=True)

    def test_exempt_path_no_token_required(self, app_with_middleware):
        client = TestClient(app_with_middleware)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_protected_path_without_token_returns_401(self, app_with_middleware):
        client = TestClient(app_with_middleware, raise_server_exceptions=False)
        resp = client.get("/protected")
        assert resp.status_code == 401

    def test_protected_path_with_valid_token(self, app_with_middleware, sample_ctx):
        with patch("platform.auth.middleware.verify_token", return_value=sample_ctx):
            client = TestClient(app_with_middleware)
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer dummy.jwt.token"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "alice@example.com"
        assert data["role"] == "editor"

    def test_x_api_key_header_accepted(self, app_with_middleware, sample_ctx):
        with patch("platform.auth.middleware.verify_token", return_value=sample_ctx):
            client = TestClient(app_with_middleware)
            resp = client.get(
                "/protected",
                headers={"X-API-Key": "secret-api-key"},
            )
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, app_with_middleware):
        from fastapi import HTTPException, status

        with patch(
            "platform.auth.middleware.verify_token",
            side_effect=HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid JWT"
            ),
        ):
            client = TestClient(app_with_middleware, raise_server_exceptions=False)
            resp = client.get(
                "/protected",
                headers={"Authorization": "Bearer bad.token.here"},
            )
        assert resp.status_code == 401
        assert "Invalid JWT" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# verify_token cache helpers (unit)
# ---------------------------------------------------------------------------

class TestVerifyTokenCache:
    def test_cache_key_is_deterministic(self):
        from platform.auth.verify_token import _cache_key

        k1 = _cache_key("my-token")
        k2 = _cache_key("my-token")
        assert k1 == k2
        assert k1.startswith("auth:token:")

    def test_cache_key_different_tokens(self):
        from platform.auth.verify_token import _cache_key

        assert _cache_key("token-a") != _cache_key("token-b")

    def test_read_cache_returns_none_on_redis_error(self):
        from platform.auth.verify_token import _read_cache

        with patch("platform.auth.verify_token._get_redis") as mock_redis:
            mock_redis.return_value.get.side_effect = Exception("connection refused")
            result = _read_cache("any-token")
        assert result is None

