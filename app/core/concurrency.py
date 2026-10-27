"""Concurrency utilities for safe asynchronous execution across sync and async callers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
from typing import Any, Callable


def run_async_safely(coro_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute an async coroutine/callable synchronously and safely.

    If an event loop is already running in the current thread (e.g. inside FastAPI
    or pytest-asyncio), execution is dispatched to a single-worker ThreadPoolExecutor
    with copied contextvars to prevent 'RuntimeError: This event loop is already running'.
    Otherwise, asyncio.run is called directly.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        ctx = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                ctx.run, lambda: asyncio.run(coro_fn(*args, **kwargs))
            ).result()
    return asyncio.run(coro_fn(*args, **kwargs))


def wrap_safe_invoke(compiled_graph: Any) -> Any:
    """Monkey-patch compiled_graph.invoke with a concurrency-safe wrapper delegating to ainvoke."""
    _ainvoke = compiled_graph.ainvoke

    def _safe_invoke(input: Any, config: Any | None = None, **kwargs: Any) -> Any:
        return run_async_safely(_ainvoke, input, config=config, **kwargs)

    compiled_graph.invoke = _safe_invoke  # type: ignore[assignment]
    return compiled_graph
