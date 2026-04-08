# Shared Tenancy Metadata Schema

> **Canonical reference** for all downstream RAG assistant repos.
> Every vector index record **MUST** include these metadata fields for multi-tenant isolation and ACL enforcement.

## Required Vector Metadata Fields

All downstream repos (`gdrive-rag-assistant`, `policy-sop-assistant`, `intraday-ops-intelligence`) must include these fields on every document chunk stored in their respective vector indexes:

| Field | Type | Description | Required |
|---|---|---|---|
| `tenant_id` | `string (UUID)` | Top-level tenant partition key | **REQUIRED** |
| `workspace_id` | `string (UUID)` | Secondary workspace partition key | **REQUIRED** |
| `assistant_type` | `string` | One of: `gdrive`, `policy`, `intraday` | **REQUIRED** |
| `corpus_id` | `string` | Logical corpus/collection identifier | **REQUIRED** |
| `acl_principals` | `string[]` | IAM principals with read access | RECOMMENDED |
| `created_at` | `datetime (ISO 8601)` | Document ingestion timestamp | **REQUIRED** |
| `updated_at` | `datetime (ISO 8601)` | Last update/re-embed timestamp | RECOMMENDED |
| `source_uri` | `string` | Origin URI (GCS, Drive, BigQuery) | RECOMMENDED |

## Control Plane DB Schema

```sql
-- Tenants: top-level organizational units
CREATE TABLE tenants (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        VARCHAR(255) NOT NULL,
  slug        VARCHAR(100) UNIQUE NOT NULL,
  plan        VARCHAR(50)  NOT NULL DEFAULT 'free',
  region      VARCHAR(50)  NOT NULL DEFAULT 'us-central1',
  status      VARCHAR(50)  NOT NULL DEFAULT 'pending',
  contact_email VARCHAR(255) NOT NULL,
  metadata    JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ
);

-- Workspaces: logical namespaces scoped to a tenant
CREATE TABLE workspaces (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name           VARCHAR(255) NOT NULL,
  slug           VARCHAR(100) NOT NULL,
  status         VARCHAR(50) NOT NULL DEFAULT 'active',
  corpus_id      VARCHAR(255),
  assistant_type VARCHAR(50),
  metadata       JSONB,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ,
  UNIQUE (tenant_id, slug)
);

-- Workspace Memberships
CREATE TABLE workspace_memberships (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      VARCHAR(255) NOT NULL,
  role         VARCHAR(50) NOT NULL DEFAULT 'viewer',
  invited_by   VARCHAR(255),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (workspace_id, user_id)
);

-- Quota Policies (per workspace)
CREATE TABLE quota_policies (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id         UUID UNIQUE NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  daily_requests       BIGINT NOT NULL DEFAULT 1000,
  daily_tokens         BIGINT NOT NULL DEFAULT 500000,
  daily_ingestion_bytes BIGINT NOT NULL DEFAULT 104857600,
  monthly_requests     BIGINT NOT NULL DEFAULT 30000,
  monthly_tokens       BIGINT NOT NULL DEFAULT 15000000,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at           TIMESTAMPTZ
);

-- Audit Events (immutable append-only)
CREATE TABLE audit_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID REFERENCES tenants(id) ON DELETE SET NULL,
  workspace_id  UUID,
  actor_id      VARCHAR(255) NOT NULL,
  action        VARCHAR(50) NOT NULL,
  resource_type VARCHAR(100) NOT NULL,
  resource_id   VARCHAR(255) NOT NULL,
  payload_hash  VARCHAR(64),
  ip_address    VARCHAR(45),
  user_agent    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## REST API Contract

| Verb | Path | Response | Notes |
|---|---|---|---|
| POST | `/platform/v1/tenants` | `{tenantId, defaultWorkspaceId, status}` | 201 Created |
| GET | `/platform/v1/tenants/{id}` | `TenantResponse` | 200 / 404 |
| PATCH | `/platform/v1/tenants/{id}` | `TenantResponse` | 200 |
| POST | `/platform/v1/tenants/{id}/workspaces` | `WorkspaceResponse` | 201 |
| GET | `/platform/v1/tenants/{id}/workspaces` | `WorkspaceResponse[]` | 200 |
| POST | `/platform/v1/workspaces/{id}/members` | `MemberResponse` | 201 / 409 dup |
| GET | `/platform/v1/workspaces/{id}/quotas` | `QuotaResponse` | 200 |

## Isolation Guarantee

All queries in downstream assistants **MUST** include `tenant_id` and `workspace_id` in both relational queries (SQL `WHERE` clauses) and vector metadata filters. Cross-workspace data leakage must be verified via automated isolation tests before any enterprise onboarding.

## Redis Quota Keys

```
quota:{workspace_id}:{YYYY-MM-DD}:requests   # INCRBY, TTL 2 days
quota:{workspace_id}:{YYYY-MM-DD}:tokens     # INCRBY, TTL 2 days
quota:{workspace_id}:{YYYY-MM-DD}:bytes      # INCRBY, TTL 2 days
```

## GCP Resources

| Resource | Purpose |
|---|---|
| Cloud SQL PostgreSQL | Canonical control plane store |
| Cloud Memorystore (Redis) | Quota hot-path enforcement |
| Cloud Run | Hosts `/platform/v1` REST API |
| Secret Manager | DB credentials, API keys |
| Pub/Sub topic `platform.events` | Async downstream consumers |
