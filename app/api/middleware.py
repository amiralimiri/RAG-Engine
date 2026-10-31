import time
import uuid
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

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
        trace_id = request.headers.get("x-trace-id") or request.headers.get("trace-id")

        # Bind transport context to contextvars
        context = {"request_id": request_id}
        if user_id:
            context["user_id"] = user_id
        if session_id:
            context["session_id"] = session_id
        if trace_id:
            context["trace_id"] = trace_id.replace("-", "").lower()

        structlog.contextvars.bind_contextvars(**context)

        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            try:
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
            finally:
                # Ensure contextvars do not leak into future tasks on recycled threads
                structlog.contextvars.clear_contextvars()
