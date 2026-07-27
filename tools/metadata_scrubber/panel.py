"""Metadata Scrubber UI; execution is submitted to the shell-owned job queue."""
from __future__ import annotations

import csv
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class MetadataScrubberPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BROOM
    RUN_LABEL = "Scrub Selected"

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        ctk.CTkLabel(
            b,
            text=("Copies each image to the output folder with EXIF, XMP and AI-recipe metadata "
                  "removed.\nSources are opened read-only and never modified."),
            text_color=t.TEXT_MUTED, font=t.font(11), justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._build_output_row(b, "Output folder (blank = ./clean beside each source)")

        toggles = ctk.CTkFrame(b, fg_color="transparent")
        toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (no writes)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select()          # destructive-by-default is wrong; preview first
        self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select()
        self._mirror.pack(side="left", padx=20)

        toggles2 = ctk.CTkFrame(b, fg_color="transparent")
        toggles2.pack(fill="x", pady=(8, 0))
        self._keep_icc = ctk.CTkCheckBox(toggles2, text="Keep colour profile (ICC)",
                                         font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._keep_icc.select()
        self._keep_icc.pack(side="left")
        self._copy_non_images = ctk.CTkCheckBox(toggles2, text="Copy non-image files through",
                                                font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._copy_non_images.select()
        self._copy_non_images.pack(side="left", padx=20)

        self._build_run_row(b)

    def _collect_options(self):
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ScrubOptions(
            out_root=out_root,
            input_root=input_root,
            mirror=mirror,
            keep_icc=bool(self._keep_icc.get()),
            copy_non_images=bool(self._copy_non_images.get()),
            dry_run=bool(self._dry.get()),
        )

    # -- queue submission (tool-specific) --------------------------------------
    def _build_submission(self, files: list[Path], opts: e.ScrubOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="metadata_scrubber",
            tool_version="1",
            workflow_version="scrub.v1",
            inputs=files,
            settings=opts.to_dict(),
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action in ("scrubbed", "already-clean", "copied"):
                return ItemOutcome.completed(data, res.reason)
            if res.action in ("skipped", "dry-run"):
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        def validate_stored(item: ItemRecord) -> bool:
            return e.validate_result(self._result_from_record(item))

        return QueueSubmission(
            definition=definition,
            label=f"Metadata Scrubber · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts,
                                                  cancelled=lambda: token.is_cancelled),
            classify=classify,
            validate_stored=validate_stored,
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="metadata_scrubber"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        scrubbed = sum(r.action == "scrubbed" for r in results)
        clean = sum(r.action in ("already-clean", "copied") for r in results)
        skipped = sum(r.action in ("skipped", "dry-run") for r in results)
        failed = len(results) - scrubbed - clean - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(scrubbed, clean, skipped, failed, remaining, payload.manifest,
                   payload.report_path, self._queue_completion_state(completion),
                   report.recovered, report.reused)

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined",
                        detail="batch.quarantined")

    def _write_manifest(self, opts: e.ScrubOptions, results: list) -> str | None:
        """CSV row per file — the audit trail for what left the building."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "scrub_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["source", "action", "removed_keys", "output", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.found, r.out_path or "", r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"scrubbed": "✓", "already-clean": "○", "copied": "→",
                "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"scrubbed": t.STATE["done"][1],
                 "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        extra = f"  — {res.reason}"
        if res.action == "scrubbed" and res.out_path:
            extra += f"  → {res.out_path}"
        self._logline(f"  {icon} {Path(res.src).name}{extra}", color)

    def _done(self, scrubbed, clean, skipped, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = (f"scrubbed {scrubbed} · already clean {clean} · "
                   f"skipped {skipped} · failed {failed}")
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
