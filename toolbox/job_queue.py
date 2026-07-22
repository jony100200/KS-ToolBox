"""Shell-owned, lazy, single-worker queue for durable batch submissions."""
from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from toolbox.batch_core import (
    BatchProgress,
    BatchReport,
    BatchRunner,
    CancellationToken,
    ClassifyResult,
    ItemRecord,
    JobDefinition,
    JobState,
    ProgressCallback,
    StoredResultValidator,
)

QueueExecute = Callable[[Path, CancellationToken], object]


@dataclass(frozen=True)
class QueueSubmission:
    definition: JobDefinition
    label: str
    execute: QueueExecute
    classify: ClassifyResult
    validate_stored: StoredResultValidator | None = None
    priority: int = 0
    on_progress: ProgressCallback | None = None
    on_complete: Callable[[BatchReport], None] | None = None
    on_error: Callable[[Exception], None] | None = None
    on_cancel: Callable[[QueueSnapshot], None] | None = None
    finalize: Callable[[BatchReport], object] | None = None


@dataclass(frozen=True)
class QueueSnapshot:
    job_id: str
    tool_id: str
    label: str
    state: JobState
    completed_items: int
    total_items: int
    priority: int
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    detail: str = ""
    persisted: bool = False
    last_item: ItemRecord | None = None


@dataclass(frozen=True)
class QueueCompletion:
    report: BatchReport
    value: object = None
    error: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueueFinalization:
    """Finalizer output plus non-fatal problems that must remain visible."""

    value: object = None
    warnings: tuple[str, ...] = ()


StoreFactory = Callable[[], object]
QueueSubscriber = Callable[[QueueSnapshot], None]


def _default_store_factory():
    from toolbox.sqlite_job_store import SQLiteJobStore
    return SQLiteJobStore()


class JobQueue:
    """One bounded execution lane, started only when the first job is submitted."""

    def __init__(self, store_factory: StoreFactory | None = None) -> None:
        self._store_factory = store_factory or _default_store_factory
        self._lock = threading.RLock()
        self._pending: list[tuple[int, int, QueueSubmission]] = []
        self._tokens: dict[str, CancellationToken] = {}
        self._snapshots: dict[str, QueueSnapshot] = {}
        self._completions: dict[str, QueueCompletion] = {}
        self._subscribers: dict[int, QueueSubscriber] = {}
        self._subscriber_errors: list[str] = []
        self._sequence = 0
        self._subscriber_sequence = 0
        self._worker: threading.Thread | None = None
        self._closing = False

    def submit(self, submission: QueueSubmission) -> str:
        job_id = submission.definition.job_id
        with self._lock:
            if self._closing:
                raise RuntimeError("job queue is closing")
            existing = self._snapshots.get(job_id)
            if existing and existing.state not in {
                JobState.COMPLETED,
                JobState.COMPLETED_WITH_WARNINGS,
                JobState.FAILED,
                JobState.CANCELLED,
            }:
                raise ValueError(f"job is already queued or running: {job_id}")
            self._sequence += 1
            token = CancellationToken()
            snapshot = QueueSnapshot(
                job_id=job_id,
                tool_id=submission.definition.tool_id,
                label=submission.label,
                state=JobState.QUEUED,
                completed_items=0,
                total_items=len(submission.definition.inputs),
                priority=submission.priority,
                submitted_at=time.time(),
            )
            self._tokens[job_id] = token
            self._snapshots[job_id] = snapshot
            heapq.heappush(self._pending, (-submission.priority, self._sequence, submission))
            self._ensure_worker_locked()
        self._notify(snapshot)
        return job_id

    def pause(self, job_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(job_id)
            snapshot = self._snapshots.get(job_id)
            if token is None or snapshot is None or snapshot.state not in {
                JobState.QUEUED, JobState.PREPARING, JobState.RUNNING
            }:
                return False
            token.pause()
            updated = replace(snapshot, state=JobState.PAUSED, detail="paused by user")
            self._snapshots[job_id] = updated
        self._notify(updated)
        return True

    def resume(self, job_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(job_id)
            snapshot = self._snapshots.get(job_id)
            if token is None or snapshot is None or snapshot.state is not JobState.PAUSED:
                return False
            token.resume()
            state = JobState.RUNNING if snapshot.started_at is not None else JobState.QUEUED
            updated = replace(snapshot, state=state, detail="")
            self._snapshots[job_id] = updated
        self._notify(updated)
        return True

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(job_id)
            snapshot = self._snapshots.get(job_id)
            if token is None or snapshot is None or snapshot.state in {
                JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                JobState.FAILED, JobState.CANCELLED,
            }:
                return False
            token.cancel()
            updated = replace(snapshot, state=JobState.CANCELLING, detail="cancellation requested")
            self._snapshots[job_id] = updated
        self._notify(updated)
        return True

    def snapshot(self, job_id: str) -> QueueSnapshot | None:
        with self._lock:
            return self._snapshots.get(job_id)

    def history(self) -> list[QueueSnapshot]:
        with self._lock:
            return sorted(self._snapshots.values(), key=lambda item: item.submitted_at, reverse=True)

    def completion(self, job_id: str) -> QueueCompletion | None:
        with self._lock:
            return self._completions.get(job_id)

    def load_persisted_history(self, limit: int = 100) -> list[QueueSnapshot]:
        """Load database history on demand; startup never opens SQLite."""
        with self._store_factory() as store:
            reports = store.list_reports(limit=limit)
        loaded: list[QueueSnapshot] = []
        with self._lock:
            for report in reports:
                if report.job_id in self._snapshots:
                    continue
                snapshot = QueueSnapshot(
                    job_id=report.job_id,
                    tool_id=report.tool_id,
                    label=report.tool_id.replace("_", " ").title(),
                    state=(report.state if report.state in {
                        JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                        JobState.FAILED, JobState.CANCELLED,
                    } else JobState.RECOVERED),
                    completed_items=sum(
                        item.state.value in {
                            "completed", "completed_with_warnings", "skipped", "quarantined"
                        }
                        for item in report.items
                    ),
                    total_items=len(report.items),
                    priority=0,
                    submitted_at=report.created_at,
                    finished_at=report.updated_at,
                    detail=("interrupted; resubmit from its tool to recover"
                            if report.state not in {
                                JobState.COMPLETED, JobState.COMPLETED_WITH_WARNINGS,
                                JobState.FAILED, JobState.CANCELLED,
                            } else ""),
                    persisted=True,
                )
                self._snapshots[report.job_id] = snapshot
                loaded.append(snapshot)
        return loaded

    def subscribe(self, callback: QueueSubscriber) -> Callable[[], None]:
        with self._lock:
            self._subscriber_sequence += 1
            key = self._subscriber_sequence
            self._subscribers[key] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(key, None)

        return unsubscribe

    @property
    def subscriber_errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._subscriber_errors)

    def close(self, timeout: float = 5.0) -> bool:
        with self._lock:
            self._closing = True
            for token in self._tokens.values():
                token.cancel()
            worker = self._worker
        if worker and worker is not threading.current_thread():
            worker.join(timeout=timeout)
        return not worker or not worker.is_alive()

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._drain, name="ks-job-queue", daemon=True)
            self._worker.start()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._worker = None
                    return
                _, _, submission = heapq.heappop(self._pending)
                token = self._tokens[submission.definition.job_id]
            self._execute(submission, token)

    def _execute(self, submission: QueueSubmission, token: CancellationToken) -> None:
        job_id = submission.definition.job_id
        if token.is_cancelled:
            self._set_terminal(job_id, JobState.CANCELLED, "cancelled before start")
            self._call(submission.on_cancel, self.snapshot(job_id))
            return
        if token.is_paused and not token.wait_if_paused():
            self._set_terminal(job_id, JobState.CANCELLED, "cancelled while paused")
            self._call(submission.on_cancel, self.snapshot(job_id))
            return
        self._update(job_id, state=JobState.RUNNING, started_at=time.time(), detail="")

        def progress(update: BatchProgress) -> None:
            self._update(
                job_id,
                state=JobState.PAUSED if token.is_paused else JobState.RUNNING,
                completed_items=update.completed_items,
                total_items=update.total_items,
                last_item=update.item,
            )
            self._call(submission.on_progress, update)

        try:
            with self._store_factory() as store:
                report = BatchRunner(store).run(
                    submission.definition,
                    lambda path: submission.execute(path, token),
                    submission.classify,
                    cancellation=token,
                    on_progress=progress,
                    validate_stored=submission.validate_stored,
                )
            detail = "reused completed job" if report.reused else ""
            value = None
            finalize_error = ""
            finalize_warnings: tuple[str, ...] = ()
            if submission.finalize is not None:
                try:
                    finalized = submission.finalize(report)
                    if isinstance(finalized, QueueFinalization):
                        value = finalized.value
                        finalize_warnings = finalized.warnings
                    else:
                        value = finalized
                except Exception as ex:  # noqa: BLE001 - outputs remain, report warning is visible
                    finalize_error = f"{type(ex).__name__}: {ex}"
                    detail = f"completion finalization failed: {finalize_error}"
            if finalize_warnings:
                detail = "; ".join(finalize_warnings)
            with self._lock:
                self._completions[job_id] = QueueCompletion(
                    report, value, finalize_error, finalize_warnings
                )
            self._call(submission.on_complete, report)
            final_state = report.state
            if (finalize_error or finalize_warnings) and final_state is JobState.COMPLETED:
                final_state = JobState.COMPLETED_WITH_WARNINGS
            self._update(
                job_id,
                state=final_state,
                completed_items=sum(
                    item.state.value in {
                        "completed", "completed_with_warnings", "skipped", "quarantined"
                    }
                    for item in report.items
                ),
                total_items=len(report.items),
                finished_at=time.time(),
                detail=detail,
            )
        except Exception as ex:  # noqa: BLE001 - queue isolates one job failure
            self._set_terminal(job_id, JobState.FAILED, f"{type(ex).__name__}: {ex}")
            self._call(submission.on_error, ex)

    def _set_terminal(self, job_id: str, state: JobState, detail: str) -> None:
        self._update(job_id, state=state, finished_at=time.time(), detail=detail)

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            current = self._snapshots[job_id]
            updated = replace(current, **changes)
            self._snapshots[job_id] = updated
        self._notify(updated)

    def _call(self, callback, value) -> None:
        if callback is None:
            return
        try:
            callback(value)
        except Exception as ex:  # noqa: BLE001 - callback cannot stop queue
            with self._lock:
                self._subscriber_errors.append(f"callback failed: {type(ex).__name__}: {ex}")

    def _notify(self, snapshot: QueueSnapshot) -> None:
        with self._lock:
            subscribers = list(self._subscribers.values())
        for callback in subscribers:
            self._call(callback, snapshot)
