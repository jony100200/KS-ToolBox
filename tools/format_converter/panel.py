"""Format Converter — the tool's UI. Thin over engine.py. The one twist: the
"Convert to" targets depend on the kinds of files loaded, so the dropdown
refreshes whenever the file list changes.
"""
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

_ALL_TARGETS = sorted({tgt for (_k, tgt) in e.DISPATCH})


class FormatConverterPanel(BaseBatchPanel):
    FILE_EXTS = e.ALL_EXTS
    FILE_LABEL = "file"
    RESULTS_ICON = Icons.ARROW
    RUN_LABEL = "Preview & Convert"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Convert to", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._target = ctk.CTkOptionMenu(row, values=_ALL_TARGETS, width=140,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._target.set("png"); self._target.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        self._kinds_hint = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._kinds_hint.grid(row=1, column=1, sticky="w")
        # advanced (only relevant options apply per conversion; harmless otherwise)
        adv = ctk.CTkFrame(b, fg_color="transparent"); adv.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(adv, text="JPEG/WebP quality", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._quality = c.entry(adv, width=60); self._quality.insert(0, "90")
        self._quality.grid(row=1, column=0, sticky="w", padx=(0, 20), pady=(2, 0))
        ctk.CTkLabel(adv, text="GIF width / fps", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        gifrow = ctk.CTkFrame(adv, fg_color="transparent"); gifrow.grid(row=1, column=1, sticky="w", padx=(0, 20), pady=(2, 0))
        self._gifw = c.entry(gifrow, width=60); self._gifw.insert(0, "480"); self._gifw.pack(side="left")
        self._fps = c.entry(gifrow, width=50); self._fps.insert(0, "12"); self._fps.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(adv, text="PDF→image DPI", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._dpi = c.entry(adv, width=60); self._dpi.insert(0, "150")
        self._dpi.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./converted beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list conversions — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))
        self._refresh_tools_hint()

    def _refresh_tools_hint(self):
        have_ff = bool(e.resolve_tool("ffmpeg"))
        av = "ffmpeg ✓" if have_ff else "ffmpeg ✗ (audio/video needs it)"
        self._tools_hint.configure(text=f"images: Pillow · audio/video: {av} · docs: optional (see README)")

    def _render_files(self):
        """Base file rendering + refresh the target dropdown from the loaded kinds."""
        super()._render_files()
        kinds = sorted({e.source_kind(p.suffix) for p in self._files} - {""})
        if not kinds:
            self._kinds_hint.configure(text=""); return
        target_sets = [set(e.targets_for(kind)) for kind in kinds]
        common = set.intersection(*target_sets) if target_sets else set()
        targets = set.union(*target_sets) if target_sets else set()
        values = sorted(common or targets) or _ALL_TARGETS
        self._target.configure(values=values)
        if self._target.get() not in values:
            self._target.set(values[0])
        suffix = "" if common else " · no target fits every kind"
        self._kinds_hint.configure(text=f"loaded: {', '.join(kinds)}{suffix}")

    # -- option collection -----------------------------------------------------
    def _collect_options(self):
        try:
            quality = int(self._quality.get()); gifw = int(self._gifw.get())
            fps = int(self._fps.get()); dpi = int(self._dpi.get())
        except ValueError:
            self._logline("Quality / GIF / DPI must be integers.", t.STATE["error"][1]); return None
        if not 1 <= quality <= 100 or not 16 <= gifw <= 8192 or not 0 <= fps <= 240 or not 36 <= dpi <= 1200:
            self._logline(
                "Quality 1–100 · GIF width 16–8192 · FPS 0–240 · DPI 36–1200.",
                t.STATE["error"][1],
            )
            return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ConvertOptions(target=self._target.get(), out_root=out_root, input_root=input_root,
                                mirror=mirror, dry_run=bool(self._dry.get()), quality=quality,
                                gif_width=gifw, fps=fps, dpi=dpi)

    # -- queue submission ------------------------------------------------------

    def _pre_run_check(self, opts: e.ConvertOptions) -> bool:
        incompatible = [
            path.name for path in self._files
            if (e.source_kind(path.suffix), opts.target) not in e.DISPATCH
        ]
        if incompatible:
            shown = ", ".join(incompatible[:4])
            more = "" if len(incompatible) <= 4 else f" (+{len(incompatible) - 4} more)"
            self._logline(
                f"Target {opts.target} does not support: {shown}{more}. Split this mixed batch.",
                t.STATE["error"][1],
            )
            return False
        if opts.dry_run:
            return True
        return self._check_output_collisions(e.find_output_collisions(self._files, opts))

    def _build_submission(self, files: list[Path], opts: e.ConvertOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="format_converter",
            tool_version="1",
            workflow_version="multi-family-convert.v2",
            inputs=files,
            settings=asdict(opts),
            max_retries=1,
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "converted":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(
                res.reason, retryable=res.retryable, data=data
            )

        return QueueSubmission(
            definition=definition,
            label=f"Format Converter · {len(files)} file(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="format_converter"
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
        return e.Result(
            item.input_path,
            "failed",
            item.details or "conversion item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.ConvertOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "convert_manifest.csv"
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
            if res.detail:
                extra += f"  ({res.detail})"
            if res.action == "converted" and res.out_path:
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, converted, skipped, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"converted {converted} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
