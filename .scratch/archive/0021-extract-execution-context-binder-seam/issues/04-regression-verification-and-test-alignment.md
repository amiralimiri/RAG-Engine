# Ticket 04: Regression Verification & End-to-End Test Suite Alignment

- **Part of:** ADR-0021 (`docs/specs/0021-extract-execution-context-binder-seam-spec.md`)
- **Blockers:** Ticket 03
- **Scope:** Full test suite (`tests/rag/`, `tests/api/`, `tests/`)
- **Labels:** `closed`
- **Status:** closed

---

## Objective

Run the complete test suite across the entire repository to ensure zero regressions across all workflows, API endpoints, streaming protocols, memory persistence, and evaluation suites.

---

## Detailed Requirements

1. **Verify Full Test Suite**:
   - Run `uv run pytest -q`.
   - Ensure all 869+ tests pass with zero failures.

2. **Verify Specific Integration Paths**:
   - `tests/api/test_routes_chat_memory.py`: Multi-turn chat and memory routes.
   - `tests/api/test_routes_multi_workflow.py`: Workflow switching and profile resolution.
   - `tests/test_chat_stream.py`: SSE streaming with listener side-effects.
   - `tests/rag/test_listener_dispatch.py`: Listener lifecycle dispatching.

3. **Check Documentation Alignment**:
   - Confirm `GLOSSARY.md`, `docs/spec.md`, and `docs/adr/0021-extract-execution-context-binder-seam.md` accurately match the final implementation.

---

## Verification Criteria

- Zero test failures across the test suite (`uv run pytest -q`).
- Zero regressions in API routes or streaming behavior.
