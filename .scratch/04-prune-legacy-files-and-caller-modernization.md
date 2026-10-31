# 04: Zero-Shim Pruning of Legacy Files & Caller Modernization

**What to build:**
Complete codebase unification by pruning obsolete legacy evaluation files (`app/services/evaluation.py` and `evaluation/judge.py`), updating `POST /api/feedback` to use `get_evaluator().score_metric(...)`, and modernizing all remaining test suites.

**Blocked by:** 03: Experiment Runner Migration & Metric Consolidation

**Status:** ready-for-agent

- [ ] `app/api/routes.py` (`POST /api/feedback`) invokes `get_evaluator().score_metric(...)`
- [ ] Delete `app/services/evaluation.py` permanently
- [ ] Delete `evaluation/judge.py` permanently
- [ ] Modernize all tests importing from `app.services.evaluation` or `evaluation.judge` to import from `app.rag.evaluation`
- [ ] Verify full regression test suite (`uv run pytest`) passes with 100% green tests
