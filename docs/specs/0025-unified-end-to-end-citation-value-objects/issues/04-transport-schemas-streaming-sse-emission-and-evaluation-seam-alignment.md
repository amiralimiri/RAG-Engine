# 04: Transport Schemas, Streaming SSE Emission & Evaluation Seam Alignment

**What to build:** Every wire-facing response — synchronous chat, streaming terminal metadata, and session history — declares typed citations and serializes them through standard model dumping, so the defensive citation-serialization helper disappears. Meanwhile the online evaluation listener grades answers using retrieved context text alone, no longer scraping citation payloads for document content.

**Blocked by:** 02 — End-to-End Workflow Engine & Agent Graph State Typing; 03 — Strongly-Typed Conversation Memory Storage & SQLite Deserialization

**Status:** completed

- [x] The synchronous chat response declares citations as a typed list of `Citation`.
- [x] The streaming terminal metadata event declares citations as a typed list of `Citation`.
- [x] The session history turn response declares citations as a typed list of `Citation`.
- [x] The defensive citation-serialization helper is deleted; SSE terminal metadata frames are produced by standard model dumping.
- [x] A streaming run emits terminal metadata whose citations serialize to the same JSON shape clients already consume.
- [x] A synchronous chat response serializes citations to the same JSON shape clients already consume.
- [x] The online evaluation listener derives contexts exclusively from the output's retrieved contexts (or an injected context extractor), with the citation-inspection fallback removed.
- [x] Online evaluation with an output that has citations but no retrieved contexts does not treat citation metadata as document evidence.
- [x] Test assertions across the API, streaming, and evaluation suites use attribute access on citations rather than dictionary indexing.
- [x] The full regression suite passes end to end.
