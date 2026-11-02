# 05: HTTP Route Integration, E2E Wire Streaming & Regression Verification

**What to build:**
Wiring the presentation layer to the decoupled execution engine by routing `POST /api/chat/stream` through `SseStreamAdapter.format(...)`, updating transport integration tests, and running the full regression test suite to ensure 100% backward compatibility.

**Blocked by:**
03: Transport Wire Seam & SseStreamAdapter, 04: Refactor WorkflowRunner to Pure Event Generator & Explicit Memory

**Status:** ready-for-agent

- [ ] Update `POST /api/chat/stream` in `app/api/routes.py` to pipe `runner.stream(wf_input)` through `SseStreamAdapter.format(...)`.
- [ ] Update any legacy tests in `tests/test_chat_stream.py` that call `runner.stream()` directly to use `SseStreamAdapter.format()` when asserting on wire string responses.
- [ ] Run full test suite via `pytest` to ensure 100% pass across multi-workflow execution, streaming, conversation memory rewind, and evaluation benchmarks.
- [ ] Verify that no performance or regression anomalies occur.
