# Technical Specification: Response Streaming with Server-Sent Events (SSE)

- **ADR Reference:** `docs/adr/0004-response-streaming-with-server-sent-events.md`
- **Specification Document:** `docs/specs/0004-response-streaming-spec.md`
- **Target Version:** `0.4.0`
- **Status:** Approved / Ready for Implementation

---

## 1. System Overview

This specification operationalizes ADR 0004 to introduce low-latency response streaming via Server-Sent Events (SSE) from the FastAPI backend to the vanilla JS client. By decoupling graph state progression from final token generation and streaming incremental tokens directly from generation nodes, the architecture reduces perceived Time to First Token (TTFT) from **2.0–6.5 seconds down to sub-second** while providing real-time visibility into self-corrective LangGraph transitions.

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                         Browser Client                                           │
│                                                                                                  │
│   ┌───────────────────────────┐         ┌────────────────────────────────────────────────────┐   │
│   │   In-Feed Thinking Radar  │         │                 Assistant Message                  │   │
│   │ - "Searching KB... 1.2s"  │  ────>  │ - Incremental Markdown Render (RAF Throttled)      │   │
│   │ - "Grading evidence..."   │  (1st   │ - Deferred Highlight.js syntax highlighting        │   │
│   │ (Live 'step' updates)     │  Token) │ - Collapsible Reasoning Accordion (on 'metadata')  │   │
│   └───────────────────────────┘         └────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────▲────────────────────────────────────────────────────────┘
                                          │
                     Text/Event-Stream (SSE) via Fetch ReadableStream
                     Payloads: step | token | metadata | error
                                          │
┌─────────────────────────────────────────┴────────────────────────────────────────────────────────┐
│                                        FastAPI Engine                                            │
│                                                                                                  │
│   POST /api/chat        ──> Synchronous legacy invocation (invoke())                             │
│   POST /api/chat/stream ──> StreamingResponse (async generator consuming astream_events v2)      │
│                              │                                                                   │
│                              ├── Cancellation Handler (asyncio.CancelledError -> audit & flush)  │
│                              └── Contextvars & Langfuse Callback Propagation                     │
└─────────────────────────────────────────▲────────────────────────────────────────────────────────┘
                                          │
                        LangGraph Workflow (astream_events v2)
                                          │
            ┌─────────────────────────────┴─────────────────────────────┐
            │                                                           │
   Internal Nodes (JSON)                                       Generation Nodes (Async)
   - route_question (Classifier)                              - generate_from_kb (ainvoke)
   - grade_kb / grade_web (Graders)                           - generate_from_web (ainvoke)
   - rewrite_query (Query Reformulator)                       - direct_answer (ainvoke)
   [Emits 'step' events; tokens suppressed]                   [Whitelisted: emits 'token' deltas]
```

---

## 2. File Change Manifest

| File | Action | Description / Purpose |
|---|---|---|
| `app/api/schemas.py` | **Create** | Centralized Pydantic schemas (`ChatRequest`, `ChatResponse`, `FeedbackRequest`, `StreamStepEvent`, `StreamTokenEvent`, `StreamMetadataEvent`, `StreamErrorEvent`). |
| `app/api/routes.py` | **Modify** | Add `POST /api/chat/stream`, import schemas from `app.api.schemas`, bind contextvars, and run the streaming generator with `asyncio.CancelledError` interceptors. |
| `app/rag/fix_single_llm.py` | **Modify** | Convert `generate_from_kb`, `generate_from_web`, and `direct_answer` to `async def` using `await llm().ainvoke(...)`. Expose `astream_chat(...)` generator helper. |
| `app/rag/fix_single_jev.py` | **Modify** | Convert generation nodes to `async def` using `await llm().ainvoke(...)`. Expose `astream_chat(...)` generator helper. |
| `static/js/app.js` | **Modify** | Implement `readEventStream()` parser on `ReadableStream`, wire RAF-throttled progressive Markdown rendering, drive dynamic Thinking bubble transitions, and handle interrupted session caching. |
| `tests/test_chat_stream.py` | **Create** | Integration test suite verifying SSE wire protocols, event taxonomy, token isolation, pre-stream vs mid-stream errors, and cancellation cleanup. |
| `tests/test_frontend_streaming.py` | **Create** | Test suite validating streaming DOM integration, client state restoration for interrupted transcripts, and backward compatibility of `/api/chat`. |

---

## 3. Wire Protocol & Event Schema Contracts

All streaming communication uses standard SSE formatting (`event: <type>\ndata: <json>\n\n`). Every data payload is encoded as a single-line JSON string.

### 3.1 Event Taxonomy

#### 1. `event: step`
Signals entry or transition into a discrete LangGraph node.
- **Wire Payload:**
  ```json
  {
    "step": "retrieve_kb",
    "label": "Searching internal knowledge base...",
    "timestamp": 1729000000.123
  }
  ```
- **Pydantic Model:**
  ```python
  class StreamStepEvent(BaseModel):
      step: str
      label: str
      timestamp: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
  ```

#### 2. `event: token`
Delivers an incremental text delta from an active response generation node.
- **Wire Payload:**
  ```json
  {
    "delta": "To reset your corporate password, "
  }
  ```
- **Pydantic Model:**
  ```python
  class StreamTokenEvent(BaseModel):
      delta: str
  ```

#### 3. `event: metadata`
Emitted once upon successful graph completion immediately before stream termination.
- **Wire Payload:**
  ```json
  {
    "trace_id": "9cf8b1a23d4e5f6a",
    "workflow": "fix_single_jev",
    "source_used": "private_kb",
    "citations": [
      {
        "title": "IT_Policy_2026.pdf",
        "url": "",
        "type": "private_kb"
      }
    ],
    "trace": [
      "Router → KB",
      "Private KB retrieval → 3 chunks",
      "KB evidence grade → GOOD",
      "Answer generation → PRIVATE KB"
    ],
    "rewritten_query": "password reset procedure"
  }
  ```
- **Pydantic Model:**
  ```python
  class StreamMetadataEvent(BaseModel):
      trace_id: str | None = None
      workflow: str
      source_used: str
      citations: list[dict[str, Any]] = Field(default_factory=list)
      trace: list[str] = Field(default_factory=list)
      rewritten_query: str | None = None
  ```

#### 4. `event: error`
Emitted if an exception occurs mid-stream after HTTP 200 headers have already been committed.
- **Wire Payload:**
  ```json
  {
    "message": "Upstream LLM rate limit encountered",
    "code": 500
  }
  ```
- **Pydantic Model:**
  ```python
  class StreamErrorEvent(BaseModel):
      message: str
      code: int = 500
  ```

---

### 3.2 Canonical Step Labels (`STEP_LABELS`)

The backend standardizes node names into human-readable labels:

```python
STEP_LABELS: dict[str, str] = {
    "route_question": "Analyzing inquiry & routing...",
    "retrieve_kb": "Searching internal knowledge base...",
    "grade_kb": "Evaluating evidence sufficiency...",
    "search_web": "Consulting external technical documentation...",
    "grade_web": "Verifying web source credibility...",
    "rewrite_query": "Refining search terms...",
    "generate_from_kb": "Synthesizing answer from knowledge base...",
    "generate_from_web": "Drafting answer from external documentation...",
    "direct_answer": "Formulating direct response...",
    "insufficient": "Assessing fallback options...",
}
```

---

## 4. Backend Engine & LangGraph Async Streaming

### 4.1 Request Contract (`app/api/schemas.py`)

Centralize schemas in `app/api/schemas.py`:

```python
from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=3000)
    workflow: str = "fix_single_jev"
    session_id: str | None = None
    user_id: str | None = None
    trace_id: str | None = None
    stream_steps: bool = Field(default=True, description="Whether to emit intermediate graph step events")

class ChatResponse(BaseModel):
    workflow: str
    answer: str
    source_used: str
    trace: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    rewritten_query: str
    trace_id: str | None = None
```

### 4.2 Generation Node Asynchrony (`app/rag/fix_single_*.py`)

To prevent blocking the event loop and allow fine-grained token streaming via `astream_events`, generation nodes in both `fix_single_llm.py` and `fix_single_jev.py` are converted to `async def`:

```python
# Whitelisted generation node names:
GENERATION_NODES = {"generate_from_kb", "generate_from_web", "direct_answer"}

async def generate_from_kb(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_kb")
    context = "\n\n".join(f"[Source: {d.metadata.get('source','unknown')}]\n{d.page_content}" for d in state["kb_docs"])
    prompt = f"""
You are an enterprise IT support copilot. Answer ONLY from the private company KB below.
Be concise, actionable, and safe. If steps are present, present them clearly.
Do not invent policy details. Mention that the answer is based on the company's private knowledge base.
Question: {state['question']}\n\nPrivate KB:\n{context}
"""
    response = await llm().ainvoke(prompt)
    answer = response.content if hasattr(response, "content") else str(response)
    
    citations = []
    seen = set()
    for d in state["kb_docs"]:
        src = d.metadata.get("source", "Private KB")
        if src not in seen:
            seen.add(src)
            citations.append({"title": src.split("/")[-1], "url": "", "type": "private_kb"})
            
    return {
        "answer": answer,
        "source_used": "private_kb",
        "citations": citations,
        "trace": ["Answer generation → PRIVATE KB"],
    }

async def generate_from_web(state: AgentState):
    structlog.contextvars.bind_contextvars(step="generate_from_web")
    prompt = f"""
You are an enterprise IT support copilot. The private company KB was insufficient.
Answer ONLY from the web evidence below. Clearly say this is external web information and may need IT validation before changing company-managed systems.
Question: {state['question']}\n\nWeb evidence:\n{state['web_results']}
"""
    response = await llm().ainvoke(prompt)
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "web_search",
        "trace": ["Answer generation → WEB SEARCH"],
    }

async def direct_answer(state: AgentState):
    structlog.contextvars.bind_contextvars(step="direct_answer")
    response = await llm().ainvoke(f"Respond briefly and naturally to: {state['question']}")
    answer = response.content if hasattr(response, "content") else str(response)
    return {
        "answer": answer,
        "source_used": "direct",
        "trace": ["Direct response → no retrieval"],
    }
```

### 4.3 Streaming Generator & Token Filtering Engine (`app/api/routes.py`)

The streaming endpoint consumes `graph.astream_events(initial_state, version="v2")` with strict token filtering:

```python
import json
import asyncio
from typing import AsyncGenerator
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

GENERATION_NODES = {"generate_from_kb", "generate_from_web", "direct_answer"}

def format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

async def chat_event_generator(
    graph,
    initial_state: dict,
    config: dict,
    workflow: str,
    question: str,
    trace_id: str | None,
    stream_steps: bool = True,
    callback = None,
) -> AsyncGenerator[str, None]:
    accumulated_trace = []
    final_output = {}
    tokens_delivered = 0

    try:
        async for event in graph.astream_events(initial_state, version="v2", config=config):
            ev_type = event.get("event")
            node_name = event.get("metadata", {}).get("langgraph_node")

            # 1. Step transitions
            if stream_steps and ev_type == "on_chain_start" and node_name in STEP_LABELS:
                yield format_sse("step", {
                    "step": node_name,
                    "label": STEP_LABELS[node_name],
                    "timestamp": asyncio.get_event_loop().time(),
                })

            # 2. Whitelisted Token Delta Streaming (Strictly isolated from routers and graders)
            elif ev_type == "on_chat_model_stream" and node_name in GENERATION_NODES:
                chunk = event.get("data", {}).get("chunk")
                delta_content = getattr(chunk, "content", "") if chunk else ""
                if delta_content:
                    tokens_delivered += 1
                    yield format_sse("token", {"delta": delta_content})

            # 3. State capture on node completion
            elif ev_type == "on_chain_end" and node_name:
                node_output = event.get("data", {}).get("output")
                if isinstance(node_output, dict):
                    if "trace" in node_output:
                        accumulated_trace.extend(node_output["trace"])
                    final_output.update(node_output)

        # 4. Terminal Metadata Event
        resolved_tid = extract_trace_id(callback) or trace_id
        yield format_sse("metadata", {
            "trace_id": resolved_tid,
            "workflow": workflow,
            "source_used": final_output.get("source_used", "direct"),
            "citations": final_output.get("citations", []),
            "trace": accumulated_trace or final_output.get("trace", []),
            "rewritten_query": final_output.get("current_query", question),
        })

        # Finalize successful audit record
        write_audit(
            workflow=workflow,
            question=question,
            source_used=final_output.get("source_used", "direct"),
            trace=accumulated_trace or final_output.get("trace", []),
            trace_id=resolved_tid,
        )

    except asyncio.CancelledError:
        logger.warning("Streaming cancelled by client disconnect", trace_id=trace_id)
        write_audit(
            workflow=workflow,
            question=question,
            source_used="client_disconnected",
            trace=accumulated_trace + ["Stream aborted by client disconnect"],
            trace_id=trace_id,
        )
        raise

    except Exception as exc:
        logger.exception("Error during graph execution stream: %s", exc)
        yield format_sse("error", {"message": f"Inference stream failed: {str(exc)}", "code": 500})

    finally:
        if callback and hasattr(callback, "flush"):
            try:
                callback.flush()
            except Exception:
                pass


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest):
    graph = WORKFLOW_GRAPHS.get(payload.workflow)
    if graph is None:
        raise HTTPException(status_code=400, detail=f"Unknown workflow: {payload.workflow}")

    sanitized_tid = payload.trace_id.replace("-", "").lower() if payload.trace_id else uuid.uuid4().hex
    handler = get_langfuse_callback(
        workflow_name=payload.workflow,
        session_id=payload.session_id,
        user_id=payload.user_id,
        trace_id=sanitized_tid,
        metadata={"classifier": payload.workflow, "streaming": True},
    )

    structlog.contextvars.bind_contextvars(
        workflow=payload.workflow,
        trace_id=sanitized_tid,
        session_id=payload.session_id,
    )

    initial_state = {
        "question": payload.question,
        "current_query": "",
        "kb_docs": [],
        "web_results": "",
        "kb_grade": "",
        "web_grade": "",
        "answer": "",
        "source_used": "",
        "retry_count": 0,
        "trace": [],
        "citations": [],
        "trace_id": sanitized_tid,
    }

    config = {"callbacks": [handler]} if handler else {}

    return StreamingResponse(
        chat_event_generator(
            graph=graph,
            initial_state=initial_state,
            config=config,
            workflow=payload.workflow,
            question=payload.question,
            trace_id=sanitized_tid,
            stream_steps=payload.stream_steps,
            callback=handler,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

---

## 5. Client-Side SSE Parser & UI Lifecycle (`static/js/app.js`)

### 5.1 Byte Stream Parser (`readEventStream`)

A zero-build vanilla JS generator that reads UTF-8 byte chunks, accumulates partial lines, splits on SSE boundaries (`\n\n`), and yields typed event frames:

```javascript
async function* readEventStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const parts = buffer.split("\n\n");
      buffer = parts.pop(); // Retain incomplete frame in buffer

      for (const part of parts) {
        if (!part.trim()) continue;
        const lines = part.split("\n");
        let eventType = "message";
        let dataStr = "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventType = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataStr = line.slice(5).trim();
          }
        }

        if (dataStr) {
          try {
            const data = JSON.parse(dataStr);
            yield { event: eventType, data };
          } catch (err) {
            console.warn("Failed to parse SSE JSON chunk:", dataStr, err);
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
```

### 5.2 UI Transition State Machine

1. **Submission**:
   - Call `showThinking(activeWorkflow)`.
   - Disable input and submit button.
2. **`step` Event Handling**:
   - Find label inside `thinkingBubble` (`.thinking-workflow` or `.thinking-step`).
   - Dynamically update text: `wfEl.textContent = event.data.label;`.
3. **First `token` Event Handling**:
   - Invoke `hideThinking()`.
   - Allocate and mount assistant message placeholder element in the DOM with an empty `.body` container.
   - Begin streaming text accumulation.
4. **Subsequent `token` Events**:
   - Append `event.data.delta` to the in-memory string.
   - Schedule DOM render update via `requestAnimationFrame(renderMarkdownBatch)`.
5. **`metadata` Event Handling**:
   - Finalize Markdown rendering.
   - Execute `hljs.highlightElement()` on all `pre code` tags inside the message bubble.
   - Attach copy buttons to code blocks.
   - Construct and append the Collapsible Reasoning Accordion (with trace steps, source badge, Langfuse trace ID chip, and feedback buttons).
   - Persist completed transcript into `localStorage` (`rag_session_<id>`).
   - Re-enable composer input and send button.
6. **`error` Event or Connection Abort**:
   - If tokens were already mounted, append an inline warning indicator: `<div class="stream-interrupted-banner">⚠️ Response interrupted</div>`.
   - Persist partial transcript into `localStorage` marked with `source_used: "interrupted"`.
   - Display a floating toast error.
   - Re-enable composer input.

### 5.3 Render Throttling & Progressive Sanitization

```javascript
let renderPending = false;
let accumulatedMarkdown = "";

function scheduleProgressiveRender(contentEl) {
  if (renderPending) return;
  renderPending = true;

  window.requestAnimationFrame(() => {
    // Parse Markdown and sanitize with DOMPurify
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
      contentEl.innerHTML = DOMPurify.sanitize(marked.parse(accumulatedMarkdown));
    } else {
      contentEl.textContent = accumulatedMarkdown;
    }
    
    // Auto-scroll chat window smoothly
    if (chatArea) {
      chatArea.scrollTop = chatArea.scrollHeight;
    }
    renderPending = false;
  });
}
```

---

## 6. State Persistence & Interruption Semantics

### 6.1 Normal Completion Payload
```json
{
  "role": "assistant",
  "text": "Complete markdown text...",
  "source_used": "private_kb",
  "trace_id": "9cf8b1a23d4e",
  "trace": ["Router → KB", "Private KB retrieval → 3 chunks", "Answer generation → PRIVATE KB"],
  "workflow": "fix_single_jev",
  "citations": [{"title": "IT_Policy.pdf", "url": "", "type": "private_kb"}],
  "timestamp": 1729000000000,
  "feedback": {"score": null, "comment": null}
}
```

### 6.2 Interrupted / Incomplete Payload
If the stream terminates unexpectedly or an error frame arrives mid-stream:
```json
{
  "role": "assistant",
  "text": "Partial text received before connection was severed... [Interrupted / Incomplete]",
  "source_used": "interrupted",
  "trace_id": "9cf8b1a23d4e",
  "trace": ["Stream terminated prematurely"],
  "workflow": "fix_single_jev",
  "citations": [],
  "timestamp": 1729000000000,
  "feedback": {"score": null, "comment": null}
}
```

---

## 7. Verification & Test Plan

### 7.1 Automated Backend Integration Tests (`tests/test_chat_stream.py`)

Using `httpx.AsyncClient` with `client.stream("POST", "/api/chat/stream", json=...)`:

```python
import pytest
import json
import httpx
from fastapi.testclient import TestClient
from app.main import app

@pytest.mark.asyncio
async def test_chat_stream_protocol_and_token_isolation():
    """Verify SSE event stream returns correct event sequence and strictly isolates generation tokens."""
    async with httpx.AsyncClient(app=app, base_url="http://test") as ac:
        payload = {
            "question": "How do I reset my company laptop password?",
            "workflow": "fix_single_llm",
            "stream_steps": True,
        }
        async with ac.stream("POST", "/api/chat/stream", json=payload) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            
            events = []
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split("event:")[1].strip())
            
            # Assert proper taxonomy sequence
            assert "step" in events
            assert "token" in events
            assert "metadata" in events
            
            # Ensure no raw JSON router/grader brackets are leaked as single-chunk tokens
            # (e.g., {"route":"kb"})

@pytest.mark.asyncio
async def test_chat_stream_client_cancellation_audit():
    """Verify that disconnecting mid-stream triggers CancelledError and records audit row."""
    # Connect and break loop after receiving 1st token chunk, then verify audit database
    ...

def test_chat_sync_backward_compatibility():
    """Verify legacy POST /api/chat remains 100% operational with identical payload."""
    client = TestClient(app)
    res = client.post("/api/chat", json={"question": "Hello", "workflow": "fix_single_llm"})
    assert res.status_code == 200
    assert "answer" in res.json()
    assert "trace_id" in res.json()
```

### 7.2 Automated Frontend Integration Tests (`tests/test_frontend_streaming.py`)

1. **Asset & Schema Parity**: Assert that `app.js` contains the `readEventStream` generator function, handles the 4 SSE event types (`step`, `token`, `metadata`, `error`), and dispatches to `POST /api/chat/stream`.
2. **Thinking Transition Logic**: Assert that `app.js` transitions `thinkingBubble` dynamically on `step` and calls `hideThinking()` on the first `token`.
3. **Offline / Interrupted Session Restoration**: Verify client session persistence retains messages with `source_used: "interrupted"` if connection drops.
4. **XSS & DOMPurify Ingestion**: Verify simulated streaming chunks with unclosed `<img>` or `<script>` tags are sanitized through `DOMPurify` without script execution.

---

## 8. Non-Functional Requirements & Guardrails

1. **Air-Gapped Zero-Build Policy**: No npm packages, external polyfills, or CDN links. The SSE reader is written in standard vanilla JS compatible with all modern browsers.
2. **Reverse Proxy Compatibility**: The response must explicitly include `X-Accel-Buffering: no` to instruct Nginx, Envoy, or Traefik not to buffer chunked packets.
3. **Execution Concurrency**: Async streaming functions must avoid sync blocking calls (`time.sleep` or blocking I/O) on the main event thread, leveraging `starlette.concurrency.run_in_threadpool` if sync classification fallbacks are executed.
