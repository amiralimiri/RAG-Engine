# Work Packages / Implementation Tickets: Structured Logging & Context Propagation

- **Specification Reference:** [`docs/specs/0002-structured-logging-and-context-propagation-spec.md`](./0002-structured-logging-and-context-propagation-spec.md)
- **ADR Reference:** [`docs/adr/0002-structured-logging-and-context-propagation.md`](../adr/0002-structured-logging-and-context-propagation.md)
- **Status:** Ready for Implementation (`ready-for-agent`)

---

## Dependency Graph

```
[Ticket 1: Deps & Config]
        │
        ▼
[Ticket 2: Core Logger & Scrubbing Engine]
        │
        ├────────────────────────────────┐
        ▼                                ▼
[Ticket 3: HTTP Middleware]    [Ticket 5: LangGraph Step Propagation]
        │
        ▼
[Ticket 4: Context Preservation in Routes & Tasks]
        │
        └────────────────────────────────┬───────────────────────────┘
                                         ▼
                             [Ticket 6: Automated Test Suite]
```

---

## Ticket 1: Dependencies and Settings Configuration

- **Title:** Add `structlog` dependency and logging settings in `app/core/config.py`
- **Label:** `ready-for-agent`
- **Blockers:** None
- **Files Modified:**
  - `pyproject.toml`
  - `app/core/config.py`

### Description
Add `structlog>=24.4.0` to the project dependencies in `pyproject.toml`. Extend Pydantic `Settings` in `app/core/config.py` with typed configuration parameters for log level, output formatting, and log file rotation defaults.

### Acceptance Criteria
- [x] `structlog>=24.4.0` added to `dependencies` list in `pyproject.toml`.
- [x] `Settings` class has the following fields with sensible defaults:
  - `log_level: str = "INFO"`
  - `log_format: Literal["auto", "json", "console"] = "auto"`
  - `log_to_file: bool = False`
  - `log_file_path: str = str(BASE_DIR / "logs" / "app.log")`
  - `log_max_bytes: int = 20_971_520` (20 MB)
  - `log_backup_count: int = 5`
- [x] Environment variables `LOG_LEVEL`, `LOG_FORMAT`, `LOG_TO_FILE`, `LOG_FILE_PATH`, `LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` properly override defaults.

---

## Ticket 2: Core Logging Engine, Scrubbing Processor, and Stdlib Bridge

- **Title:** Implement structured logging pipeline, PII/secret scrubbing, and stdlib bridge
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 1
- **Files Modified:**
  - `app/core/logger.py`

### Description
Rewrite `app/core/logger.py` to establish a production-grade Structlog pipeline. Implement recursive PII/secret scrubbing (`scrub_sensitive_data`) matching sensitive keys and precompiled regexes (Bearer tokens, OpenAI keys, Pinecone/Tavily keys) replaced with `"[REDACTED]"`. Configure `structlog.stdlib.ProcessorFormatter` so standard library `logging.getLogger(__name__)` calls pass through the identical processing pipeline. Attach `sys.stdout` handler, optional `RotatingFileHandler`, and quiet noisy external loggers (`httpx`, `httpcore`, `openai`, `langchain`, `watchfiles`, `uvicorn.access`) to `logging.WARNING`.

### Acceptance Criteria
- [x] `scrub_sensitive_data` recursively redacts sensitive dict keys (`password`, `secret`, `api_key`, `token`, `auth`, `cookie`) with `"[REDACTED]"`.
- [x] `scrub_sensitive_data` replaces `Bearer ...`, `sk-...`, `tvly-...`, `pcsk_...` with `"[REDACTED]"`.
- [x] `ProcessorFormatter` installed as formatter for root logger handler.
- [x] Format automatically renders colored console logs in local development (`app_env == "development"`) and JSON in production.
- [x] When `log_to_file=True`, a `RotatingFileHandler` is registered writing to `log_file_path` with parent directory creation.
- [x] External loggers (`httpx`, `httpcore`, `openai`, `langchain`, `watchfiles`, `uvicorn.access`) clamped to `WARNING`.
- [x] Helper function `get_logger(name)` exposed.

---

## Ticket 3: HTTP Correlation & Access Logging Middleware

- **Title:** Implement `CorrelationLoggingMiddleware` and mount in FastAPI app
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 2
- **Files Modified:**
  - `app/api/middleware.py` (New)
  - `app/main.py`

### Description
Implement a Starlette/FastAPI `CorrelationLoggingMiddleware`. On inbound requests, generate or extract `request_id` (via `X-Request-ID`), extract `user_id` and `session_id`, and bind them to `structlog.contextvars`. Time request duration and emit a structured access log line upon completion. Suppress `/api/health` and `/static/*` unless `log_level == "DEBUG"`. Ensure contextvars are cleared in a `finally` block to prevent leakage between requests. Mount the middleware on `FastAPI` in `app/main.py`.

### Acceptance Criteria
- [x] `CorrelationLoggingMiddleware` created in `app/api/middleware.py`.
- [x] Middleware sets `X-Request-ID` header on outbound HTTP responses.
- [x] Access log event `http_request_finished` records `http_method`, `http_path`, `http_status`, `duration_ms`, `client_ip`, and `user_agent`.
- [x] `/api/health` and `/static/*` requests emit at `DEBUG` level and do not appear in `INFO` logs.
- [x] `structlog.contextvars.clear_contextvars()` executed in the `finally` teardown.
- [x] Middleware added to `app = FastAPI(...)` in `app/main.py`.

---

## Ticket 4: Context Preservation in Async Routes & Background Tasks

- **Title:** Wire trace correlation and context propagation in API routes and background tasks
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 3
- **Files Modified:**
  - `app/api/routes.py`

### Description
Update `app/api/routes.py` to bind `trace_id` and `workflow` to `structlog.contextvars` as soon as Langfuse telemetry handlers are created in `chat()`. Implement a safe `run_with_context` runner using `contextvars.copy_context().run` and use it to dispatch background feedback scoring tasks in `POST /api/feedback`, ensuring worker threads retain correlation IDs without cross-request pollution.

### Acceptance Criteria
- [x] Helper `run_with_context(func, *args, **kwargs)` implemented using `contextvars.copy_context().run`.
- [x] `POST /api/chat` binds `trace_id` and `workflow` to contextvars upon initialization.
- [x] `POST /api/feedback` binds `trace_id` and wraps `_submit_feedback_task` in `run_with_context`.
- [x] Error logs in route exception blocks include `request_id`, `trace_id`, and `workflow` context.

---

## Ticket 5: LangGraph Node Step Context Propagation

- **Title:** Bind active workflow node step in `fix_single_llm.py` and `fix_single_jev.py`
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 2
- **Files Modified:**
  - `app/rag/fix_single_llm.py`
  - `app/rag/fix_single_jev.py`

### Description
Update graph execution nodes in `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` to call `structlog.contextvars.bind_contextvars(step="<node_name>")` upon entry. This ensures that logs emitted by retrievers, vectorstores, web search tools, or LLM wrappers automatically include the active graph step.

### Acceptance Criteria
- [x] `app/rag/fix_single_llm.py` nodes (`route_question`, `retrieve_documents`, `grade_evidence`, `generate_answer`, `rewrite_query`) bind `step`.
- [x] `app/rag/fix_single_jev.py` nodes (`jev_route_question`, `retrieve_documents`, `jev_grade_evidence`, `generate_answer`, `rewrite_query`) bind `step`.
- [x] Standalone file boundaries and graph invocation signatures remain unchanged.

---

## Ticket 6: Automated Test Suite & Validation

- **Title:** Implement unit and integration tests for logging, scrubbing, and context propagation
- **Label:** `ready-for-agent`
- **Blockers:** Ticket 3, Ticket 4, Ticket 5
- **Files Modified:**
  - `tests/unit/test_logging.py` (New)

### Description
Create a comprehensive test suite in `tests/unit/test_logging.py` validating:
1. PII and secret redaction processor across dictionaries and text strings.
2. Context variable injection (`request_id`, `trace_id`, `step`) in log outputs.
3. Dev console vs JSON formatting behavior.
4. Third-party logger noise suppression levels.
5. Access middleware header propagation, health check filtering, and context clearing.

### Acceptance Criteria
- [x] Unit tests for `scrub_sensitive_data` test key masking and regex patterns (`sk-...`, `Bearer ...`).
- [x] Unit test verifies standard library `logging.getLogger(__name__)` output contains contextvars.
- [x] Middleware test verifies `X-Request-ID` is generated and echoed on response.
- [x] Healthcheck suppression test verifies `/api/health` does not emit `INFO` logs.
- [x] All tests execute and pass via `pytest tests/unit/test_logging.py`.
