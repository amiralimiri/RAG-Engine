"""Unit tests for WorkflowRunner execution pipeline memory orchestration (Ticket 05)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.rag.memory.base import ConversationTurn
from app.rag.memory.in_memory import InMemoryConversationStore
from app.rag.workflows.base import (
    EvalContext,
    WorkflowInfo,
    WorkflowInput,
    WorkflowOutput,
)
from app.rag.workflows.registry import WorkflowRegistry
from app.rag.workflows.runner import WorkflowRunner, reset_workflow_runner


class MockStatefulWorkflow:
    """Mock Workflow capturing input state and simulating execution."""

    def __init__(self, workflow_id: str = "mock_stateful") -> None:
        self.info = WorkflowInfo(
            id=workflow_id,
            name="Mock Stateful Workflow",
            description="Mock workflow capturing state for memory tests",
            step_labels={"step_1": "Executing step 1"},
            generation_nodes={"generate"},
            metadata={"domain": "testing"},
        )
        self.last_initial_state: dict[str, Any] | None = None
        self.mock_graph = MagicMock()
        self.mock_graph.invoke.side_effect = self._graph_invoke
        self.mock_graph.ainvoke = AsyncMock(side_effect=self._graph_invoke)
        self.mock_graph.astream_events = self._graph_astream_events

    def _graph_invoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.last_initial_state = dict(state)
        return {
            "question": state.get("question", ""),
            "messages": list(state.get("messages", [])),
            "answer": f"Answer for: {state.get('question', '')}",
            "source_used": "vectorstore",
            "trace": ["step_1", "generate"],
            "citations": [{"title": "doc.pdf", "url": "", "type": "private_kb"}],
            "retrieved_contexts": ["retrieved chunk"],
            "current_query": state.get("question", ""),
            "trace_id": state.get("trace_id", "mock-trace-id"),
        }

    async def _graph_astream_events(self, state: dict[str, Any], version: str = "v2", config: Any = None):
        self.last_initial_state = dict(state)
        # Yield start event
        yield {"event": "on_chain_start", "metadata": {"langgraph_node": "step_1"}, "data": {}}
        # Yield token events
        text = f"Stream answer for: {state.get('question', '')}"
        for token in text.split(" "):
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "generate"},
                "data": {"chunk": token + " "},
            }
        # Yield chain end event with state output
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "generate"},
            "data": {
                "output": {
                    "question": state.get("question", ""),
                    "messages": list(state.get("messages", [])),
                    "answer": text,
                    "source_used": "vectorstore",
                    "trace": ["step_1", "generate"],
                    "citations": [{"title": "doc.pdf", "url": "", "type": "private_kb"}],
                    "retrieved_contexts": ["retrieved chunk"],
                }
            },
        }

    def get_compiled_graph(self, variant_id: str | None = None) -> Any:
        return self.mock_graph

    def to_initial_state(self, input: Any, trace_id: str | None = None) -> dict[str, Any]:
        msgs = getattr(input, "messages", None) or []
        state_msgs = list(msgs)
        q = getattr(input, "question", "")
        if q:
            state_msgs.append(HumanMessage(content=q))
        return {
            "question": q,
            "messages": state_msgs,
            "trace_id": trace_id or getattr(input, "trace_id", None),
            "session_id": getattr(input, "session_id", None),
            "retrieved_contexts": [],
            "trace": [],
        }

    def to_workflow_output(self, final_state: dict[str, Any], trace_id: str | None = None) -> WorkflowOutput:
        return WorkflowOutput(
            workflow=self.info.id,
            answer=final_state.get("answer", ""),
            source_used=final_state.get("source_used", "direct"),
            trace=final_state.get("trace", []),
            citations=final_state.get("citations", []),
            rewritten_query=final_state.get("current_query") or final_state.get("question"),
            trace_id=trace_id or final_state.get("trace_id"),
        )

    def extract_eval_context(self, final_state: dict[str, Any]) -> EvalContext:
        return EvalContext(
            source_used=final_state.get("source_used", "direct"),
            retrieved_contexts=final_state.get("retrieved_contexts", []),
        )

    def handle_error(self, exc: Exception, current_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "answer": f"Fallback error response: {exc}",
            "source_used": "error_fallback",
            "trace": current_state.get("trace", []) + ["error fallback"],
            "citations": [],
            "retrieved_contexts": [],
            "trace_id": current_state.get("trace_id"),
        }


@pytest.fixture(autouse=True)
def clean_context():
    structlog.contextvars.clear_contextvars()
    reset_workflow_runner()
    yield
    structlog.contextvars.clear_contextvars()
    reset_workflow_runner()


@pytest.mark.asyncio
async def test_runner_memory_injection():
    """Verify memory adapter is injected and accessible via property."""
    memory = InMemoryConversationStore()
    runner = WorkflowRunner(memory=memory)
    assert runner.memory is memory

    # Default memory fallback
    default_runner = WorkflowRunner()
    assert default_runner.memory is not None


@pytest.mark.asyncio
async def test_runner_hydrates_history_across_turns():
    """Run turn 1, verify persisted. Run turn 2, verify turn 1 is hydrated into graph state."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)

    session_id = "test-session-1"

    # Turn 1
    req1 = WorkflowInput(question="Hello world", session_id=session_id, workflow=mock_wf.info.id)
    resp1 = await runner.aexecute(req1)
    assert "Hello world" in resp1.answer

    # Verify memory saved turn 0
    turns = await memory.aget_turns(session_id)
    assert len(turns) == 1
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "Hello world"
    assert turns[0].assistant_answer == resp1.answer

    # Turn 2
    req2 = WorkflowInput(question="What did I just say?", session_id=session_id, workflow=mock_wf.info.id)
    resp2 = await runner.aexecute(req2)
    assert resp2.answer is not None

    # Verify graph initial state had hydrated history from turn 1
    assert mock_wf.last_initial_state is not None
    initial_msgs = mock_wf.last_initial_state["messages"]
    # Turn 1 (Human + AI) + Turn 2 Human = 3 messages
    assert len(initial_msgs) == 3
    assert isinstance(initial_msgs[0], HumanMessage)
    assert initial_msgs[0].content == "Hello world"
    assert isinstance(initial_msgs[1], AIMessage)
    assert initial_msgs[1].content == resp1.answer
    assert isinstance(initial_msgs[2], HumanMessage)
    assert initial_msgs[2].content == "What did I just say?"

    # Verify memory now has 2 turns
    turns = await memory.aget_turns(session_id)
    assert len(turns) == 2
    assert turns[1].turn_index == 1
    assert turns[1].user_query == "What did I just say?"


@pytest.mark.asyncio
async def test_runner_skips_intermediate_tool_scratchpads():
    """Execute workflow with tool messages in state. Verify memory contains only user query and assistant answer, zero ToolMessage entries."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()

    # Graph returns intermediate ToolMessage and tool-calling AIMessage
    def _invoke_with_scratchpad(state: dict[str, Any], config: Any = None):
        return {
            "question": state.get("question", ""),
            "messages": [
                HumanMessage(content=state.get("question", "")),
                AIMessage(
                    content="",
                    tool_calls=[{"name": "kb_tool", "args": {"query": "q"}, "id": "call_1"}],
                ),
                ToolMessage(content="internal tool output scratchpad", tool_call_id="call_1"),
                AIMessage(content="Final synthesized assistant answer"),
            ],
            "answer": "Final synthesized assistant answer",
            "source_used": "knowledge_base",
            "trace": ["agent", "tools", "agent"],
            "citations": [],
            "retrieved_contexts": [],
            "trace_id": "test-tid",
        }

    mock_wf.mock_graph.invoke.side_effect = _invoke_with_scratchpad
    mock_wf.mock_graph.ainvoke.side_effect = _invoke_with_scratchpad
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-scratchpad"

    req = WorkflowInput(question="Look up policy", session_id=session_id, workflow=mock_wf.info.id)
    await runner.aexecute(req)

    turns = await memory.aget_turns(session_id)
    assert len(turns) == 1
    turn = turns[0]
    assert turn.user_query == "Look up policy"
    assert turn.assistant_answer == "Final synthesized assistant answer"

    # Verify projected messages for next turn contains NO ToolMessage
    msgs = turn.to_messages()
    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)
    assert not any(isinstance(m, ToolMessage) for m in msgs)


@pytest.mark.asyncio
async def test_runner_atomic_safe_rewind():
    """Create a 3-turn session. Execute turn 2 with rewind_turn_index = 1. Verify history at index 1 is replaced and index 2 is removed."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-rewind"

    # Populate 3 turns (indices 0, 1, 2)
    for i in range(3):
        t = ConversationTurn(
            session_id=session_id,
            turn_index=i,
            user_query=f"Query {i}",
            assistant_answer=f"Answer {i}",
            source_used="direct",
        )
        await memory.asave_turn(t)

    existing_turns = await memory.aget_turns(session_id)
    assert len(existing_turns) == 3

    # Rewind to turn 1 (keep turn 0, replace turn 1, delete turn 2)
    req = WorkflowInput(
        question="Rewritten Query 1",
        session_id=session_id,
        rewind_turn_index=1,
        workflow=mock_wf.info.id,
    )
    resp = await runner.aexecute(req)
    assert "Rewritten Query 1" in resp.answer

    # Verify hydrated messages during execution only had Turn 0
    assert mock_wf.last_initial_state is not None
    initial_msgs = mock_wf.last_initial_state["messages"]
    assert len(initial_msgs) == 3  # Turn 0 (Human + AI) + Rewritten Human
    assert initial_msgs[0].content == "Query 0"
    assert initial_msgs[1].content == "Answer 0"
    assert initial_msgs[2].content == "Rewritten Query 1"

    # Verify storage now has 2 turns: Turn 0 and new Turn 1
    new_turns = await memory.aget_turns(session_id)
    assert len(new_turns) == 2
    assert new_turns[0].turn_index == 0
    assert new_turns[0].user_query == "Query 0"
    assert new_turns[1].turn_index == 1
    assert new_turns[1].user_query == "Rewritten Query 1"
    assert new_turns[1].assistant_answer == resp.answer


@pytest.mark.asyncio
async def test_runner_safe_rewind_preserves_on_failure():
    """Execute with rewind_turn_index = 1 and mock an inference failure. Verify original turns 1 and 2 remain intact in storage!"""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()

    # Fail graph execution
    mock_wf.mock_graph.ainvoke.side_effect = RuntimeError("Simulated inference failure")
    # Also make handle_error raise so execution aborts
    mock_wf.handle_error = MagicMock(side_effect=RuntimeError("Unhandled pipeline crash"))
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-rewind-fail"

    # Populate 3 turns (0, 1, 2)
    for i in range(3):
        t = ConversationTurn(
            session_id=session_id,
            turn_index=i,
            user_query=f"Query {i}",
            assistant_answer=f"Answer {i}",
            source_used="direct",
        )
        await memory.asave_turn(t)

    req = WorkflowInput(
        question="Failing Rewritten Query",
        session_id=session_id,
        rewind_turn_index=1,
        workflow=mock_wf.info.id,
    )

    with pytest.raises(Exception):
        await runner.aexecute(req)

    # Crucial assertion: Turns 1 and 2 were NOT deleted because execution failed!
    intact_turns = await memory.aget_turns(session_id)
    assert len(intact_turns) == 3
    assert [t.turn_index for t in intact_turns] == [0, 1, 2]
    assert intact_turns[1].user_query == "Query 1"
    assert intact_turns[2].user_query == "Query 2"


@pytest.mark.asyncio
async def test_runner_streaming_persists_on_success():
    """Run runner.stream(...), consume stream, verify turn saved."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-stream"

    req = WorkflowInput(
        question="Stream question",
        session_id=session_id,
        workflow=mock_wf.info.id,
    )

    events: list[str] = []
    async for frame in runner.stream(req):
        events.append(frame)

    assert any("event: token" in f for f in events)
    assert any("event: metadata" in f for f in events)

    # Verify turn persisted to memory
    turns = await memory.aget_turns(session_id)
    assert len(turns) == 1
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "Stream question"
    assert "Stream answer for: Stream question" in turns[0].assistant_answer


@pytest.mark.asyncio
async def test_runner_streaming_aborts_persistence_on_client_cancel():
    """Trigger premature cancellation on generator; verify partial turn is NOT saved."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-cancel"

    req = WorkflowInput(
        question="Cancelled question",
        session_id=session_id,
        workflow=mock_wf.info.id,
    )

    gen = runner.stream(req)
    # Read only the first frame (step event) then close generator (client disconnect)
    first_frame = await anext(gen)
    assert "event: step" in first_frame
    await gen.aclose()

    # Verify NO turn persisted to memory
    turns = await memory.aget_turns(session_id)
    assert len(turns) == 0


@pytest.mark.asyncio
async def test_runner_sync_execute_memory_hydration_and_persistence():
    """Verify synchronous runner.execute() cleanly hydrates and persists without event loop deadlock."""
    registry = WorkflowRegistry()
    mock_wf = MockStatefulWorkflow()
    registry.register(mock_wf)

    memory = InMemoryConversationStore()
    runner = WorkflowRunner(registry=registry, memory=memory)
    session_id = "test-session-sync"

    req1 = WorkflowInput(question="Sync Question 1", session_id=session_id, workflow=mock_wf.info.id)
    resp1 = runner.execute(req1)
    assert "Sync Question 1" in resp1.answer

    turns = await memory.aget_turns(session_id)
    assert len(turns) == 1
    assert turns[0].turn_index == 0
    assert turns[0].user_query == "Sync Question 1"

    req2 = WorkflowInput(question="Sync Question 2", session_id=session_id, workflow=mock_wf.info.id)
    resp2 = runner.execute(req2)
    assert "Sync Question 2" in resp2.answer

    turns = await memory.aget_turns(session_id)
    assert len(turns) == 2
    assert turns[1].turn_index == 1
    assert turns[1].user_query == "Sync Question 2"
