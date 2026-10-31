# 01: Node Constants, Source Resolution Harmonization & Query Fallback

**What to build:**
Update `app/rag/workflows/react_agent.py` constants and state converters ahead of compiler migration:
1. Update `DEFAULT_REACT_GENERATION_NODES` to include both `"agent"` and `"model"` (`{"agent", "model"}`).
2. Update `DEFAULT_REACT_STEP_LABELS` to map both `"agent"` and `"model"` to `"Reasoning & planning response..."` alongside `"tools": "Executing selected tool..."`.
3. Implement `_resolve_source_used(messages, tools_called, explicit_source=None)` private helper and reuse it across both `to_chat_response` and `extract_eval_context`.
4. In `to_chat_response`, add fallback extraction of the user query from the latest `HumanMessage` in `messages` if `final_state.get("question")` is missing.
5. Add unit tests in `tests/rag/test_react_agent.py` validating node constants, source resolution harmonization, and query fallback extraction.

**Blocked by:** None (can start immediately)

**Status:** closed

- [x] `DEFAULT_REACT_GENERATION_NODES` contains `{"agent", "model"}`
- [x] `DEFAULT_REACT_STEP_LABELS` contains `"agent"`, `"model"`, and `"tools"`
- [x] `_resolve_source_used` helper implemented and shared across `to_chat_response` and `extract_eval_context`
- [x] `to_chat_response` resolves question from `HumanMessage` when `final_state.get("question")` is absent
- [x] Unit tests added in `tests/rag/test_react_agent.py` covering node constants, source resolution, and query extraction
- [x] Run `uv run pytest tests/rag/test_react_agent.py` green
