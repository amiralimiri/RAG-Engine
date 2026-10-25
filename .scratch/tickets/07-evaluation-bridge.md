# Ticket 07: DeepEval Judge to Langfuse Scoring Bridge

## Status
Completed

## Dependencies
Ticket 02 (`02-telemetry-module.md`), Ticket 06 (`06-api-routes-chat-and-feedback.md`)

## Scope
Implement `app/services/evaluation.py` to bridge DeepEval judge evaluations (`evaluation/judge.py`) with Langfuse trace scores. Enable scoring traces on metrics like faithfulness and answer relevancy, and reporting scores directly into Langfuse.

## Deliverables
1. `app/services/evaluation.py`:
   - `score_trace_faithfulness(trace_id: str, question: str, answer: str, retrieved_contexts: list[str]) -> Optional[float]`
   - `score_trace_metric(trace_id: str, metric_name: str, score: float, comment: Optional[str] = None) -> bool`
2. `tests/test_evaluation_bridge.py`: Unit tests verifying evaluation execution, score reporting, and fail-open behavior.

## Acceptance Criteria
- [x] `score_trace_metric()` submits `name`, `value`, and `comment` to Langfuse client via `client.create_score()` (or `client.score()`).
- [x] If Langfuse client is `None` or disabled, evaluation bridge returns `None`/`False` without raising an exception.
- [x] If judge evaluation fails or times out, the error is logged and returns `None` (fail-open).
- [x] Scores submitted to Langfuse are strictly normalized between `0.0` and `1.0`.

## TDD Test Requirements
Create `tests/test_evaluation_bridge.py`:
- `test_score_trace_metric_submits_to_langfuse(monkeypatch)`: Mock Langfuse client, call `score_trace_metric("tr-1", "faithfulness", 0.95, "looks good")`, verify `client.create_score` (or `client.score`) called with exact arguments and returns `True`.
- `test_score_trace_metric_handles_disabled_telemetry(monkeypatch)`: Set client to `None`, call `score_trace_metric()`, assert returns `False` and no exception raised.
- `test_score_trace_metric_handles_langfuse_error(monkeypatch)`: Mock `client.score` raising network exception, assert function catches error and returns `False`.
