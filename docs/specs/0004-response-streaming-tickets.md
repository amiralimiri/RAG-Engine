# Work Packages / Implementation Tickets: Response Streaming with Server-Sent Events (SSE)

- **Specification Reference:** [`docs/specs/0004-response-streaming-spec.md`](./0004-response-streaming-spec.md)
- **ADR Reference:** [`docs/adr/0004-response-streaming-with-server-sent-events.md`](../adr/0004-response-streaming-with-server-sent-events.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│  Ticket 1: Schemas & Wire Models                       │
│  (app/api/schemas.py)                                  │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 2: Async Graph Generation Nodes                │
│  (app/rag/fix_single_llm.py, fix_single_jev.py)        │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│  Ticket 3: Backend Streaming Route & Telemetry Teardown│
│  (app/api/routes.py)                                   │
└───────────────┬────────────────────────────┬───────────┘
                │                            │
                ▼                            ▼
┌──────────────────────────────────┐ ┌──────────────────────────────────┐
│  Ticket 4: Backend Stream Tests  │ │  Ticket 5: Client SSE Parser     │
│  (tests/test_chat_stream.py)     │ │  (static/js/app.js - Parser)     │
└──────────────────────────────────┘ └───────────────┬──────────────────┘
                                                     │
                                                     ▼
                                     ┌──────────────────────────────────┐
                                     │  Ticket 6: Progressive UI State  │
                                     │  (static/js/app.js - UI/Storage) │
                                     └───────────────┬──────────────────┘
                                                     │
                                                     ▼
                                     ┌──────────────────────────────────┐
                                     │  Ticket 7: E2E Integration Tests │
                                     │  (tests/test_frontend_streaming) │
                                     └──────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files | Prerequisites | Status |
|---|---|---|---|---|
| **Ticket 1** | Schemas & Wire Models | `app/api/schemas.py` | None | `completed` |
| **Ticket 2** | Async Graph Generation Nodes | `app/rag/fix_single_llm.py`<br>`app/rag/fix_single_jev.py` | Ticket 1 | `completed` |
| **Ticket 3** | Backend Streaming Route & Telemetry Teardown | `app/api/routes.py` | Ticket 1, Ticket 2 | `completed` |
| **Ticket 4** | Backend Automated Streaming Tests | `tests/test_chat_stream.py` | Ticket 3 | `completed` |
| **Ticket 5** | Zero-Dependency Client SSE Parser | `static/js/app.js` | Ticket 3 | `completed` |
| **Ticket 6** | Progressive UI State Machine & Interrupted Persistence | `static/js/app.js` | Ticket 5 | `completed` |
| **Ticket 7** | End-to-End Integration Tests & Verification | `tests/test_frontend_streaming.py` | Ticket 4, Ticket 6 | `ready-for-agent` |

---

## Ticket 1: Schemas & Wire Models

- **Title:** Centralize Pydantic Schemas and SSE Wire Event Models in `app/api/schemas.py`
- **Label:** `completed`
- **Blockers:** None
- **Files Created:**
  - `app/api/schemas.py`
- **Files Modified:**
  - `app/api/routes.py` (import models from schemas)

### Description
Extract and centralize request, response, and streaming wire models into `app/api/schemas.py`. Update `ChatRequest` to include `stream_steps: bool = True` while maintaining backward compatibility with `POST /api/chat`. Define strict Pydantic schemas for the 4 SSE event types: `step`, `token`, `metadata`, and `error`.

### Acceptance Criteria
- [x] `app/api/schemas.py` is created and exports:
  - `ChatRequest` (including `question`, `workflow`, `session_id`, `user_id`, `trace_id`, and `stream_steps: bool = True`).
  - `ChatResponse` (standard synchronous response contract).
  - `FeedbackRequest` (telemetry scoring payload).
  - `StreamStepEvent` (`step: str`, `label: str`, `timestamp: float`).
  - `StreamTokenEvent` (`delta: str`).
  - `StreamMetadataEvent` (`trace_id: str | None`, `workflow: str`, `source_used: str`, `citations: list`, `trace: list`, `rewritten_query: str | None`).
  - `StreamErrorEvent` (`message: str`, `code: int = 500`).
- [x] `app/api/routes.py` imports `ChatRequest` and `FeedbackRequest` from `app.api.schemas` with zero regressions on existing `/api/chat` and `/api/feedback` routes.
- [x] Automated validation test passes for schema serialization and deserialization.

---

## Ticket 2: Async Graph Generation Nodes

- **Title:** Convert LangGraph generation nodes to asynchronous invocation in `fix_single_llm.py` and `fix_single_jev.py`
- **Label:** `completed`
- **Blockers:** Ticket 1
- **Files Modified:**
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`

### Description
Convert response synthesis nodes (`generate_from_kb`, `generate_from_web`, `direct_answer`) to `async def` and replace synchronous `llm().invoke()` with `await llm().ainvoke()`. This allows LangGraph's event loop to stream token events smoothly without thread blocking while leaving intermediate classification and grading nodes intact.

### Acceptance Criteria
- [x] In `app/rag/fix_single_llm.py`:
  - `generate_from_kb`, `generate_from_web`, and `direct_answer` are defined as `async def` and invoke `await llm().ainvoke(...)`.
  - The compiled graph (`agent_graph`) compiles and passes validation.
  - Synchronous `ask()` continues to run via `agent_graph.invoke()` without breaking synchronous callers.
- [x] In `app/rag/fix_single_jev.py`:
  - `generate_from_kb`, `generate_from_web`, and `direct_answer` are defined as `async def` and invoke `await llm().ainvoke(...)`.
  - The compiled graph (`agent_graph`) compiles and passes validation.
  - Synchronous `ask()` remains functional without regressions.
- [x] Intermediate nodes (`route_question`, `grade_kb`, `grade_web`, `rewrite_query`) remain unchanged and output structured dictionaries.

---

## Ticket 3: Backend Streaming Route & Telemetry Teardown

- **Title:** Implement `POST /api/chat/stream` with `StreamingResponse`, `astream_events` filtering, and cancellation cleanup
- **Label:** `completed`
- **Blockers:** Ticket 1, Ticket 2
- **Files Modified:**
  - `app/api/routes.py`

### Description
Implement the SSE streaming endpoint `POST /api/chat/stream`. Consume LangGraph's `agent_graph.astream_events(initial_state, version="v2")`. Filter emitted events so that only whitelisted generation nodes (`generate_from_kb`, `generate_from_web`, `direct_answer`) emit `event: token` frames. Emit `event: step` with friendly labels from `STEP_LABELS`. Wrap the generator in an `asyncio.CancelledError` handler to record an audit record with `source_used: "client_disconnected"` and flush the Langfuse trace if the client disconnects mid-stream.

### Acceptance Criteria
- [x] `POST /api/chat/stream` is registered on FastAPI's router returning `media_type="text/event-stream"`.
- [x] Streaming headers include:
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
  - `X-Accel-Buffering: no`
- [x] Event filtering ensures:
  - `step` events are emitted on `on_chain_start` for known node names mapped via `STEP_LABELS`.
  - `token` events are emitted **only** from `on_chat_model_stream` events originating from whitelisted generation nodes.
  - Intermediate router and grader JSON deltas are never emitted as tokens.
  - `metadata` event is emitted upon completion with `trace_id`, `source_used`, `citations`, and `trace`.
- [x] Intercepting `asyncio.CancelledError`:
  - Logs a warning with the associated `trace_id`.
  - Writes an audit row to `query_audit` with `source_used = "client_disconnected"`.
  - Calls `handler.flush()` on the Langfuse callback handler if present.
- [x] Pre-stream errors raise standard HTTP status codes (e.g. 400 for unknown workflow); mid-stream runtime errors emit `event: error`.

---

## Ticket 4: Backend Automated Streaming Tests

- **Title:** Implement backend streaming test suite in `tests/test_chat_stream.py`
- **Label:** `completed`
- **Blockers:** Ticket 3
- **Files Created:**
  - `tests/test_chat_stream.py`

### Description
Create a comprehensive test suite using `httpx.AsyncClient` that validates the SSE wire protocol, verifies event taxonomy and payload structures, asserts token isolation from routers/graders, tests client cancellation handling, and verifies that the synchronous `POST /api/chat` remains backward compatible.

### Acceptance Criteria
- [x] `tests/test_chat_stream.py` executes successfully with `pytest`.
- [x] Test scenario 1: Validates that `POST /api/chat/stream` emits `step`, `token`, and `metadata` events formatted as valid JSON data blocks.
- [x] Test scenario 2: Asserts token isolation—verifies that no JSON router output (`{"route": ...}`) appears as a stream token.
- [x] Test scenario 3: Client disconnection test—verifies that aborting the stream early writes a `client_disconnected` audit log.
- [x] Test scenario 4: Pre-stream error test—verifies that an invalid workflow name immediately returns HTTP 400.
- [x] Test scenario 5: Backward compatibility test—verifies that synchronous `POST /api/chat` functions identically to pre-streaming behavior.

---

## Ticket 5: Zero-Dependency Client SSE Parser

- **Title:** Implement vanilla JS `ReadableStream` parser in `static/js/app.js`
- **Label:** `completed`
- **Blockers:** Ticket 3
- **Files Modified:**
  - `static/js/app.js`

### Description
Implement a robust, zero-dependency streaming reader in `static/js/app.js` that consumes `fetch()` responses using `ReadableStream` and `TextDecoder`. The parser must handle arbitrary TCP chunk fragmentation, buffer partial lines across read boundaries, split on SSE frame delimiters (`\n\n`), parse `event:` and `data:` fields, and yield deserialized event payloads.

### Acceptance Criteria
- [x] `readEventStream(response)` async generator function is implemented in `static/js/app.js`.
- [x] Uses native `response.body.getReader()` and `TextDecoder("utf-8")`.
- [x] Properly retains partial lines in an internal buffer when chunk boundaries slice across lines or double newlines.
- [x] Yields `{ event, data }` objects where `data` is parsed JSON.
- [x] Gracefully catches and logs malformed JSON chunks without crashing the stream iteration.
- [x] Guarantees reader lock release (`reader.releaseLock()`) inside a `finally` block.

---

## Ticket 6: Progressive UI State Machine & Interrupted Persistence

- **Title:** Implement in-feed thinking transition, RAF progressive rendering, and interrupted session recovery in `static/js/app.js`
- **Label:** `completed`
- **Blockers:** Ticket 5
- **Files Modified:**
  - `static/js/app.js`
  - `static/css/style.css`
  - `templates/index.html`
- **Files Created:**
  - `tests/test_ticket6_streaming_ui_js.js`

### Description
Refactor the message submission lifecycle in `static/js/app.js` to use `POST /api/chat/stream`. Maintain the animated `thinkingBubble` during `step` events, updating its label live. On the first `token` chunk, remove the thinking bubble and mount the streaming assistant bubble. Schedule DOM updates through `requestAnimationFrame` and sanitize with `DOMPurify`. Defer `highlight.js` syntax highlighting and reasoning accordion attachment until the `metadata` event arrives. If the stream aborts or errors prematurely, persist the partial transcript to `localStorage` flagged as `[Interrupted / Incomplete]`.

### Acceptance Criteria
- [x] Message dispatch calls `fetch("/api/chat/stream", ...)` and iterates over `readEventStream(res)`.
- [x] Dynamic Thinking State:
  - `step` events update the label inside `thinkingBubble` in real time.
  - Arrival of the first `token` chunk calls `hideThinking()` and mounts the assistant message element in the DOM.
- [x] Progressive Rendering:
  - Token deltas are accumulated in an in-memory string.
  - Markdown compilation (`marked.parse`) and sanitization (`DOMPurify.sanitize`) are throttled using `requestAnimationFrame`.
  - Chat container auto-scrolls smoothly to follow the stream.
- [x] Stream Completion (`metadata` event):
  - Finalizes Markdown and executes `hljs.highlightElement()` on all code blocks.
  - Renders and appends the collapsible "Reasoning & Audit Steps" accordion.
  - Binds Langfuse trace ID chip and thumbs up/down feedback buttons.
  - Persists completed message to `localStorage` (`rag_session_<session_id>`).
  - Re-enables input field and send button.
- [x] Stream Interruption Handling:
  - If a network error occurs or stream is aborted before `metadata`, partial tokens are preserved in the DOM with a warning banner.
  - The partial message is persisted to `localStorage` with `source_used: "interrupted"` and `[Interrupted / Incomplete]` appended.

---

## Ticket 7: End-to-End Integration Tests & Verification

- **Title:** Implement E2E frontend-backend streaming test suite in `tests/test_frontend_streaming.py`
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 4, Ticket 6
- **Files Created:**
  - `tests/test_frontend_streaming.py`

### Description
Author an integration test suite verifying the end-to-end streaming integration. The suite asserts that `static/js/app.js` contains all required streaming components, checks static asset mounts, validates schema contracts across both streaming and synchronous routes, verifies XSS sanitization during progressive chunk ingestion, and ensures zero regressions across the whole test suite.

### Acceptance Criteria
- [ ] `tests/test_frontend_streaming.py` executes cleanly and passes under `pytest`.
- [ ] Asserts that `app.js` includes `readEventStream`, `scheduleProgressiveRender`, and references `/api/chat/stream`.
- [ ] Verifies that HTML escaping and DOMPurify sanitization are active in streaming handlers.
- [ ] Runs the complete test suite (`pytest`) across all existing and new tests to confirm zero regressions across the application.
