# 07: Full Verification & E2E Regression Gate

**What to build:** Execute full system verification across the unified codebase: run the complete test suite, verify clean importability, confirm zero residual dead symbols or compatibility files, and ensure zero regressions across HTTP endpoints, streaming, and background evaluation.

**Blocked by:** 01: Test Isolation Fixture Foundation, 02: Canonical Ingestion & Storage Facade Pruning, 03: Pure Engine Factory & Tool Decoupling, 04: Legacy Workflow Shims & Dictionary Deletion, 05: Route & Observability Test Suite Modernization, 06: Telemetry, Contexts & Spans Test Suite Consolidation

**Status:** open

---

## Tasks

- [ ] Verify deleted files no longer exist:
  - `app/rag/workflows/legacy.py`
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`
  - `app/rag/vectorstore.py`
  - `app/services/ingestion.py`
  - `tests/rag/test_legacy_compat.py`
  - `tests/test_indexing_regression.py`
- [ ] Scan codebase for deprecated symbol usage:
  - No occurrences of `WORKFLOW_ASKS` or `WORKFLOW_GRAPHS` across `app/` and `tests/`.
  - No occurrences of `_LegacyUnifiedWorkflowAdapter` or `reset_legacy_defaults`.
  - No occurrences of `app.rag.vectorstore` or `app.services.ingestion`.
  - No occurrences of `app.rag.fix_single_llm` or `app.rag.fix_single_jev`.
  - No occurrences of `web_search_tool` or `_llm` in `app/rag/engine.py`.
- [ ] Run full test suite:
  - `uv run pytest -v`
  - Verify all tests pass with zero failures.
- [ ] Verify external scripts:
  - `uv run python evaluation/run_experiment.py --help` runs without error.
  - `uv run python run.py --help` (or checking app startup) executes cleanly.

## Verification
- `uv run pytest` passes 100%.
- Git status confirms exact intended modifications and deletions.
