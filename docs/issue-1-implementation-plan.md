# Shared Multi-Tenant SaaS Platform Control Plane
## Epic Issue #1: Shared Multi-Tenant SaaS Platform Control Plane for All RAG Assistants

### Overview

Deliver a shared, centralized multi-tenant SaaS control plane hosted in `rag-pipeline-gcp-vertexai` that all four RAG assistants consume. This establishes the canonical data model for tenants, workspaces, users, roles, plans, quotas, and per-tenant corpus isolation — enabling enterprise-grade onboarding, billing, and security across the entire portfolio.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│              rag-pipeline-gcp-vertexai (Control Plane)       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Tenant   │  │  Auth    │  │  Quota   │  │  Platform  │  │
│  │   API    │  │Middleware│  │  Engine  │  │   Admin    │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │               Cloud SQL (PostgreSQL)                    │  │
│  │  tenants | workspaces | users | roles | quotas | plans │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
         │               │               │               │
   gdrive-rag      policy-sop      intraday-ops    future-rag
   -assistant      -assistant      -intelligence   -assistants
```

### Phase 1: Core Data Model & Tenant API (Weeks 1-3)

#### 1.1 Cloud SQL Schema Design

**Tenants Table:**
```sql
CREATE TABLE tenants (
  tenant_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name          VARCHAR(255) NOT NULL,
  slug          VARCHAR(100) UNIQUE NOT NULL,
  plan          VARCHAR(50) NOT NULL DEFAULT 'starter',
  status        VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata      JSONB DEFAULT '{}'
);
```

**Workspaces Table:**
```sql
CREATE TABLE workspaces (
  workspace_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  name          VARCHAR(255) NOT NULL,
  corpus_id     VARCHAR(500),
  region        VARCHAR(50) NOT NULL DEFAULT 'us-central1',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  settings      JSONB DEFAULT '{}'
);
```

**Users & Roles:**
```sql
CREATE TABLE users (
  user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     UUID NOT NULL REFERENCES tenants(tenant_id),
  email         VARCHAR(255) NOT NULL,
  role          VARCHAR(50) NOT NULL DEFAULT 'member',
  workspace_ids UUID[] DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### 1.2 REST API Endpoints (Cloud Run)

```
POST   /api/v1/tenants              - Create tenant
GET    /api/v1/tenants/{tenant_id}  - Get tenant details
PUT    /api/v1/tenants/{tenant_id}  - Update tenant
DELETE /api/v1/tenants/{tenant_id}  - Deactivate tenant

POST   /api/v1/workspaces           - Create workspace
GET    /api/v1/workspaces/{id}      - Get workspace
GET    /api/v1/tenants/{id}/workspaces - List workspaces for tenant

POST   /api/v1/users                - Onboard user
GET    /api/v1/users/{user_id}      - Get user profile
PUT    /api/v1/users/{user_id}/roles - Update user role
```

#### 1.3 FastAPI Implementation (`platform/tenant_api.py`)

```python
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import uuid

app = FastAPI(title="RAG Platform Control Plane API")

class TenantCreate(BaseModel):
    name: str
    slug: str
    plan: str = "starter"

class WorkspaceCreate(BaseModel):
    tenant_id: str
    name: str
    region: str = "us-central1"

@app.post("/api/v1/tenants", status_code=201)
async def create_tenant(payload: TenantCreate, db=Depends(get_db)):
    tenant_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO tenants (tenant_id, name, slug, plan) VALUES ($1, $2, $3, $4)",
        tenant_id, payload.name, payload.slug, payload.plan
    )
    return {"tenant_id": tenant_id, "status": "created"}
```

### Phase 2: Auth Middleware (JWT/IAM) (Weeks 3-5)

#### 2.1 JWT Token Structure

```json
{
  "sub": "user-uuid",
  "tenant_id": "tenant-uuid",
  "workspace_ids": ["ws-uuid-1", "ws-uuid-2"],
  "role": "admin",
  "iat": 1700000000,
  "exp": 1700003600
}
```

#### 2.2 Auth Middleware (`platform/auth_middleware.py`)

```python
import jwt
from fastapi import Request, HTTPException
from functools import wraps

async def tenant_auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    request.state.tenant_id = payload["tenant_id"]
    request.state.user_id = payload["sub"]
    request.state.role = payload["role"]
    request.state.workspace_ids = payload.get("workspace_ids", [])
    
    return await call_next(request)
```

#### 2.3 GCP IAM Integration

- Service accounts per tenant: `rag-tenant-{slug}@{project}.iam.gserviceaccount.com`
- Workload Identity for Cloud Run services
- Per-workspace Vertex AI corpus access controls via IAM bindings

### Phase 3: Quota Policy Engine (Weeks 5-7)

#### 3.1 Quota Plans

| Plan     | Queries/Day | Corpus Size | Workspaces | API Rate (req/min) |
|----------|-------------|-------------|------------|--------------------|
| Starter  | 1,000       | 10 GB       | 1          | 60                 |
| Growth   | 10,000      | 100 GB      | 5          | 300                |
| Business | 100,000     | 1 TB        | 25         | 1,000              |
| Enterprise | Unlimited | Unlimited   | Unlimited  | Custom             |

#### 3.2 Quota Engine (`platform/quota_engine.py`)

```python
import redis
from datetime import datetime

class QuotaEngine:
    def __init__(self, redis_client, db):
        self.redis = redis_client
        self.db = db
    
    async def check_and_increment(self, tenant_id: str, query_type: str) -> bool:
        key = f"quota:{tenant_id}:{query_type}:{datetime.utcnow().date()}"
        plan_limits = await self._get_plan_limits(tenant_id)
        
        current = self.redis.incr(key)
        if current == 1:
            self.redis.expire(key, 86400)  # 24h TTL
        
        limit = plan_limits.get(f"{query_type}_per_day", 1000)
        if current > limit:
            raise QuotaExceededError(f"Daily {query_type} quota exceeded for tenant {tenant_id}")
        
        return True
    
    async def get_usage_summary(self, tenant_id: str) -> dict:
        today = datetime.utcnow().date()
        return {
            "queries": int(self.redis.get(f"quota:{tenant_id}:query:{today}") or 0),
            "embeddings": int(self.redis.get(f"quota:{tenant_id}:embed:{today}") or 0),
            "date": str(today)
        }
```

#### 3.3 Cloud Firestore for Quota State (Alternative)

```python
from google.cloud import firestore

db = firestore.Client()

def track_quota(tenant_id: str, workspace_id: str, operation: str):
    ref = db.collection("quotas").document(f"{tenant_id}_{workspace_id}")
    ref.set({
        operation: firestore.Increment(1),
        "last_updated": firestore.SERVER_TIMESTAMP
    }, merge=True)
```

### Phase 4: Cross-Repo Integration (Weeks 7-9)

#### 4.1 Shared Library Package (`rag-platform-sdk`)

```python
# pip install rag-platform-sdk
from rag_platform_sdk import TenantContext, get_tenant_context

# In gdrive-rag-assistant
@app.post("/ingest")
async def ingest_document(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context)
):
    # ctx.tenant_id, ctx.workspace_id, ctx.corpus_id all populated
    await ingest_to_corpus(ctx.corpus_id, document)
```

#### 4.2 Tenancy Metadata in All RAG Requests

```python
class RAGRequest(BaseModel):
    tenant_id: str
    workspace_id: str
    query: str
    user_id: str
    session_id: Optional[str] = None

class RAGResponse(BaseModel):
    answer: str
    citations: list[Citation]
    tenant_id: str
    workspace_id: str
    tokens_used: int
    latency_ms: float
```

#### 4.3 Corpus Isolation Strategy

```
Vertex AI Search:
  - data_store_id: {tenant_id}-{workspace_id}-corpus
  - One data store per workspace for full isolation

BigQuery Vector Search:
  - dataset: rag_{tenant_id}
  - table: embeddings_{workspace_id}
  - Row-level security via BQ authorized views
```

### Phase 5: Observability & Compliance (Weeks 9-11)

#### 5.1 Cloud Monitoring Dashboard

- Queries per tenant/workspace per hour
- Token cost per tenant (Gemini API usage)
- Retrieval hit rate per workspace
- Error rate by tenant and endpoint
- Quota utilization per plan tier

#### 5.2 Audit Logging to BigQuery

```python
async def log_audit_event(
    tenant_id: str,
    user_id: str,
    action: str,
    resource: str,
    metadata: dict
):
    row = {
        "event_time": datetime.utcnow().isoformat(),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "action": action,
        "resource": resource,
        "metadata": json.dumps(metadata),
        "source_ip": metadata.get("ip", "unknown")
    }
    bq_client.insert_rows_json("rag_platform.audit_log", [row])
```

#### 5.3 Security & Compliance Checklist

- [ ] Multi-tenant data isolation verified via automated tests
- [ ] JWT token expiry enforced (1hr access, 7d refresh)
- [ ] All PII encrypted at rest (Cloud KMS)
- [ ] RBAC enforced at API and corpus layer
- [ ] Audit logs retained 90 days in BigQuery
- [ ] Penetration test before enterprise onboarding
- [ ] SOC2 controls documented

### Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Cross-tenant data leak | Row-level BQ security + corpus-level isolation; automated isolation tests in CI |
| Vertex AI quota exhaustion | Per-tenant quota enforcement before hitting GCP limits; quota increase requests early |
| SDK drift across repos | Versioned PyPI package with pinned deps in all consumer repos |
| Drive API rate limits | Exponential backoff + DLQ for failed sync events |
| Auth token compromise | Short-lived JWTs + refresh token rotation; revocation list in Redis |

### Success Criteria

- All four RAG assistants consume tenant context on every request
- Zero cross-tenant data leakage in security test suite
- Quota enforcement prevents any tenant from exceeding plan limits
- Onboarding time < 5 minutes per new tenant via API
- 99.9% platform API uptime SLA

