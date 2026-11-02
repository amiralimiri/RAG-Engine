# Work Packages / Implementation Tickets: Unified Prompt Store Protocol Seam and Zero-Shim Migration

- **Specification Reference:** [`docs/specs/0018-unified-prompt-store-protocol-seam-spec.md`](./0018-unified-prompt-store-protocol-seam-spec.md)
- **ADR Reference:** [`docs/adr/0018-unified-prompt-store-protocol-seam.md`](../adr/0018-unified-prompt-store-protocol-seam.md)
- **Domain Guidelines:** [`GLOSSARY.md`](../../GLOSSARY.md) | [`docs/agents/codebase-unification.md`](../agents/codebase-unification.md)
- **Status:** Ready for Implementation

---

## Dependency Graph

```
┌────────────────────────────────────────────────────────┐
│ Ticket 01: Core PromptStore Seam, RenderedPrompt Model │
│ & Local Adapter                                        │
│ (.scratch/0018-.../issues/01-core-prompt-store-seam...)│
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 02: Langfuse CMS Production Adapter, Factory    │
│ & Test Fixtures                                        │
│ (.scratch/0018-.../issues/02-langfuse-cms-production...)
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 03: Dependency Injection in Workflow Engine,    │
│ Classifiers & Main Startup                             │
│ (.scratch/0018-.../issues/03-dependency-injection...)  │
└───────────┬────────────────────────────────────────────┘
            │
            ▼
┌────────────────────────────────────────────────────────┐
│ Ticket 04: Zero-Shim Pruning of app/services/prompts.py│
│ & Test Suite Modernization                             │
│ (.scratch/0018-.../issues/04-zero-shim-pruning...)     │
└────────────────────────────────────────────────────────┘
```

---

## Implementation Summary Matrix

| Ticket | Title | Target Files / Scope | Blockers | Status |
|---|---|---|---|---|
| **Ticket 01** | Core PromptStore Seam, RenderedPrompt Model & Local Adapter | `app/rag/prompts/base.py`, `app/rag/prompts/local.py`, `tests/rag/test_prompt_store.py` | None | `ready-for-agent` |
| **Ticket 02** | Langfuse CMS Production Adapter, Factory & Test Fixtures | `app/rag/prompts/langfuse.py`, `app/rag/prompts/__init__.py`, `tests/conftest.py`, `tests/rag/test_langfuse_prompt_store.py` | Ticket 01 | `ready-for-agent` |
| **Ticket 03** | Dependency Injection in Workflow Engine, Classifiers & Main Startup | `app/rag/engine.py`, `app/rag/classifiers.py`, `app/main.py`, `tests/test_workflow_engine.py`, `tests/test_workflow_classifiers.py` | Ticket 02 | `ready-for-agent` |
| **Ticket 04** | Zero-Shim Pruning of `app/services/prompts.py` & Modernization | `tests/test_prompts_cms.py`, delete `app/services/prompts.py`, full test regression | Ticket 03 | `ready-for-agent` |

---

## Ticket Locations

1. [Ticket 01: Core PromptStore Seam, RenderedPrompt Model & Local Adapter](../../.scratch/0018-unified-prompt-store-protocol-seam/issues/01-core-prompt-store-seam-and-local-adapter.md)
2. [Ticket 02: Langfuse CMS Production Adapter, Factory & Test Fixtures](../../.scratch/0018-unified-prompt-store-protocol-seam/issues/02-langfuse-cms-production-adapter-and-fixtures.md)
3. [Ticket 03: Dependency Injection in Workflow Engine, Classifiers & Main Startup](../../.scratch/0018-unified-prompt-store-protocol-seam/issues/03-dependency-injection-in-engine-classifiers-and-main.md)
4. [Ticket 04: Zero-Shim Pruning of `app/services/prompts.py` & Test Suite Modernization](../../.scratch/0018-unified-prompt-store-protocol-seam/issues/04-zero-shim-pruning-and-test-modernization.md)
