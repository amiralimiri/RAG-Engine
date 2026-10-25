import logging
import logging.handlers
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
    # Pinecone and Tavily keys
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

    render_as_json = (
        settings.log_format == "json"
        or (settings.log_format == "auto" and settings.app_env != "development")
    )

    renderer: structlog.types.Processor
    if render_as_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    # Shared processors for both structlog and standard library logs
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

    # Configure structlog engine
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Formatter that bridges stdlib LogRecords with structlog processors
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder()] + shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # 1. Primary stdout stream handler
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

    # Silence noisy external library loggers
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

    # Ensure application and critical server loggers respect configured level
    logging.getLogger("uvicorn.error").setLevel(log_level)
    logging.getLogger("app").setLevel(log_level)
