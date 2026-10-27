# ADR 0005: Enterprise Langfuse v5 LLMOps Evolution & Infrastructure Consolidation

## Status
Accepted (Supersedes ADR 0001)

## Context
In ADR 0001, basic Langfuse observability was introduced to trace the self-corrective LangGraph workflows (`fix_single_llm` and `fix_single_jev`). While request-level traces were linked to Langfuse, critical production LLMOps capabilities remained unaddressed or fragmented across redundant systems:

1. **Redundant Dual Persistence (`audit.py` & `data/audit.db`)**: 
   Every query executed an explicit synchronous SQLite insert via `app/services/audit.py`. Since Langfuse v5 provides query persistence, session grouping, user attribution, and trace inspection, local SQLite storage introduced operational overhead, schema migration maintenance (`ALTER TABLE` logic), and disk I/O contention without adding value.
2. **Disconnected Evaluation Engine (`app/services/evaluation.py`)**: 
   A DeepEval evaluation service was implemented in Ticket 07, but it remained completely disconnected from runtime chat traffic. Traces in Langfuse lacked automated faithfulness and relevancy quality scores.
3. **Missing Cost & Token Visibility**: 
   Langfuse dashboards showed incomplete financial analytics. OpenAI-compatible LLM proxy calls (GapGPT) and custom TypeSafe Classifier spans (`typesafe/jev-1.13`) lacked explicit cost reporting and deterministic unit economics.
4. **Hardcoded Prompts without Lifecycle Management**: 
   All prompt strings (routing instructions, evidence graders, query rewriters, and answer generators) were hardcoded inside Python modules. Experimenting with prompt iterations required code deployments, and spans lacked version linking to measure performance shifts across prompt revisions.
5. **Opaque Retrieval & Lack of Comparative Experimentation**: 
   Vector store retrieval (`retrieve_kb`) and external web search (`search_web`) were not instrumented as explicit child `retriever` / `tool` observations inside LangGraph. Furthermore, there was no automated mechanism to benchmark `fix_single_llm` against `fix_single_jev` on standardized golden datasets within Langfuse Experiments.

---

## Decisions

### 1. Pillar 1: Decommissioning Redundant Local SQLite Audit Store
The application completely eliminates SQLite query auditing:
- **Eradicate `app/services/audit.py` and `data/audit.db`**: All invocations of `write_audit()` in `/api/chat` and `/api/chat/stream` are removed.
- **Single Source of Truth**: Langfuse is the sole telemetry and trace persistence engine.
- **Offline / Disconnected Resilience**: When `LANGFUSE_ENABLED=false` (local development or air-gapped CI test runs), structured JSON logging (`structlog` to stdout / `app.log`) provides complete auditability without maintaining a local relational schema.
- **Lifespan Clean-Up**: Remove `init_db()` startup execution from `app/main.py`.

---

### 2. Pillar 2: Asynchronous Online DeepEval Scoring Engine
Automated LLM-as-a-judge evaluations run continuously against live production traffic without degrading user-facing latency:
- **Execution Model**: Evaluations execute asynchronously via FastAPI `BackgroundTasks` on `POST /api/chat`, and via background completion hooks (`run_in_threadpool`) upon SSE stream completion on `POST /api/chat/stream`.
- **Sampling & Metric Configuration**:
  - `EVAL_SAMPLE_RATE` (`float`, default `1.0` in staging/dev, configurable `0.0` to `1.0` in production) samples traces deterministically using `random.random() < settings.eval_sample_rate`.
  - `EVAL_METRICS` (`list[str]`, default `["faithfulness", "answer_relevancy"]`) governs active metrics.
- **Direct Answer & Empty Context Resilience**:
  - `FaithfulnessMetric` requires retrieval context. When `source_used == "direct"` or `retrieved_contexts` is empty, faithfulness evaluation is automatically skipped, evaluating only `AnswerRelevancyMetric`.
- **Fault-Tolerant Circuit Boundary**: Online evaluation runs within a strict 30-second timeout and fail-open `try/except` boundary. Network timeouts, rate limits, or judge errors log warnings and never impact API responses or retry indefinitely.
- **Trace Metric Attachment**: Calculated scores and judge reasoning comments are posted directly to the trace via `client.create_score()` / `client.score()`.

---

### 3. Pillar 3: Retrieved Context Formalization in Graph State
To provide the judge with exact context chunks:
- **State Schema Extension**: Add `retrieved_contexts: list[str]` to `AgentState`.
- **Workflow State Accumulation**:
  - In `retrieve_kb`, chunks are extracted: `[d.page_content for d in docs]`.
  - In `search_web`, retrieved snippets are formatted: `[f"{item.get('title')}: {item.get('content')}" for item in results]`.
- **Return Contract**: Both `fix_single_llm.ask()` and `fix_single_jev.ask()` return `retrieved_contexts` alongside `answer`, `source_used`, and `trace`.

---

### 4. Pillar 4: Deterministic Token & Cost Accounting
Explicit unit economics are configured and reported to Langfuse:
- **Settings Multipliers (`app/core/config.py`)**:
  - `llm_cost_per_1k_input_tokens: float = 0.0015`
  - `llm_cost_per_1k_output_tokens: float = 0.0020`
  - `jev_cost_per_1k_input_tokens: float = 0.0008`
  - `jev_cost_per_1k_output_tokens: float = 0.0008`
- **Observation Injection**:
  - For JEV classifier spans (`_record_jev_generation`), character-based token estimates (`usage_details = {"input": ..., "output": ..., "total": ...}`) are mapped to explicit monetary costs:
    $$\text{cost} = \frac{\text{tokens} \times \text{rate}}{1000}$$
  - Pass `usage_details` and `cost_details` directly to `client.start_observation(as_type="generation", ...)`.
  - For standard ChatOpenAI calls via GapGPT, token usage reported in response metadata is captured by LangChain's callback handler and augmented with pricing metadata.

---

### 5. Pillar 5: Langfuse Prompt CMS Integration (`app/services/prompts.py`)
All prompt templates are managed dynamically via Langfuse Prompt CMS with complete fail-open local resilience:
- **Service Layer (`app/services/prompts.py`)**:
  - Manages prompt retrieval using `client.get_prompt(name, cache_ttl_seconds=300, fallback=DEFAULT_STRING)`.
  - Implements a `FallbackPrompt` dataclass (`compile(**kwargs)`, `version=0`) ensuring that if Langfuse is disabled or unreachable, workflow nodes continue seamlessly without conditionals.
- **Taxonomy**:
  - `rag:router:llm` & `rag:router:jev`
  - `rag:grade_kb:llm` & `rag:grade_kb:jev`
  - `rag:grade_web:llm` & `rag:grade_web:jev`
  - `rag:rewrite_query`
  - `rag:generate_kb`
  - `rag:generate_web`
  - `rag:direct_answer`
- **Template Variables**: Adheres to standard mustache syntax (`{{question}}`, `{{context}}`, `{{web_results}}`).
- **Prompt-to-Observation Linking**: The resolved `PromptClient` object is passed into LLM invocations and JEV `start_observation` calls, linking generations to prompt versions in Langfuse.

---

### 6. Pillar 6: Rich Retriever & Tool Observation Propagation
To eliminate opaque graph execution:
- Node functions accept `config: RunnableConfig` from LangGraph.
- Calls to `get_retriever().invoke(query, config=config)` propagate the `LangfuseCallbackHandler`, triggering `on_retriever_start` and `on_retriever_end`.
- Calls to `web_search_tool().invoke({"query": query}, config=config)` trigger `on_tool_start` and `on_tool_end`.
- Vector chunks, similarity scores, query text, and document metadata appear as first-class child observations nested directly under the workflow root trace.

---

### 7. Pillar 7: Langfuse Dataset Experimentation Runner
A standalone benchmarking suite enables empirical comparison between `fix_single_llm` and `fix_single_jev`:
- **Curated Gold Benchmark (`data/gold_benchmarks.json`)**: Contains 10 representative test cases covering greetings, private KB lookups, multi-chunk queries, web fallback scenarios, query rewriting, and unanswerable edge cases.
- **Experiment Runner (`evaluation/run_experiment.py`)**:
  - Connects to Langfuse Dataset `it-support-benchmark` (auto-seeding from `data/gold_benchmarks.json` if absent).
  - Uses Langfuse v5's `client.run_experiment()` to execute both workflows against the dataset.
  - Automatically scores both workflows using DeepEval (Faithfulness & Answer Relevancy).
  - Outputs a comparative terminal summary table contrasting latency, token costs, faithfulness, and answer relevancy.

---

## Architectural Consequences

### Positive
- **Reduced Maintenance**: Removal of `audit.py` and `audit.db` eliminates 80+ lines of SQLite migration plumbing and prevents SQLite table lock errors.
- **Zero Latency Impact on Users**: DeepEval evaluation runs purely in the background; Time to First Token and total response latency are unaffected.
- **Enterprise Financial Transparency**: Granular per-1k-token accounting for both GapGPT and JEV provides exact cost per query across workflows.
- **Decoupled Prompt Lifecycles**: Prompt engineers can tune system prompts, grading criteria, and generation instructions in Langfuse UI without triggering code deployments.
- **Architectural Preservation**: `fix_single_llm.py` and `fix_single_jev.py` remain strictly isolated standalone modules.

### Negative / Trade-offs
- **Background Judge Compute Overhead**: Live DeepEval scoring consumes background LLM tokens. Mitigated via `EVAL_SAMPLE_RATE` throttling.
- **Prompt Cache Staleness**: A 300-second client-side TTL means prompt updates in Langfuse take up to 5 minutes to propagate unless the service is restarted or the cache is cleared.

---

## Migration and Verification Strategy
1. **ADR Record**: Commit `docs/adr/0005-enterprise-langfuse-v5-llmops-evolution.md` and mark ADR 0001 as superseded.
2. **Technical Specification**: Produce `docs/specs/0005-enterprise-langfuse-v5-llmops-evolution-spec.md` with implementation tickets.
3. **Decommissioning**: Remove `app/services/audit.py`, `data/audit.db`, and obsolete tests (`tests/test_audit_migration.py`), updating test mocks.
4. **Verification**: Full pytest execution (`pytest tests/`) validating 100% green status across all existing and new unit/integration tests.
