# 04: Zero-Shim Pruning of `app/services/prompts.py` & Test Suite Modernization

**What to build:**
Complete the zero-shim architectural migration per ADR-0011 by modernizing `tests/test_prompts_cms.py` to test against the unified `PromptStore` seam, updating all remaining import sites across the codebase and test suite to `app/rag/prompts/`, and permanently deleting `app/services/prompts.py`. Verify that the full pytest test suite executes cleanly with 100% pass rate.

**Blocked by:** 03: Dependency Injection in Workflow Engine, Classifiers & Main Startup

**Status:** done

- [x] Modernize `tests/test_prompts_cms.py` to test `PromptStore`, `LocalPromptStore`, and `LangfusePromptStore` public interfaces without monkeypatching module globals.
- [x] Search the repository for any lingering references to `app.services.prompts` and update them to `app.rag.prompts`.
- [x] Permanently delete `app/services/prompts.py` in accordance with ADR-0011 zero-shim architecture.
- [x] Run the complete test suite (`pytest`) and verify zero regressions across all unit, integration, and API tests.
