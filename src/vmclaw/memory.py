"""AI memory — persist and recall past task executions using SQLite + sqlite-vec."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from .models import Action, Config

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

DEFAULT_DB_DIR = Path.home() / ".vmclaw"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "memory.db"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS task_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_text    TEXT    NOT NULL,
    vm_title     TEXT    NOT NULL,
    outcome      TEXT    NOT NULL,
    action_count INTEGER NOT NULL,
    actions_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS task_embeddings USING vec0(
    task_id   INTEGER PRIMARY KEY,
    embedding float[1536]
);
"""


def _serialize_f32(vec: list[float]) -> bytes:
    """Pack a list of floats into raw bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


@dataclass
class TaskRecord:
    """A past task execution retrieved from memory."""

    id: int
    task_text: str
    vm_title: str
    outcome: str
    action_count: int
    actions: list[Action]
    created_at: str
    similarity: float = 0.0


class MemoryStore:
    """SQLite-backed memory for past task executions with vector search."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._embed_client: OpenAI | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self, config: Config) -> None:
        """Open the database, load sqlite-vec, and initialise the schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        # Load sqlite-vec extension
        conn.enable_load_extension(True)
        import sqlite_vec  # noqa: E402

        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_task(
        self,
        task: str,
        vm_title: str,
        outcome: str,
        actions: list[Action],
        config: Config,
    ) -> int | None:
        """Save a completed task run.  Returns the row id, or None on failure."""
        if self._conn is None:
            return None

        actions_json = json.dumps([a.to_dict() for a in actions])

        cur = self._conn.execute(
            "INSERT INTO task_runs (task_text, vm_title, outcome, action_count, actions_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (task, vm_title, outcome, len(actions), actions_json),
        )
        task_id = cur.lastrowid

        # Best-effort embedding — if this fails the task data is still saved.
        embedding = self._get_embedding(task, config)
        if embedding is not None:
            try:
                self._conn.execute(
                    "INSERT INTO task_embeddings (task_id, embedding) VALUES (?, ?)",
                    (task_id, _serialize_f32(embedding)),
                )
            except Exception as exc:
                log.warning("Failed to save embedding: %s", exc)

        self._conn.commit()
        return task_id

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_similar(
        self,
        task: str,
        config: Config,
        limit: int = 3,
        min_similarity: float = 0.3,
    ) -> list[TaskRecord]:
        """Find past *successful* tasks similar to *task*.

        Returns up to *limit* records ordered by descending similarity.
        """
        if self._conn is None:
            return []

        embedding = self._get_embedding(task, config)
        if embedding is None:
            return []

        # sqlite-vec KNN query (returns L2 distance in ascending order).
        rows = self._conn.execute(
            """
            SELECT t.id, t.task_text, t.vm_title, t.outcome, t.action_count,
                   t.actions_json, t.created_at, e.distance
            FROM task_embeddings e
            JOIN task_runs t ON t.id = e.task_id
            WHERE e.embedding MATCH ?
              AND k = ?
            ORDER BY e.distance
            """,
            (_serialize_f32(embedding), limit * 3),
        ).fetchall()

        results: list[TaskRecord] = []
        for row in rows:
            # Convert L2 distance to approximate cosine similarity.
            # For normalised vectors: cos_sim ≈ 1 − distance² / 2
            distance = row[7]
            similarity = max(0.0, 1.0 - (distance * distance / 2.0))

            if similarity < min_similarity:
                continue
            # Only include successful completions as examples.
            if row[3] != "done":
                continue

            actions = [Action.from_dict(d) for d in json.loads(row[5])]
            results.append(
                TaskRecord(
                    id=row[0],
                    task_text=row[1],
                    vm_title=row[2],
                    outcome=row[3],
                    action_count=row[4],
                    actions=actions,
                    created_at=row[6],
                    similarity=similarity,
                )
            )
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_memory_context(records: list[TaskRecord]) -> str:
        """Format retrieved task records as few-shot prompt context."""
        if not records:
            return ""

        lines = ["\n--- Past successful tasks (for reference) ---"]
        for i, rec in enumerate(records, 1):
            lines.append(
                f"\nExample {i}: \"{rec.task_text}\" ({rec.action_count} actions)"
            )
            for j, action in enumerate(rec.actions, 1):
                desc = f"  Step {j}: {action.action.value}"
                if action.reason:
                    desc += f" - {action.reason}"
                lines.append(desc)
                if j >= 10:
                    remaining = len(rec.actions) - 10
                    if remaining > 0:
                        lines.append(f"  ... ({remaining} more steps)")
                    break
        lines.append("--- End past examples ---\n")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Embedding helper
    # ------------------------------------------------------------------

    def _get_embedding(self, text: str, config: Config) -> list[float] | None:
        """Generate an embedding vector for *text*.  Returns None on failure."""
        try:
            if self._embed_client is None:
                self._embed_client = self._create_embed_client(config)
            resp = self._embed_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=text,
            )
            return resp.data[0].embedding
        except Exception as exc:
            log.warning("Embedding request failed: %s", exc)
            return None

    @staticmethod
    def _create_embed_client(config: Config) -> OpenAI:
        """Build an OpenAI client for the embeddings endpoint."""
        if config.provider == "github":
            return OpenAI(
                api_key=config.github_token,
                base_url=config.api_base_url or "https://models.github.ai/inference",
                timeout=30.0,
            )
        kwargs: dict = {"api_key": config.openai_api_key, "timeout": 30.0}
        if config.api_base_url:
            kwargs["base_url"] = config.api_base_url
        return OpenAI(**kwargs)
