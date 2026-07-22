"""Shared completion artifacts for durable one-input/one-result batch tools."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from toolbox.batch_core import (
    BatchReport,
    ItemRecord,
    ItemState,
    write_completion_report,
)
from toolbox.job_queue import QueueFinalization
from toolbox.sqlite_job_store import default_report_dir

ResultDecoder = Callable[[ItemRecord], object]
ManifestWriter = Callable[[list[object]], str | None]


@dataclass(frozen=True)
class BatchCompletionArtifacts:
    finished_items: tuple[ItemRecord, ...]
    results: tuple[object, ...]
    manifest: str | None
    report_path: str | None


def completion_report_path(
    tool_id: str,
    job_id: str,
    *,
    out_root: Path | None,
    dry_run: bool,
) -> Path:
    root = default_report_dir()
    if out_root is not None and not dry_run:
        root = Path(out_root) / "ks_reports"
    return root / f"{tool_id}_{job_id}.json"


def prepare_batch_completion(
    report: BatchReport,
    *,
    result_from_record: ResultDecoder,
    write_manifest: ManifestWriter,
    report_path: Path,
) -> QueueFinalization:
    """Decode terminal items and write auditable outputs without hiding I/O failures."""
    finished_items = tuple(
        item for item in report.items
        if item.state in {ItemState.COMPLETED, ItemState.SKIPPED, ItemState.QUARANTINED}
    )
    results = tuple(result_from_record(item) for item in finished_items)
    warnings: list[str] = []
    manifest = None
    if not report.reused:
        try:
            manifest = write_manifest(list(results))
        except OSError as ex:
            warnings.append(f"manifest write failed: {ex}")
    try:
        written_report = write_completion_report(report, report_path)
    except OSError as ex:
        warnings.append(f"completion report write failed: {ex}")
        written_report = None
    artifacts = BatchCompletionArtifacts(
        finished_items,
        results,
        manifest,
        str(written_report) if written_report else None,
    )
    return QueueFinalization(artifacts, tuple(warnings))
