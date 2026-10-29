# 05: End-to-End Streaming & Regression Suite

**What to build:**
Verify end-to-end integration across the streaming transport layer (`chat_stream` and `chat_event_generator`) and run the full 260+ test regression suite. Ensure that promoting `retrieve_kb` and `search_web` to native `async def` nodes maintains flawless SSE step transitions, token streaming, citation emissions, and online DeepEval background evaluation tasks.

**Blocked by:** 04-self-corrective-workflow-tools

**Status:** ready-for-agent

- [ ] Verify `tests/test_chat_stream.py` passes all streaming scenarios (step events, token streaming, citation payloads) with the async tool nodes.
- [ ] Verify `tests/test_e2e_observability.py` passes all Langfuse span emissions and evaluation context hooks.
- [ ] Run full test suite: `pytest -v` across all unit, rag, api, and e2e tests with zero regressions.
- [ ] Ensure git status is clean and all scratch tickets are completed.
