# Ticket 04: Workflow Topologies Multi-Turn State Hygiene, Query Condensation & ReAct Decoupling

- **Milestone:** Unified Pluggable Conversation Memory (ADR-0016)
- **Status:** Completed
- **Dependency:** Ticket 01
- **Estimated Scope:** Bite-sized

---

## 1. Goal & Context
Bring multi-turn uniformity and context awareness into graph execution and workflow definitions:
1. Extend `AgentState` in `app/rag/state.py` with `messages: Annotated[list[BaseMessage], operator.add]`.
2. Enforce turn-to-turn hygiene in `SelfCorrectiveRagWorkflow.to_initial_state`: scrub all transient retrieval and grading artifacts (`kb_docs`, `web_results`, `retry_count`, `citations`, `retrieved_contexts`, `trace`) and default `current_query = question`.
3. Add a conditional `condense_query` node to the 10-node DAG in `app/rag/engine.py`:
   - If `len(messages) <= 1`, route directly to `route_question` (0ms overhead on Turn 1).
   - If `len(messages) > 1`, route to `condense_query` to contextualize pronouns/references before knowledge retrieval, writing standalone search query to `state["current_query"]`.
4. Inject conversation history (`messages[:-1]`) into generation node prompt calls (`generate_from_kb`, `generate_from_web`, `direct_answer`) with backward-compatible fallback.
5. Decouple `ReActAgentWorkflow` from its hardcoded `MemorySaver()` checkpointer by defaulting `checkpointer=None`.

---

## 2. Technical Requirements

### 2.1 State Definition (`app/rag/state.py`)
- Add `messages: Annotated[list[BaseMessage], operator.add]` to `AgentState`.
- Add `attachments: NotRequired[list[dict[str, Any]]]`.

### 2.2 Turn Hygiene in `SelfCorrectiveRagWorkflow` (`app/rag/workflows/self_corrective.py`)
- In `to_initial_state(input, trace_id)`:
  - Parse `messages = parse_raw_messages(getattr(input, "messages", None), question)`.
  - Set `current_query = question`.
  - Explicitly reset `kb_docs = []`, `web_results = ""`, `kb_grade = ""`, `web_grade = ""`, `answer = ""`, `source_used = ""`, `retry_count = 0`, `trace = []`, `citations = []`, `retrieved_contexts = []`.
  - Pass `messages` into state dictionary.

### 2.3 Conversational Query Condensation in `app/rag/engine.py`
- Add routing function:
  ```python
  def route_start(state: AgentState) -> Literal["condense_query", "route_question"]:
      messages = state.get("messages", [])
      if len(messages) > 1:
          return "condense_query"
      return "route_question"
  ```
- Add async node:
  ```python
  async def node_condense_query(state: AgentState, config: RunnableConfig | None = None) -> dict[str, Any]:
      structlog.contextvars.bind_contextvars(step="condense_query")
      history = state.get("messages", [])[:-1]
      current_question = state["question"]
      # Compile condensation prompt with history and question
      # Invoke model and extract clean standalone query text
      # Return {"current_query": standalone_query, "trace": [f"Query contextualized → '{standalone_query}'"]}
  ```
- Wire edges in `create_agentic_rag_graph`:
  - `graph.add_node("condense_query", node_condense_query)`
  - Replace `graph.add_edge(START, "route_question")` with:
    ```python
    graph.add_conditional_edges(START, route_start, {
        "condense_query": "condense_query",
        "route_question": "route_question",
    })
    graph.add_edge("condense_query", "route_question")
    ```
- Generation nodes update:
  - Pass `history=state.get("messages", [])[:-1]` when invoking chat model/prompts.

### 2.4 Decouple `ReActAgentWorkflow` Checkpointer (`app/rag/workflows/react_agent.py`)
- In `ReActAgentWorkflow.__init__`, set `checkpointer: Any = None` (default to `None`).
- In `get_compiled_graph`:
  ```python
  self._compiled_graph = create_agent(
      model=active_llm,
      tools=active_tools,
      checkpointer=self.checkpointer,
      system_prompt=self.system_prompt,
  )
  ```

---

## 3. TDD Implementation Steps

### Red (Failing Tests)
- Create/update `tests/test_workflow_engine_memory.py`:
  - `test_turn_1_bypasses_condense_query`: Execute graph with 1 message; verify `condense_query` is NOT in trace and `current_query == question`.
  - `test_turn_2_triggers_condense_query`: Execute graph with 2 turns (prior turn + follow up with pronoun); verify `condense_query` runs and populates `current_query`.
  - `test_transient_state_scrubbing_between_turns`: Run turn 1 with mock KB docs and citations, then construct turn 2 initial state; verify transient fields are empty lists/strings.
  - `test_react_agent_compiled_without_checkpointer`: Verify `ReActAgentWorkflow().get_compiled_graph()` has `checkpointer is None` by default and processes `messages` from initial state.

### Green (Implementation)
- Update `app/rag/state.py`.
- Update `app/rag/workflows/self_corrective.py`.
- Update `app/rag/engine.py`.
- Update `app/rag/workflows/react_agent.py`.

### Refactor
- Ensure default prompts gracefully support templates with and without `history`.
- Verify conformance tests in `tests/rag/test_self_corrective_contract.py` continue to pass.
