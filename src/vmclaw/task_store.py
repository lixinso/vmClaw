"""Persistent task history — lightweight SQLite store for mobile /api/mobile/tasks."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class TaskRecord:
    """A persisted task execution record."""

    task_id: str
    node_name: str
    vm_title: str
    task_text: str
    status: str  # running, done, error, stopped, interrupted, max_actions
    outcome: Optional[str] = None
    actions_taken: int = 0
    actions_json: str = "[]"
    created_at: str = ""
    ended_at: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {
            "task_id": self.task_id,
            "node_name": self.node_name,
            "vm_title": self.vm_title,
            "task_text": self.task_text,
            "status": self.status,
            "actions_taken": self.actions_taken,
            "created_at": self.created_at,
        }
        if self.outcome:
            d["outcome"] = self.outcome
        if self.ended_at:
            d["ended_at"] = self.ended_at
        return d


class TaskStore:
    """SQLite-backed store for task execution history.

    Database is stored at ``~/.vmclaw/tasks.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".vmclaw" / "tasks.db"
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_history (
                task_id     TEXT PRIMARY KEY,
                node_name   TEXT NOT NULL,
                vm_title    TEXT NOT NULL,
                task_text   TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                outcome     TEXT,
                actions_taken INTEGER NOT NULL DEFAULT 0,
                actions_json TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                ended_at    TEXT
            )
        """)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def create_task(
        self,
        task_id: str,
        node_name: str,
        vm_title: str,
        task_text: str,
    ) -> TaskRecord:
        now = datetime.now(timezone.utc).isoformat()
        rec = TaskRecord(
            task_id=task_id,
            node_name=node_name,
            vm_title=vm_title,
            task_text=task_text,
            status="running",
            created_at=now,
        )
        self._db().execute(
            """INSERT INTO task_history
               (task_id, node_name, vm_title, task_text, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rec.task_id, rec.node_name, rec.vm_title, rec.task_text,
             rec.status, rec.created_at),
        )
        self._db().commit()
        return rec

    def update_status(
        self,
        task_id: str,
        status: str,
        outcome: str | None = None,
        actions_taken: int = 0,
        actions_json: str = "[]",
    ) -> None:
        ended = datetime.now(timezone.utc).isoformat() if status != "running" else None
        self._db().execute(
            """UPDATE task_history
               SET status = ?, outcome = ?, actions_taken = ?,
                   actions_json = ?, ended_at = ?
               WHERE task_id = ?""",
            (status, outcome, actions_taken, actions_json, ended, task_id),
        )
        self._db().commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self._db().execute(
            "SELECT * FROM task_history WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        if status:
            rows = self._db().execute(
                "SELECT * FROM task_history WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self._db().execute(
                "SELECT * FROM task_history ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: tuple) -> TaskRecord:
        return TaskRecord(
            task_id=row[0],
            node_name=row[1],
            vm_title=row[2],
            task_text=row[3],
            status=row[4],
            outcome=row[5],
            actions_taken=row[6],
            actions_json=row[7],
            created_at=row[8],
            ended_at=row[9],
        )
