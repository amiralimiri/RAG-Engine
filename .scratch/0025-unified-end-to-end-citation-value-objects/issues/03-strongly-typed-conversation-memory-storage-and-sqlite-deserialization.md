# 03: Strongly-Typed Conversation Memory Storage & SQLite Deserialization

**What to build:** A completed conversational turn stores its citations as typed `Citation` objects in every memory adapter. When a session is read back from durable SQLite storage, its turns are rehydrated into validated `Citation` instances, so conversation history is indistinguishable in shape from a freshly executed workflow output. Appending, rewinding, and forking all preserve citation typing.

**Blocked by:** 01 — Relocate Citation Domain Model, Boundary Coercion & Zero-Shim Invariants

**Status:** ready-for-agent

- [ ] The conversation turn model declares citations as a typed list of `Citation`.
- [ ] The memory adapter contract's atomic turn append accepts a typed citation list.
- [ ] The SQLite adapter serializes typed citations to JSON on append and rehydrates them into validated `Citation` instances when reading turns back.
- [ ] Reading a turn whose stored citations are legacy dictionaries or bare strings still yields typed `Citation` instances.
- [ ] The in-memory adapter stores and returns typed `Citation` instances.
- [ ] The no-op adapter satisfies the updated contract without raising.
- [ ] Appending a turn with citations, then reading it back, returns citations equal by value to those supplied.
- [ ] Rewinding a session preserves typed citations on the surviving turns.
- [ ] Forking a session clones typed citations into the child session.
- [ ] Memory test suites for SQLite, in-memory, and contract compliance pass end to end.
