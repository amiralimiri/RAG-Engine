# 03: End-to-End Streaming, Telemetry & Full Conformance Gate

**What to build:**
Verify complete end-to-end conformance across streaming token delta filtering, step event emission, multi-turn session persistence, and telemetry spans:
1. Verify `tests/test_chat_stream.py` tests for `react_agent` (streaming tokens filtered to conversational text, tool executions emitting step events).
2. Verify `tests/test_e2e_observability.py` tests for `react_agent` (multi-turn memory persistence across HTTP turns, Langfuse spans).
3. Verify `tests/api/test_routes_multi_workflow.py` tests for `react_agent`.
4. Run full test suite regression (`uv run pytest`) across all 489+ tests with 100% green pass and zero regressions.

**Blocked by:** 02: Canonical `create_agent` Compiler Migration

**Status:** completed

- [x] `tests/test_chat_stream.py` passes 100% green
- [x] `tests/test_e2e_observability.py` passes 100% green
- [x] `tests/api/test_routes_multi_workflow.py` passes 100% green
- [x] `uv run pytest` runs across all 489+ tests with 100% green pass and zero regressions
