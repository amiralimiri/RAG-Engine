from pathlib import Path
import json
import sqlite3
from datetime import datetime, timezone
from app.core.config import get_settings

settings = get_settings()

def init_db() -> None:
    Path(settings.audit_db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.audit_db_path)
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS query_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            workflow TEXT NOT NULL DEFAULT 'unknown',
            question TEXT NOT NULL,
            source_used TEXT NOT NULL,
            trace_json TEXT NOT NULL,
            trace_id TEXT
        )"""
    )
    cur.execute("PRAGMA table_info(query_audit)")
    columns = [col[1] for col in cur.fetchall()]
    if "trace_id" not in columns:
        cur.execute("ALTER TABLE query_audit ADD COLUMN trace_id TEXT")
    con.commit()
    con.close()

def write_audit(
    workflow: str,
    question: str,
    source_used: str,
    trace: list[str],
    trace_id: str | None = None,
) -> None:
    Path(settings.audit_db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.audit_db_path)
    con.execute(
        "INSERT INTO query_audit(created_at, workflow, question, source_used, trace_json, trace_id) VALUES (?, ?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), workflow, question, source_used, json.dumps(trace), trace_id),
    )
    con.commit()
    con.close()
