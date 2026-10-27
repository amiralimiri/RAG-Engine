import asyncio
from unittest.mock import MagicMock
import pytest
from app.core.concurrency import run_async_safely, wrap_safe_invoke


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


def test_wrap_safe_invoke():
    mock_graph = MagicMock()

    async def mock_ainvoke(input_data, config=None, **kwargs):
        return {"processed": input_data, "config": config}

    mock_graph.ainvoke = mock_ainvoke
    wrapped = wrap_safe_invoke(mock_graph)

    res = wrapped.invoke({"test": "data"}, config={"key": "val"})
    assert res["processed"] == {"test": "data"}
    assert res["config"] == {"key": "val"}
