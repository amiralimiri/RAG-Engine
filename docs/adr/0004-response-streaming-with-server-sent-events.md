# ADR 0004: Response Streaming with Server-Sent Events (SSE)

## Status
Accepted

## Context
Following the completion of ADR 0001 (Langfuse Observability & Evaluation), ADR 0002 (Structured Logging & Context Propagation), and ADR 0003 (Modernized Frontend Architecture & Observability UI), the RAG Engine backend runs multi-step self-corrective LangGraph workflows (`fix_single_llm` and `fix_single_jev`).

While the conversational UI and backend observability are fully functional, the application suffers from high perceived latency:
1. **Time to First Token (TTFT) Latency**: The existing `POST /api/chat` endpoint is entirely blocking. The client must wait for the entire LangGraph pipeline to execute—routing, KB retrieval, evidence grading, optional web fallbacks, query rewriting, and full LLM answer generation—before receiving a single byte of response. End-to-end response times span **2.0 to 6.5 seconds**, leaving users staring at a loading indicator.
2. **Opaque Agent Transitions**: Although intermediate reasoning steps (`trace`) are returned in the final JSON response, users receive zero live feedback regarding graph decisions while they occur (e.g., whether the query hit the local vectorstore or fell back to web search).
3. **Connection Dropout Vulnerabilities**: If a user cancels a request, navigates away, or drops connection mid-generation, blocking server-side execution continues unmonitored or unhandled, creating orphaned Langfuse spans and unrecorded or failed audit trail entries.
4. **DOM Churn and Layout Thrashing**: Progressively streaming Markdown to a browser without a throttling strategy or safe parsing boundary can introduce cross-site scripting risks, invalid HTML trees, or browser freezing if syntax highlighters re-parse full code blocks on every token chunk.

---

## Decisions

### 1. Dual Endpoint Strategy & Shared Request Contract
To ensure zero regressions across existing automated test suites, CLI tools, and non-streaming HTTP clients:
- **Preserve Synchronous Endpoint**: `POST /api/chat` remains fully supported and unchanged as a non-streaming baseline.
- **Dedicated Streaming Endpoint**: Introduce `POST /api/chat/stream` returning `Content-Type: text/event-stream; charset=utf-8` with `Cache-Control: no-cache` and `X-Accel-Buffering: no` (disabling response buffering in reverse proxies such as Nginx).
- **Request Contract Parity**: `POST /api/chat/stream` accepts the standard `ChatRequest` schema, extended with an optional boolean flag:
  ```python
  class ChatRequest(BaseModel):
      question: str = Field(min_length=2, max_length=3000)
      workflow: str = "fix_single_jev"
      session_id: str | None = None
      user_id: str | None = None
      trace_id: str | None = None
      stream_steps: bool = Field(default=True, description="Whether to emit intermediate graph step events")
  ```

---

### 2. Standardized SSE Protocol & Wire Taxonomy
Streaming communication adheres strictly to the Server-Sent Events specification using a 4-event taxonomy. All payloads are delivered as strict JSON objects on the `data:` line to avoid character escaping, whitespace truncation, or newline stripping issues across intermediate proxies:

| Event Name | Purpose | Example Payload |
| :--- | :--- | :--- |
| `step` | Real-time LangGraph node entry / transition | `{"step": "retrieve_kb", "label": "Searching internal knowledge base...", "timestamp": 1729000000.123}` |
| `token` | Incremental LLM answer delta chunk | `{"delta": "To configure your "}` |
| `metadata` | Terminal completion payload with trace details | `{"trace_id": "a1b2c3d4", "source_used": "private_kb", "trace": [...], "citations": [...]}` |
| `error` | Mid-stream or pipeline error payload | `{"message": "Rate limit exceeded on provider", "code": 500}` |

Each SSE frame is formatted with explicit event types and double newlines:
```
event: step
data: {"step":"retrieve_kb","label":"Searching internal knowledge base..."}

event: token
data: {"delta":"Password "}

event: metadata
data: {"trace_id":"9cf8b1a2","source_used":"private_kb","citations":[{"title":"IT_Policy.pdf","url":""}],"trace":["Router → KB","Private KB retrieval → 3 chunks"]}
```

---

### 3. Server-Side Step Label Canonicalization
To keep the frontend decoupled from internal LangGraph node refactoring:
- The backend maintains a canonical dictionary mapping graph node identifiers to user-friendly status labels:
  ```python
  STEP_LABELS: dict[str, str] = {
      "route_question": "Analyzing inquiry & selecting workflow...",
      "retrieve_kb": "Searching internal knowledge base...",
      "grade_kb": "Evaluating evidence sufficiency...",
      "search_web": "Consulting external technical documentation...",
      "grade_web": "Verifying web source credibility...",
      "rewrite_query": "Refining search terms...",
      "generate_from_kb": "Synthesizing answer from knowledge base...",
      "generate_from_web": "Drafting answer from external documentation...",
      "direct_answer": "Formulating direct response...",
      "insufficient": "Assessing fallbacks...",
  }
  ```
- When an execution step begins, the server emits a `step` event containing both the internal identifier and the canonical label.

---

### 4. LangGraph `astream_events(version="v2")` & Generation Token Isolation
A critical risk in LangGraph streaming is token pollution: intermediate classification and grading nodes (`route_question`, `grade_kb`, `grade_web`, `rewrite_query`) also invoke LLMs with structured JSON prompts. If unfiltered, raw JSON tokens (`{"route": "kb"}`) leak directly into the user-facing answer stream.

To prevent token leakage and maintain clean event isolation:
1. **Async Node Conversion**: All final response synthesis nodes (`generate_from_kb`, `generate_from_web`, `direct_answer`) are converted to `async def` and use `await llm().ainvoke(...)`.
2. **Strict Node Whitelisting**: The streaming generator consumes `graph.astream_events(initial_state, version="v2")` and strictly gates token emission:
   - Token chunks are emitted via `event: token` **only** when `event == "on_chat_model_stream"` AND the event metadata satisfies:
     ```python
     event["metadata"].get("langgraph_node") in {
         "generate_from_kb",
         "generate_from_web",
         "direct_answer",
     }
     ```
   - Node progression events are captured from `on_chain_start` / `on_chain_end` where `event["name"] in STEP_LABELS`, triggering `event: step`.
   - All router and grader JSON output tokens are discarded from the client SSE stream while continuing to execute internally within LangGraph.

---

### 5. Telemetry, Contextvars & Disconnection Resilience
FastAPI's `StreamingResponse` runs asynchronously. If the client disconnects or aborts the connection mid-generation, Starlette raises `asyncio.CancelledError`. Unhandled cancellations cause dangling Langfuse spans and missing audit entries.

1. **Contextvar Preservation**: `structlog.contextvars` bindings (`workflow`, `session_id`, `trace_id`) are captured prior to generator execution and bound within the streaming generator task.
2. **Cancellation Interception**: The generator wraps stream execution in a resilient `try...except asyncio.CancelledError...finally` block:
   ```python
   try:
       async for event in graph.astream_events(initial, version="v2", config=config):
           # stream processing logic...
   except asyncio.CancelledError:
       logger.warning("Client disconnected prematurely during streaming", trace_id=trace_id)
       write_audit(
           workflow=payload.workflow,
           question=payload.question,
           source_used="client_disconnected",
           trace=accumulated_trace + ["Stream cancelled by client disconnect"],
           trace_id=trace_id,
       )
       raise
   finally:
       # Ensure Langfuse v5 client flushes open trace observations
       if handler:
           handler.flush()
   ```
3. **Two-Stage Error Handling**:
   - **Pre-stream Errors** (e.g., invalid payload or unrecognized workflow): Throw standard `HTTPException(400/422/500)` before headers are committed.
   - **Mid-stream Failures** (e.g., model rate limits or external API drops occurring after HTTP 200 headers have been sent): Emit an `event: error\ndata: {"message": "..."}\n\n` frame before closing the generator, allowing the client to surface an inline error banner.

---

### 6. Zero-Dependency Vanilla JS `ReadableStream` Parser
In conformance with ADR 0003's zero-build, vendor-minimal policy:
- Do not introduce external npm SSE libraries or polyfills.
- Use native browser `fetch()` combined with `response.body.getReader()` and `TextDecoder`.
- Implement a lightweight, robust stream buffer parser that accommodates TCP chunk fragmentation and multi-byte boundary splits:
  ```javascript
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    
    // Split on standard SSE double-newline message boundaries
    const parts = buffer.split("\n\n");
    buffer = parts.pop(); // Retain remainder in buffer

    for (const part of parts) {
      parseAndDispatchSSEFrame(part);
    }
  }
  ```

---

### 7. Progressive Ingestion, In-Feed Transition & RAF Throttling
To deliver a smooth user experience without layout thrashing or browser stutter:
1. **Thinking-to-Token Lifecycle Transition**:
   - On request submission, the existing in-feed `thinkingBubble` is displayed with its active radar animation and timer.
   - As `step` events arrive, `thinkingBubble` updates its displayed status text in real time (e.g., `"Searching internal knowledge base..."`).
   - Upon receipt of the first `token` event, `hideThinking()` is called, and the assistant message bubble is mounted into the DOM.
2. **Render Throttling via `requestAnimationFrame` (RAF)**:
   - High-throughput LLM token deltas (15–60 tokens/sec) are buffered in an accumulated text string.
   - DOM updates via `marked.parse()` and `DOMPurify.sanitize()` are scheduled using `requestAnimationFrame`, bounding DOM recalculations to the display's 60Hz/120Hz refresh rate.
3. **Deferred Syntax Highlighting**:
   - During active token streaming, code blocks are rendered with standard monospace markup without heavy AST parsing.
   - Full `highlight.js` syntax highlighting, language badge injection, and "Copy Code" button bindings are executed once upon stream completion (triggered by the `metadata` event).
4. **Accordion & Metadata Binding**:
   - The collapsible "Reasoning & Audit Steps" accordion, source badge, Langfuse trace ID chip, and RLHF feedback buttons are constructed and attached to the assistant bubble upon arrival of the `metadata` event.

---

### 8. State Persistence & Interruption Semantics
To maintain complete conversation integrity in `localStorage`:
1. **Normal Completion**:
   - The completed assistant message is appended to `rag_session_<session_id>` with full text, citations, trace steps, and `trace_id`.
2. **Interrupted Streams**:
   - If a stream is aborted by the user, closed by network failure, or halted by an `error` event after tokens have been received, the partial content is saved with:
     ```json
     {
       "role": "assistant",
       "text": "Partial text... [Interrupted / Incomplete]",
       "source_used": "interrupted",
       "trace": ["Stream terminated prematurely"],
       "trace_id": "<captured_trace_id_or_null>",
       "citations": []
     }
     ```
   - This ensures the user does not lose read text upon page reload, and clear indicators reflect that the generation was interrupted.

---

## Architectural Flow Diagram

```
Browser Client (app.js)            FastAPI (/api/chat/stream)              LangGraph (astream_events v2)
         │                                    │                                         │
         ├─── POST /api/chat/stream ─────────>│                                         │
         │    {question, workflow, ...}       │── Bind contextvars                      │
         │                                    │── astream_events(initial) ─────────────>│
         │                                    │                                         │
         │<── event: step {"label":"..."} ────│<── on_chain_start (retrieve_kb) ────────┤
         │    [Update Thinking Bubble]        │                                         │
         │                                    │<── on_chain_start (grade_kb) ───────────┤
         │                                    │    (JSON LLM stream tokens ignored)     │
         │                                    │                                         │
         │                                    │<── on_chat_model_stream ────────────────┤
         │                                    │    (from generate_from_kb)              │
         │<── event: token {"delta":"..."} ───│                                         │
         │    [Mount Assistant Bubble,        │                                         │
         │     Throttle RAF Markdown render]  │                                         │
         │<── event: token {"delta":"..."} ───│                                         │
         │                                    │                                         │
         │                                    │<── on_chain_end (END) ──────────────────┤
         │                                    │── write_audit(status="completed")       │
         │                                    │── handler.flush()                       │
         │<── event: metadata {...} ──────────│                                         │
         │    [Highlight code, render         │                                         │
         │     accordion, persist session]    │                                         │
         │                                    │                                         │
         ▼                                    ▼                                         ▼
```

---

## Consequences

### Positive
- **Instantaneous Perceived Response**: Time to First Token drops from 2–6 seconds down to sub-second (< 800ms for direct answers, immediate step indicators for multi-stage retrievals).
- **Auditability and Transparency**: Users visually observe the agent's workflow transitions (retrieval -> grading -> generation) in real time rather than waiting in the dark.
- **Robust Observability**: Langfuse traces and audit SQLite database entries remain fully consistent even during sudden client dropouts.
- **Zero Build / Air-Gapped Compatibility**: Implemented entirely in vanilla JavaScript and native standard library utilities without adding third-party dependencies or build steps.
- **Complete Backward Compatibility**: Non-streaming API clients and existing test suites continue to function through `POST /api/chat`.

### Negative / Trade-Offs
- **Network Middleware Sensitivity**: Requires HTTP proxies (e.g. Nginx, Cloudflare) to support streaming without buffering (`X-Accel-Buffering: no`).
- **Connection Resource Holding**: Asynchronous streaming holds open server connections for the duration of the LLM generation; requires proper async worker concurrency settings in Uvicorn.
- **Dual Endpoint Maintenance**: Updates to chat orchestrations must be verified across both `ask()` (synchronous invoke) and `astream_events()` (asynchronous generator).
