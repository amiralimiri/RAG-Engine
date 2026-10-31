# Retrospective: Milestone 0013 (Consolidate Evaluation Engine behind an Evaluator Seam)

**Date:** October 31, 2026  
**Status:** Completed, Reviewed, Remediated, & Archived  
**ADR Reference:** `docs/adr/0013-consolidate-evaluation-engine-behind-evaluator-seam.md`  
**Specification:** `docs/specs/0013-consolidate-evaluation-engine-behind-evaluator-seam-spec.md`  
**Work Packages / Tickets:** `docs/specs/0013-consolidate-evaluation-engine-behind-evaluator-seam-tickets.md`  
**Domain Guidelines:** `docs/agents/evaluation-engine.md` | `docs/agents/workflow-execution.md`  
**Test Suite:** 489 passed, 0 failed, 100% offline-capable, zero inter-test state leaks  

---

## 1. Executive Summary & Major Accomplishments

Milestone 0013 unified the RAG system's fractured evaluation landscape into a cohesive first-class domain subsystem (`app/rag/evaluation/`). Previously, evaluation responsibilities were splintered across `app/services/evaluation.py` (live API scoring), `evaluation/judge.py` (eager root-level LLM judge initialization), and `evaluation/run_experiment.py` (which duplicated DeepEval metric calculations and pierced `WorkflowRunner`'s private `_execute_with_state` method).

This milestone completely dismantled these legacy silos under the Zero-Shim architectural standard, deepened `WorkflowRunner` with clean execution context extraction, and established an offline-first testing double with automatic fixture teardowns.

### Key Deliverables:
- **Core Evaluation Subsystem (`app/rag/evaluation/`)**:
  - `Evaluator` protocol seam (`evaluate`, `aevaluate`, `score_metric`) in `app/rag/evaluation/base.py`.
  - `EvaluationResult` Pydantic model encapsulating normalized metrics (`faithfulness`, `answer_relevancy`, `scores`, `details`).
  - Thread-safe lazy `JudgeModels` factory in `app/rag/evaluation/judge.py`, deferring DeepEval model instantiation until first use.
  - Unified `EvaluationEngine` in `app/rag/evaluation/evaluator.py`, executing metric calculations and Langfuse score dispatches.
  - In-memory `FakeEvaluator` test double enabling deterministic metric scoring and invocation auditing without external dependencies.
- **WorkflowRunner Context Execution Deepening**:
  - Introduced `WorkflowRunner.execute_with_context(request) -> ExecutionResult` returning `response: ChatResponse` and `eval_context: EvalContext`.
  - Injected `evaluator: Evaluator` into `WorkflowRunner.__init__`, defaulting to `get_evaluator()`.
  - Streamlined background online evaluation in live chat requests to asynchronously invoke `self.evaluator.aevaluate(...)`.
- **Caller Modernization & Experiment Runner Refactor**:
  - Refactored `evaluation/run_experiment.py` to invoke `WorkflowRunner.execute_with_context()`, completely eliminating private method piercing (`_execute_with_state`).
  - Consolidated duplicate `FaithfulnessMetric` and `AnswerRelevancyMetric` test-case logic into `evaluator.evaluate(...)`.
- **Zero-Shim Pruning**:
  - Permanently deleted `app/services/evaluation.py` (190 lines).
  - Permanently deleted `evaluation/judge.py` (42 lines).
  - Migrated `POST /api/feedback` to `get_evaluator().score_metric(...)`.
  - Modernized all test suites across the repository to import from `app.rag.evaluation`.
- **Test Isolation & Conformance**:
  - Registered `clean_evaluator` autouse fixture in `tests/conftest.py` invoking `reset_evaluator()`.
  - Total test suite expanded to 489 passing tests across 6 worker processes in ~65 seconds.

---

## 2. What Went Well

1. **Clean Separation of Concerns via the Evaluator Seam**:
   - Both online request evaluation (asynchronous, threadpool-shielded) and offline batch experimentation (synchronous, CLI-driven) now share the exact same calculation engine without coupling to transport schemas.
2. **Elimination of Import Side Effects**:
   - Deleting `evaluation/judge.py` and implementing lazy `JudgeModels` removed all import-time LLM initialization and network attempts, accelerating test startup and eliminating environment variable load failures.
3. **Encapsulated Execution Context on WorkflowRunner**:
   - `execute_with_context()` provided the exact domain boundary needed by offline evaluation tools while preserving the purity of `ChatResponse` for frontend HTTP clients.
4. **Deterministic Test Isolation**:
   - `FakeEvaluator` and the autouse `clean_evaluator` fixture allowed all unit and integration tests to verify evaluation behavior deterministically with 0ms network latency and zero state leakage between tests.
5. **Strict Code Review Process**:
   - The `/code-review` workflow surfaced subtle boundary violations (such as reverse coupling to `app/api/schemas` and metric calculation gating) before they reached the main branch.

---

## 3. Friction Points & Challenges Encountered

1. **Offline Test vs. Live Experiment Disparity (Metric Calculation Gating)**:
   - *Problem*: In initial drafts of `EvaluationEngine._compute_metrics`, metric calculation was gated behind `if self.settings.eval_sample_rate > 0.0`. In offline evaluation runs or unit tests where `eval_sample_rate` was 0.0 or Langfuse was disabled, metric evaluation was bypassed completely and returned empty dictionaries, causing benchmark assertions to fail.
   - *Root Cause*: Conflating *metric calculation* (which should run whenever metrics are requested in `settings.eval_metrics`) with *remote score dispatch* (which should be sampled and gated by `eval_sample_rate` and `langfuse_enabled`).
   - *Resolution*: Decoupled the two concerns: `should_run_faith` and `should_run_rel` inspect `settings.eval_metrics` and presence of contexts, while `should_dispatch_scores` independently gates Langfuse submission on `eval_sample_rate` and `langfuse_enabled`.
2. **Reverse Coupling to Transport Schemas (`app/api/schemas`)**:
   - *Problem*: Early revisions of `app/rag/evaluation/` imported `ChatResponse` from `app.api.schemas` to inspect returned answers and trace metadata.
   - *Root Cause*: Habitual reliance on transport models instead of domain primitives.
   - *Resolution*: Stripped transport imports from `app/rag/evaluation/`. The seam accepts primitive arguments (`question: str`, `answer: str`, `source_used: str`, `retrieved_contexts: list[str]`), maintaining clean layer boundaries (`app/rag/**` never imports from `app/api/**`).
3. **Runtime Langfuse Host/Base URL Mismatch & Cloud Prompt Seeding**:
   - *Problem*: Langfuse SDK client versions vary in parameter naming (`host` vs `base_url`), causing live prompt seeding against Langfuse cloud to fail with initialization errors when configured via environment variables.
   - *Root Cause*: Divergence between Pydantic settings attribute naming and underlying SDK constructor arguments across library updates.
   - *Resolution*: Added `pydantic.AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL", ...)` to `Settings` in `app/core/config.py`, normalized trailing slashes, added a `langfuse_base_url` property alias, and passed `settings` explicitly into `get_langfuse_client(settings)`.

---

## 4. Codified Agent Guardrails & Updated Rules

To prevent regressions in subsequent milestones, the following rules have been codified in `docs/agents/evaluation-engine.md`, `docs/agents/workflow-execution.md`, and `AGENTS.md`:

1. **Evaluator Seam Invariant**:
   - Always invoke evaluation via `get_evaluator().evaluate(...)` or `active_runner.evaluator.aevaluate(...)`. Never instantiate raw DeepEval metric classes directly in callers.
2. **Context Extraction Boundary**:
   - Always extract evaluation context via `runner.execute_with_context(req) -> ExecutionResult`. Never call private `_execute_with_state` or attach dynamic attributes to response objects.
3. **Decouple Metric Calculation from Score Dispatch**:
   - Compute metrics based on `settings.eval_metrics`. Only sample/gate the remote dispatch to Langfuse via `eval_sample_rate` and `langfuse_enabled`.
4. **Lazy Judge Instantiation**:
   - Always access judge models via `get_judge_models()`. Never instantiate models at module level or root imports.
5. **Zero-Shim Rule**:
   - Never retain deprecated modules or proxy wrappers. Delete old files immediately and update callers.
6. **100% Offline Testing**:
   - Pytest suites must never invoke external judge models or Langfuse APIs. Rely on `FakeEvaluator` and autouse teardown fixtures.
7. **No Reverse Domain Coupling**:
   - `app/rag/**` must never import from `app/api/**`.

---

## 5. Artifact & Ticket Archive Status

All 4 work packages have been archived to `.scratch/archive/0013-consolidate-evaluation-engine-behind-evaluator-seam/issues/`:

| Ticket | Scope | Status |
|---|---|---|
| **Ticket 01** | Core Evaluator Seam and Lazy Judge Lifecycle | Completed & Archived |
| **Ticket 02** | WorkflowRunner Execution Context & Evaluator Injection | Completed & Archived |
| **Ticket 03** | Experiment Runner Migration & Metric Consolidation | Completed & Archived |
| **Ticket 04** | Zero-Shim Pruning of Legacy Files & Caller Modernization | Completed & Archived |

The `.scratch/` directory is pruned of all active tickets and ready for subsequent architecture milestones.

---

## 6. Future Architectural Opportunities

With the Evaluation Engine and WorkflowRunner cleanly consolidated, the next architectural candidates from the strategic review can be tackled:

1. **Candidate 2: Declarative Workflow Topologies & Workflow Registry Modernization**:
   - Refactor workflow definitions from hardcoded Python graph assemblies into declarative or composable configurations, simplifying routing modifications, prompt overrides, and node parameterization.
2. **Candidate 3: Unified Checkpointer & Multi-Turn Session Persistence**:
   - Consolidate session memory and LangGraph checkpointer storage (`MemorySaver` vs durable SQLite/Postgres) across all workflows (`react_agent`, `fix_single_llm`, `fix_single_jev`), standardizing thread ID propagation.
3. **Candidate 4: LangGraph v2 Agent Migration**:
   - Address deprecation warnings for `create_react_agent` by migrating to the canonical `langchain.agents.create_agent` seam slated for LangGraph 2.0.
