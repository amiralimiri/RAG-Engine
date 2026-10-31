# Ticket 05: Instrument fix_single_jev Workflow & JEV Token Heuristic

## Status
Completed

## Dependencies
Ticket 02 (`02-telemetry-module.md`)

## Scope
Extend the `ask()` entry point in `app/rag/fix_single_jev.py` with telemetry kwargs. Add character-based heuristic token estimation (`_estimate_jev_tokens`) and safe span generation for `TypeSafeClassifier` calls (`route_question_jev` and `grade_kb_jev`) to capture usage and costs in Langfuse.

## Deliverables
1. `app/rag/fix_single_jev.py`:
   - Implement `_estimate_jev_tokens(prompt_text: str, output_text: str) -> dict[str, int]`.
   - Update `route_question_jev` and `grade_kb_jev` to safely emit generation span metadata to Langfuse client if active.
   - Update `ask()` signature with `session_id`, `user_id`, `metadata`, `tags`, and `callback`.
   - Pass `config={"callbacks": [callback]}` to `agent_graph.invoke()`.
   - Maintain standalone file isolation and existing graph compilation intact.
2. `tests/test_jev_telemetry.py`: Unit tests validating token estimation, fail-open span recording, and callback propagation.

## Acceptance Criteria
- [x] `_estimate_jev_tokens` accurately calculates input tokens as `max(1, len(prompt) // 4)` and output tokens as `max(1, len(output) // 4)`.
- [x] JEV nodes record generation metadata to Langfuse if client is available; if client is `None` or throws an error, execution continues without failure.
- [x] `ask("...")` works backward-compatibly without keyword arguments.
- [x] `ask("...", callback=...)` attaches callbacks to LangGraph config.
- [x] Returned dictionary structure from `ask()` remains unchanged.

## TDD Test Requirements
Create `tests/test_jev_telemetry.py`:
- `test_estimate_jev_tokens()`: Test with various string lengths, verify output structure `{"input": int, "output": int, "total": int}` and minimum count of 1.
- `test_ask_jev_backward_compatibility(monkeypatch)`: Mock `agent_graph.invoke`, call `ask("hello")`, verify correct invocation without errors.
- `test_ask_jev_passes_callback(monkeypatch)`: Verify `agent_graph.invoke` receives callback in config.
- `test_jev_node_fails_open_when_langfuse_client_fails(monkeypatch)`: Mock `get_langfuse_client` returning a mock whose generation recording throws; verify `route_question_jev` executes and returns decision normally.
