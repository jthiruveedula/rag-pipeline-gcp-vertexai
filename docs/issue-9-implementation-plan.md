# Automated RAG Evaluation Harness - Implementation Plan

## Issue #9: Automated RAG evaluation harness with RAGAS metrics, golden-set regression, and per-tenant scoring dashboards

### Overview

Implement a comprehensive RAG evaluation system using RAGAS (Retrieval Augmented Generation Assessment) framework to continuously measure and monitor the quality of retrieval and generation across all tenants, with automated regression testing against golden question-answer sets.

### Architecture Components

#### 1. RAGAS Metrics Suite
- **Retrieval Metrics**:
  - Context Precision: Measures relevance of retrieved chunks
  - Context Recall: Coverage of ground truth in retrieved context
  - Context Relevancy: Signal-to-noise ratio in retrieval

- **Generation Metrics**:
  - Answer Faithfulness: Generated answer grounded in retrieved context
  - Answer Relevancy: Generated answer addresses the question
  - Answer Correctness: Semantic similarity to ground truth (when available)

- **End-to-End Metrics**:
  - Answer Semantic Similarity: Cosine similarity to expected answer
  - Hallucination Rate: Detects unsupported claims

#### 2. Golden Test Set Management
- **Schema** (BigQuery `evaluation_golden_sets` table):
  ```sql
  CREATE TABLE evaluation_golden_sets (
    id STRING,
    tenant_id STRING,
    question STRING,
    expected_answer STRING,
    expected_context ARRAY<STRING>,
    metadata JSON,
    created_at TIMESTAMP,
    last_updated TIMESTAMP
  );
  ```

- **Test Set Creation**:
  - Manual curation by tenant admins
  - Synthetic generation from documentation
  - Production sampling (user-validated queries)

#### 3. Evaluation Runner Service
- **Cloud Run Service**: `evaluation-runner`
- **Trigger Mechanisms**:
  - Scheduled (daily): Cloud Scheduler → Pub/Sub → Cloud Run
  - On-demand: API endpoint `/evaluate/{tenant_id}`
  - CI/CD: Triggered on prompt/retrieval config changes

- **Execution Flow**:
  1. Fetch golden test set for tenant
  2. Execute RAG pipeline for each question
  3. Collect: {question, retrieved_chunks, generated_answer}
  4. Compute RAGAS metrics using `ragas` Python library
  5. Store results in BigQuery
  6. Compare with historical baseline
  7. Alert on regressions

#### 4. Results Storage (BigQuery)
- **Table**: `evaluation_results`
  ```sql
  CREATE TABLE evaluation_results (
    id STRING,
    tenant_id STRING,
    eval_run_id STRING,
    question_id STRING,
    timestamp TIMESTAMP,
    -- Retrieval Metrics
    context_precision FLOAT64,
    context_recall FLOAT64,
    context_relevancy FLOAT64,
    -- Generation Metrics
    answer_faithfulness FLOAT64,
    answer_relevancy FLOAT64,
    answer_correctness FLOAT64,
    answer_similarity FLOAT64,
    -- Metadata
    latency_ms INT64,
    model_version STRING,
    retrieval_config JSON,
    passed_threshold BOOL
  );
  ```

#### 5. Per-Tenant Dashboards (Looker Studio / Data Studio)
- **Views**:
  - Scorecard: Current vs. historical average for each metric
  - Time Series: Metric trends over time
  - Question Heatmap: Per-question performance matrix
  - Regression Alerts: Questions that dropped > 10% in score
  - Model Comparison: A/B test different prompts/models

- **Filters**:
  - Date range
  - Tenant ID
  - Question categories/tags
  - Model version

#### 6. Alerting System
- **Regression Detection**:
  - Compare current eval run average with 7-day moving average
  - Alert if any metric drops > 15%
  - Alert if > 25% of questions fail threshold

- **Alert Channels**:
  - Email to tenant admin
  - Slack webhook
  - PagerDuty (for critical tenants)

### Implementation Tasks

#### Phase 1: RAGAS Integration (Week 1)
- [ ] Add `ragas` Python library to requirements
- [ ] Create `src/eval/ragas_evaluator.py` module
- [ ] Implement metric computation functions:
  ```python
  def evaluate_rag_response(
      question: str,
      retrieved_contexts: List[str],
      generated_answer: str,
      ground_truth_answer: Optional[str] = None,
      ground_truth_contexts: Optional[List[str]] = None
  ) -> Dict[str, float]
  ```
- [ ] Unit tests with sample data

#### Phase 2: Golden Set Infrastructure (Week 1-2)
- [ ] Create BigQuery `evaluation_golden_sets` table
- [ ] Build admin API endpoints:
  - `POST /api/v1/eval/golden-set/{tenant_id}` - Upload test set
  - `GET /api/v1/eval/golden-set/{tenant_id}` - Retrieve test set
  - `PUT /api/v1/eval/golden-set/{tenant_id}/question/{id}` - Update question
- [ ] Create CSV/JSON import utility
- [ ] Seed initial test sets for pilot tenants (10-20 questions each)

#### Phase 3: Evaluation Runner (Week 2)
- [ ] Create `evaluation-runner` Cloud Run service
- [ ] Implement evaluation orchestrator:
  ```python
  async def run_evaluation(tenant_id: str, test_set_id: str) -> EvalRun:
      # Fetch test set
      # For each question:
      #   - Call RAG API
      #   - Compute RAGAS metrics
      #   - Store results
      # Generate summary report
  ```
- [ ] Add concurrency control (parallel question execution)
- [ ] Store results in `evaluation_results` BigQuery table

#### Phase 4: Scheduling & Triggers (Week 2-3)
- [ ] Set up Cloud Scheduler for daily evaluations
- [ ] Create Pub/Sub topic `evaluation-trigger`
- [ ] Add API endpoint for on-demand evaluation
- [ ] Integrate with CI/CD: Trigger evaluation on config changes

#### Phase 5: Regression Detection (Week 3)
- [ ] Implement baseline comparison logic:
  ```python
  def detect_regressions(
      current_run: EvalRun,
      baseline_window_days: int = 7
  ) -> List[Regression]:
      # Compare metrics
      # Identify failing questions
      # Calculate significance
  ```
- [ ] Create alerting service:
  - Email templates
  - Slack webhook integration
  - Alert deduplication logic

#### Phase 6: Dashboard Development (Week 3-4)
- [ ] Create BigQuery views for dashboard:
  ```sql
  CREATE VIEW eval_metrics_summary AS
  SELECT 
    tenant_id,
    DATE(timestamp) as eval_date,
    AVG(context_precision) as avg_context_precision,
    AVG(answer_faithfulness) as avg_answer_faithfulness,
    ...
  FROM evaluation_results
  GROUP BY tenant_id, eval_date;
  ```
- [ ] Build Looker Studio dashboard:
  - Connect to BigQuery views
  - Design scorecard, time series, heatmap visualizations
  - Add tenant filter
- [ ] Share dashboard template with tenant admins

#### Phase 7: Testing & Documentation (Week 4)
- [ ] Integration tests with live API
- [ ] Load testing (100+ concurrent evaluations)
- [ ] Write admin documentation:
  - How to create golden test sets
  - How to interpret metrics
  - How to set alert thresholds
- [ ] Write developer documentation for extending metrics

### Acceptance Criteria

1. **Automated Evaluation**:
   - Daily scheduled evaluation runs for all active tenants
   - Evaluation completes in < 10 minutes for 50-question test set
   - On-demand evaluation available via API

2. **Metric Coverage**:
   - All 6+ RAGAS metrics computed and stored
   - Per-question breakdown available
   - Historical data retained for 90 days minimum

3. **Regression Detection**:
   - Automatically detect > 15% metric drops
   - Alert sent within 5 minutes of evaluation completion
   - Alert includes failing question IDs and metric details

4. **Dashboard Usability**:
   - Tenant-scoped dashboard with filters
   - Real-time data (< 5 minute lag from evaluation)
   - Drill-down to individual question performance

5. **Golden Set Management**:
   - Tenant admins can CRUD test questions via API
   - Support for 100+ questions per tenant
   - Version control for test set changes

### Technology Stack

- **Evaluation Framework**: RAGAS (Python library)
- **Compute**: Cloud Run (evaluation-runner service)
- **Storage**: BigQuery (golden sets, evaluation results)
- **Scheduling**: Cloud Scheduler + Pub/Sub
- **Visualization**: Looker Studio / Data Studio
- **Alerting**: SendGrid (email), Slack webhooks

### Dependencies

- `ragas>=0.1.0`: Core evaluation library
- `langchain>=0.1.0`: For LLM-based metric computation
- `sentence-transformers`: For embedding-based similarity
- `pandas`, `numpy`: Data manipulation
- `google-cloud-bigquery`, `google-cloud-scheduler`

### Risk Analysis and Mitigation

#### Risk 1: RAGAS Metrics Require LLM Calls
- **Impact**: Cost ($0.001/question) and latency (2-5s/question)
- **Mitigation**: 
  - Cache metric results for identical question/answer pairs
  - Use smaller models (Gemini Flash) for evaluation
  - Batch evaluation runs during off-peak hours

#### Risk 2: Golden Set Quality
- **Impact**: Poor test sets → misleading metrics
- **Mitigation**:
  - Provide test set creation guidelines
  - Implement quality checks (e.g., expected_answer length > 10 chars)
  - Start with small pilot test sets and iterate

#### Risk 3: Evaluation Drift
- **Impact**: Metrics change due to model/prompt updates, not quality
- **Mitigation**:
  - Track configuration changes in metadata
  - A/B test evaluations: old vs. new config side-by-side
  - Manual review of first 10 regressions

### Timeline Estimate

**Total**: 3-4 weeks (1 engineer)

- Week 1: RAGAS integration + Golden set schema
- Week 2: Evaluation runner + Scheduling
- Week 3: Regression detection + Alerting
- Week 4: Dashboard + Documentation

### Success Metrics

- Evaluation coverage: 100% of production tenants have golden test sets
- Evaluation frequency: Daily runs with > 95% success rate
- Regression detection: Catch quality drops within 24 hours
- Dashboard adoption: 80%+ of tenant admins view dashboard monthly
- Mean time to resolution: Regressions fixed within 48 hours of alert
