# Coding Standards & Architecture Guidelines

This document serves as the high-level standard and review index for the RAG-Engine repository. Mechanical syntax, import sorting, and language upgrades are enforced automatically by `ruff` and `pyright` (see `pyproject.toml`). Architectural decisions and judgement calls are documented across focused guidelines in `docs/agents/`.

---

## 1. Automated Guardrails

- **Linter & Formatter**: Run `ruff check .` and `ruff check --fix .` (rules: `F401` unused imports, `I` isort, `UP` pyupgrade for Python 3.14).
- **Static Type Analysis**: Run `pyright` using configuration in `pyproject.toml`.
- **Test Suite**: Run `pytest` with test isolation fixtures. Do not mock pass-through re-exports; patch authoritative origin seams.

---

## 2. Domain & Execution Guidelines

Detailed architectural guidelines and patterns are modularized in `docs/agents/`:

- **[Codebase Unification & Zero-Shim Architecture](docs/agents/codebase-unification.md)**:
  Zero-shim dependency injection standards, obsolete symbol pruning, test isolation fixture architecture, and patching authoritative origins rather than pass-through transport imports.
- **[Workflow Execution & Pipeline Seam Patterns](docs/agents/workflow-execution.md)**:
  `WorkflowRunner` execution pipeline, constructor auto-wiring, unidirectional dependency flow (`app/rag/**` never imports `app/api/**`), PEP 484 explicit union typing (`Any | None = None`), and internal state privacy (`_execute_with_state`).
- **[Tool Abstractions](docs/agents/tool-abstractions.md)**:
  Dual-output `BaseTool` seams, tool-level decoupling from transport, and offline test doubles.
- **[Knowledge Store & Indexing](docs/agents/knowledge-store.md)**:
  `VectorStoreAdapter` protocol seam, deterministic chunk hashing, and offline test doubles.
- **[Domain Docs & ADRs](docs/agents/domain.md)**:
  Single-context domain modeling (`GLOSSARY.md` + `docs/adr/` at the repo root).
- **[Issue Tracker](docs/agents/issue-tracker.md)** & **[Triage Labels](docs/agents/triage-labels.md)**:
  Canonical issue conventions, roles, and milestone tracking.
