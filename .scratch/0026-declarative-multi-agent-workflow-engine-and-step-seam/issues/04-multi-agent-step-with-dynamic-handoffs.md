# 04: Multi-Agent Step with Dynamic Handoffs

**What to build:**
The autonomous agent step primitive with dynamic handoff tool generation in `app/rag/steps/agent.py`.
Implements `AgentStep` (`@register_step("agent")`), which encapsulates an autonomous multi-turn tool-calling loop using scoped tools, a prompt template, and a chat model.
When `handoffs: list[str]` are specified, `AgentStep` automatically synthesizes structured handoff tools (`transfer_to_<target>(reason: str)`). When the agent model invokes a transfer tool, it records the handoff reason and updates `active_agent = target_agent` in the blackboard state, enabling seamless multi-agent orchestration without custom router nodes.

**Blocked by:**
03: Concrete Step Implementations

**Status:**
completed

- [x] Implement `AgentStep` in `app/rag/steps/agent.py` subtyping `BaseStep` and registering as `@register_step("agent")`.
- [x] Implement `create_handoff_tool(target_agent: str) -> StructuredTool` generating dynamic transfer tools with typed docstrings and schemas.
- [x] Implement tool-calling execution loop with model binding and error isolation.
- [x] Handle transfer tool calls by appending an `AIMessage` with tool call details and updating `active_agent: str` in state.
- [x] Re-export `AgentStep` from `app/rag/steps/__init__.py`.
- [x] Add unit test suite in `tests/rag/steps/test_agent_step.py` verifying tool invocation, prompt compilation, and dynamic handoff tool execution with fake models and tools.
- [x] Pass `ruff check` and `pyright` on modified modules.
