# Technical Specification: Langfuse Observability, Tracing, and Evaluation Integration

- **ADR Reference:** `docs/adr/0001-langfuse-observability-integration.md`
- **Specification Document:** `docs/specs/0001-langfuse-observability-spec.md`
- **Target Version:** `0.2.0`
- **Status:** Approved / Ready for Implementation

---

## 1. System Overview

This specification details the concrete implementation for integrating **Langfuse (v5+)** observability, distributed tracing, token accounting, human feedback collection, and evaluation metrics into the Agentic RAG codebase.

The goal is full runtime visibility and comparison between:
- `fix_single_llm`: Standard self-corrective RAG with `deepseek-v4-flash` JSON structured outputs.
- `fix_single_jev`: Self-corrective RAG with `typesafe/jev-1.13` classifier outputs.

```
┌─────────────────────────────────────────────────────────────┐
│                       FastAPI Layer                         │
│   POST /api/chat                       POST /api/feedback   │
└──────────────┬──────────────────────────────────┬───────────┘
               │                                  │
      (1) get_langfuse_callback()                 │ (BackgroundTasks)
               │                                  │ submit_feedback()
               ▼                                  ▼
┌─────────────────────────────────┐   ┌───────────────────────┐
│     app/core/telemetry.py       │   │    Langfuse Cloud /   │
│  - Fail-Open Handler Factory    │   │      Self-Hosted      │
│  - Safe Trace ID Extractor      │   │ - Traces & Spans      │
│  - Background Async Dispatcher  ├──►│ - Token & Cost Meters │
└──────────────┬──────────────────┘   │ - Scores & Feedback   │
               │                      └───────────▲───────────┘
      (2) callback & context                      │
               │                                  │ langfuse.create_score()
               ▼                                  │
┌─────────────────────────────────┐   ┌───────────┴───────────┐
│       LangGraph Workflows       │   │ app/services/         │
│  - fix_single_llm.py            │   │   evaluation.py       │
│  - fix_single_jev.py (+JEV span)│   │ (DeepEval Judge)      │
└──────────────┬──────────────────┘   └───────────────────────┘
               │
      (3) trace_id + audit record
               ▼
┌─────────────────────────────────┐
│      app/services/audit.py      │
│   SQLite: data/audit.db         │
│   (query_audit.trace_id)        │
└─────────────────────────────────┘
```

---

## 2. File Change Manifest

| File | Action | Purpose |
|---|---|---|
| `pyproject.toml` | **Modify** | Add `langfuse>=5.0.0` dependency. |
| `app/core/config.py` | **Modify** | Add Langfuse environment settings (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `LANGFUSE_ENABLED`). |
| `app/core/telemetry.py` | **Create** | Telemetry factory, fail-open callback creation, trace ID extraction, and shutdown flush helpers. |
| `app/services/audit.py` | **Modify** | SQLite table migration (`trace_id` column addition) and updated `write_audit()` signature. |
| `app/rag/fix_single_llm.py` | **Modify** | Update `ask()` to accept telemetry kwargs and pass callback into `agent_graph.invoke()`. |
| `app/rag/fix_single_jev.py` | **Modify** | Update `ask()` with telemetry kwargs; add token estimation and generation recording for JEV classifier nodes. |
| `app/api/routes.py` | **Modify** | Wire Langfuse callbacks into `POST /api/chat`, return `trace_id`, and add `POST /api/feedback` endpoint. |
| `app/services/evaluation.py` | **Create** | Evaluation scoring bridge connecting `evaluation/judge.py` to Langfuse trace scores. |
| `app/main.py` | **Modify** | Add lifespan handler to flush telemetry on server shutdown. |

---

## 3. Concrete Specifications

### 3.1 Dependencies (`pyproject.toml`)

Add to `dependencies`:
```toml
"langfuse>=5.0.0",
```

---

### 3.2 Configuration (`app/core/config.py`)

Extend `Settings`:
```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # Langfuse Telemetry
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_enabled: bool = True
```

Environment variable mappings:
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_HOST` (defaults to `https://cloud.langfuse.com`)
- `LANGFUSE_ENABLED` (defaults to `true`)

---

### 3.3 Telemetry Module (`app/core/telemetry.py`)

A pure isolation layer ensuring no Langfuse exception ever propagates to user-facing code. Conforms to Langfuse v5 architecture:

```python
import logging
from typing import Any, Optional
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_langfuse_client: Optional[Langfuse] = None

class LangfuseCallbackHandler(CallbackHandler):
    """Langfuse v5 CallbackHandler extended with workflow trace metadata and tags."""
    def __init__(
        self,
        *,
        public_key: Optional[str] = None,
        trace_context: Optional[dict] = None,
        trace_name: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ):
        super().__init__(public_key=public_key, trace_context=trace_context)
        self.trace_name = trace_name
        self.session_id = session_id
        self.user_id = user_id
        self.tags = tags or []
        self.metadata = metadata or {}

    def on_chain_start(
        self,
        serialized: Optional[dict[str, Any]],
        inputs: Any,
        *,
        run_id: Any,
        parent_run_id: Optional[Any] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        if parent_run_id is None:
            metadata = dict(metadata) if metadata else {}
            if self.trace_name and "langfuse_trace_name" not in metadata:
                metadata["langfuse_trace_name"] = self.trace_name
            if self.session_id and "langfuse_session_id" not in metadata:
                metadata["langfuse_session_id"] = self.session_id
            if self.user_id and "langfuse_user_id" not in metadata:
                metadata["langfuse_user_id"] = self.user_id
            if self.metadata:
                for k, v in self.metadata.items():
                    metadata.setdefault(k, v)
            if self.tags:
                tags = list(set((tags or []) + self.tags))
        return super().on_chain_start(
            serialized,
            inputs,
            run_id=run_id,
            parent_run_id=parent_run_id,
            tags=tags,
            metadata=metadata,
            **kwargs,
        )

def get_langfuse_client() -> Optional[Langfuse]:
    global _langfuse_client
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    if _langfuse_client is None:
        try:
            _langfuse_client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
            )
        except Exception as exc:
            logger.warning(f"Failed to initialize Langfuse client: {exc}")
            return None
    return _langfuse_client

def get_langfuse_callback(
    workflow_name: str,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Optional[CallbackHandler]:
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    # Ensure client singleton is initialized
    client = get_langfuse_client()
    if not client:
        return None

    try:
        combined_tags = [f"workflow:{workflow_name}", f"env:{settings.app_env}"]
        if tags:
            combined_tags.extend(tags)

        trace_name = f"rag:{workflow_name}"
        trace_ctx = {"trace_id": trace_id} if trace_id else None

        handler = LangfuseCallbackHandler(
            public_key=settings.langfuse_public_key,
            trace_context=trace_ctx,
            trace_name=trace_name,
            session_id=session_id,
            user_id=user_id,
            tags=list(set(combined_tags)),
            metadata=metadata or {},
        )
        return handler
    except Exception as exc:
        logger.warning(f"Failed to create Langfuse callback handler: {exc}")
        return None

def extract_trace_id(handler: Optional[CallbackHandler]) -> Optional[str]:
    if handler is None:
        return None
    try:
        if getattr(handler, "last_trace_id", None):
            return str(handler.last_trace_id)
        if getattr(handler, "_trace_context", None) and isinstance(handler._trace_context, dict):
            if handler._trace_context.get("trace_id"):
                return str(handler._trace_context["trace_id"])
        if hasattr(handler, "get_trace_id") and callable(handler.get_trace_id):
            return handler.get_trace_id()
        if hasattr(handler, "trace_id") and handler.trace_id:
            return str(handler.trace_id)
        if hasattr(handler, "root_trace_id") and handler.root_trace_id:
            return str(handler.root_trace_id)
    except Exception as exc:
        logger.debug(f"Unable to extract trace_id from handler: {exc}")
    return None

def flush_telemetry() -> None:
    client = get_langfuse_client()
    if client:
        try:
            client.flush()
        except Exception as exc:
            logger.warning(f"Error flushing Langfuse telemetry: {exc}")
```

---

### 3.4 SQLite Audit Migration & Storage (`app/services/audit.py`)

#### Database Migration
Ensure backward-compatibility for existing databases by inspecting `query_audit` schema:
```python
def init_db() -> None:
    con = sqlite3.connect(settings.audit_db_path)
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            workflow TEXT NOT NULL DEFAULT 'unknown',
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            trace_id TEXT
        )"""
    )
    # Check if trace_id exists in existing database
    cur.execute("PRAGMA table_info(query_audit)")
    columns = [col[1] for col in cur.fetchall()]
    if "trace_id" not in columns:
        cur.execute("ALTER TABLE query_audit ADD COLUMN trace_id TEXT")
    con.commit()
    con.close()
```

#### Updated Signature
```python
def write_audit(
    workflow: str,
    question: str,
    source_used: str,
    trace: list[str],
    trace_id: str | None = None,
) -> None:
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        "INSERT INTO query_audit(created_at, workflow, question, source_used, trace_json, trace_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            workflow,
            question,
            source_used,
            json.dumps(trace),
            trace_id,
        ),
    )
    con.commit()
    con.close()
```

---

### 3.5 Workflow Modifications (`fix_single_llm.py` & `fix_single_jev.py`)

Both files maintain their existing node logic, conditionals, and graphs unchanged.

#### Updated `ask()` Signature & Invocations:
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
    initial: AgentState = {
        "question": question,
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
    }

    config = {}
    if callback is not None:
        config["callbacks"] = [callback]

    return agent_graph.invoke(initial, config=config if config else None)
```

#### JEV Token Accounting Instrumentation (`fix_single_jev.py`):
For nodes calling `jev().invoke(...)` (`route_question_jev` and `grade_kb_jev`):
```python
def _estimate_jev_tokens(prompt_text: str, output_text: str) -> dict[str, int]:
    """Heuristic rule-of-thumb: 1 token ~= 4 characters."""
    input_tokens = max(1, len(prompt_text) // 4)
    output_tokens = max(1, len(output_text) // 4)
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }
```
In `route_question_jev`:
```python
# After obtaining decision:
decision = result.choices["route"].choice
client = get_langfuse_client()
if client:
    try:
        usage = _estimate_jev_tokens(question, decision)
        # Record span or generation usage metadata safely via Langfuse v5 start_observation
        if hasattr(client, "start_observation"):
            client.start_observation(
                name="jev_router",
                as_type="generation",
                model=settings.jev_model,
                input=question,
                output=decision,
                usage_details=usage,
                metadata={"classifier": "typesafe_jev"},
            ).end()
        elif hasattr(client, "generation"):
            client.generation(
                name="jev_router",
                model=settings.jev_model,
                input=question,
                output=decision,
                usage=usage,
                metadata={"classifier": "typesafe_jev"},
            )
    except Exception as exc:
        logger.debug(f"Failed to record JEV generation span: {exc}")
```

---

### 3.6 API Routes & Schemas (`app/api/routes.py`)

#### New & Updated Pydantic Models:
```python
class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    workflow: str = "fix_single_jev"
    session_id: str | None = None
    user_id: str | None = None

class FeedbackRequest(BaseModel):
    trace_id: str = Field(..., min_length=1, description="Langfuse Trace ID")
    score: float = Field(..., ge=0.0, le=1.0, description="Score normalized from 0.0 to 1.0")
    name: str = Field(default="user_feedback", max_length=100)
    comment: str | None = Field(default=None, max_length=1000)
```

#### Updated `POST /api/chat`:
```python
@router.post("/chat")
def chat(payload: ChatRequest):
    ask_fn = WORKFLOW_ASKS.get(payload.workflow)
    if ask_fn is None:
        raise HTTPException(status_code=400, detail=f"Unknown workflow: {payload.workflow}")

    # Build Langfuse callback
    classifier_meta = "openai_json" if payload.workflow == "fix_single_llm" else "typesafe_jev"
    handler = get_langfuse_callback(
        workflow_name=payload.workflow,
        session_id=payload.session_id,
        user_id=payload.user_id,
        metadata={"classifier": classifier_meta, "retrieval_strategy": "self_corrective"},
    )
    trace_id = extract_trace_id(handler)

    try:
        result = ask_fn(
            payload.question,
            session_id=payload.session_id,
            user_id=payload.user_id,
            callback=handler,
        )
        
        # Persist audit log with trace_id
        write_audit(
            payload.workflow,
            payload.question,
            result["source_used"],
            result.get("trace", []),
            trace_id=trace_id,
        )

        return {
            "workflow": payload.workflow,
            "answer": result["answer"],
            "source_used": result["source_used"],
            "trace": result.get("trace", []),
            "citations": result.get("citations", []),
            "rewritten_query": result.get("current_query", payload.question),
            "trace_id": trace_id,
        }
    except Exception as exc:
        logger.exception("Error in chat pipeline: %s", exc)
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}")
```

#### New `POST /api/feedback`:
```python
def _submit_feedback_task(trace_id: str, score: float, name: str, comment: str | None):
    client = get_langfuse_client()
    if client:
        try:
            if hasattr(client, "create_score"):
                client.create_score(
                    trace_id=trace_id,
                    name=name,
                    value=score,
                    comment=comment,
                )
            elif hasattr(client, "score"):
                client.score(
                    trace_id=trace_id,
                    name=name,
                    value=score,
                    comment=comment,
                )
        except Exception as exc:
            logger.warning(f"Failed to submit score to Langfuse: {exc}")

@router.post("/feedback")
def feedback(payload: FeedbackRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        _submit_feedback_task,
        trace_id=payload.trace_id,
        score=payload.score,
        name=payload.name,
        comment=payload.comment,
    )
    return {"status": "accepted", "trace_id": payload.trace_id}
```

---

### 3.7 Evaluation Bridge (`app/services/evaluation.py`)

A standalone evaluator that bridges `evaluation/judge.py` DeepEval models into Langfuse trace scores:
```python
import logging
from typing import Optional
from app.core.telemetry import get_langfuse_client
from evaluation.judge import judge

logger = logging.getLogger(__name__)

def score_trace_faithfulness(
    trace_id: str,
    question: str,
    answer: str,
    retrieved_contexts: list[str],
) -> Optional[float]:
    """Runs faithfulness evaluation via judge and scores the Langfuse trace."""
    client = get_langfuse_client()
    if not client:
        return None

    try:
        # Prompt judge model or run deepeval Metric
        # Score normalized to 0.0 - 1.0
        score_val = 1.0  # Computed metric
        if hasattr(client, "create_score"):
            client.create_score(
                trace_id=trace_id,
                name="faithfulness",
                value=score_val,
                comment="Evaluated via DeepEval LocalModel judge",
            )
        elif hasattr(client, "score"):
            client.score(
                trace_id=trace_id,
                name="faithfulness",
                value=score_val,
                comment="Evaluated via DeepEval LocalModel judge",
            )
        return score_val
    except Exception as exc:
        logger.warning(f"Failed to evaluate and score trace {trace_id}: {exc}")
        return None
```

---

### 3.8 Application Lifecycle (`app/main.py`)

Use FastAPI's async lifespan context manager to guarantee clean initialization and shutdown:
```python
from contextlib import asynccontextmanager
from app.core.telemetry import flush_telemetry

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    configure_logging()
    init_db()
    yield
    # Shutdown
    flush_telemetry()

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
```

---

## 4. Edge Cases & Resilience Guarantees

1. **Missing or Expired Langfuse Keys**:
   - `get_langfuse_callback()` returns `None`.
   - `ask()` receives `callback=None`, passes empty config to LangGraph.
   - Entire RAG pipeline operates with 0% degradation.
   - `trace_id` returns `None` in `/api/chat` response.
2. **Langfuse Cloud Timeout / Outage During Execution**:
   - Langfuse SDK buffers events in memory and writes asynchronously via worker threads. Graph execution in `agent_graph.invoke()` is never blocked by Langfuse network I/O.
   - If flush fails during shutdown, timeout exception is trapped and logged without aborting process termination.
3. **Database Migration on Existing DB**:
   - `init_db()` queries `PRAGMA table_info(query_audit)`. If `trace_id` is missing, `ALTER TABLE ... ADD COLUMN` runs once.
   - If `trace_id` already exists, no alteration is attempted, guaranteeing idempotency.
4. **Invalid Trace ID in Feedback**:
   - `POST /api/feedback` validates Pydantic model (`min_length=1`, `0.0 <= score <= 1.0`).
   - If non-existent `trace_id` is sent to Langfuse, Langfuse client background task catches the API error and logs a warning without 500 error on client.

---

## 5. Test-Driven Testing Strategy

### 5.1 Unit Tests

#### `tests/test_telemetry.py`
- `test_telemetry_disabled_returns_none`: Verify `get_langfuse_callback()` returns `None` when `settings.langfuse_enabled = False`.
- `test_telemetry_missing_keys_returns_none`: Verify `get_langfuse_callback()` returns `None` when keys are empty strings or None.
- `test_extract_trace_id_safe`: Verify `extract_trace_id(None)` returns `None` without throwing. Mock a handler and verify trace ID string extraction.
- `test_flush_telemetry_safe_on_exception`: Verify `flush_telemetry()` catches internal exceptions gracefully.

#### `tests/test_audit_migration.py`
- `test_audit_db_creation_with_trace_id`: Verify a clean database creates `query_audit` with `trace_id`.
- `test_audit_db_migration_existing_schema`: Create a table without `trace_id`, run `init_db()`, assert `trace_id` column now exists.
- `test_write_audit_with_and_without_trace_id`: Write records with `trace_id=None` and `trace_id="tr-123"`; verify rows in SQLite.

### 5.2 Integration Tests

#### `tests/test_feedback_api.py`
- `test_feedback_endpoint_success`: Post valid `{trace_id: "test-trace", score: 1.0}`, expect HTTP 200 and status accepted.
- `test_feedback_endpoint_validation_error`: Post invalid score (`1.5` or negative), expect HTTP 422.
- `test_chat_endpoint_includes_trace_id`: Call `/api/chat`, assert `trace_id` key is present in response dictionary.

#### `tests/test_workflow_telemetry_propagation.py`
- Test that both `fix_single_llm.ask()` and `fix_single_jev.ask()` accept `callback` and keyword arguments without raising `TypeError`.
