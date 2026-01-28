import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

DEFAULT_DB_PATH = os.getenv(
    "CASE_REGISTRY_PATH",
    os.path.join(os.path.dirname(__file__), "cases", "case_registry.sqlite")
)


class CaseRegistry:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    case_name TEXT,
                    folder_path TEXT,
                    manual_docx_path TEXT,
                    generated_docx_path TEXT,
                    diff_path TEXT,
                    missing_fields TEXT,
                    filled_fields TEXT,
                    extracted_fields TEXT,
                    manual_fields TEXT,
                    summary TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS case_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    kind TEXT,
                    field_name TEXT,
                    expected TEXT,
                    actual TEXT,
                    note TEXT,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cases_name ON cases(case_name)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_case_obs_case_id ON case_observations(case_id)"
            )

    def add_case(
        self,
        case_name: str,
        folder_path: str,
        manual_docx_path: str,
        generated_docx_path: str,
        diff_path: str,
        missing_fields: Optional[Dict[str, Any]] = None,
        filled_fields: Optional[Dict[str, Any]] = None,
        extracted_fields: Optional[Dict[str, Any]] = None,
        manual_fields: Optional[Dict[str, Any]] = None,
        summary: Optional[str] = None,
    ) -> int:
        payload = {
            "missing_fields": missing_fields or {},
            "filled_fields": filled_fields or {},
            "extracted_fields": extracted_fields or {},
            "manual_fields": manual_fields or {},
        }
        created_at = datetime.utcnow().isoformat(timespec="seconds")
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO cases (
                    created_at,
                    case_name,
                    folder_path,
                    manual_docx_path,
                    generated_docx_path,
                    diff_path,
                    missing_fields,
                    filled_fields,
                    extracted_fields,
                    manual_fields,
                    summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    case_name,
                    folder_path,
                    manual_docx_path,
                    generated_docx_path,
                    diff_path,
                    json.dumps(payload["missing_fields"], ensure_ascii=False),
                    json.dumps(payload["filled_fields"], ensure_ascii=False),
                    json.dumps(payload["extracted_fields"], ensure_ascii=False),
                    json.dumps(payload["manual_fields"], ensure_ascii=False),
                    summary or "",
                ),
            )
        return int(cursor.lastrowid)

    def add_observation(
        self,
        case_id: int,
        kind: str,
        field_name: str = "",
        expected: str = "",
        actual: str = "",
        note: str = "",
    ) -> None:
        created_at = datetime.utcnow().isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO case_observations (
                    case_id,
                    created_at,
                    kind,
                    field_name,
                    expected,
                    actual,
                    note
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    created_at,
                    kind,
                    field_name,
                    expected,
                    actual,
                    note,
                ),
            )

    def close(self) -> None:
        self.conn.close()
