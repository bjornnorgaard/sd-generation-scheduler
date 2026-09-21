"""SQLite-backed job store. Pure stdlib — no WebUI imports."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lib_generation_scheduler import constants

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    position    REAL NOT NULL,
    args        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    context     TEXT NOT NULL,
    user        TEXT,
    inputs_dir  TEXT,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    error       TEXT,
    result      TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_position ON jobs (status, position);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MOVES = ("top", "up", "down", "bottom")
CLEAR_SCOPES = ("pending", "finished", "all")


class JobNotFound(KeyError):
    pass


class InvalidJobState(ValueError):
    pass


@dataclass
class Job:
    id: int
    kind: str
    status: str
    position: float
    summary: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    user: str | None = None
    inputs_dir: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        """JSON-safe view for the API (never includes the raw argument payload)."""
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "position": self.position,
            "summary": self.summary,
            "context": self.context,
            "user": self.user,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": self.result,
        }


_COLUMNS = (
    "id, kind, status, position, summary, context, user, inputs_dir, "
    "created_at, started_at, finished_at, error, result"
)


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _row_to_job(row: sqlite3.Row | tuple) -> Job:
    return Job(
        id=row[0],
        kind=row[1],
        status=row[2],
        position=row[3],
        summary=_loads(row[4]) or {},
        context=_loads(row[5]) or {},
        user=row[6],
        inputs_dir=row[7],
        created_at=row[8],
        started_at=row[9],
        finished_at=row[10],
        error=row[11],
        result=_loads(row[12]),
    )


class JobStore:
    """Thread-safe queue persistence. One connection, guarded by a re-entrant lock."""

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → autocommit; compound operations use BEGIN IMMEDIATE.
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        if str(path) != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- helpers ---------------------------------------------------------

    def _transaction(self):
        return _Transaction(self._db, self._lock)

    def _select(self, where: str = "", params: Iterable[Any] = (), tail: str = "") -> list[Job]:
        sql = f"SELECT {_COLUMNS} FROM jobs {where} {tail}"
        with self._lock:
            return [_row_to_job(r) for r in self._db.execute(sql, tuple(params)).fetchall()]

    # -- create / read ---------------------------------------------------

    def add_job(
        self,
        kind: str,
        args_json: str,
        *,
        summary: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        user: str | None = None,
        inputs_dir: str | Path | None = None,
    ) -> Job:
        if kind not in constants.KINDS:
            raise ValueError(f"Unknown job kind: {kind!r}")
        with self._transaction() as db:
            (top,) = db.execute("SELECT COALESCE(MAX(position), 0) FROM jobs").fetchone()
            cursor = db.execute(
                "INSERT INTO jobs (kind, status, position, args, summary, context, user, "
                "inputs_dir, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    constants.PENDING,
                    top + 1,
                    args_json,
                    json.dumps(summary or {}),
                    json.dumps(context or {}),
                    user,
                    str(inputs_dir) if inputs_dir else None,
                    self._clock(),
                ),
            )
            job_id = cursor.lastrowid
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: int) -> Job | None:
        jobs = self._select("WHERE id = ?", (job_id,))
        return jobs[0] if jobs else None

    def get_args(self, job_id: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT args FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return row[0]

    def pending(self) -> list[Job]:
        return self._select("WHERE status = ?", (constants.PENDING,), "ORDER BY position, id")

    def running(self) -> Job | None:
        jobs = self._select("WHERE status = ?", (constants.RUNNING,), "ORDER BY id LIMIT 1")
        return jobs[0] if jobs else None

    def finished(self, limit: int | None = None) -> list[Job]:
        marks = ",".join("?" for _ in constants.FINISHED_STATUSES)
        tail = "ORDER BY finished_at DESC, id DESC"
        if limit is not None:
            tail += f" LIMIT {int(limit)}"
        return self._select(f"WHERE status IN ({marks})", constants.FINISHED_STATUSES, tail)

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(constants.ALL_STATUSES, 0)
        with self._lock:
            for status, n in self._db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status"):
                counts[status] = n
        return counts

    def has_pending(self) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM jobs WHERE status = ? LIMIT 1", (constants.PENDING,)
            ).fetchone()
        return row is not None

    def pending_ahead(self, job_id: int) -> int:
        """Number of jobs that will run before ``job_id`` (running job included)."""
        job = self.get(job_id)
        if job is None or job.status != constants.PENDING:
            return 0
        with self._lock:
            (ahead,) = self._db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = ? "
                "OR (status = ? AND (position < ? OR (position = ? AND id < ?)))",
                (constants.RUNNING, constants.PENDING, job.position, job.position, job.id),
            ).fetchone()
        return ahead

    # -- state transitions -----------------------------------------------

    def claim_next(self) -> Job | None:
        """Atomically move the first pending job to ``running`` and return it."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY position, id LIMIT 1",
                (constants.PENDING,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status = ?, started_at = ?, error = NULL, result = NULL "
                "WHERE id = ?",
                (constants.RUNNING, self._clock(), row[0]),
            )
        return self.get(row[0])

    def finish(
        self,
        job_id: int,
        status: str,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> Job:
        if status not in constants.FINISHED_STATUSES:
            raise ValueError(f"Not a finished status: {status!r}")
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ?, result = ? "
                "WHERE id = ? AND status = ?",
                (
                    status,
                    self._clock(),
                    error,
                    json.dumps(result) if result is not None else None,
                    job_id,
                    constants.RUNNING,
                ),
            )
            if cursor.rowcount == 0:
                raise InvalidJobState(f"Job {job_id} is not running")
        job = self.get(job_id)
        assert job is not None
        return job

    def recover_interrupted(self) -> int:
        """Jobs left ``running`` by a crash / restart become ``interrupted``."""
        with self._transaction() as db:
            cursor = db.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE status = ?",
                (
                    constants.INTERRUPTED,
                    self._clock(),
                    "WebUI stopped while this job was running.",
                    constants.RUNNING,
                ),
            )
            return cursor.rowcount

    # -- editing ---------------------------------------------------------

    def remove(self, job_id: int) -> Job:
        """Delete a job that is not running. Returns it so callers can clean up files."""
        with self._transaction() as db:
            job = self.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job.status == constants.RUNNING:
                raise InvalidJobState("Cannot remove the running job — interrupt it first.")
            db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return job

    def clear(self, scope: str) -> list[Job]:
        if scope not in CLEAR_SCOPES:
            raise ValueError(f"Unknown clear scope: {scope!r}")
        statuses: list[str] = []
        if scope in ("pending", "all"):
            statuses.append(constants.PENDING)
        if scope in ("finished", "all"):
            statuses.extend(constants.FINISHED_STATUSES)
        marks = ",".join("?" for _ in statuses)
        with self._transaction() as db:
            removed = self._select(f"WHERE status IN ({marks})", statuses)
            db.execute(f"DELETE FROM jobs WHERE status IN ({marks})", statuses)
        return removed

    def move(self, job_id: int, where: str) -> bool:
        """Reorder a pending job. Returns False when it is already at the boundary."""
        if where not in MOVES:
            raise ValueError(f"Unknown move: {where!r}")
        with self._transaction() as db:
            job = self.get(job_id)
            if job is None:
                raise JobNotFound(job_id)
            if job.status != constants.PENDING:
                raise InvalidJobState("Only pending jobs can be reordered.")
            queue = self.pending()
            index = next(i for i, j in enumerate(queue) if j.id == job_id)

            if where == "top":
                if index == 0:
                    return False
                new_position = queue[0].position - 1
            elif where == "bottom":
                if index == len(queue) - 1:
                    return False
                new_position = queue[-1].position + 1
            else:
                neighbour_index = index - 1 if where == "up" else index + 1
                if not 0 <= neighbour_index < len(queue):
                    return False
                neighbour = queue[neighbour_index]
                db.execute(
                    "UPDATE jobs SET position = ? WHERE id = ?", (job.position, neighbour.id)
                )
                new_position = neighbour.position
            db.execute("UPDATE jobs SET position = ? WHERE id = ?", (new_position, job_id))
        return True

    def duplicate(self, job_id: int, *, inputs_dir: str | Path | None = None) -> Job:
        """Append a fresh pending copy of ``job_id`` (any status but not its results)."""
        source = self.get(job_id)
        if source is None:
            raise JobNotFound(job_id)
        return self.add_job(
            source.kind,
            self.get_args(job_id),
            summary=source.summary,
            context=source.context,
            user=source.user,
            inputs_dir=inputs_dir,
        )

    def prune_finished(self, keep: int) -> list[Job]:
        """Drop the oldest finished jobs beyond ``keep``; returns what was removed."""
        keep = max(int(keep), 0)
        with self._transaction() as db:
            stale = self.finished()[keep:]
            for job in stale:
                db.execute("DELETE FROM jobs WHERE id = ?", (job.id,))
        return stale

    # -- meta ------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


class _Transaction:
    """``BEGIN IMMEDIATE`` … ``COMMIT`` while holding the store lock."""

    def __init__(self, db: sqlite3.Connection, lock: threading.RLock) -> None:
        self._db = db
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        try:
            self._db.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        return self._db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._lock.release()
