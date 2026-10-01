"""Headless contracts and executor for durable item-oriented batch jobs.

The UI submits a :class:`JobDefinition`; it does not own retry, recovery, or
failure-isolation policy.  This module deliberately knows nothing about
CustomTkinter, SQLite, codecs, or individual tools.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


class JobState(str, Enum):
    CREATED = "created"
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    RECOVERED = "recovered"


class ItemState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    SKIPPED = "skipped"
    FAILED = "failed"
    QUARANTINED = "quarantined"


def json_value(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation or fail loudly."""
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        return {key: json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value is not JSON-compatible: {type(value).__name__}")


def _input_identity(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve(strict=False)
    try:
        stat = resolved.stat()
        return {"path": str(resolved), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"path": str(resolved), "size": None, "mtime_ns": None}


@dataclass(frozen=True)
class JobDefinition:
    job_id: str
    tool_id: str
    tool_version: str
    workflow_version: str
    inputs: tuple[str, ...]
    settings: Mapping[str, Any]
    max_retries: int = 0

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        tool_version: str,
        workflow_version: str,
        inputs: list[str | Path] | tuple[str | Path, ...],
        settings: Mapping[str, Any],
        max_retries: int = 0,
        identity_dependencies: list[str | Path] | tuple[str | Path, ...] | None = None,
    ) -> "JobDefinition":
        if not tool_id.strip():
            raise ValueError("tool_id is required")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        identities = [_input_identity(Path(item)) for item in inputs]
        if not identities:
            raise ValueError("a batch job requires at least one input")
        normalized_settings = json_value(settings)
        identity = {
            "tool_id": tool_id,
            "tool_version": tool_version,
            "workflow_version": workflow_version,
            "inputs": identities,
            "settings": normalized_settings,
            "max_retries": max_retries,
        }
        if identity_dependencies is not None:
            dependency_by_path = {item["path"]: item for item in identities}
            for dependency in identity_dependencies:
                item = _input_identity(Path(dependency))
                dependency_by_path[item["path"]] = item
            identity["identity_dependencies"] = sorted(
                dependency_by_path.values(), key=lambda item: item["path"]
            )
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        job_id = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]
        return cls(
            job_id=job_id,
            tool_id=tool_id,
            tool_version=tool_version,
            workflow_version=workflow_version,
            inputs=tuple(item["path"] for item in identities),
            settings=normalized_settings,
            max_retries=max_retries,
        )


@dataclass(frozen=True)
class ItemOutcome:
    state: ItemState
    details: str = ""
    retryable: bool = False
    data: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        # QUARANTINED is produced by the runner after a failure exhausts its
        # retry policy; accepting it here keeps the persisted outcome typed.
        allowed = {
            ItemState.COMPLETED,
            ItemState.COMPLETED_WITH_WARNINGS,
            ItemState.SKIPPED,
            ItemState.FAILED,
            ItemState.QUARANTINED,
        }
        if self.state not in allowed:
            raise ValueError(f"executor outcome cannot be {self.state.value}")
        if self.data is not None:
            json_value(self.data)

    @classmethod
    def completed(cls, data: Mapping[str, Any] | None = None, details: str = "") -> "ItemOutcome":
        return cls(ItemState.COMPLETED, details=details, data=data)

    @classmethod
    def warning(cls, data: Mapping[str, Any] | None = None, details: str = "") -> "ItemOutcome":
        return cls(ItemState.COMPLETED_WITH_WARNINGS, details=details, data=data)

    @classmethod
    def skipped(cls, data: Mapping[str, Any] | None = None, details: str = "") -> "ItemOutcome":
        return cls(ItemState.SKIPPED, details=details, data=data)

    @classmethod
    def failed(
        cls,
        details: str,
        *,
        retryable: bool = False,
        data: Mapping[str, Any] | None = None,
    ) -> "ItemOutcome":
        return cls(ItemState.FAILED, details=details, retryable=retryable, data=data)


@dataclass(frozen=True)
class ItemRecord:
    position: int
    input_path: str
    state: ItemState
    attempts: int = 0
    details: str = ""
    data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PreparedJob:
    recovered: bool = False
    already_complete: bool = False


@dataclass(frozen=True)
class BatchReport:
    job_id: str
    tool_id: str
    state: JobState
    items: tuple[ItemRecord, ...]
    created_at: float
    updated_at: float
    recovered: bool = False
    reused: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        counts = {state.value: 0 for state in ItemState}
        for item in self.items:
            counts[item.state.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "job_id": self.job_id,
            "tool_id": self.tool_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "recovered": self.recovered,
            "reused": self.reused,
            "counts": self.counts,
            "diagnostics": list(self.diagnostics),
            "items": [
                {
                    "position": item.position,
                    "input_path": item.input_path,
                    "state": item.state.value,
                    "attempts": item.attempts,
                    "details": item.details,
                    "data": json_value(item.data),
                }
                for item in self.items
            ],
        }


@dataclass(frozen=True)
class BatchProgress:
    job_id: str
    completed_items: int
    total_items: int
    item: ItemRecord


class JobStore(Protocol):
    def prepare(self, definition: JobDefinition) -> PreparedJob: ...

    def set_job_state(self, job_id: str, state: JobState) -> None: ...

    def pending_items(self, job_id: str) -> list[ItemRecord]: ...

    def begin_item(self, job_id: str, position: int) -> int: ...

    def finish_item(self, job_id: str, position: int, outcome: ItemOutcome) -> None: ...

    def defer_item(self, job_id: str, position: int, details: str) -> None: ...

    def reset_items(self, job_id: str, positions: list[int]) -> None: ...

    def build_report(self, job_id: str) -> BatchReport: ...


class CancellationToken:
    """Cooperative pause/cancel state, optionally linked to an existing Event."""

    def __init__(self, external_cancel: threading.Event | None = None) -> None:
        self._condition = threading.Condition()
        self._cancelled = False
        self._paused = False
        self._external_cancel = external_cancel

    def cancel(self) -> None:
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            if not self._cancelled:
                self._paused = True

    def resume(self) -> None:
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    @property
    def is_cancelled(self) -> bool:
        external = self._external_cancel
        return self._cancelled or bool(external and external.is_set())

    @property
    def is_paused(self) -> bool:
        with self._condition:
            return self._paused

    def wait_if_paused(self) -> bool:
        """Wait at a safe item boundary. Return False when cancellation wins."""
        with self._condition:
            while self._paused and not self.is_cancelled:
                self._condition.wait(timeout=0.25)
            return not self.is_cancelled


ExecuteItem = Callable[[Path], Any]
ClassifyResult = Callable[[Any], ItemOutcome]
ProgressCallback = Callable[[BatchProgress], None]
StoredResultValidator = Callable[[ItemRecord], bool]


class BatchRunner:
    """Run one durable job serially with per-item retry and failure isolation."""

    def __init__(self, store: JobStore) -> None:
        self._store = store

    def run(
        self,
        definition: JobDefinition,
        execute: ExecuteItem,
        classify: ClassifyResult,
        *,
        cancellation: CancellationToken | None = None,
        on_progress: ProgressCallback | None = None,
        validate_stored: StoredResultValidator | None = None,
    ) -> BatchReport:
        token = cancellation or CancellationToken()
        diagnostics: list[str] = []
        prepared = self._store.prepare(definition)
        if prepared.already_complete:
            stored = self._store.build_report(definition.job_id)
            invalid: list[int] = []
            for item in stored.items:
                if item.state not in {ItemState.COMPLETED, ItemState.COMPLETED_WITH_WARNINGS}:
                    continue
                if validate_stored is None:
                    invalid.append(item.position)
                    continue
                try:
                    valid = validate_stored(item)
                except Exception as ex:  # noqa: BLE001 - invalid cache entry, not a job crash
                    valid = False
                    diagnostics.append(
                        f"stored-result validator failed for item {item.position}: "
                        f"{type(ex).__name__}: {ex}"
                    )
                if not valid:
                    invalid.append(item.position)
            if not invalid:
                return replace(stored, reused=True, diagnostics=tuple(diagnostics))
            self._store.reset_items(definition.job_id, invalid)
            prepared = PreparedJob(recovered=True)

        self._store.set_job_state(definition.job_id, JobState.PREPARING)
        self._store.set_job_state(definition.job_id, JobState.RUNNING)
        total = len(definition.inputs)

        for item in self._store.pending_items(definition.job_id):
            if token.is_cancelled:
                self._store.set_job_state(definition.job_id, JobState.CANCELLING)
                self._store.set_job_state(definition.job_id, JobState.CANCELLED)
                return replace(
                    self._store.build_report(definition.job_id),
                    recovered=prepared.recovered,
                    diagnostics=tuple(diagnostics),
                )
            if token.is_paused:
                self._store.set_job_state(definition.job_id, JobState.PAUSED)
                if not token.wait_if_paused():
                    self._store.set_job_state(definition.job_id, JobState.CANCELLING)
                    self._store.set_job_state(definition.job_id, JobState.CANCELLED)
                    return replace(
                        self._store.build_report(definition.job_id),
                        recovered=prepared.recovered,
                        diagnostics=tuple(diagnostics),
                    )
                self._store.set_job_state(definition.job_id, JobState.RUNNING)

            while True:
                attempts = self._store.begin_item(definition.job_id, item.position)
                try:
                    outcome = classify(execute(Path(item.input_path)))
                    if not isinstance(outcome, ItemOutcome):
                        raise TypeError("classify must return ItemOutcome")
                except Exception as ex:  # noqa: BLE001 - isolate one malformed item
                    if token.is_cancelled:
                        self._store.defer_item(
                            definition.job_id,
                            item.position,
                            f"cancelled during execution: {type(ex).__name__}: {ex}",
                        )
                        self._store.set_job_state(definition.job_id, JobState.CANCELLING)
                        self._store.set_job_state(definition.job_id, JobState.CANCELLED)
                        return replace(
                            self._store.build_report(definition.job_id),
                            recovered=prepared.recovered,
                            diagnostics=tuple(diagnostics),
                        )
                    outcome = ItemOutcome.failed(
                        f"{type(ex).__name__}: {ex}",
                        data={"exception_type": type(ex).__name__},
                    )

                if outcome.state is ItemState.FAILED and outcome.retryable and attempts <= definition.max_retries:
                    continue
                if outcome.state is ItemState.FAILED:
                    outcome = replace(outcome, state=ItemState.QUARANTINED, retryable=False)
                self._store.finish_item(definition.job_id, item.position, outcome)

                if on_progress is not None:
                    current = ItemRecord(
                        position=item.position,
                        input_path=item.input_path,
                        state=outcome.state,
                        attempts=attempts,
                        details=outcome.details,
                        data=outcome.data,
                    )
                    try:
                        on_progress(BatchProgress(definition.job_id, item.position + 1, total, current))
                    except Exception as ex:  # noqa: BLE001 - subscriber errors cannot lose work
                        diagnostics.append(f"progress subscriber failed: {type(ex).__name__}: {ex}")
                break

        report = self._store.build_report(definition.job_id)
        quarantined = report.counts[ItemState.QUARANTINED.value]
        warned = report.counts[ItemState.COMPLETED_WITH_WARNINGS.value]
        if report.items and quarantined == len(report.items):
            final_state = JobState.FAILED
        elif quarantined or warned:
            final_state = JobState.COMPLETED_WITH_WARNINGS
        else:
            final_state = JobState.COMPLETED
        self._store.set_job_state(definition.job_id, final_state)
        return replace(
            self._store.build_report(definition.job_id),
            recovered=prepared.recovered,
            diagnostics=tuple(diagnostics),
        )


def write_completion_report(report: BatchReport, path: str | Path) -> Path:
    """Atomically persist a portable JSON morning report."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.{os.getpid()}.part")
    payload = json.dumps(report.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target
