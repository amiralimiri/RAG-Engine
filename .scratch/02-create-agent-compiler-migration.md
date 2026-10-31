# 02: Canonical `create_agent` Compiler Migration

**What to build:**
Modernize `ReActAgentWorkflow.get_compiled_graph()` to use `from langchain.agents import create_agent`:
1. Replace `from langgraph.prebuilt import create_react_agent` with `from langchain.agents import create_agent`.
2. Update compiler invocation in `get_compiled_graph()`:
   ```python
   self._compiled_graph = create_agent(
       model=active_llm,
       tools=active_tools,
       checkpointer=active_checkpointer,
       system_prompt=self.system_prompt,
   )
   ```
3. Verify graph node topology produces `['__start__', 'model', 'tools', '__end__']`.
4. Add unit test asserting that invoking `get_compiled_graph()` and running ReAct workflow produces zero `LangGraphDeprecatedSinceV10` warnings.

**Blocked by:** 01: Node Constants, Source Resolution Harmonization & Query Fallback

**Status:** completed

- [x] Import modernized to `from langchain.agents import create_agent`
- [x] Compiler invocation updated to `system_prompt=self.system_prompt`
- [x] Graph node topology verified to have `model` and `tools`
- [x] Zero `LangGraphDeprecatedSinceV10` deprecation warnings on compilation or execution
- [x] `tests/rag/test_react_agent.py` passes 100% green without deprecation warnings
