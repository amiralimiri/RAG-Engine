import json
import sqlite3
import pytest
from app.services import audit


def test_init_db_creates_table_with_trace_id(tmp_path, monkeypatch):
    db_file = tmp_path / "test_audit.db"
    monkeypatch.setattr(audit.settings, "audit_db_path", str(db_file))

    audit.init_db()

    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(query_audit)")
    columns = [row[1] for row in cur.fetchall()]
    con.close()

    expected_columns = [
        "id",
        "created_at",
        "workflow",
        "question",
        "source_used",
        "trace_json",
        "trace_id",
    ]
    for col in expected_columns:
        assert col in columns


def test_init_db_migrates_legacy_schema(tmp_path, monkeypatch):
    db_file = tmp_path / "legacy_audit.db"
    monkeypatch.setattr(audit.settings, "audit_db_path", str(db_file))

    # Manually create table with legacy schema (no trace_id)
    con = sqlite3.connect(str(db_file))
    con.execute(
        """CREATE TABLE query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            workflow TEXT NOT NULL DEFAULT 'unknown',
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL
        )"""
    )
    con.execute(
        "INSERT INTO query_audit(created_at, workflow, question, source_used, trace_json) "
        "VALUES (?, ?, ?, ?, ?)",
        ("2026-01-01T00:00:00Z", "rag", "legacy question", "kb", json.dumps(["step1"])),
    )
    con.commit()
    con.close()

    # Run migration
    audit.init_db()

    # Verify schema updated and legacy row preserved
    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(query_audit)")
    columns = [row[1] for row in cur.fetchall()]
    assert "trace_id" in columns

    cur.execute("SELECT id, created_at, workflow, question, source_used, trace_json, trace_id FROM query_audit")
    rows = cur.fetchall()
    con.close()

    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] == "2026-01-01T00:00:00Z"
    assert rows[0][2] == "rag"
    assert rows[0][3] == "legacy question"
    assert rows[0][4] == "kb"
    assert json.loads(rows[0][5]) == ["step1"]
    assert rows[0][6] is None


def test_init_db_is_idempotent(tmp_path, monkeypatch):
    db_file = tmp_path / "idempotent_audit.db"
    monkeypatch.setattr(audit.settings, "audit_db_path", str(db_file))

    # Calling init_db multiple times sequentially must not throw any error
    audit.init_db()
    audit.init_db()
    audit.init_db()

    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("PRAGMA table_info(query_audit)")
    columns = [row[1] for row in cur.fetchall()]
    con.close()

    assert "trace_id" in columns
    assert columns.count("trace_id") == 1


def test_write_audit_stores_trace_id(tmp_path, monkeypatch):
    db_file = tmp_path / "write_audit.db"
    monkeypatch.setattr(audit.settings, "audit_db_path", str(db_file))

    audit.init_db()
    audit.write_audit("llm", "q", "kb", ["t1"], trace_id="trace-xyz")

    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("SELECT workflow, question, source_used, trace_json, trace_id FROM query_audit")
    rows = cur.fetchall()
    con.close()

    assert len(rows) == 1
    workflow, question, source_used, trace_json, trace_id = rows[0]
    assert workflow == "llm"
    assert question == "q"
    assert source_used == "kb"
    assert json.loads(trace_json) == ["t1"]
    assert trace_id == "trace-xyz"


def test_write_audit_without_trace_id(tmp_path, monkeypatch):
    db_file = tmp_path / "write_audit_default.db"
    monkeypatch.setattr(audit.settings, "audit_db_path", str(db_file))

    audit.init_db()
    audit.write_audit("llm", "q2", "web", ["t2"])

    con = sqlite3.connect(str(db_file))
    cur = con.cursor()
    cur.execute("SELECT workflow, question, source_used, trace_json, trace_id FROM query_audit")
    rows = cur.fetchall()
    con.close()

    assert len(rows) == 1
    workflow, question, source_used, trace_json, trace_id = rows[0]
    assert workflow == "llm"
    assert question == "q2"
    assert source_used == "web"
    assert json.loads(trace_json) == ["t2"]
    assert trace_id is None
