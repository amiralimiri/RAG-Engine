# Ticket 04: Instrument fix_single_llm Workflow

## Status
Pending

## Dependencies
Ticket 02 (`02-telemetry-module.md`)

## Scope
Extend the `ask()` entry point in `app/rag/fix_single_llm.py` with telemetry kwargs (`session_id`, `user_id`, `metadata`, `tags`, `callback`). Attach the callback to `agent_graph.invoke()`. Ensure zero disruption to graph nodes, conditional edges, or internal logic.

## Deliverables
1. `app/rag/fix_single_llm.py`:
   - Update `ask()` function signature to accept:
     ```python
     def ask(
         question: str,
         *,
         session_id: str | None = None,
         user_id: str | None = None,
         metadata: dict | None = None,
         tags: list[str] | None = None,
         callback: Any | None = None,
     ) -> dict:
     ```
   - If `callback` is present, construct `config = {"callbacks": [callback]}` and pass it to `agent_graph.invoke(initial, config=config)`.
   - Maintain standalone file isolation and existing graph compilation intact.
2. `tests/test_llm_telemetry.py`: Unit tests verifying invocation with and without callback.

## Acceptance Criteria
- [ ] `ask("How do I reset password?")` works without any keyword arguments (100% backward-compatible).
- [ ] `ask("...", callback=mock_callback)` attaches the callback to LangGraph invocation config.
- [ ] No nodes, conditional routes, prompt strings, or helper functions inside `fix_single_llm.py` are modified or removed.
- [ ] Returned dictionary structure from `ask()` remains unchanged (`answer`, `source_used`, `trace`, `citations`, etc.).

## TDD Test Requirements
Create `tests/test_llm_telemetry.py`:
- `test_ask_llm_backward_compatibility(monkeypatch)`: Mock `agent_graph.invoke`, call `ask("hello")`, assert invocation received expected initial state and empty/None config.
- `test_ask_llm_passes_callback(monkeypatch)`: Mock `agent_graph.invoke`, pass a dummy callback object to `ask("hello", callback=dummy_cb)`, assert `agent_graph.invoke` was called with `config={"callbacks": [dummy_cb]}`.
