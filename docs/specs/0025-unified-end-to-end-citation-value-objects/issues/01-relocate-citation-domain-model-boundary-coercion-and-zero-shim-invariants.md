# 01: Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants

**What to build:** The canonical `Citation` value object becomes a foundational domain model that every subsystem can depend on without importing the tool execution package. Memory, workflow, and transport modules can all reference the same immutable `Citation` type, and any legacy dictionary or shorthand-string citation payload is normalized into a typed `Citation` at the model boundary — so no downstream consumer ever has to guess whether it is holding a dict or a model.

**Blocked by:** None (can start immediately)

**Status:** completed

- [x] `Citation` is defined in the shared state domain module alongside `RouteDecision` and `EvidenceGrade`, and remains an immutable value object with `title`, `url`, and `type` fields.
- [x] `Citation` is re-exported from the tool execution package so existing tool callers and imports continue to work unchanged.
- [x] `Citation` exposes no dict-like subscripting (`__getitem__`) or `.get()` accessors; all consumers use attribute access.
- [x] A reusable before-validator coerces legacy dictionaries and shorthand strings into validated `Citation` instances on `WorkflowOutput`, `ConversationTurn`, and `ChatResponse` at initialization time.
- [x] Coercion accepts a mixed list containing `Citation` instances, dictionaries, and strings, and returns a uniform typed list preserving input order.
- [x] Coercion of empty, `None`, or whitespace-only input yields an empty typed list rather than raising.
- [x] Unit tests cover immutability, re-export availability, and every coercion branch (model, dict, string, mixed, empty).
- [x] The full existing test suite passes with no new failures introduced by the relocation.
