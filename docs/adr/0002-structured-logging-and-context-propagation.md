# ADR 0002: Production-Grade Structured Logging, Context Propagation, and PII Sanitization

## Status
Accepted

## Context
The enterprise IT support RAG system operates asynchronous FastAPI endpoints and standalone LangGraph multi-step workflows (`fix_single_llm` and `fix_single_jev`). Currently, logging relies on basic Python standard library configuration (`logging.basicConfig`) in `app/core/logger.py` producing unstructured plaintext lines formatted as:
`%(asctime)s | %(levelname)s | %(name)s | %(message)s`

### Key Constraints & Challenges
1. **Unstructured Output in Production**: Logs cannot be ingested, indexed, or queried effectively by modern log platforms (Datadog, Loki, CloudWatch, Elasticsearch) because they lack machine-readable JSON structure.
2. **Missing Correlation Context**: Critical correlation identifiers (`request_id`, `trace_id`, `workflow`, `user_id`, `session_id`, `step`) are absent from log records. Tracing an HTTP request through internal services and down into LangGraph agent nodes requires manual log stitching.
3. **Noisy Dependencies**: High-frequency HTTP polling and internal network libraries (`httpx`, `httpcore`, `openai`, `langchain`, `watchfiles`) flood log outputs with routine `INFO` statements for every token chunk and search request.
4. **PII and Secret Leakage Risk**: Raw log records and exception tracebacks risk exposing sensitive authorization headers (`Bearer ...`), API keys (`sk-...`, Tavily, Pinecone), and user PII.
5. **Zero Breaking Changes**: Existing calls using standard library `logging.getLogger(__name__)` across the codebase must continue to function seamlessly without forced mass-refactoring.

---

## Decisions

### 1. Dual-Core Architecture: Structlog + Standard Library Bridge
We adopt `structlog` as the primary structured logging engine while installing `structlog.stdlib.ProcessorFormatter` as the central bridge for the Python standard library `logging` module.

- **Unified Formatting**: All log records—whether originating from `structlog.get_logger()`, application standard library `logging.getLogger(__name__)`, or third-party dependencies—flow through the exact same processing pipeline.
- **Environment-Aware Rendering**:
  - `LOG_FORMAT="auto"` (default): Automatically selects `structlog.dev.ConsoleRenderer` (colored, human-readable terminal format) when `APP_ENV="development"`, and `structlog.processors.JSONRenderer` (compact newline-delimited JSON) in all other environments (staging, production).
  - Explicit override is supported via `LOG_FORMAT="json"` or `LOG_FORMAT="console"`.

### 2. Standardized Context Correlation via `contextvars`
Contextual identifiers are managed using Python's native `contextvars` module through `structlog.contextvars`:
- **Identifiers Tracked**:
  - `request_id`: UUID4 or inbound `X-Request-ID` header representing the HTTP transport lifecycle.
  - `trace_id`: Langfuse trace ID representing the LLM/agentic workflow execution graph.
  - `workflow`: Active agent graph name (`fix_single_llm`, `fix_single_jev`).
  - `user_id` & `session_id`: Client user and conversational session identifiers.
  - `step`: Current active LangGraph node (e.g., `retrieve`, `grade_evidence`, `generate`).
- **Decoupled Identity**: `request_id` and `trace_id` remain distinct context variables placed side-by-side in log entries, guaranteeing that requests failing prior to Langfuse initialization or scheduled background jobs without HTTP requests remain unambiguously traceable.
- **Node-Level Propagation**: LangGraph execution nodes bind their `step` name into contextvars upon entering the node, ensuring that downstream calls (retrievers, LLMs, tools) automatically inherit step metadata.

### 3. Context Lifecycle and Background Task Isolation
- **Request Teardown**: HTTP middleware ensures `structlog.contextvars.clear_contextvars()` runs in a `finally` block at the end of every HTTP request cycle to prevent thread-pool context leakage.
- **Async & Background Task Propagation**: Background tasks (such as `_submit_feedback_task`) and offloaded threadpool jobs are explicitly wrapped using `contextvars.copy_context().run(func, *args, **kwargs)` to safely snapshot and preserve correlation context across asynchronous boundaries.

### 4. Structured HTTP Access Logging Middleware
- Default raw plaintext access logging from `uvicorn.access` is suppressed.
- A custom FastAPI middleware intercepts inbound requests and outbound responses, logging structured metrics:
  - Fields: `http_method`, `http_path`, `http_status`, `duration_ms`, `client_ip`, `user_agent`.
  - Suppressed Paths: High-frequency polling endpoints (`/api/health`) and static assets (`/static/*`) are suppressed from access logs at `INFO` level and logged only when `LOG_LEVEL="DEBUG"`.

### 5. Multi-Tier PII and Secret Scrubbing Processor
A custom structlog processor (`scrub_sensitive_data`) is placed early in the processing chain:
- **Key-Based Redaction**: Recursively inspects log events and extra metadata dictionaries; any key matching sensitive patterns (`*password*`, `*token*`, `*api_key*`, `*authorization*`, `*secret*`, `*cookie*`) has its value replaced with `"[REDACTED]"`.
- **Regex Pattern Redaction**: String values, log messages, and serialized exceptions are filtered against precompiled regular expressions matching:
  - Bearer tokens: `Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*`
  - OpenAI / Third-party API keys: `sk-[A-Za-z0-9_\-]{20,}`
  - Generic token prefixes and high-entropy API secrets.
- **Standardized Token**: All scrubbed values are replaced strictly with `"[REDACTED]"` (zero partial leakage).

### 6. Output Streams and Rotation Strategy
Adhering to 12-factor application principles:
- **Primary Stream (`stdout`)**: Standard output is the primary log sink for containerized, serverless, and cloud deployments.
- **Opt-In File Rotation**: Enabled when `LOG_TO_FILE=True`. Configured via standard library `RotatingFileHandler` with configurable parameters:
  - `LOG_FILE_PATH`: Defaults to `logs/app.log` (directories created automatically if absent).
  - `LOG_MAX_BYTES`: Defaults to 20 MB (`20_971_520` bytes).
  - `LOG_BACKUP_COUNT`: Defaults to 5 rotating generations.

### 7. Third-Party Noise Suppression
External third-party libraries that generate high volume during LLM chunking, vector querying, or network retries are clamped to `logging.WARNING`:
- `httpx`, `httpcore`, `openai`, `langchain`, `langsmith`, `watchfiles`, `urllib3`, `asyncio`.
The application namespace (`app`, `uvicorn.error`) strictly adheres to the configured `LOG_LEVEL` (`INFO` or `DEBUG`).

### 8. Configuration Schema (`app/core/config.py`)
Logging configuration parameters are managed via Pydantic `Settings`:
```python
log_level: str = "INFO"
log_format: Literal["auto", "json", "console"] = "auto"
log_to_file: bool = False
log_file_path: str = "logs/app.log"
log_max_bytes: int = 20_971_520  # 20 MB
log_backup_count: int = 5
```

---

## Consequences

### Positive
- **Standardized Observability**: Unified JSON output across all log producers simplifies ingestion into Datadog, ELK, or Grafana Loki.
- **End-to-End Distributed Tracing**: Correlation across HTTP requests (`request_id`), Langfuse traces (`trace_id`), and agent graph execution nodes (`step`).
- **Zero Risk of Credential Leaks**: Automated scrubbing catches exposed API keys and auth headers before emission.
- **Backward Compatibility**: Existing `logging.getLogger(__name__)` calls work without modification while immediately gaining structured formatting and contextvars.
- **Clean Signal-to-Noise Ratio**: Health check probes and raw HTTP transport chunking logs no longer obscure application business logic.

### Trade-offs & Mitigations
- **Dependency Addition**: Adds `structlog` to project dependencies. *Mitigation*: Structlog is lightweight, pure-Python with optional C-extensions, and recognized as the standard for structured logging in modern Python.
- **CPU Overhead for Scrubbing**: Scanning messages and dictionary keys adds minor string inspection overhead per log line. *Mitigation*: Scrubbing uses precompiled regexes and fast dictionary key checks, running only on emitted log entries that pass the log-level filter.

---

## Implementation Roadmap

1. **Dependency Addition**: Add `structlog>=24.4.0` to `pyproject.toml`.
2. **Settings Update**: Add logging fields (`log_level`, `log_format`, `log_to_file`, `log_file_path`, `log_max_bytes`, `log_backup_count`) to `app/core/config.py`.
3. **Core Logging Module (`app/core/logger.py`)**:
   - Implement `scrub_sensitive_data` processor.
   - Configure `structlog` processors (`TimeStamper`, `add_log_level`, `StackInfoRenderer`, `format_exc_info`, contextvars merger).
   - Configure standard library bridge (`ProcessorFormatter`) and third-party logger levels.
   - Attach console handler (`sys.stdout`) and optional `RotatingFileHandler`.
4. **FastAPI Structured Access Middleware**:
   - Create `app/api/middleware.py` (or integrate into `app/main.py`) to manage `request_id` generation, contextvars binding, request timing, and structured access log emission.
   - Ensure contextvars clearing in the request lifecycle `finally` block.
5. **Background Task Isolation**:
   - Implement a context-preserving execution wrapper for background tasks (e.g. feedback submission) using `contextvars.copy_context().run`.
6. **LangGraph Node Context Binding**:
   - Update `app/rag/fix_single_llm.py` and `app/rag/fix_single_jev.py` graph nodes to bind `step` into `structlog.contextvars` during node execution.
7. **Verification & Testing**:
   - Validate JSON output formatting, dev console formatting, sensitive token redaction, and `trace_id`/`request_id` correlation via automated test cases.
