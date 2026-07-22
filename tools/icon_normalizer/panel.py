"""Icon Normalizer UI; execution is submitted to the shell-owned job queue."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import (
    ItemOutcome,
    ItemRecord,
    JobDefinition,
    JobState,
)
from toolbox.batch_reporting import (
    completion_report_path,
    prepare_batch_completion,
)
from toolbox.job_queue import QueueCompletion, QueueFinalization, QueueSubmission
from . import engine as e


class IconNormalizerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Normalize"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Icon size (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._size = ctk.CTkOptionMenu(row, values=["64", "128", "256", "512"], width=90,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._size.set("256"); self._size.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Padding %", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._padding = c.entry(row, width=80); self._padding.insert(0, "0")
        self._padding.grid(row=1, column=1, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./normalized beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._trim = ctk.CTkCheckBox(toggles, text="Trim transparent margins", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._trim.select(); self._trim.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        try:
            padding = float(self._padding.get() or "0")
        except ValueError:
            self._logline("Padding % must be a number.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.NormalizeOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                                  size=int(self._size.get()), padding_pct=padding,
                                  trim=bool(self._trim.get()), dry_run=bool(self._dry.get()))

    # -- queue submission (tool-specific) --------------------------------------

    def _build_submission(self, files: list[Path], opts: e.NormalizeOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="icon_normalizer",
            tool_version="1",
            workflow_version="normalize.v1",
            inputs=files,
            settings=asdict(opts),
        )
        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "converted":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Icon Normalizer · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts),
            classify=classify,
            validate_stored=lambda item: e.validate_result(self._result_from_record(item)),
            finalize=lambda report: self._prepare_completion(report, opts),
        )

    def _prepare_completion(self, report, opts: e.NormalizeOptions) -> QueueFinalization:
        return prepare_batch_completion(
            report,
            result_from_record=self._result_from_record,
            write_manifest=lambda results: self._write_manifest(opts, results),
            report_path=completion_report_path(
                "icon_normalizer", report.job_id,
                out_root=opts.out_root, dry_run=opts.dry_run,
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        converted = sum(result.action == "converted" for result in results)
        skipped = sum(result.action in {"skipped", "dry-run"} for result in results)
        failed = len(results) - converted - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            converted, skipped, failed, remaining, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined",
                        detail="batch.quarantined")

    def _write_manifest(self, opts: e.NormalizeOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-images run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "icon_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "before", "after", "out_path", "detail", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.before, r.after, r.out_path, r.detail, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"converted": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("converted", "dry-run"):
            extra = f"  {res.before} → {res.after}"
            if res.out_path and res.action == "converted":
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _batch_failed(self, details: str):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._active_job_id = None
        self._status.set_state("FAILED", "error")
        self._logline(f"batch core failed: {details}", t.STATE["error"][1])

    def _done(self, converted, skipped, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._active_job_id = None
        if job_state is JobState.CANCELLED:
            self._status.set_state("CANCELLED", "waiting")
        elif job_state is JobState.COMPLETED_WITH_WARNINGS:
            self._status.set_state("WARNINGS", "waiting"); self._progress.set(1)
        elif job_state is JobState.FAILED:
            self._status.set_state("FAILED", "error"); self._progress.set(1)
        else:
            self._status.set_state("DONE", "done"); self._progress.set(1)
        summary = f"normalized {converted} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._summary.configure(text=summary)
        if recovered:
            self._logline("  recovered unfinished work from the previous run", t.TEXT_MUTED)
        if reused:
            self._logline("  reused valid outputs and prior item decisions", t.TEXT_MUTED)
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)
        if report_path:
            self._logline(f"  completion report: {report_path}", t.TEXT_MUTED)
