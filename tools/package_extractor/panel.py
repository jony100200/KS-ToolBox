"""Package Extractor UI; secure archive work uses the shell-owned queue."""
from __future__ import annotations

import csv
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


class PackageExtractorPanel(BaseBatchPanel):
    FILE_EXTS = e.ARCHIVE_EXTS
    FILE_LABEL = "archive"
    RESULTS_ICON = Icons.FOLDER
    RUN_LABEL = "Preview & Extract"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # output folder (required) — each archive lands in its own <stem>/ here
        self._build_output_row(b, "Output folder (required — each archive extracts into its own subfolder)")

        # extension filter + collision policy
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(row, text="Only extract these extensions (blank = all, e.g. .png, .fbx)",
                     text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w", columnspan=2)
        self._filter = c.entry(row, width=260)
        self._filter.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="On name clash", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._collision = ctk.CTkOptionMenu(row, values=["rename", "skip"], width=120,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                            button_hover_color=t.NEUTRAL_HOVER)
        self._collision.set("rename")
        self._collision.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list contents — no writes)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._nested = ctk.CTkCheckBox(toggles, text="Also extract nested archives (bounded)",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._nested.pack(side="left", padx=20)

        # run row
        self._build_run_row(b)

    def _collect_options(self):
        out = self._out_entry.get().strip()
        if not out:
            self._logline("Output folder is required.", t.STATE["error"][1]); return None
        return e.ExtractOptions(
            out_root=Path(out),
            filter_exts=e.norm_exts(self._filter.get()),
            collision=self._collision.get(),
            nested_depth=(2 if self._nested.get() else 0),
            dry_run=bool(self._dry.get()),
        )

    # -- queue submission (tool-specific) --------------------------------------

    def _pre_run_check(self, opts: e.ExtractOptions) -> bool:
        if opts.dry_run:
            return True
        return self._check_output_collisions(e.find_output_collisions(self._files, opts))

    def _build_submission(self, files: list[Path], opts: e.ExtractOptions) -> QueueSubmission:
        settings = asdict(opts)
        settings["filter_exts"] = sorted(opts.filter_exts)
        definition = JobDefinition.create(
            tool_id="package_extractor",
            tool_version="1",
            workflow_version="secure-extract.v2",
            inputs=files,
            settings=settings,
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "extracted":
                if res.degraded:
                    return ItemOutcome.warning(data, res.reason)
                return ItemOutcome.completed(data, res.reason)
            if res.action == "dry-run":
                if res.degraded:
                    return ItemOutcome.warning(data, res.reason)
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Package Extractor · {len(files)} archive(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="package_extractor"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        extracted = sum(result.action == "extracted" for result in results)
        previewed = sum(result.action == "dry-run" for result in results)
        failed = len(results) - extracted - previewed
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            extracted, previewed, failed, remaining, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            item.input_path,
            "failed",
            item.details or "archive item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.ExtractOptions, results: list) -> str | None:
        """Top-level CSV summarising every archive in the run (per-archive JSON+CSV
        reports are written by the engine into each archive's folder)."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "extract_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "written", "skipped", "rejected",
                            "errors", "out_path", "report", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.written, r.skipped, r.rejected,
                            r.errors, r.out_path, r.report_path, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"extracted": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"extracted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        if res.degraded:
            icon = "⚠"
            color = t.STATE["error"][1]
        name = Path(res.src).name
        self._logline(f"  {icon} {name}  — {res.reason}", color)
        if res.rejected:
            self._logline(
                f"      ⚠ {res.rejected} unsafe entr(y/ies) rejected (see report)",
                t.STATE["error"][1],
            )
        if res.errors:
            self._logline(
                f"      ⚠ {res.errors} extraction error(s) recorded (see report)",
                t.STATE["error"][1],
            )
        if res.report_path:
            self._logline(f"      report: {res.report_path}", t.TEXT_MUTED)

    def _done(self, extracted, previewed, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"extracted {extracted} · preview {previewed} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
