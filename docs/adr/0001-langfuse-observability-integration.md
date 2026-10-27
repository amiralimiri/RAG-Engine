# ADR 0001: Langfuse Observability, Tracing, and Evaluation Integration

## Status
Superseded by ADR 0005

## Context
The enterprise IT support copilot codebase implements agentic RAG using LangGraph. The system currently maintains multiple parallel graph workflows:
- `fix_single_llm`: A self-corrective RAG workflow relying on standard ChatOpenAI LLM structured outputs (`deepseek-v4-flash`) for question routing and evidence grading.
- `fix_single_jev`: A parallel self-corrective RAG workflow employing TypeSafe Classifier (`typesafe/jev-1.13`) for deterministic, fast routing and grading decisions.

### Key Constraints & Drivers
1. **Standalone Workflow Preservation**: The separate file structures (`app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py`) must remain intact without deduplication or merging.
2. **Deep Observability**: Full execution visibility into LangGraph nodes, conditional edges, retrieval steps (Pinecone), web searches (Tavily), and LLM generation calls.
3. **Token & Cost Tracking**: Granular usage tracking for both standard OpenAI-compatible endpoints (`deepseek-v4-flash` via GapGPT) and non-standard classifier endpoints (`typesafe/jev-1.13`).
4. **Distinct Workflow Telemetry**: Clear isolation between workflows in traces, analytics, filters, and comparative dashboards.
5. **Prompt Observability & Scoring**: Tracing prompt inputs, retrieved contexts, and model generations, with a unified bridge to capture both human end-user feedback and DeepEval judge evaluations.
6. **Zero-Downtime / Fail-Open Resilience**: Observability failures (network timeouts, missing credentials, outages) must never interrupt production chat completions.

---

## Decisions

### 1. Centralized Telemetry Factory (`app/core/telemetry.py`)
All Langfuse client initialization, callback instantiation, trace ID extraction, and fail-open guards will be encapsulated in a dedicated module (`app/core/telemetry.py`). Workflows and API routes will consume callbacks exclusively through this module.

- **Fail-Open Strategy**: If `LANGFUSE_ENABLED=False` or required credentials (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`) are missing, the factory returns `None` and operations degrade silently to untraced execution.
- **Async & Non-Blocking**: Tracing events are buffered and dispatched in background worker threads by the Langfuse SDK. Lifecycle hooks ensure graceful flushing during application shutdown.

### 2. Request-to-Graph Callback Propagation
To preserve the standalone structure of `fix_single_llm.py` and `fix_single_jev.py` while providing request-level tracing:
- The `ask()` signature in both workflows will be updated to accept optional telemetry parameters:
  ```python
  def ask(
      question: str,
      *,
      session_id: str | None = None,
      user_id: str | None = None,
      metadata: dict | None = None,
      tags: list[str] | None = None,
      callback: CallbackHandler | None = None,
  ) -> dict:
  ```
- Graph compilation remains module-level and unchanged.
- `agent_graph.invoke(initial_state, config={"callbacks": [callback]})` attaches the handler per request.
- If called without a callback (e.g., in unit tests or CLI scripts), `ask()` either obtains a default callback or safely executes untraced.

### 3. Strict Naming, Tagging, and Metadata Taxonomy
To cleanly separate and compare workflows within Langfuse:
- **Trace Name**: `rag:<workflow_name>` (e.g., `rag:fix_single_llm` vs `rag:fix_single_jev`).
- **Tags**: `["workflow:<name>", "env:<app_env>"]`.
- **Trace Metadata**:
  - `fix_single_llm`: `{"classifier": "openai_json", "retrieval_strategy": "self_corrective"}`
  - `fix_single_jev`: `{"classifier": "typesafe_jev", "retrieval_strategy": "self_corrective"}`
- Session IDs and User IDs are propagated from HTTP headers (`X-Session-ID`, `X-User-ID`) or generated per chat conversation.

### 4. Token & Cost Accounting for Non-Standard Models
- `deepseek-v4-flash`: Usage is captured via standard ChatOpenAI response payloads. Custom model pricing definitions will be configured in the Langfuse project settings.
- `typesafe/jev-1.13`: Because `TypeSafeClassifier` does not emit standard OpenAI `token_usage` dictionaries, JEV node executions in `fix_single_jev.py` will be enriched by tracking input/output character lengths (applying a `chars // 4` rule-of-thumb token estimation) and recording explicit span metadata with `model="typesafe/jev-1.13"`. A custom model cost definition will be maintained in Langfuse.

### 5. Two-Phase Prompt Tracking
- **Phase 1 (Immediate)**: Leverage native LangChain/LangGraph `CallbackHandler` generation tracking. All prompt templates, formatted contexts, retrieval chunk contents, and output completions are automatically captured and linked to node spans without refactoring existing prompt f-strings.
- **Phase 2 (Subsequent)**: Introduce Langfuse Prompt CMS (`langfuse.get_prompt`) with local fallback defaults, allowing prompt iteration via UI without requiring code deployments.

### 6. Local Audit DB & API Response Unification
- The SQLite table in `data/audit.db` (`app/services/audit.py`) will be altered to add an optional `trace_id TEXT` column.
- The `POST /api/chat` response schema will return `trace_id`.
- This creates bidirectional traceability: local audit records point directly to cloud Langfuse trace visualizers.

### 7. Human Feedback and DeepEval Scoring Bridge
- **User Feedback**: A new endpoint `POST /api/feedback` will receive `{trace_id, score, name, comment}` and submit scores via FastAPI `BackgroundTasks` to avoid blocking client requests.
- **Automated Judge**: An evaluation scoring bridge (`app/services/evaluation.py`) will run DeepEval metrics (`faithfulness`, `answer_relevancy`) using `JudgeModels` from `evaluation/judge.py` and attach the resulting scores directly to the corresponding Langfuse `trace_id`.

---

## Consequences

### Positive
- **Zero Refactoring of Core Graph Logic**: Standalone workflow files maintain their isolated implementations while gaining observability.
- **Unified Comparative Analytics**: Direct side-by-side comparison of latency, cost, and accuracy between the LLM router/grader and JEV router/grader in Langfuse dashboards.
- **End-to-End Tracing**: Visibility spanning HTTP request reception, routing decision, vector retrieval, evidence grading, web search fallback, query rewriting, final synthesis, and evaluation scoring.
- **High Reliability**: Fail-open guarantees prevent observability outages from impacting end-user availability.

### Trade-offs & Mitigations
- **Estimated JEV Tokens**: Token usage for `TypeSafeClassifier` is calculated using character approximations rather than raw hardware token counts. *Mitigation*: Calibrated through empirical sample benchmarks.
- **Audit DB Migration**: Adding `trace_id` requires backward-compatible SQLite migration. *Mitigation*: Handled via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` or schema migration check at startup.
- **Dependency Addition**: Adds `langfuse` to project dependencies. *Mitigation*: Lightweight SDK with asynchronous HTTP batching.

---

## Implementation Roadmap

1. **Dependencies**: Add `langfuse>=5.0.0` to `pyproject.toml`.
2. **Configuration**: Add Langfuse settings (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENABLED`) to `app/core/config.py`.
3. **Telemetry Core**: Implement `app/core/telemetry.py` conforming to Langfuse v5 standards (`from langfuse.langchain import CallbackHandler`, `get_langfuse_client()`, `extract_trace_id()`).
4. **Audit Extension**: Update `app/services/audit.py` to support `trace_id`.
5. **Workflow Telemetry**:
   - Update `ask()` in `app/rag/fix_single_llm.py` to accept telemetry kwargs and pass callback to `agent_graph.invoke()`.
   - Update `ask()` in `app/rag/fix_single_jev.py` similarly, adding JEV span enrichment using Langfuse v5 `start_observation`.
6. **API Routes**:
   - Update `POST /api/chat` in `app/api/routes.py` to initialize telemetry, pass callbacks, persist `trace_id`, and return it.
   - Add `POST /api/feedback` endpoint with background dispatch using `client.create_score()`.
7. **Evaluation Bridge**: Implement `app/services/evaluation.py` connecting `evaluation/judge.py` to Langfuse trace scores via `client.create_score()`.
