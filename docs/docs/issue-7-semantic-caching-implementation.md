# Semantic Answer Caching with Redis - Implementation Plan

## Issue #7: Semantic answer caching with Redis and cosine-similarity deduplication to reduce Gemini API costs

### Overview

Implement a semantic answer cache using Cloud Memorystore (Redis) and embedding-based similarity lookup to reduce Gemini API costs by 30-60%. The cache will encode incoming queries, search for sufficiently similar prior queries, and return cached answers when similarity threshold is met.

### Problem Statement

Every `/ask` request currently calls the Gemini API even for semantically identical questions. For enterprise tenants with large user bases, many queries converge on the same policy questions (e.g., "What is the PTO policy?"). This leads to:
- Unnecessary Gemini API costs
- Increased latency for repeat questions
- Higher quota consumption

### Architecture Components

#### 1. Cache Layer (`cache/semantic_cache.py`)

**New Module**: `cache/semantic_cache.py`

**Core Functions**:
- `get(query: str, tenant_id: str, threshold: float = 0.92) -> Optional[CachedAnswer]`
  - Embeds query using `text-embedding-004`
  - Searches Redis for similar queries using cosine similarity
  - Returns cached answer if similarity ≥ threshold
  - Per-tenant isolation via namespace

- `set(query: str, answer: str, sources: List[str], tenant_id: str, ttl: int = 3600)`
  - Stores query embedding + answer in Redis
  - Default 1-hour TTL
  - Tenant-scoped key: `cache:{tenant_id}:{embedding_hash}`

**Data Model**:
```python
@dataclass
class CachedAnswer:
    answer: str
    sources: List[str]
    query_embedding: List[float]
    cached_at: datetime
    similarity_score: float
```

#### 2. Integration Points

**Modified**: `pipeline/generator.py`
- Add cache check before Gemini API call
- On cache miss: call Gemini and populate cache
- On cache hit: return cached answer directly

**Pseudo-code**:
```python
async def generate_answer(query: str, tenant_id: str, context: List[str]):
    # Check cache first
    cached = await semantic_cache.get(query, tenant_id, threshold=0.92)
    if cached:
        metrics.increment("cache_hit", tenant_id=tenant_id)
        return cached.answer, cached.sources
    
    # Cache miss - call Gemini
    metrics.increment("cache_miss", tenant_id=tenant_id)
    answer, sources = await call_gemini(query, context)
    
    # Populate cache for next time
    await semantic_cache.set(query, answer, sources, tenant_id)
    return answer, sources
```

#### 3. Infrastructure

**Cloud Memorystore (Redis)**:
- Instance: Standard tier, 5GB memory
- Region: Same as Cloud Run (us-central1)
- VPC: Serverless VPC connector for Cloud Run access
- Persistence: Enabled with RDB snapshots

**Dependencies**:
- `redis-py`: Redis client library
- `numpy`: For cosine similarity calculations
- `google-cloud-aiplatform`: For text-embedding-004

#### 4. Configuration

**Tenant-specific settings** (in `tenant_config`):
```yaml
cache:
  enabled: true
  similarity_threshold: 0.92
  ttl_seconds: 3600
  max_cache_size_mb: 100
```

**Environment variables**:
```
REDIS_HOST=10.x.x.x
REDIS_PORT=6379
REDIS_PASSWORD=<secret>
CACHE_ENABLED=true
```

### Implementation Tasks

- [ ] Set up Cloud Memorystore (Redis) instance in GCP
- [ ] Create VPC Serverless Connector for Cloud Run → Redis connectivity
- [ ] Implement `cache/semantic_cache.py` module
  - [ ] `get()` method with embedding similarity search
  - [ ] `set()` method with TTL and tenant namespacing
  - [ ] `invalidate()` method for corpus updates
  - [ ] Connection pooling and error handling
- [ ] Integrate cache into `pipeline/generator.py`
  - [ ] Add cache check before Gemini call
  - [ ] Add cache population after Gemini response
  - [ ] Add fallback logic if cache unavailable
- [ ] Implement observability
  - [ ] `cache_hit_rate` metric per tenant
  - [ ] `cache_latency` metric
  - [ ] `embedding_generation_time` metric
  - [ ] Cloud Monitoring dashboard
- [ ] Add tenant-level configuration
  - [ ] Configurable similarity threshold
  - [ ] Configurable TTL
  - [ ] Cache enable/disable toggle
- [ ] Implement cache invalidation strategy
  - [ ] On document re-ingestion
  - [ ] On tenant request
  - [ ] TTL-based expiration
- [ ] Unit tests
  - [ ] Test cache hit with high similarity (>0.92)
  - [ ] Test cache miss with low similarity (<0.92)
  - [ ] Test tenant isolation
  - [ ] Test TTL expiration
- [ ] Integration tests
  - [ ] End-to-end flow with Redis
  - [ ] Performance benchmarks
  - [ ] Load testing (1000-query benchmark)
- [ ] Documentation
  - [ ] Architecture documentation
  - [ ] Runbook for cache operations
  - [ ] Tenant configuration guide

### Technical Stack

- **Cache**: Cloud Memorystore (Redis)
- **Embeddings**: Vertex AI text-embedding-004
- **Backend**: Python 3.11 with FastAPI
- **Client Library**: redis-py with async support
- **Vector Similarity**: NumPy cosine similarity
- **Monitoring**: Cloud Monitoring + custom metrics

### Acceptance Criteria

✅ **Functionality**:
- Semantically equivalent queries (similarity ≥ 0.92) return same cached answer
- Example: "What is parental leave?" vs "Tell me about parental leave policy"
- Cache miss correctly calls Gemini and populates cache

✅ **Performance**:
- Cache hit rate ≥ 30% on 1000-query benchmark with representative queries
- Cache lookup latency < 50ms (p95)
- Total request latency reduced by 60%+ on cache hits (vs full Gemini call)

✅ **Security & Isolation**:
- Tenant A's cache entries never returned for Tenant B queries
- Redis connection authenticated and encrypted
- No PII stored in cache keys (only embedding hashes)

✅ **Observability**:
- `cache_hit_rate` metric emitted per tenant
- Cloud Monitoring dashboard shows cache performance
- Alerts configured for low hit rate or high error rate

✅ **Reliability**:
- Cache failures do not break `/ask` endpoint (graceful fallback)
- Cache invalidated correctly when documents re-ingested
- Redis connection pool handles connection failures

### Success Metrics

- **Cost Reduction**: 30-60% reduction in Gemini API calls
- **Hit Rate**: ≥ 30% cache hit rate in steady state
- **Latency**: < 50ms cache lookup (p95)
- **Accuracy**: > 95% user satisfaction with cached answers (no incorrect answers)

### Timeline

Estimated: 1-2 weeks for full implementation and testing

**Week 1**:
- Day 1-2: Infrastructure setup (Redis, VPC connector)
- Day 3-4: Core cache module implementation
- Day 5: Integration with generator pipeline

**Week 2**:
- Day 1-2: Testing and benchmarking
- Day 3: Observability and monitoring
- Day 4-5: Documentation and deployment

### Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Redis downtime breaks service | High | Implement graceful fallback to direct Gemini calls |
| Cache poisoning with incorrect answers | High | Implement cache invalidation on doc updates; monitor user feedback |
| High memory usage for embeddings | Medium | Use embedding hash instead of full vectors; implement LRU eviction |
| Embedding generation adds latency | Medium | Batch embedding requests; use async processing |
| Low hit rate in practice | Medium | Tune similarity threshold per tenant; analyze query patterns |

### Follow-up Work

- Advanced cache warming strategies
- Semantic clustering for better cache utilization
- Cross-tenant cache for common queries (with privacy controls)
- ML-based cache eviction policies
- Integration with prompt template versioning (Issue #6)
