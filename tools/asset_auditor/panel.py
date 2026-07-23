"""Asset Auditor — the tool's UI. Thin over engine.py: collect the image files +
options, run the (read-only) audit on a worker thread, stream category counts
back via after(), then write the HTML/JSON/CSV reports. No inspection logic here.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class AssetAuditorPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "file"
    RESULTS_ICON = Icons.CHART
    RUN_LABEL = "Run Audit"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._stage_progress: tuple[int, int, str] | None = None
        self._last_stage_position = 0

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Near-dup Hamming (of 64 bits)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._hamming = c.entry(row, width=80); self._hamming.insert(0, "8")
        self._hamming.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Oversized over (MB)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._oversized = c.entry(row, width=80); self._oversized.insert(0, "25")
        self._oversized.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Min dimension (px)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._min_dim = c.entry(row, width=80); self._min_dim.insert(0, "32")
        self._min_dim.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # output folder (the report destination — the tool's only output)
        self._build_output_row(b, "Report folder (blank = ./asset_audit beside the scanned folder)")

        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._health = ctk.CTkCheckBox(toggles, text="Image health flags (dark / bright / low-contrast)",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._health.select(); self._health.pack(side="left")

        self._build_run_row(b)

    def _collect_options(self):
        try:
            hamming = int(self._hamming.get() or "8")
            oversized = float(self._oversized.get() or "25")
            min_dim = int(self._min_dim.get() or "32")
        except ValueError:
            self._logline("Hamming / MB / dimension must be numbers.", t.STATE["error"][1])
            return None
        scan_root = self._resolve_input_root()
        out = self._out_entry.get().strip()
        if out:
            out_root = Path(out)
        elif scan_root:
            out_root = Path(scan_root) / "asset_audit"
        elif self._files:
            out_root = self._files[0].parent / "asset_audit"
        else:
            out_root = None
        opts = e.AuditOptions(out_root=out_root, scan_root=scan_root,
                              near_dup_hamming=hamming, oversized_mb=oversized,
                              min_dimension=min_dim, check_health=bool(self._health.get()))
        normalized, error = e.normalized_options(opts)
        if normalized is None:
            self._logline(error, t.STATE["error"][1])
            return None
        return normalized

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.AuditOptions):
        def progress(i, total, name):
            self.after(0, self._tick, i, total, name)

        result = e.process(
            files, opts, progress=progress, cancelled=lambda: self._stop.is_set()
        )
        self.after(0, self._done, result, result.out_path)

    def _build_submission(self, files: list[Path], opts: e.AuditOptions) -> QueueSubmission:
        self._stage_progress = None
        self._last_stage_position = 0
        files = sorted(
            files, key=lambda path: str(path).casefold()
        )
        definition = JobDefinition.create(
            tool_id="asset_auditor",
            tool_version="1",
            workflow_version="grouped-audit.v2",
            inputs=[files[0]],
            identity_dependencies=files,
            settings=asdict(opts),
            max_retries=1,
        )

        def execute(_anchor: Path, token) -> e.AuditResult:
            return e.process(
                files,
                opts,
                progress=self._record_stage_progress,
                cancelled=lambda: token.is_cancelled,
            )

        def classify(result: e.AuditResult) -> ItemOutcome:
            data = result.to_dict()
            if result.action == "audited":
                if result.warnings:
                    return ItemOutcome.warning(data, result.reason)
                return ItemOutcome.completed(data, result.reason)
            return ItemOutcome.failed(
                result.reason, retryable=result.retryable, data=data
            )

        return QueueSubmission(
            definition=definition,
            label=f"Asset Auditor · {len(files)} file(s)",
            execute=execute,
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="asset_auditor"
            ),
        )

    def _pre_run_check(self, _opts: e.AuditOptions) -> bool:
        if len(self._files) <= e.MAX_AUDIT_FILES:
            return True
        self._logline(
            f"Audit limit is {e.MAX_AUDIT_FILES:,} files; split this collection.",
            t.STATE["error"][1],
        )
        return False

    def _record_stage_progress(self, i: int, total: int, name: str) -> None:
        """Worker-safe handoff; Tk reads this tuple during its normal poll."""
        self._stage_progress = (i, total, name)

    def _on_queue_snapshot(self, snapshot) -> None:
        super()._on_queue_snapshot(snapshot)
        progress = self._stage_progress
        if progress is not None and progress[0] != self._last_stage_position:
            self._last_stage_position = progress[0]
            self._tick(*progress)

    def _tick(self, i: int, total: int, name: str):
        self._progress.set(i / total if total else 1)
        interval = max(1, total // 100)
        if i == 1 or i == total or i % interval == 0:
            self._logline(f"[{i}/{total}] {name}", t.TEXT_MUTED)

    def _write_manifest(self, _opts: e.AuditOptions,
                        results: list[e.AuditResult]) -> str | None:
        """The engine already publishes the three-file audit report atomically."""
        return next(
            (result.out_path for result in results if result.action == "audited"),
            None,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.AuditResult:
        if item.data and {"action", "reason"}.issubset(item.data):
            return e.AuditResult(**item.data)
        return e.AuditResult(
            "failed", item.details or "asset audit quarantined",
            detail="batch.quarantined",
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        result = payload.results[0] if payload.results else e.AuditResult(
            "failed", "asset audit produced no terminal result",
            detail="batch.empty",
        )
        self._done(
            result, payload.manifest or result.out_path, payload.report_path,
            self._queue_completion_state(completion), completion.report.recovered,
            completion.report.reused,
        )

    def _show(self, result: e.AuditResult, _position: int, _total: int):
        icon = "✓" if result.action == "audited" else "✗"
        color = t.STATE["done"][1] if result.action == "audited" else t.STATE["error"][1]
        self._logline(f"  {icon} {result.reason}", color)
        if result.out_path:
            self._logline(f"    report: {result.out_path}", t.TEXT_MUTED)
        for warning in result.warnings[:5]:
            self._logline(f"    warning: {warning}", t.STATE["waiting"][1])

    def _done(self, result: e.AuditResult, manifest: str | None = None,
              report_path: str | None = None, job_state=JobState.COMPLETED,
              recovered: bool = False, reused: bool = False):
        issues = result.issues

        # a per-category breakdown in the results log
        rows = [
            ("exact duplicate groups", result.counts.get("exact_duplicate_groups", 0)),
            ("near-duplicate groups", result.counts.get("near_duplicate_groups", 0)),
            ("corrupt / unreadable", result.counts.get("corrupt", 0)),
            ("unsafe filenames", result.counts.get("unsafe_names", 0)),
            ("empty files", result.counts.get("empty_files", 0)),
            ("oversized files", result.counts.get("oversized", 0)),
            ("empty folders", result.counts.get("empty_folders", 0)),
            ("tiny images", result.counts.get("tiny_images", 0)),
            ("image health flags", result.counts.get("health_flags", 0)),
        ]
        self._logline("", t.TEXT_MAIN)
        for label, n in rows:
            color = t.STATE["waiting"][1] if n else t.TEXT_MUTED
            self._logline(f"  {n:>4}  {label}", color)
        if result.resolution_histogram:
            top = list(result.resolution_histogram.items())[:5]
            self._logline("  resolutions: " + ", ".join(f"{k}×{v}" for k, v in top), t.TEXT_MUTED)

        self._finish_queue_ui(
            f"{result.scanned} scanned · {issues} issue(s)",
            job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
