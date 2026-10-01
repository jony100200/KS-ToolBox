"""Image Rescale UI; execution is submitted to the shell-owned job queue."""
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
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e

_MODE_LABELS = {
    "longest_side": "Longest side (px)",
    "max_mp": "Max megapixels",
    "scale_factor": "Scale factor",
    "fit_inside": "Fit inside (WxH)",
}


class ImageRescalePanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.EXPAND
    RUN_LABEL = "Resize / Upscale Selected"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._on_mode_change(self._mode.get())

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._mode = ctk.CTkOptionMenu(row, values=list(e.MODES), width=160, command=self._on_mode_change,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._mode.set("longest_side"); self._mode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        # the single value entry whose meaning depends on the mode
        self._val_label = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._val_label.grid(row=0, column=1, sticky="w")
        self._val = c.entry(row, width=100); self._val.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        # second entry (only for fit_inside height)
        self._val2_label = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._val2_label.grid(row=0, column=2, sticky="w")
        self._val2 = c.entry(row, width=100); self._val2.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # resample + snap
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row2, text="Resample", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._resample = ctk.CTkOptionMenu(row2, values=["auto", "lanczos", "bicubic", "bilinear", "nearest"],
                                           width=120, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._resample.set("auto"); self._resample.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row2, text="Snap to multiple of (0=off)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._snap = c.entry(row2, width=80); self._snap.insert(0, "0")
        self._snap.grid(row=1, column=1, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./resized beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.pack(side="left")
        self._upscale = ctk.CTkCheckBox(toggles, text="Allow upscaling", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._upscale.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        ai_row = ctk.CTkFrame(b, fg_color="transparent"); ai_row.pack(fill="x", pady=(8, 0))
        self._ai_upscale = ctk.CTkCheckBox(
            ai_row, text="AI upscale (Real-ESRGAN)", font=t.font(11),
            fg_color=t.ACCENT_BLUE, command=self._on_ai_change,
        )
        self._ai_upscale.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(ai_row, text="Model", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._ai_model = ctk.CTkOptionMenu(
            ai_row, values=list(e.ai_model_choices()), width=210,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER,
        )
        self._ai_model.set("realesrgan-x4plus")
        self._ai_model.pack(side="left", padx=(8, 16))
        self._ai_status = ctk.CTkLabel(ai_row, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._ai_status.pack(side="left")
        self._refresh_ai_status()
        # run row
        self._build_run_row(b)

    def _refresh_ai_status(self):
        status = e.ai_runtime_status()
        color = t.STATE["done"][1] if status["ready"] else t.STATE["error"][1]
        self._ai_status.configure(text=str(status["details"]), text_color=color)

    def _on_ai_change(self):
        if self._ai_upscale.get():
            self._upscale.select()
        self._refresh_ai_status()

    def _on_mode_change(self, mode: str):
        """The value fields relabel to match the chosen mode; height shows only for fit_inside."""
        defaults = {"longest_side": ("1024", ""), "max_mp": ("1.0", ""),
                    "scale_factor": ("0.5", ""), "fit_inside": ("1024", "1024")}
        self._val_label.configure(text=_MODE_LABELS[mode])
        self._val.delete(0, "end"); self._val.insert(0, defaults[mode][0])
        if mode == "fit_inside":
            self._val_label.configure(text="Fit width (px)")
            self._val2_label.configure(text="Fit height (px)")
            self._val2.delete(0, "end"); self._val2.insert(0, defaults[mode][1])
            self._val2.grid(); self._val2_label.grid()
        else:
            self._val2_label.configure(text=""); self._val2.grid_remove(); self._val2_label.grid_remove()

    def _collect_options(self):
        mode = self._mode.get()
        try:
            snap = int(self._snap.get() or "0")
            kw: dict = {}
            if mode == "longest_side":
                kw["longest_side"] = int(float(self._val.get()))
            elif mode == "max_mp":
                kw["max_mp"] = float(self._val.get())
            elif mode == "scale_factor":
                kw["scale_factor"] = float(self._val.get())
            elif mode == "fit_inside":
                kw["fit_w"] = int(float(self._val.get())); kw["fit_h"] = int(float(self._val2.get()))
        except ValueError:
            self._logline("Mode value(s) must be numbers.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        ai_upscale = bool(self._ai_upscale.get())
        if ai_upscale and not e.ai_runtime_status()["ready"]:
            self._logline("AI model is not ready: " + str(e.ai_runtime_status()["details"]), t.STATE["error"][1])
            return None
        return e.ResizeOptions(out_root=out_root, input_root=input_root, mirror=mirror, mode=mode,
                               allow_upscale=bool(self._upscale.get()) or ai_upscale, snap=snap,
                               resample=self._resample.get(), ai_upscale=ai_upscale,
                               ai_model=self._ai_model.get(), dry_run=bool(self._dry.get()), **kw)

    # -- queue submission (tool-specific) --------------------------------------
    def _build_submission(self, files: list[Path], opts: e.ResizeOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="image_rescale",
            tool_version="2",
            workflow_version="resize.v2",
            inputs=files,
            settings=asdict(opts),
            identity_dependencies=e.ai_runtime_dependencies(opts),
        )
        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "resized":
                return ItemOutcome.completed(data, res.reason)
            if res.action in ("skipped", "dry-run"):
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        def validate_stored(item: ItemRecord) -> bool:
            result = self._result_from_record(item)
            return e.validate_result(result)

        return QueueSubmission(
            definition=definition,
            label=f"Image Rescale · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts, cancelled=lambda: token.is_cancelled),
            classify=classify,
            validate_stored=validate_stored,
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="image_rescale"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        resized = sum(result.action == "resized" for result in results)
        skipped = sum(result.action in ("skipped", "dry-run") for result in results)
        failed = len(results) - resized - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            resized, skipped, failed, remaining, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined",
                        detail="batch.quarantined")

    def _write_manifest(self, opts: e.ResizeOptions, results: list) -> str | None:
        """CSV row per file — essential for auditing a 100s-of-images run."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "resize_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "before", "after", "out_path", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.before, r.after, r.out_path, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"resized": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"resized": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("resized", "dry-run"):
            extra = f"  {res.before} → {res.after}"
            if res.out_path and res.action == "resized":
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, resized, skipped, failed, remaining=0, manifest=None, report_path=None,
              job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"resized {resized} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
