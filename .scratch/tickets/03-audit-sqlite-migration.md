# Ticket 03: SQLite Audit Migration & Trace ID Persistence

## Status
Pending

## Dependencies
Ticket 01 (`01-dependencies-and-config.md`)

## Scope
Extend the SQLite query audit schema in `app/services/audit.py` to store an optional `trace_id TEXT` column. Implement safe, idempotent startup migration to ensure existing databases are not corrupted or dropped.

## Deliverables
1. `app/services/audit.py`:
   - Update `init_db()` to inspect existing table columns via `PRAGMA table_info(query_audit)`.
   - Run `ALTER TABLE query_audit ADD COLUMN trace_id TEXT` if `trace_id` is missing.
   - Update `write_audit(workflow: str, question: str, source_used: str, trace: list[str], trace_id: str | None = None)` to insert `trace_id`.
2. `tests/test_audit_migration.py`: Unit tests verifying table creation, migration idempotency, and data insertion.

## Acceptance Criteria
- [ ] Fresh database: calling `init_db()` creates `query_audit` with `(id, created_at, workflow, question, source_used, trace_json, trace_id)`.
- [ ] Existing database without `trace_id`: calling `init_db()` alters the table and preserves all pre-existing rows.
- [ ] Already migrated database: calling `init_db()` runs cleanly without throwing `duplicate column name` error.
- [ ] `write_audit()` accepts `trace_id` parameter (default `None`).
- [ ] Records inserted with or without `trace_id` persist correctly and can be retrieved via SQLite query.

## TDD Test Requirements
Create `tests/test_audit_migration.py`:
- `test_init_db_creates_table_with_trace_id(tmp_path, monkeypatch)`: Set `audit_db_path` to temp file, run `init_db()`, check `PRAGMA table_info` contains `trace_id`.
- `test_init_db_migrates_legacy_schema(tmp_path, monkeypatch)`: Create table manually with old schema (without `trace_id`), insert a legacy row, run `init_db()`, assert legacy row remains intact and `trace_id` column is added.
- `test_init_db_is_idempotent(tmp_path, monkeypatch)`: Run `init_db()` multiple times sequentially; ensure no exceptions.
- `test_write_audit_stores_trace_id(tmp_path, monkeypatch)`: Call `write_audit("llm", "q", "kb", ["t1"], trace_id="trace-xyz")` and verify column value in SQLite.
