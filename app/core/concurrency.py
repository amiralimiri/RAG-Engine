"""Concurrency utilities for safe asynchronous execution across sync and async callers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
from collections.abc import Callable
from typing import Any

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_thread: threading.Thread | None = None
_bg_lock = threading.Lock()


def _get_background_loop() -> asyncio.AbstractEventLoop:
    """Return a dedicated background event loop running on a daemon thread.

    Using a persistent background event loop prevents issues where async HTTP client connection pools
    (such as httpx / langchain-openai cached clients) are tied to a closed event loop when
    multiple sync callers run sequentially via asyncio.run().
    """
    global _bg_loop, _bg_thread
    with _bg_lock:
        if _bg_loop is None or not _bg_loop.is_running():
            _bg_loop = asyncio.new_event_loop()
            _bg_thread = threading.Thread(target=_bg_loop.run_forever, daemon=True)
            _bg_thread.start()
        return _bg_loop


def run_async_safely(coro_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Execute an async coroutine/callable synchronously and safely.

    If called from any thread (with or without an active event loop), execution is dispatched
    to a persistent background event loop with caller contextvars propagated to prevent
    event loop collision, httpx connection pool closure, and contextvar loss.
    """
    loop = _get_background_loop()
    ctx = contextvars.copy_context()

    async def _runner() -> Any:
        return await coro_fn(*args, **kwargs)

    def _submit() -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(ctx.run(_runner), loop)

    fut = _submit()
    return fut.result()


def wrap_safe_invoke(compiled_graph: Any) -> Any:
    """Monkey-patch compiled_graph.invoke with a concurrency-safe wrapper delegating to ainvoke."""
    _ainvoke = compiled_graph.ainvoke

    def _safe_invoke(input: Any, config: Any | None = None, **kwargs: Any) -> Any:
        return run_async_safely(_ainvoke, input, config=config, **kwargs)

    compiled_graph.invoke = _safe_invoke  # type: ignore[assignment]
    return compiled_graph


def shutdown_background_loop(timeout: float = 2.0) -> None:
    """Cleanly stop and close the background event loop and join its thread."""
    global _bg_loop, _bg_thread
    with _bg_lock:
        if _bg_loop is not None and _bg_loop.is_running():
            _bg_loop.call_soon_threadsafe(_bg_loop.stop)
        if _bg_thread is not None and _bg_thread.is_alive():
            _bg_thread.join(timeout=timeout)
        if _bg_loop is not None and not _bg_loop.is_running() and not _bg_loop.is_closed():
            _bg_loop.close()
        _bg_loop = None
        _bg_thread = None


