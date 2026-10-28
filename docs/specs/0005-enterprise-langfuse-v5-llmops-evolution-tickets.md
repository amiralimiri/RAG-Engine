# Work Packages / Implementation Tickets: Enterprise Langfuse v5 LLMOps Evolution & Infrastructure Consolidation

- **Specification Reference:** [`docs/specs/0005-enterprise-langfuse-v5-llmops-evolution-spec.md`](./0005-enterprise-langfuse-v5-llmops-evolution-spec.md)
- **ADR Reference:** [`docs/adr/0005-enterprise-langfuse-v5-llmops-evolution.md`](../adr/0005-enterprise-langfuse-v5-llmops-evolution.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
┌─────────────────────────────────┐   ┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│ Ticket 01: Decommission Audit   │   │ Ticket 02: Config & Cost Multi- │   │ Ticket 06: Formalize Retrieved  │
│ DB (SQLite & Lifespan Cleanup)  │   │ pliers (app/core/config.py)     │   │ Contexts in AgentState          │
│ (audit.py, main.py, routes.py)  │   │                                 │   │ (state.py, LLM & JEV asks)      │
└───────────────┬─────────────────┘   └───────────────┬─────────────────┘   └───────────────┬─────────────────┘
                │                                     │                                     │
                │                                     ▼                                     │
                │                     ┌─────────────────────────────────┐                   │
                │                     │ Ticket 03: Langfuse Prompt CMS  │                   │
                │                     │ Service & Fallback Catalog      │                   │
                │                     │ (app/services/prompts.py)       │                   │
                │                     └───────────────┬─────────────────┘                   │
                │                                     │                                     │
                │              ┌──────────────────────┴──────────────────────┐              │
                │              ▼                                             ▼              │
                │ ┌─────────────────────────────────┐       ┌─────────────────────────────────┐
                │ │ Ticket 04: Retriever & Web Tool │       │ Ticket 05: JEV Cost Accounting  │
                │ │ Observations in LLM & JEV Nodes │       │ & Prompt Observation Linking    │
                │ │ (fix_single_llm, fix_single_jev)│       │ (app/rag/fix_single_jev.py)     │
                │ └────────────────┬────────────────┘       └────────────────┬────────────────┘
                │                  │                                         │              │
                ▼                  │                                         │              ▼
┌──────────────────────────────────┴┐                                        │ ┌───────────────────────────────┐
│ Ticket 07: Asynchronous Online   │                                        │ │ Ticket 08: Golden Dataset     │
│ DeepEval Scoring Engine & Routes  │                                        │ │ & Experiment Runner           │
│ (app/services/evaluation.py)      │                                        │ │ (gold_benchmarks, runner.py)  │
└─────────────────┬─────────────────┘                                        │ └───────────────┬───────────────┘
                  │                                                          │                 │
                  └──────────────────────────────┬───────────────────────────┴─────────────────┘
                                                 │
                                                 ▼
                                ┌──────────────────────────────────┐
                                │ Ticket 09: Comprehensive TDD &   │
                                │ End-to-End Validation Suite      │
                                └──────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Decommission SQLite Audit Store | `app/services/audit.py`, `data/audit.db`, `app/main.py`, `app/api/routes.py`, `tests/test_audit_migration.py` | None | `completed` |
| **Ticket 02** | Configuration Settings & Unit Cost Accounting | `app/core/config.py` | None | `completed` |
| **Ticket 03** | Langfuse Prompt CMS Service & Fallback Catalog | `app/services/prompts.py`, `tests/test_prompts_cms.py` | Ticket 02 | `completed` |
| **Ticket 04** | Instrument Vector Retriever & Web Search Spans | `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py` | Ticket 02 | `completed` |
| **Ticket 05** | JEV Cost Accounting & Prompt Observation Linking | `app/rag/fix_single_jev.py` | Ticket 02, Ticket 03 | `completed` |
| **Ticket 06** | Formalize Retrieved Contexts in Graph State | `app/rag/state.py`, `app/rag/fix_single_llm.py`, `app/rag/fix_single_jev.py` | None | `completed` |
| **Ticket 07** | Asynchronous Online DeepEval Scoring Engine & Routes | `app/services/evaluation.py`, `app/api/routes.py`, `tests/test_online_evaluation.py` | Ticket 01, Ticket 02, Ticket 06 | `completed` |
| **Ticket 08** | Benchmark Golden Dataset & Experiment Runner | `data/gold_benchmarks.json`, `evaluation/run_experiment.py`, `tests/test_experiment_runner.py` | Ticket 06 | `completed` |
| **Ticket 09** | Comprehensive TDD & End-to-End Test Suite | `tests/test_token_cost_accounting.py`, `tests/test_chat_stream.py`, `tests/test_feedback_api.py`, `tests/test_e2e_observability.py` | Tickets 01–08 | `ready` |

---

## Ticket 01: Decommission SQLite Audit Store

- **Title:** Eradicate Redundant Local SQLite Audit Store (`app/services/audit.py` and `data/audit.db`)
- **Blockers:** None
- **Files Modified/Deleted:**
  - `app/services/audit.py` (Delete)
  - `data/audit.db` (Delete from filesystem and `.gitignore`)
  - `tests/test_audit_migration.py` (Delete)
  - `app/main.py` (Modify: remove `init_db()` from lifespan startup)
  - `app/api/routes.py` (Modify: remove `write_audit()` imports and route calls)
- **Deliverables:**
  1. Remove `app/services/audit.py` and remove references to `audit_db_path` from runtime modules.
  2. In `app/main.py`, ensure lifespan executes startup logging without SQLite table checks, retaining `flush_telemetry()` on shutdown.
  3. In `app/api/routes.py`, remove all three calls to `write_audit()`, ensuring completions are logged via structured JSON `structlog` events (`chat_query_completed`).
  4. Delete `tests/test_audit_migration.py`.
- **Acceptance Criteria:**
  - [x] `app/services/audit.py` is deleted and nowhere imported across the codebase.
  - [x] `POST /api/chat` and `POST /api/chat/stream` execute cleanly without invoking SQLite.
  - [x] Application starts and shuts down cleanly via FastAPI `lifespan` without `init_db()`.
  - [x] `git status` shows `tests/test_audit_migration.py` removed.
- **TDD Verification:**
  ```bash
  uv run pytest tests/test_e2e_observability.py -k "test_lifespan"
  ```

---

## Ticket 02: Configuration Settings & Unit Cost Accounting

- **Title:** Update `Settings` with Online Evaluation Gates and Per-1k-Token Cost Multipliers
- **Blockers:** None
- **Files Modified:**
  - `app/core/config.py`
- **Deliverables:**
  1. Remove obsolete `audit_db_path: str = "./data/audit.db"`.
  2. Add Prompt CMS setting `langfuse_prompt_cache_ttl_seconds: int = 300`.
  3. Add Online Evaluation settings:
     - `eval_sample_rate: float = 1.0`
     - `eval_metrics: list[str] = ["faithfulness", "answer_relevancy"]`
     - `eval_timeout_seconds: float = 30.0`
  4. Add Unit Token Cost settings (in USD per 1,000 tokens):
     - `llm_cost_per_1k_input_tokens: float = 0.0015`
     - `llm_cost_per_1k_output_tokens: float = 0.0020`
     - `jev_cost_per_1k_input_tokens: float = 0.0008`
     - `jev_cost_per_1k_output_tokens: float = 0.0008`
- **Acceptance Criteria:**
  - [x] `get_settings()` exposes all evaluation and cost settings with verified default values.
  - [x] `audit_db_path` attribute is removed from `Settings`.
  - [x] Existing environment variable parsing remains backward-compatible via `.env`.
- **TDD Verification:**
  ```bash
  uv run python -c "from app.core.config import get_settings; s = get_settings(); assert s.eval_sample_rate == 1.0; assert s.jev_cost_per_1k_input_tokens == 0.0008"
  ```

---

## Ticket 03: Langfuse Prompt CMS Service & Fallback Catalog

- **Title:** Implement `app/services/prompts.py` with 300s TTL Caching and Fail-Open `FallbackPrompt`
- **Blockers:** Ticket 02
- **Files Created/Modified:**
  - `app/services/prompts.py` (Create)
  - `tests/test_prompts_cms.py` (Create)
- **Deliverables:**
  1. Create `FallbackPrompt` dataclass with `compile(**kwargs)` (supporting `{{variable}}` regex substitution) and `version=0`.
  2. Populate `LOCAL_PROMPTS` dictionary covering all 10 standard prompt keys:
     - `rag:router:llm`, `rag:router:jev`
     - `rag:grade_kb:llm`, `rag:grade_kb:jev`
     - `rag:grade_web:llm`, `rag:grade_web:jev`
     - `rag:rewrite_query`
     - `rag:generate_kb`
     - `rag:generate_web`
     - `rag:direct_answer`
  3. Implement `get_workflow_prompt(name: str) -> TextPromptClient | FallbackPrompt` using `client.get_prompt(name, cache_ttl_seconds=300, fallback=LOCAL_PROMPTS[name])`.
  4. Gracefully catch network or client errors and return `FallbackPrompt` without throwing exceptions.
- **Acceptance Criteria:**
  - [x] `get_workflow_prompt` returns a prompt object with `.compile(...)` and `.version` attributes.
  - [x] When Langfuse is unreachable or `LANGFUSE_ENABLED=false`, `FallbackPrompt` compiles identical text to the current hardcoded strings.
  - [x] All 10 prompt keys in `LOCAL_PROMPTS` compile without errors when passed required variables.
- **TDD Verification:**
  ```bash
  uv run pytest tests/test_prompts_cms.py
  ```

---

## Ticket 04: Instrument Vector Retriever & Web Search Spans

- **Title:** Propagate `RunnableConfig` into Retrieval and Web Search Nodes Across Standalone Workflows
- **Blockers:** Ticket 02
- **Files Modified:**
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`
- **Deliverables:**
  1. Update `retrieve_kb(state: AgentState, config: RunnableConfig | None = None)` in both files to invoke `get_retriever().invoke(state["current_query"], config=config)`.
  2. Update `search_web(state: AgentState, config: RunnableConfig | None = None)` in both files to invoke `web_search_tool().invoke({"query": state["current_query"]}, config=config)`.
  3. Ensure `fix_single_llm.py` and `fix_single_jev.py` remain strictly decoupled without shared helper imports between each other.
- **Acceptance Criteria:**
  - [x] `retrieve_kb` passes `config` down to VectorStore retriever, firing `on_retriever_start` and `on_retriever_end`.
  - [x] `search_web` passes `config` down to TavilySearch tool, firing `on_tool_start` and `on_tool_end`.
  - [x] Standalone file boundary between `fix_single_llm.py` and `fix_single_jev.py` is 100% preserved.
- **TDD Verification:**
  ```bash
  uv run pytest tests/ -k "test_ask or test_chat"
  ```

---

## Ticket 05: JEV Cost Accounting & Prompt Observation Linking

- **Title:** Calculate Deterministic Unit Costs and Link Prompts in Custom JEV Generation Spans
- **Blockers:** Ticket 02, Ticket 03
- **Files Modified:**
  - `app/rag/fix_single_jev.py`
- **Deliverables:**
  1. Update `_record_jev_generation` in `app/rag/fix_single_jev.py` to accept optional `prompt_client: Any | None = None`.
  2. Compute deterministic `cost_details`:
     $$\text{input\_cost} = \frac{\text{usage['input']} \times \text{jev\_cost\_per\_1k\_input\_tokens}}{1000.0}$$
     $$\text{output\_cost} = \frac{\text{usage['output']} \times \text{jev\_cost\_per\_1k\_output\_tokens}}{1000.0}$$
  3. Pass `cost_details` and `usage_details` to `client.start_observation(as_type="generation", ...)`.
  4. In `route_question_jev`, `grade_kb_jev`, and `grade_web_jev`, obtain the prompt via `get_workflow_prompt(...)` and pass it into `_record_jev_generation()`.
- **Acceptance Criteria:**
  - [x] JEV generation observations include both `usage_details` and `cost_details` in USD.
  - [x] Observations are linked to the active prompt version when `prompt_client` is supplied.
  - [x] Graph execution succeeds when `LANGFUSE_ENABLED=false` without calculation errors.
- **TDD Verification:**
  ```bash
  uv run pytest tests/ -k "test_jev or test_chat"
  ```

---

## Ticket 06: Formalize Retrieved Contexts in Graph State

- **Title:** Add `retrieved_contexts: list[str]` to `AgentState` and Workflow Return Dictionaries
- **Blockers:** None
- **Files Modified:**
  - `app/rag/state.py`
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`
- **Deliverables:**
  1. In `app/rag/state.py`, add `retrieved_contexts: NotRequired[list[str]]` to `AgentState`.
  2. In `retrieve_kb`, populate `retrieved_contexts` with `[d.page_content for d in docs]`.
  3. In `search_web`, append web search snippets (`Title: ...\nContent: ...`) to `retrieved_contexts`.
  4. In `ask()` for both `fix_single_llm.py` and `fix_single_jev.py`, return `retrieved_contexts` in the result dictionary.
- **Acceptance Criteria:**
  - [x] `ask()` returns `"retrieved_contexts": [...]` containing raw chunk strings for KB and Web answers.
  - [x] When route is direct, `"retrieved_contexts"` is empty list `[]`.
  - [x] Both workflows exhibit identical return dictionary structures for retrieved contexts.
- **TDD Verification:**
  ```bash
  uv run python -c "from app.rag import fix_single_llm, fix_single_jev; r1 = fix_single_llm.ask('hi'); r2 = fix_single_jev.ask('hi'); assert 'retrieved_contexts' in r1 and 'retrieved_contexts' in r2"
  ```

---

## Ticket 07: Asynchronous Online DeepEval Scoring Engine & Routes

- **Title:** Implement `run_online_evaluation_task` and Wire Background Execution into Chat & Streaming Routes
- **Blockers:** Ticket 01, Ticket 02, Ticket 06
- **Files Modified/Created:**
  - `app/services/evaluation.py` (Modify)
  - `app/api/routes.py` (Modify)
  - `tests/test_online_evaluation.py` (Create)
- **Deliverables:**
  1. In `app/services/evaluation.py`, implement `async def run_online_evaluation_task(...)`:
     - Checks `settings.langfuse_enabled` and sampling rate (`random.random() < settings.eval_sample_rate`).
     - Evaluates `FaithfulnessMetric` (only when `source_used != "direct"` and `retrieved_contexts` is non-empty).
     - Evaluates `AnswerRelevancyMetric`.
     - Enforces a 30-second `asyncio.wait_for` timeout.
     - Submits normalized scores and judge reasoning to Langfuse via `score_trace_metric()`.
  2. In `app/api/routes.py`:
     - In `POST /api/chat`, enqueue `run_online_evaluation_task` via FastAPI `BackgroundTasks`.
     - In `chat_event_generator()` (`POST /api/chat/stream`), trigger `run_online_evaluation_task` asynchronously upon stream completion.
- **Acceptance Criteria:**
  - [x] `/api/chat` returns HTTP 200 immediately without waiting for DeepEval judge execution.
  - [x] `/api/chat/stream` emits the final `metadata` event without waiting for evaluation.
  - [x] Faithfulness is skipped when `source_used == "direct"` or context is empty.
  - [x] Judge timeouts or exceptions do not crash the server or propagate to the user.
- **TDD Verification:**
  ```bash
  uv run pytest tests/test_online_evaluation.py
  ```

---

## Ticket 08: Benchmark Golden Dataset & Experiment Runner

- **Title:** Create `data/gold_benchmarks.json` and Langfuse Datasets CLI Runner `evaluation/run_experiment.py`
- **Blockers:** Ticket 06
- **Files Created:**
  - `data/gold_benchmarks.json` (Create)
  - `evaluation/run_experiment.py` (Create)
  - `tests/test_experiment_runner.py` (Create)
- **Deliverables:**
  1. Author `data/gold_benchmarks.json` with 10 curated test cases across greeting, private KB, web fallback, query rewrite, and insufficient evidence categories.
  2. Build `evaluation/run_experiment.py` CLI script:
     - Automatically verifies/seeds `it-support-benchmark` in Langfuse Datasets from `data/gold_benchmarks.json`.
     - Executes both `fix_single_llm` and `fix_single_jev` against all benchmark items.
     - Computes latency, token costs, faithfulness, and answer relevancy.
     - Submits scores to Langfuse.
     - Prints a comparative summary table in the terminal.
  3. Write unit tests in `tests/test_experiment_runner.py` validating the JSON benchmark schema and seeding logic.
- **Acceptance Criteria:**
  - [x] `data/gold_benchmarks.json` contains valid JSON with exactly 10 cases conforming to the benchmark schema.
  - [x] `evaluation/run_experiment.py --help` runs cleanly.
  - [x] The experiment runner can execute against mock or live workflows and output formatted benchmark metrics.
- **TDD Verification:**
  ```bash
  uv run pytest tests/test_experiment_runner.py
  ```

---

## Ticket 09: Comprehensive TDD & End-to-End Test Suite

- **Title:** Implement Unit Cost Tests, Clean Up Legacy Mocks, and Validate 100% Green E2E Suite
- **Blockers:** Tickets 01–08
- **Files Created/Modified:**
  - `tests/test_token_cost_accounting.py` (Create)
  - `tests/test_chat_stream.py` (Modify: strip `write_audit` monkeypatches)
  - `tests/test_feedback_api.py` (Modify: strip `write_audit` monkeypatches)
  - `tests/test_e2e_observability.py` (Modify: remove `init_db` assertions)
- **Deliverables:**
  1. Author `tests/test_token_cost_accounting.py` testing JEV token heuristic, cost formulas, and observation keyword arguments.
  2. Clean up `tests/test_chat_stream.py`, `tests/test_feedback_api.py`, and `tests/test_e2e_observability.py` to remove legacy `write_audit` mocks.
  3. Execute full regression test suite confirming zero test failures.
- **Acceptance Criteria:**
  - [ ] All new tests (`test_prompts_cms.py`, `test_online_evaluation.py`, `test_experiment_runner.py`, `test_token_cost_accounting.py`) pass.
  - [ ] Legacy tests execute with 0 failures and 0 references to `write_audit`.
  - [ ] Total test count increases with 100% pass rate.
- **TDD Verification:**
  ```bash
  uv run pytest
  ```
