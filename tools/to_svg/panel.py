"""To SVG — the tool's UI. Thin over engine.py via BaseBatchPanel: collect
images + vtracer options, run on a worker thread, stream results back. No
vectorize logic here."""
from __future__ import annotations

import csv
import importlib.util
import os
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.engine_common import find_output_collisions
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class ToSvgPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.EXPAND
    RUN_LABEL = "Preview & Vectorize"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Color mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._colormode = ctk.CTkOptionMenu(row, values=list(e.COLORMODES), width=100,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._colormode.set("color"); self._colormode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Filter speckle (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._speckle = c.entry(row, width=80); self._speckle.insert(0, "4")
        self._speckle.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Color precision", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._precision = ctk.CTkOptionMenu(row, values=["4", "5", "6", "7", "8"], width=70,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._precision.set("6"); self._precision.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./svg beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)

    def _collect_options(self):
        try:
            speckle = int(self._speckle.get() or "4")
        except ValueError:
            self._logline("Filter speckle must be a number.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.SvgOptions(out_root=out_root, input_root=input_root, mirror=mirror,
                            colormode=self._colormode.get(), filter_speckle=speckle,
                            color_precision=int(self._precision.get()),
                            dry_run=bool(self._dry.get()))

    def _pre_run_check(self, opts: e.SvgOptions) -> bool:
        normalized, options_error = e.normalized_options(opts)
        if normalized is None:
            self._logline(options_error, t.STATE["error"][1])
            return False
        collisions = find_output_collisions(
            self._files, lambda source: [e.plan_output(source, normalized)]
        )
        if not self._check_output_collisions(collisions):
            return False
        if normalized.dry_run:
            return True
        if importlib.util.find_spec("vtracer") is None:
            self._logline(
                "vtracer is not installed — install the optional vtracer package.",
                t.STATE["error"][1],
            )
            return False
        existing = sum(
            e.plan_output(source, normalized).exists() for source in self._files
        )
        return messagebox.askyesno(
            "Confirm SVG batch",
            f"{len(self._files)} image(s) will be vectorized.\n"
            f"{existing} existing SVG file(s) may be replaced atomically.\n\n"
            "Continue?",
            icon="warning",
            parent=self,
        )

    # -- batch loop (tool-specific) --------------------------------------------

    def _work(self, files: list[Path], opts: e.SvgOptions):
        converted = failed = skipped = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "converted":
                converted += 1
            elif res.action == "dry-run":
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, converted, skipped, failed, manifest)

    def _build_submission(
        self, files: list[Path], opts: e.SvgOptions
    ) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="to_svg",
            tool_version="1",
            workflow_version="vtracer-svg.v1",
            inputs=files,
            settings=asdict(opts),
        )

        def classify(result: e.Result) -> ItemOutcome:
            data = result.to_dict()
            if result.action == "converted":
                return ItemOutcome.completed(data, result.reason)
            if result.action == "dry-run":
                return ItemOutcome.skipped(data, result.reason)
            return ItemOutcome.failed(result.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"To SVG · {len(files)} image(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts,
                expected_src=item.input_path,
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="to_svg"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        converted = sum(result.action == "converted" for result in results)
        skipped = sum(result.action == "dry-run" for result in results)
        failed = len(results) - converted - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            converted, skipped, failed, payload.manifest, remaining,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            item.input_path, "failed",
            item.details or "SVG item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.SvgOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        path = root / "svg_manifest.csv"
        candidate = root / "svg_manifest.part.csv"
        try:
            root.mkdir(parents=True, exist_ok=True)
            with open(candidate, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow([
                    "src", "action", "out_path", "bytes", "sha256",
                    "elements", "paths", "detail", "reason",
                ])
                for r in results:
                    artifact = r.artifact or {}
                    w.writerow([
                        r.src, r.action, r.out_path,
                        artifact.get("bytes", ""), artifact.get("sha256", ""),
                        artifact.get("elements", ""), artifact.get("paths", ""),
                        r.detail, r.reason,
                    ])
                f.flush()
                os.fsync(f.fileno())
            candidate.replace(path)
        except BaseException as ex:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup:
                raise OSError(
                    f"SVG manifest failed and staged cleanup failed: {cleanup}"
                ) from ex
            raise
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"converted": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        extra = f"  → {res.out_path}" if res.action == "converted" else f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, converted, skipped, failed, manifest=None, remaining=0,
              report_path=None, job_state=JobState.COMPLETED,
              recovered=False, reused=False):
        summary = f"vectorized {converted} · previewed {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
