from unittest.mock import MagicMock

import pytest

from app.core.concurrency import run_async_safely, shutdown_background_loop, wrap_safe_invoke


def test_run_async_safely_without_running_loop():
    async def sample_coro(val: int) -> int:
        return val * 2

    assert run_async_safely(sample_coro, 21) == 42


@pytest.mark.asyncio
async def test_run_async_safely_inside_running_loop():
    async def sample_coro(val: str) -> str:
        return f"result: {val}"

    # Current loop is running in pytest-asyncio
    res = run_async_safely(sample_coro, "hello")
    assert res == "result: hello"


def test_run_async_safely_propagates_contextvars():
    """Contextvars bound on the caller thread must propagate into run_async_safely."""
    import structlog

    structlog.contextvars.bind_contextvars(request_id="req-test-safe", step="test_step")

    async def sample_coro() -> dict:
        return structlog.contextvars.get_contextvars()

    res = run_async_safely(sample_coro)
    assert res.get("request_id") == "req-test-safe"
    assert res.get("step") == "test_step"



def test_wrap_safe_invoke():
    mock_graph = MagicMock()

    async def mock_ainvoke(input_data, config=None, **kwargs):
        return {"processed": input_data, "config": config}

    mock_graph.ainvoke = mock_ainvoke
    wrapped = wrap_safe_invoke(mock_graph)

    res = wrapped.invoke({"test": "data"}, config={"key": "val"})
    assert res["processed"] == {"test": "data"}
    assert res["config"] == {"key": "val"}


def test_shutdown_background_loop():
    # Ensure loop is active first
    async def sample():
        return 1

    assert run_async_safely(sample) == 1
    shutdown_background_loop()

    # Verify that a subsequent call seamlessly re-creates loop
    assert run_async_safely(sample) == 1
    shutdown_background_loop()

