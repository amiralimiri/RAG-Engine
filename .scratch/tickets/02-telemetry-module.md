# Ticket 02: Telemetry Module & Callback Factory

## Status
Pending

## Dependencies
Ticket 01 (`01-dependencies-and-config.md`)

## Scope
Create the centralized telemetry module (`app/core/telemetry.py`) containing the Langfuse client singleton, callback handler factory, trace ID extraction helper, and shutdown flush utility. Enforce strict fail-open resilience.

## Deliverables
1. `app/core/telemetry.py`:
   - `get_langfuse_client() -> Optional[Langfuse]`
   - `get_langfuse_callback(...) -> Optional[CallbackHandler]`
   - `extract_trace_id(handler: Optional[CallbackHandler]) -> Optional[str]`
   - `flush_telemetry() -> None`
2. `tests/test_telemetry.py`: Comprehensive unit tests covering fail-open scenarios and callback construction.

## Acceptance Criteria
- [ ] If `langfuse_enabled` is `False`, `get_langfuse_client()` and `get_langfuse_callback()` return `None`.
- [ ] If `langfuse_public_key` or `langfuse_secret_key` is `None` or empty, `get_langfuse_client()` and `get_langfuse_callback()` return `None`.
- [ ] Any exception during `Langfuse()` or `CallbackHandler()` creation is trapped and logged as a warning; function returns `None` (fail-open).
- [ ] `get_langfuse_callback()` properly formats:
  - `trace_name = f"rag:{workflow_name}"`
  - default tags: `["workflow:<workflow_name>", "env:<app_env>"]` merged with any user-provided `tags`.
  - passes `session_id`, `user_id`, and `metadata`.
- [ ] `extract_trace_id()` safely handles `None`, handler objects with `get_trace_id()`, `trace_id`, or `root_trace_id`, and returns `str | None` without throwing.
- [ ] `flush_telemetry()` calls `client.flush()` and catches any exception safely.

## TDD Test Requirements
Create `tests/test_telemetry.py`:
- `test_callback_returns_none_when_disabled(monkeypatch)`: Set `LANGFUSE_ENABLED=False`, call `get_langfuse_callback("test")`, assert result is `None`.
- `test_callback_returns_none_when_keys_missing(monkeypatch)`: Unset keys, verify result is `None`.
- `test_callback_created_with_tags_and_metadata(monkeypatch)`: Provide valid keys, verify created `CallbackHandler` attributes (trace name, tags, metadata).
- `test_extract_trace_id_handles_none_and_exceptions()`: Verify `extract_trace_id(None) is None` and broken objects do not raise.
- `test_flush_telemetry_safe_when_unconfigured()`: Call `flush_telemetry()` when client is `None` and ensure no exceptions.
