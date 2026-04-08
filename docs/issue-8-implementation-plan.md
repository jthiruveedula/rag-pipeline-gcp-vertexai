# Async Ingestion Pipeline with Pub/Sub - Implementation Plan

## Issue #8: Async ingestion pipeline with Pub/Sub, dead-letter queues, and per-tenant backpressure control

### Overview

Implement an asynchronous document ingestion pipeline using Google Cloud Pub/Sub to decouple API requests from processing, enabling scalable, tenant-isolated ingestion with proper error handling and backpressure control.

### Architecture Components

#### 1. Pub/Sub Infrastructure
- **Main Topic**: `ingest-jobs` - receives ingestion requests
- **Dead-Letter Topic**: `ingest-dlq` - captures failed messages after retry exhaustion
- **Subscriptions**: Per-tenant or shared with filtering

#### 2. API Layer (api/ingest.py)
- Publish ingestion request to Pub/Sub instead of synchronous processing
- Return `job_id` immediately to client
- Payload structure:
  ```json
  {
    "job_id": "uuid",
    "tenant_id": "string",
    "document_uri": "gs://bucket/file",
    "metadata": {},
    "timestamp": "ISO8601"
  }
  ```

#### 3. Worker Service (workers/ingest_worker.py)
- Cloud Run service subscribing to `ingest-jobs`
- Process documents:
  - Chunk text
  - Generate embeddings
  - Write to BigQuery Vector Search
- Update job status in Firestore/BigQuery

#### 4. Per-Tenant Backpressure Control
- **Redis Semaphore**: `tenant:{tenant_id}:concurrent_limit`
- Configurable via `tenant_config` table
- Default: 10 concurrent jobs per tenant
- Workers check semaphore before processing

#### 5. Dead-Letter Queue Handler
- Separate Cloud Run service monitoring `ingest-dlq`
- Alert tenant admin on persistent failures
- Log to BigQuery for analysis

### Implementation Tasks

#### Phase 1: Pub/Sub Setup (Week 1)
- [ ] Create Pub/Sub topics and subscriptions via Terraform
- [ ] Configure dead-letter queue with max delivery attempts (5)
- [ ] Set up IAM permissions for Cloud Run to publish/subscribe

#### Phase 2: API Modification (Week 1-2)
- [ ] Modify `api/ingest.py` to publish messages instead of inline processing
- [ ] Add job status tracking (Firestore or BigQuery table)
- [ ] Create `/ingest/status/{job_id}` endpoint
- [ ] Return job metadata: `{"job_id": "...", "status": "pending", "queue_position": 42}`

#### Phase 3: Worker Implementation (Week 2)
- [ ] Create `workers/ingest_worker.py` Cloud Run service
- [ ] Implement Pub/Sub push subscription handler
- [ ] Port existing chunking/embedding logic from synchronous path
- [ ] Add Redis-based backpressure check
- [ ] Update job status: `pending` → `processing` → `complete`/`failed`

#### Phase 4: Backpressure Control (Week 2-3)
- [ ] Add `tenant_config.max_concurrent_ingests` column
- [ ] Implement Redis semaphore acquire/release in worker
- [ ] Add metrics: `ingest_queue_depth`, `ingest_latency_p99`
- [ ] Handle semaphore timeout (requeue message)

#### Phase 5: Dead-Letter Handling (Week 3)
- [ ] Create `workers/dlq_handler.py` Cloud Run service
- [ ] Subscribe to `ingest-dlq` topic
- [ ] Log failures to `ingest_failures` BigQuery table
- [ ] Send email/Slack alerts to tenant admin

#### Phase 6: Observability (Week 3)
- [ ] Emit Cloud Monitoring metrics:
  - `ingest_queue_depth` per tenant
  - `ingest_latency_p50/p99`
  - `ingest_success_rate`
  - `dlq_message_count`
- [ ] Add structured logging with correlation IDs
- [ ] Create Cloud Monitoring dashboard

### Acceptance Criteria

1. **API Response Time**: 
   - `/ingest` API returns `< 100ms` (vs. previous 30+ seconds)
   - Returns `job_id` immediately

2. **Throughput**:
   - Handle 1000+ concurrent documents across all tenants
   - Single tenant limited by `max_concurrent_ingests` setting

3. **Reliability**:
   - DLQ captures poison-pill documents without blocking queue
   - Failed jobs retried up to 5 times with exponential backoff

4. **Isolation**:
   - One tenant's large upload doesn't block other tenants
   - Per-tenant concurrency limits enforced

5. **Observability**:
   - Job status queryable via `/ingest/status/{job_id}`
   - Metrics visible in Cloud Monitoring
   - DLQ failures logged and alertable

### Technology Stack

- **Messaging**: Google Cloud Pub/Sub
- **Workers**: Cloud Run (Python 3.11+)
- **State Management**: Redis (Memorystore) + Firestore/BigQuery
- **Monitoring**: Cloud Monitoring, Cloud Logging
- **IaC**: Terraform

### Risk Analysis and Mitigation

#### Risk 1: Message Ordering
- **Impact**: Documents processed out of order for same entity
- **Mitigation**: Use Pub/Sub ordering keys if required, or design idempotent processing

#### Risk 2: Cost Increase
- **Impact**: Pub/Sub and Cloud Run costs
- **Mitigation**: 
  - Batch small files into single messages
  - Use pull subscriptions with batching for high-volume tenants
  - Set Cloud Run max instances per tenant

#### Risk 3: Retry Storms
- **Impact**: Transient failures cause excessive retries
- **Mitigation**: 
  - Exponential backoff (Pub/Sub native)
  - Circuit breaker pattern for downstream dependencies
  - Max 5 retry attempts before DLQ

### Timeline Estimate

**Total**: 2-3 weeks (1 engineer)

- Week 1: Infrastructure setup + API changes
- Week 2: Worker implementation + backpressure control
- Week 3: DLQ handling + observability + testing

### Success Metrics

- API latency reduction: 30s → < 100ms (99%+)
- Zero ingestion blocking across tenants
- DLQ message rate < 0.1% of total messages
- 99.9% job completion rate (excluding poison-pill documents)
