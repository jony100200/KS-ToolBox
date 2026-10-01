"""SQLite persistence adapter for :mod:`toolbox.batch_core`.

SQLite is part of Python, updates one item at a time, and provides transactional
crash recovery without adding a runtime dependency or rewriting large JSON
checkpoint files after every item.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


_SQLITE_DLL_DIRECTORY = None


def _without_conflicting_sqlite_dll_dirs(path_value: str, expected_dll: Path) -> str:
    """Remove PATH directories that contain a different ``sqlite3.dll``."""
    expected = expected_dll.resolve(strict=False)
    kept: list[str] = []
    for raw_dir in path_value.split(os.pathsep):
        if not raw_dir:
            continue
        candidate = Path(raw_dir) / "sqlite3.dll"
        try:
            conflicts = candidate.is_file() and candidate.resolve(strict=False) != expected
        except OSError:
            conflicts = False
        if not conflicts:
            kept.append(raw_dir)
    return os.pathsep.join(kept)


def _prepare_stdlib_sqlite_dll() -> None:
    """Keep a foreign Windows ``sqlite3.dll`` from breaking Python's extension.

    Shell extensions can put an older SQLite DLL on the process search path.
    The queue requires the SQLite version bundled with the active interpreter,
    so add that DLL directory to Python's extension-module search path and
    remove PATH directories containing a different SQLite DLL before importing
    the standard-library wrapper.  This affects only the Toolbox process and
    its children; it never changes the user's system PATH or SageThumbs files.
    If Windows still rejects the extension, ``job_queue`` uses its explicit
    session-only fallback and shows a visible degraded-mode warning.
    """
    if os.name != "nt":
        return
    sqlite_dll = Path(sys.base_prefix) / "DLLs" / "sqlite3.dll"
    if not sqlite_dll.is_file():
        return
    global _SQLITE_DLL_DIRECTORY
    _SQLITE_DLL_DIRECTORY = os.add_dll_directory(str(sqlite_dll.parent))
    safe_path = _without_conflicting_sqlite_dll_dirs(
        os.environ.get("PATH", ""), sqlite_dll
    )
    os.environ["PATH"] = str(sqlite_dll.parent) + os.pathsep + safe_path


_prepare_stdlib_sqlite_dll()

import sqlite3

from toolbox.batch_core import (
    BatchReport,
    ItemOutcome,
    ItemRecord,
    ItemState,
    JobDefinition,
    JobState,
    PreparedJob,
    json_value,
)


def default_state_dir() -> Path:
    override = os.environ.get("KS_TOOLBOX_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base) / "KS Toolbox" if base else Path.home() / ".ks-toolbox"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "KS Toolbox"
    base = os.environ.get("XDG_STATE_HOME")
    return Path(base).expanduser() / "ks-toolbox" if base else Path.home() / ".local" / "state" / "ks-toolbox"


def default_job_store_path() -> Path:
    return default_state_dir() / "jobs.sqlite3"


def default_report_dir() -> Path:
    return default_state_dir() / "reports"


class SQLiteJobStore:
    """Transactional job/item store. Create it lazily when a batch is submitted."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_job_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    tool_id TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    settings_json TEXT NOT NULL,
                    max_retries INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_items (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    input_path TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    details TEXT NOT NULL DEFAULT '',
                    result_json TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (job_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_job_items_state
                    ON job_items(job_id, state, position);
                """
            )

    def prepare(self, definition: JobDefinition) -> PreparedJob:
        now = time.time()
        settings_json = json.dumps(json_value(definition.settings), sort_keys=True, separators=(",", ":"))
        with self._db:
            row = self._db.execute("SELECT * FROM jobs WHERE job_id = ?", (definition.job_id,)).fetchone()
            if row is None:
                self._db.execute(
                    """INSERT INTO jobs
                       (job_id, tool_id, tool_version, workflow_version, settings_json,
                        max_retries, state, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        definition.job_id,
                        definition.tool_id,
                        definition.tool_version,
                        definition.workflow_version,
                        settings_json,
                        definition.max_retries,
                        JobState.QUEUED.value,
                        now,
                        now,
                    ),
                )
                self._db.executemany(
                    """INSERT INTO job_items
                       (job_id, position, input_path, state, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    [
                        (definition.job_id, position, input_path, ItemState.PENDING.value, now)
                        for position, input_path in enumerate(definition.inputs)
                    ],
                )
                return PreparedJob()

            expected = (
                definition.tool_id,
                definition.tool_version,
                definition.workflow_version,
                settings_json,
                definition.max_retries,
            )
            actual = (
                row["tool_id"],
                row["tool_version"],
                row["workflow_version"],
                row["settings_json"],
                row["max_retries"],
            )
            stored_inputs = tuple(
                item["input_path"]
                for item in self._db.execute(
                    "SELECT input_path FROM job_items WHERE job_id = ? ORDER BY position",
                    (definition.job_id,),
                )
            )
            if actual != expected or stored_inputs != definition.inputs:
                raise ValueError(f"job identity collision or incompatible definition: {definition.job_id}")

            previous = JobState(row["state"])
            if previous in {JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS}:
                return PreparedJob(already_complete=True)

            self._db.execute(
                """UPDATE job_items SET state = ?, updated_at = ?
                   WHERE job_id = ? AND state = ?""",
                (ItemState.PENDING.value, now, definition.job_id, ItemState.RUNNING.value),
            )
            self._db.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RECOVERED.value, now, definition.job_id),
            )
            return PreparedJob(recovered=True)

    def set_job_state(self, job_id: str, state: JobState) -> None:
        with self._db:
            changed = self._db.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (state.value, time.time(), job_id),
            ).rowcount
        if changed != 1:
            raise KeyError(f"unknown job: {job_id}")

    def pending_items(self, job_id: str) -> list[ItemRecord]:
        rows = self._db.execute(
            """SELECT position, input_path, state, attempts, details, result_json
               FROM job_items WHERE job_id = ? AND state = ? ORDER BY position""",
            (job_id, ItemState.PENDING.value),
        ).fetchall()
        return [self._record(row) for row in rows]

    def begin_item(self, job_id: str, position: int) -> int:
        now = time.time()
        with self._db:
            changed = self._db.execute(
                """UPDATE job_items
                   SET state = ?, attempts = attempts + 1, details = '', result_json = NULL, updated_at = ?
                   WHERE job_id = ? AND position = ? AND state IN (?, ?)""",
                (
                    ItemState.RUNNING.value,
                    now,
                    job_id,
                    position,
                    ItemState.PENDING.value,
                    ItemState.RUNNING.value,
                ),
            ).rowcount
            row = self._db.execute(
                "SELECT attempts FROM job_items WHERE job_id = ? AND position = ?",
                (job_id, position),
            ).fetchone()
        if changed != 1 or row is None:
            raise KeyError(f"item cannot start: {job_id}/{position}")
        return int(row["attempts"])

    def finish_item(self, job_id: str, position: int, outcome: ItemOutcome) -> None:
        result_json = None
        if outcome.data is not None:
            result_json = json.dumps(json_value(outcome.data), sort_keys=True, separators=(",", ":"))
        with self._db:
            changed = self._db.execute(
                """UPDATE job_items
                   SET state = ?, details = ?, result_json = ?, updated_at = ?
                   WHERE job_id = ? AND position = ? AND state = ?""",
                (
                    outcome.state.value,
                    outcome.details,
                    result_json,
                    time.time(),
                    job_id,
                    position,
                    ItemState.RUNNING.value,
                ),
            ).rowcount
            self._db.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                (time.time(), job_id),
            )
        if changed != 1:
            raise KeyError(f"item cannot finish: {job_id}/{position}")

    def defer_item(self, job_id: str, position: int, details: str) -> None:
        with self._db:
            changed = self._db.execute(
                """UPDATE job_items
                   SET state = ?, details = ?, result_json = NULL, updated_at = ?
                   WHERE job_id = ? AND position = ? AND state = ?""",
                (
                    ItemState.PENDING.value,
                    details,
                    time.time(),
                    job_id,
                    position,
                    ItemState.RUNNING.value,
                ),
            ).rowcount
        if changed != 1:
            raise KeyError(f"item cannot be deferred: {job_id}/{position}")

    def reset_items(self, job_id: str, positions: list[int]) -> None:
        if not positions:
            return
        now = time.time()
        with self._db:
            changed = 0
            for position in positions:
                changed += self._db.execute(
                    """UPDATE job_items
                       SET state = ?, attempts = 0, details = '', result_json = NULL, updated_at = ?
                       WHERE job_id = ? AND position = ?""",
                    (ItemState.PENDING.value, now, job_id, position),
                ).rowcount
            self._db.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
                (JobState.RECOVERED.value, now, job_id),
            )
        if changed != len(positions):
            raise KeyError(f"one or more items could not be reset for job: {job_id}")

    def build_report(self, job_id: str) -> BatchReport:
        job = self._db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job is None:
            raise KeyError(f"unknown job: {job_id}")
        rows = self._db.execute(
            """SELECT position, input_path, state, attempts, details, result_json
               FROM job_items WHERE job_id = ? ORDER BY position""",
            (job_id,),
        ).fetchall()
        return BatchReport(
            job_id=job_id,
            tool_id=job["tool_id"],
            state=JobState(job["state"]),
            items=tuple(self._record(row) for row in rows),
            created_at=float(job["created_at"]),
            updated_at=float(job["updated_at"]),
        )

    def list_reports(self, limit: int = 100) -> list[BatchReport]:
        if limit <= 0:
            return []
        rows = self._db.execute(
            "SELECT job_id FROM jobs ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self.build_report(row["job_id"]) for row in rows]

    @staticmethod
    def _record(row: sqlite3.Row) -> ItemRecord:
        data = json.loads(row["result_json"]) if row["result_json"] else None
        return ItemRecord(
            position=int(row["position"]),
            input_path=row["input_path"],
            state=ItemState(row["state"]),
            attempts=int(row["attempts"]),
            details=row["details"],
            data=data,
        )

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "SQLiteJobStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
