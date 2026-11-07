# 01: State & Multimodal Foundations

**What to build:**
The foundational domain value objects for multimodal inputs and blackboard multi-agent coordination in `app/rag/state.py`.
Defines an immutable `Attachment` domain model (`id`, `mime_type`, `url`, `content_bytes`, `metadata`), a boundary validator `coerce_attachments` (supporting strings, dictionaries, and model instances), and evolves `AgentState` into a message-centric blackboard state supporting `messages`, `attachments`, `context: dict[str, Any]`, `active_agent: str | None`, `citations: Sequence[Citation]`, and `trace: list[str]`, while maintaining 100% backward compatibility for all legacy keys (`kb_docs`, `web_results`, `retry_count`, `kb_grade`, `web_grade`, `answer`, `source_used`).

**Blocked by:**
None (can start immediately).

**Status:**
ready-for-agent

- [x] Define immutable `Attachment` domain model in `app/rag/state.py` with `id: str`, `mime_type: str`, `url: str | None`, `content_bytes: bytes | None`, and `metadata: dict[str, Any]`.
- [x] Implement `coerce_attachments` boundary validator coercing shorthand strings, raw dictionaries, and model instances into `list[Attachment]`.
- [x] Define `AttachmentList = Annotated[list[Attachment], BeforeValidator(coerce_attachments)]`.
- [x] Evolve `AgentState(TypedDict)` with blackboard fields (`messages`, `attachments`, `context`, `active_agent`, `citations`, `trace`) alongside legacy keys.
- [x] Add unit test suite in `tests/rag/test_state.py` testing `Attachment` validation, immutability, `coerce_attachments`, and `AgentState` key handling.
- [x] Pass `ruff check` and `pyright` on modified modules.
