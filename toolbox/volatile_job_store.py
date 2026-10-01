"""Session-only JobStore fallback when Python's SQLite extension is unavailable.

The normal Toolbox store is SQLite-backed and durable.  This implementation is
used only when importing that standard-library extension fails in a damaged or
third-party-DLL-conflicted Python process.  It preserves batch isolation,
retry, pause, cancellation and per-item reporting for the current run, while
explicitly giving up restart recovery and stored-history reuse.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from toolbox.batch_core import (
    BatchReport,
    ItemOutcome,
    ItemRecord,
    ItemState,
    JobDefinition,
    JobState,
    PreparedJob,
)


@dataclass
class _Job:
    definition: JobDefinition
    state: JobState
    items: list[ItemRecord]
    created_at: float
    updated_at: float


class VolatileJobStore:
    """A non-persistent JobStore with the same batch-run contract as SQLite."""

    degraded_warning = (
        "SQLite is unavailable; this batch ran in session-only mode. "
        "Restart recovery and stored-history reuse are unavailable."
    )

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}

    def prepare(self, definition: JobDefinition) -> PreparedJob:
        job = self._jobs.get(definition.job_id)
        now = time.time()
        if job is None:
            self._jobs[definition.job_id] = _Job(
                definition=definition,
                state=JobState.QUEUED,
                items=[
                    ItemRecord(position, input_path, ItemState.PENDING)
                    for position, input_path in enumerate(definition.inputs)
                ],
                created_at=now,
                updated_at=now,
            )
            return PreparedJob()
        if job.definition != definition:
            raise ValueError(f"job identity collision or incompatible definition: {definition.job_id}")
        if job.state in {JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS}:
            return PreparedJob(already_complete=True)
        job.items = [
            replace(item, state=ItemState.PENDING)
            if item.state is ItemState.RUNNING else item
            for item in job.items
        ]
        job.state = JobState.RECOVERED
        job.updated_at = now
        return PreparedJob(recovered=True)

    def set_job_state(self, job_id: str, state: JobState) -> None:
        job = self._job(job_id)
        job.state = state
        job.updated_at = time.time()

    def pending_items(self, job_id: str) -> list[ItemRecord]:
        return [item for item in self._job(job_id).items if item.state is ItemState.PENDING]

    def begin_item(self, job_id: str, position: int) -> int:
        job = self._job(job_id)
        item = self._item(job, position)
        if item.state not in {ItemState.PENDING, ItemState.RUNNING}:
            raise KeyError(f"item cannot start: {job_id}/{position}")
        item = replace(
            item, state=ItemState.RUNNING, attempts=item.attempts + 1,
            details="", data=None,
        )
        job.items[position] = item
        job.updated_at = time.time()
        return item.attempts

    def finish_item(self, job_id: str, position: int, outcome: ItemOutcome) -> None:
        job = self._job(job_id)
        item = self._item(job, position)
        if item.state is not ItemState.RUNNING:
            raise KeyError(f"item cannot finish: {job_id}/{position}")
        job.items[position] = replace(
            item, state=outcome.state, details=outcome.details, data=outcome.data,
        )
        job.updated_at = time.time()

    def defer_item(self, job_id: str, position: int, details: str) -> None:
        job = self._job(job_id)
        item = self._item(job, position)
        if item.state is not ItemState.RUNNING:
            raise KeyError(f"item cannot defer: {job_id}/{position}")
        job.items[position] = replace(item, state=ItemState.PENDING, details=details, data=None)
        job.updated_at = time.time()

    def reset_items(self, job_id: str, positions: list[int]) -> None:
        job = self._job(job_id)
        for position in positions:
            item = self._item(job, position)
            job.items[position] = replace(
                item, state=ItemState.PENDING, attempts=0, details="", data=None,
            )
        job.state = JobState.RECOVERED
        job.updated_at = time.time()

    def build_report(self, job_id: str) -> BatchReport:
        job = self._job(job_id)
        return BatchReport(
            job_id=job.definition.job_id,
            tool_id=job.definition.tool_id,
            state=job.state,
            items=tuple(job.items),
            created_at=job.created_at,
            updated_at=job.updated_at,
        )

    @staticmethod
    def _item(job: _Job, position: int) -> ItemRecord:
        try:
            item = job.items[position]
        except IndexError as exc:
            raise KeyError(f"unknown item position: {position}") from exc
        if item.position != position:
            raise KeyError(f"unknown item position: {position}")
        return item

    def _job(self, job_id: str) -> _Job:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise KeyError(f"unknown job: {job_id}") from exc

    def close(self) -> None:
        return None

    def __enter__(self) -> "VolatileJobStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
