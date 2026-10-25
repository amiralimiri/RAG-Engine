# Technical Specification: Production-Grade Structured Logging, Context Propagation, and PII Sanitization

- **ADR Reference:** `docs/adr/0002-structured-logging-and-context-propagation.md`
- **Specification Document:** `docs/specs/0002-structured-logging-and-context-propagation-spec.md`
- **Target Version:** `0.2.0`
- **Status:** Approved / Ready for Implementation

---

## 1. System Overview

This specification establishes the concrete technical design for upgrading the RAG engine's logging system into a high-performance, production-grade structured logging pipeline.

The architecture decouples the logger interface from formatting and output sinks, guarantees end-to-end trace and request correlation via `contextvars`, strips sensitive credentials and PII prior to serialization, suppresses noisy third-party libraries, and supports zero breaking changes for existing standard library logging invocations.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                   Log Producers                                        │
│  - FastAPI Routes & Middleware   - Background Tasks            - Standard Library deps │
│  - LangGraph Nodes (LLM / JEV)   - Langfuse Telemetry Handlers - Third-party libraries │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │ structlog / logging.getLogger()
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             Context Enrichment Layer                                   │
│  structlog.contextvars: [request_id, trace_id, workflow, user_id, session_id, step]    │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                             PII & Secret Sanitization                                  │
│  scrub_sensitive_data: Redacts keys (*password*, *token*, *key*) and regex patterns   │
│                        (Bearer, sk-..., auth headers) -> "[REDACTED]"                  │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
                                            ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        Structlog Processor Pipeline Formatter                          │
│  - structlog.processors.TimeStamper(fmt="iso")                                         │
│  - structlog.stdlib.add_log_level / add_logger_name                                    │
│  - structlog.processors.StackInfoRenderer / format_exc_info                            │
│  - structlog.stdlib.ProcessorFormatter                                                │
└─────────────────────────────────────┬──────────────────────────────────────────────────┘
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                                     ▼
     ┌───────────────────────────┐         ┌───────────────────────────┐
     │      Primary Output       │         │      Optional Output      │
     │     sys.stdout Handler    │         │   RotatingFileHandler     │
     │  - Dev: Colored Console   │         │   (when LOG_TO_FILE=True) │
     │  - Prod: NDJSON           │         │   - logs/app.log (20MB x5)│
     └───────────────────────────┘         └───────────────────────────┘
```

---

## 2. File Change Manifest

| File | Action | Purpose |
|---|---|---|
| `pyproject.toml` | **Modify** | Add `structlog>=24.4.0` dependency. |
| `app/core/config.py` | **Modify** | Add structured logging configuration settings (`log_level`, `log_format`, `log_to_file`, `log_file_path`, etc.). |
| `app/core/logger.py` | **Rewrite** | Implement `scrub_sensitive_data` processor, standard library bridge (`ProcessorFormatter`), third-party suppression, and handlers. |
| `app/api/middleware.py` | **Create** | Implement `CorrelationLoggingMiddleware` handling `request_id`, contextvars binding, request timing, and access log emission. |
| `app/main.py` | **Modify** | Mount `CorrelationLoggingMiddleware`, initialize logging early, and ensure graceful context handling. |
| `app/api/routes.py` | **Modify** | Update background task submission to preserve context variables using `copy_context().run`. |
| `app/rag/fix_single_llm.py` | **Modify** | Bind `step` in `contextvars` across LangGraph nodes (`route_question`, `retrieve_documents`, `grade_evidence`, `generate_answer`, `rewrite_query`). |
| `app/rag/fix_single_jev.py` | **Modify** | Bind `step` in `contextvars` across LangGraph nodes (`jev_route_question`, `retrieve_documents`, `jev_grade_evidence`, `generate_answer`, `rewrite_query`). |
| `tests/unit/test_logging.py` | **Create** | Unit tests verifying JSON formatting, secret scrubbing, third-party suppression, and contextvar propagation. |

---

## 3. Concrete Specifications

### 3.1 Dependencies (`pyproject.toml`)

Add `structlog` under `dependencies`:
```toml
dependencies = [
    # ... existing dependencies ...
    "structlog>=24.4.0",
]
```

---

### 3.2 Configuration Settings (`app/core/config.py`)

Extend `Settings` with typed logging parameters:

```python
from typing import Literal

class Settings(BaseSettings):
    # ... existing settings ...

    # Logging Configuration
    log_level: str = "INFO"
    log_format: Literal["auto", "json", "console"] = "auto"
    log_to_file: bool = False
    log_file_path: str = str(BASE_DIR / "logs" / "app.log")
    log_max_bytes: int = 20_971_520  # 20 MB
    log_backup_count: int = 5
```

Environment Variable Mapping:
- `LOG_LEVEL` (e.g. `DEBUG`, `INFO`, `WARNING`, `ERROR`)
- `LOG_FORMAT` (`auto`, `json`, `console`)
- `LOG_TO_FILE` (`true` or `false`)
- `LOG_FILE_PATH` (defaults to `<project_root>/logs/app.log`)
- `LOG_MAX_BYTES` (integer byte limit)
- `LOG_BACKUP_COUNT` (integer number of rotated files to retain)

---

### 3.3 Core Logger & Pipeline (`app/core/logger.py`)

This module configures:
1. PII and secret redaction processor (`scrub_sensitive_data`).
2. Composable structlog processor pipeline.
3. Standard library redirection through `structlog.stdlib.ProcessorFormatter`.
4. Handlers for `sys.stdout` and optional `RotatingFileHandler`.
5. Noise suppression for external libraries.

```python
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Any, MutableMapping
import structlog
from structlog.types import EventDict, WrappedLogger
from app.core.config import get_settings

# Precompiled regexes for high-entropy secrets and credential tokens
SENSITIVE_KEY_PATTERNS = re.compile(
    r"(?i)(password|secret|api[_-]?key|token|auth(orization)?|cookie|credential|session)"
)
REGEX_PATTERNS = [
    # Bearer tokens
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-\._~\+\/]+=*"), "Bearer [REDACTED]"),
    # OpenAI / standard sk-... tokens
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "[REDACTED]"),
    # Tavily / Pinecone style keys (alphanumeric strings >= 30 chars with hyphens)
    (re.compile(r"pcsk_[A-Za-z0-9_\-]{30,}"), "[REDACTED]"),
    (re.compile(r"tvly-[A-Za-z0-9_\-]{30,}"), "[REDACTED]"),
]
REDACTED_STR = "[REDACTED]"


def _scrub_value(value: Any) -> Any:
    """Recursively scrub strings, mappings, and sequences."""
    if isinstance(value, str):
        for pattern, replacement in REGEX_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, MutableMapping):
        return {
            k: (REDACTED_STR if SENSITIVE_KEY_PATTERNS.search(str(k)) else _scrub_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        scrubbed = [_scrub_value(v) for v in value]
        return type(value)(scrubbed)
    return value


def scrub_sensitive_data(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Structlog processor to scrub credentials and PII from keys and string values."""
    for key, value in list(event_dict.items()):
        if SENSITIVE_KEY_PATTERNS.search(str(key)):
            event_dict[key] = REDACTED_STR
        else:
            event_dict[key] = _scrub_value(value)
    return event_dict


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retrieve a bound structlog logger."""
    return structlog.get_logger(name)


def configure_logging() -> None:
    """Initialize structured logging pipeline with stdlib interoperability."""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Determine renderer based on settings and environment
    render_as_json = (
        settings.log_format == "json"
        or (settings.log_format == "auto" and settings.app_env != "development")
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if render_as_json
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    # Shared processors executed for both structlog and standard library logs
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        scrub_sensitive_data,
    ]

    # Configure structlog internal engine
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Formatter that bridges stdlib LogRecords with Structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Configure Root Logger handlers
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # 1. Primary stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(log_level)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # 2. Optional Rotating File Handler
    if settings.log_to_file:
        log_file = Path(settings.log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_file),
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Silence noisy third-party loggers to WARNING
    noisy_loggers = [
        "httpx",
        "httpcore",
        "openai",
        "langchain",
        "langchain_core",
        "langchain_community",
        "langsmith",
        "watchfiles",
        "urllib3",
        "asyncio",
        "uvicorn.access",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # Keep critical server loggers responsive
    logging.getLogger("uvicorn.error").setLevel(log_level)
    logging.getLogger("app").setLevel(log_level)
```

---

### 3.4 HTTP Correlation & Access Middleware (`app/api/middleware.py`)

A Starlette/FastAPI middleware that:
1. Extracts or generates `request_id` (using `X-Request-ID`).
2. Extracts `user_id` and `session_id` from custom headers (`X-User-ID`, `X-Session-ID`).
3. Binds these context variables to `structlog.contextvars`.
4. Accurately measures request execution latency.
5. Emits a structured access log line (suppressing `/api/health` and `/static/*` unless `DEBUG`).
6. Ensures context variables are cleared in a `finally` block to prevent pool recycling contamination.

```python
import time
import uuid
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import structlog

logger = structlog.get_logger("app.access")

SUPPRESSED_PATHS = {"/api/health"}

class CorrelationLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Clear any residual contextvars for safety
        structlog.contextvars.clear_contextvars()

        # Extract or generate correlation IDs
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        user_id = request.headers.get("x-user-id")
        session_id = request.headers.get("x-session-id")

        # Bind transport context to contextvars
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
        )

        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        except Exception:
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            path = request.url.path
            is_suppressed = path in SUPPRESSED_PATHS or path.startswith("/static/")

            log_kwargs = {
                "http_method": request.method,
                "http_path": path,
                "http_status": status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent"),
            }

            if is_suppressed:
                logger.debug("http_request_finished", **log_kwargs)
            elif status_code >= 500:
                logger.error("http_request_finished", **log_kwargs)
            elif status_code >= 400:
                logger.warning("http_request_finished", **log_kwargs)
            else:
                logger.info("http_request_finished", **log_kwargs)

            # Ensure contextvars do not leak into future tasks on recycled threads
            structlog.contextvars.clear_contextvars()
```

---

### 3.5 Background Task Context Isolation (`app/api/routes.py`)

FastAPI `BackgroundTasks` run asynchronously after the HTTP response has finished. To prevent context truncation or leakage:

```python
import contextvars

def run_with_context(func: Callable, *args: Any, **kwargs: Any) -> None:
    """Execute a function inside a snapshot of the current contextvars."""
    ctx = contextvars.copy_context()
    ctx.run(func, *args, **kwargs)
```

In `feedback()`:
```python
@router.post("/feedback")
def feedback(payload: FeedbackRequest, background_tasks: BackgroundTasks):
    # Bind trace_id explicitly for the task
    structlog.contextvars.bind_contextvars(trace_id=payload.trace_id)
    background_tasks.add_task(
        run_with_context,
        _submit_feedback_task,
        trace_id=payload.trace_id,
        score=payload.score,
        name=payload.name,
        comment=payload.comment,
    )
    return {"status": "accepted", "trace_id": payload.trace_id}
```

In `chat()`:
```python
# Upon obtaining trace_id from Langfuse:
trace_id = extract_trace_id(handler)
if trace_id:
    structlog.contextvars.bind_contextvars(trace_id=trace_id, workflow=payload.workflow)
```

---

### 3.6 LangGraph Node Context Propagation

Both `fix_single_llm.py` and `fix_single_jev.py` will update `step` in contextvars when entering each graph node.

#### In `app/rag/fix_single_llm.py`:
```python
import structlog

def route_question(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="route_question")
    # ... existing routing logic ...

def retrieve_documents(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="retrieve_documents")
    # ... existing retrieval logic ...

def grade_evidence(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="grade_evidence")
    # ... existing grading logic ...

def generate_answer(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="generate_answer")
    # ... existing generation logic ...

def rewrite_query(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    # ... existing rewrite logic ...
```

#### In `app/rag/fix_single_jev.py`:
```python
import structlog

def jev_route_question(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="jev_route_question")
    # ... existing jev routing logic ...

def retrieve_documents(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="retrieve_documents")
    # ... existing retrieval logic ...

def jev_grade_evidence(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="jev_grade_evidence")
    # ... existing jev grading logic ...

def generate_answer(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="generate_answer")
    # ... existing generation logic ...

def rewrite_query(state: AgentState) -> dict:
    structlog.contextvars.bind_contextvars(step="rewrite_query")
    # ... existing rewrite logic ...
```

---

### 3.7 Application Lifespan & Mounting (`app/main.py`)

Mount the correlation middleware on the FastAPI app:

```python
from app.api.middleware import CorrelationLoggingMiddleware
from app.core.logger import configure_logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    configure_logging()
    init_db()
    yield
    # Shutdown
    flush_telemetry()

app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CorrelationLoggingMiddleware)
```

---

## 4. Testing & Verification Plan

### 4.1 Unit Test Suite (`tests/unit/test_logging.py`)

1. **PII & Secret Scrubbing Test**:
   - Verify that dictionary keys matching `api_key`, `authorization`, `password` have their values replaced with `"[REDACTED]"`.
   - Verify regex replacement for `sk-...`, `Bearer ...`, and Tavily/Pinecone secret patterns in arbitrary string messages.
2. **Contextvars Propagation Test**:
   - Verify that `request_id`, `trace_id`, and `step` are injected into output records when set in contextvars.
3. **JSON vs Console Output Formatter**:
   - Assert valid parseable JSON output when `render_as_json=True`.
   - Assert standard human-readable format when `render_as_json=False`.
4. **Third-Party Noise Suppression**:
   - Verify `logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING`.
   - Verify `logging.getLogger("uvicorn.access").getEffectiveLevel() == logging.WARNING`.
5. **FastAPI Middleware Integration**:
   - Execute a test request via `httpx.AsyncClient` or `TestClient`:
     - Verify `x-request-id` response header.
     - Verify that `/api/health` is logged at `DEBUG` and suppressed from `INFO`.
     - Verify contextvars are completely cleared after request completion.

---

## 5. Acceptance Criteria

- [ ] `structlog` added to `pyproject.toml` and installed.
- [ ] `Settings` in `app/core/config.py` contains typed logging fields with defaults.
- [ ] `app/core/logger.py` configures unified `ProcessorFormatter`, stdout stream, optional rotating file handler, and `scrub_sensitive_data`.
- [ ] Existing `logging.getLogger(__name__)` calls across the codebase emit structured output without code changes.
- [ ] `CorrelationLoggingMiddleware` is active in `app/main.py` and emits structured access logs with `duration_ms`, `http_status`, `request_id`.
- [ ] `/api/health` and `/static/*` are filtered at `INFO` level.
- [ ] No API keys, Bearer tokens, or passwords appear in plaintext in log streams.
- [ ] LangGraph nodes dynamically populate `step` in contextvars during execution.
- [ ] Background tasks safely snapshot context using `contextvars.copy_context().run`.
- [ ] All unit and integration tests pass cleanly.
