# Tenant-Scoped Secret Manager Integration - Implementation Plan

## Issue #10: Tenant-scoped Secret Manager integration for API keys, webhook tokens, and external connector credentials

### Overview

Implement a secure, tenant-isolated secret management system using Google Cloud Secret Manager to store and retrieve sensitive credentials (API keys, webhook tokens, OAuth tokens, database passwords) with proper access controls, audit logging, and automatic rotation capabilities.

### Architecture Components

#### 1. Secret Naming Convention
- **Pattern**: `projects/{project_id}/secrets/{tenant_id}/{secret_type}/{secret_name}`
- **Examples**:
  - `projects/rag-prod/secrets/tenant-acme/api-key/openai`
  - `projects/rag-prod/secrets/tenant-acme/webhook-token/slack`
  - `projects/rag-prod/secrets/tenant-acme/db-password/postgres-readonly`

#### 2. Secret Types and Schema
- **API Keys**: Third-party service keys (OpenAI, Anthropic, Pinecone, etc.)
- **Webhook Tokens**: Inbound/outbound webhook authentication
- **OAuth Tokens**: OAuth 2.0 access/refresh tokens for external services
- **Database Credentials**: Connection strings, passwords for tenant-specific DBs
- **Encryption Keys**: Tenant-specific encryption keys for PII

#### 3. Access Control (IAM)
- **Service Account Per Tenant**:
  ```
  rag-tenant-{tenant_id}@{project}.iam.gserviceaccount.com
  ```
  - Permissions: `roles/secretmanager.secretAccessor` scoped to `secrets/{tenant_id}/*`
  - Used by Cloud Run services via Workload Identity

- **Admin Access**:
  - Platform admins: `roles/secretmanager.admin` (create/update/delete)
  - Tenant admins: Custom role `tenant-secret-manager` (read/rotate only their secrets)

#### 4. Secret Management API
- **Endpoints**:
  ```python
  POST /api/v1/tenants/{tenant_id}/secrets
  GET /api/v1/tenants/{tenant_id}/secrets
  GET /api/v1/tenants/{tenant_id}/secrets/{secret_id}
  PUT /api/v1/tenants/{tenant_id}/secrets/{secret_id}/rotate
  DELETE /api/v1/tenants/{tenant_id}/secrets/{secret_id}
  ```

- **Request Schema** (POST):
  ```json
  {
    \"secret_type\": \"api-key\",
    \"secret_name\": \"openai\",
    \"value\": \"sk-...\",
    \"description\": \"OpenAI API key for production\",
    \"rotation_days\": 90,
    \"metadata\": {
      \"cost_center\": \"engineering\",
      \"owner\": \"john@acme.com\"
    }
  }
  ```

#### 5. Secret Retrieval Service
- **Python Module**: `src/platform/secrets.py`
  ```python
  from google.cloud import secretmanager

  class TenantSecretManager:
      def get_secret(self, tenant_id: str, secret_type: str, secret_name: str) -> str:
          \"\"\"Retrieve secret with automatic caching and version pinning\"\"\"
          secret_path = f\"projects/{project}/secrets/{tenant_id}/{secret_type}/{secret_name}/versions/latest\"
          response = self.client.access_secret_version(request={\"name\": secret_path})
          return response.payload.data.decode('UTF-8')
      
      def set_secret(self, tenant_id: str, secret_type: str, secret_name: str, value: str, metadata: dict) -> str:
          \"\"\"Create or update secret with new version\"\"\"
          # Implementation
  ```

- **Caching Strategy**:
  - In-memory cache (Redis) with 5-minute TTL
  - Cache key: `secret:{tenant_id}:{secret_type}:{secret_name}`
  - Invalidate on rotation events

#### 6. Audit Logging
- **Log All Secret Operations**:
  - Access (who, when, which secret)
  - Creation/Update/Deletion
  - Rotation events
  - Failed access attempts

- **BigQuery Table**: `audit_logs.secret_access`
  ```sql
  CREATE TABLE audit_logs.secret_access (
    timestamp TIMESTAMP,
    tenant_id STRING,
    secret_name STRING,
    operation STRING,  -- access|create|update|delete|rotate
    user_identity STRING,
    service_account STRING,
    source_ip STRING,
    success BOOL,
    error_message STRING
  );
  ```

#### 7. Automatic Rotation
- **Cloud Scheduler Job** (daily):
  - Check secrets with `rotation_days` metadata
  - Alert tenants 7 days before expiration
  - Auto-generate new versions for system-managed secrets

- **Rotation Workflow**:
  1. Generate new secret value
  2. Create new version in Secret Manager
  3. Update consuming services (rolling restart)
  4. Mark old version as deprecated (retain for 7 days)
  5. Auto-disable old version after grace period

### Implementation Tasks

#### Phase 1: Secret Manager Setup (Week 1)
- [ ] Enable Secret Manager API in GCP project
- [ ] Define IAM structure (service accounts, custom roles)
- [ ] Create Terraform modules for secret creation
- [ ] Set up secret naming conventions and validation rules

#### Phase 2: Secret Management API (Week 1-2)
- [ ] Implement `TenantSecretManager` Python class
- [ ] Build CRUD API endpoints with FastAPI
- [ ] Add authentication and tenant isolation checks
- [ ] Implement secret encryption at rest (customer-managed keys)

#### Phase 3: Access Control (Week 2)
- [ ] Create per-tenant service accounts
- [ ] Configure Workload Identity for Cloud Run
- [ ] Implement IAM policy bindings (least privilege)
- [ ] Add rate limiting (max 100 secret accesses/min per tenant)

#### Phase 4: Caching Layer (Week 2-3)
- [ ] Implement Redis cache for secrets
- [ ] Add cache invalidation on secret rotation
- [ ] Monitor cache hit rate (target > 80%)
- [ ] Implement fallback to Secret Manager on cache miss

#### Phase 5: Audit Logging (Week 3)
- [ ] Create BigQuery audit log table
- [ ] Implement Cloud Logging sink to BigQuery
- [ ] Add structured logging to all secret operations
- [ ] Build audit dashboard (Looker Studio)

#### Phase 6: Automatic Rotation (Week 3-4)
- [ ] Implement rotation scheduler (Cloud Scheduler + Cloud Functions)
- [ ] Add rotation reminder emails (7 days before expiration)
- [ ] Build self-service rotation UI for tenant admins
- [ ] Test rotation workflow with OAuth tokens

#### Phase 7: Migration and Documentation (Week 4)
- [ ] Migrate existing hardcoded secrets to Secret Manager
- [ ] Update deployment scripts to inject secrets as env vars
- [ ] Write admin documentation (secret types, rotation process)
- [ ] Write developer documentation (SDK usage, best practices)

### Acceptance Criteria

1. **Security**:
   - Zero secrets hardcoded in code or config files
   - All secrets encrypted at rest and in transit
   - Per-tenant IAM isolation enforced
   - Failed access attempts logged and alerted

2. **Performance**:
   - Secret retrieval < 10ms (with cache hit)
   - Cache hit rate > 80%
   - No impact on API response times

3. **Auditability**:
   - All secret operations logged to BigQuery
   - Audit logs retained for 1 year
   - Dashboard showing secret access patterns per tenant

4. **Usability**:
   - Tenant admins can manage their secrets via UI
   - Automatic rotation reduces manual burden
   - Clear error messages for access denials

5. **Compliance**:
   - SOC 2 Type II compliant
   - GDPR-compliant secret handling (encryption, access logs)
   - Rotation policy enforced (90-day default)

### Technology Stack

- **Secret Storage**: Google Cloud Secret Manager
- **Caching**: Redis (Memorystore)
- **Access Control**: Cloud IAM, Workload Identity
- **Audit Logging**: Cloud Logging + BigQuery
- **Rotation Scheduler**: Cloud Scheduler + Cloud Functions
- **API Framework**: FastAPI (Python)

### Security Best Practices

1. **Principle of Least Privilege**:
   - Service accounts have access only to their tenant's secrets
   - Developers never have direct access to production secrets

2. **Defense in Depth**:
   - Secrets encrypted at rest (Google-managed or customer-managed keys)
   - TLS 1.3 for all secret transmissions
   - Network policies restrict Secret Manager access to GCP-only

3. **Secret Hygiene**:
   - No secrets in environment variables (use Secret Manager mounting)
   - Secrets never logged or exposed in error messages
   - Old secret versions auto-disabled after rotation

4. **Incident Response**:
   - Immediate secret rotation capability (< 5 minutes)
   - Automated alerts on unusual access patterns
   - Runbook for secret compromise scenarios

### Risk Analysis and Mitigation

#### Risk 1: Secret Sprawl
- **Impact**: Hard to track which secrets are in use
- **Mitigation**:
  - Enforce naming conventions
  - Regular audit of unused secrets (delete after 90 days of inactivity)
  - Dashboard showing secret inventory per tenant

#### Risk 2: Rotation Breakage
- **Impact**: Automatic rotation breaks dependent services
- **Mitigation**:
  - Grace period: Keep old version active for 7 days
  - Pre-rotation testing: Validate new secret before disabling old
  - Rollback mechanism: Instantly revert to previous version

#### Risk 3: Cache Poisoning
- **Impact**: Stale secrets served from cache after rotation
- **Mitigation**:
  - Pub/Sub notification on secret rotation \u2192 cache invalidation
  - Short TTL (5 minutes) to limit stale window
  - Versioned cache keys: `secret:{tenant}:{name}:{version}`

#### Risk 4: Cost Overruns
- **Impact**: Secret Manager API calls can be expensive ($0.03/10k accesses)
- **Mitigation**:
  - Aggressive caching (80%+ hit rate)
  - Batch secret loading at service startup
  - Monitor per-tenant API usage and set quotas

### Timeline Estimate

**Total**: 3-4 weeks (1-2 engineers)

- Week 1: Setup + API implementation
- Week 2: Access control + Caching
- Week 3: Audit logging + Rotation scheduler
- Week 4: Migration + Documentation + Testing

### Success Metrics

- **Security Posture**: 0 secrets in code/config (100% in Secret Manager)
- **Performance**: < 10ms secret retrieval (p99)
- **Rotation Compliance**: 100% of secrets rotated within policy window
- **Audit Coverage**: 100% of secret access logged
- **Tenant Adoption**: 80%+ of tenants using self-service secret management
