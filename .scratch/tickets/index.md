# Langfuse Observability Integration - Implementation Plan & Tickets

This directory contains the bite-sized, sequential, test-driven implementation tickets for integrating Langfuse observability, tracing, token tracking, feedback, and evaluation into the Enterprise IT Support Agentic RAG system.

## Specification Reference
- **ADR:** `docs/adr/0001-langfuse-observability-integration.md`
- **Technical Specification:** `docs/spec.md` (`docs/specs/0001-langfuse-observability-spec.md`)

---

## Ticket Dependency Graph

```
[01: Dependencies & Config]
             │
             ▼
   [02: Telemetry Module] ──────────────┐
             │                          │
             ▼                          ▼
[03: SQLite Audit Migration]  [05: JEV Token Instrumentation]
             │                          │
             ▼                          │
[04: LLM Workflow Telemetry]            │
             │                          │
             └───────────┬──────────────┘
                         ▼
           [06: API Routes & Feedback]
                         │
                         ▼
             [07: Evaluation Bridge]
                         │
                         ▼
        [08: Lifespan & E2E Verification]
```

---

## Ticket Index

| Ticket | File | Title | Dependencies | Deliverables |
|---|---|---|---|---|
| **01** | `01-dependencies-and-config.md` | Add Dependencies and Configuration | None | `pyproject.toml`, `app/core/config.py`, tests |
| **02** | `02-telemetry-module.md` | Create Telemetry Module & Callback Factory | 01 | `app/core/telemetry.py`, `tests/test_telemetry.py` |
| **03** | `03-audit-sqlite-migration.md` | SQLite Audit Migration & Trace ID Persistence | 01 | `app/services/audit.py`, `tests/test_audit_migration.py` |
| **04** | `04-workflow-instrumentation-fix-single-llm.md` | Instrument `fix_single_llm` Workflow | 02 | `app/rag/fix_single_llm.py`, `tests/test_llm_telemetry.py` |
| **05** | `05-workflow-instrumentation-fix-single-jev.md` | Instrument `fix_single_jev` Workflow & JEV Token Heuristic | 02 | `app/rag/fix_single_jev.py`, `tests/test_jev_telemetry.py` |
| **06** | `06-api-routes-chat-and-feedback.md` | Update Chat API & Add Feedback Endpoint | 02, 03, 04, 05 | `app/api/routes.py`, `tests/test_feedback_api.py` |
| **07** | `07-evaluation-bridge.md` | DeepEval Judge to Langfuse Scoring Bridge | 02, 06 | `app/services/evaluation.py`, `tests/test_evaluation_bridge.py` |
| **08** | `08-main-lifespan-and-e2e-verification.md` | FastAPI Lifespan & End-to-End Test Suite | 01 - 07 | `app/main.py`, `tests/test_e2e_observability.py` |
