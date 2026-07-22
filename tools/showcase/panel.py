"""Showcase UI; presentation renders execute through the shell-owned queue."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_reporting import completion_report_path, prepare_batch_completion
from toolbox.job_queue import QueueCompletion, QueueFinalization, QueueSubmission
from . import engine as e


class ShowcasePanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.GRID
    RUN_LABEL = "Preview & Render"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._on_mode_change(self._mode.get())

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # common row: mode + cell + padding (apply to every mode)
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._mode = ctk.CTkOptionMenu(row, values=list(e.MODES), width=150, command=self._on_mode_change,
                                       fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._mode.set("contact"); self._mode.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Cell size (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._cell = c.entry(row, width=90); self._cell.insert(0, "256")
        self._cell.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Padding (px)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._padding = c.entry(row, width=90); self._padding.insert(0, "16")
        self._padding.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # per-mode sub-options live in one box; only the active mode's frame shows
        self._mode_box = ctk.CTkFrame(b, fg_color="transparent"); self._mode_box.pack(fill="x", pady=(10, 0))
        self._build_contact_frame()
        self._build_hero_frame()
        self._build_ba_frame()

        self._build_output_row(b, "Output folder (blank = ./showcase beside sources)")

        # shared toggle
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list outputs — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.pack(side="left", padx=20)

        self._build_run_row(b)

    def _build_contact_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        row = ctk.CTkFrame(f, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Columns", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._cols = c.entry(row, width=70); self._cols.insert(0, "4")
        self._cols.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Title header (optional)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._title = c.entry(row, width=240)
        self._title.grid(row=1, column=1, sticky="w", pady=(2, 0))
        self._labels = ctk.CTkCheckBox(f, text="Filename labels under thumbnails", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._labels.select(); self._labels.pack(anchor="w", pady=(8, 0))
        self._contact_frame = f

    def _build_hero_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        row = ctk.CTkFrame(f, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Backdrop", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._bg_style = ctk.CTkOptionMenu(row, values=list(e.BG_STYLES), width=130,
                                           fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._bg_style.set("solid"); self._bg_style.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Backdrop colour (hex)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._bg_color = c.entry(row, width=110); self._bg_color.insert(0, "#111827")
        self._bg_color.grid(row=1, column=1, sticky="w", pady=(2, 0))
        row2 = ctk.CTkFrame(f, fg_color="transparent"); row2.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row2, text="Caption (optional)", text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        self._caption = c.entry(row2); self._caption.pack(fill="x", pady=(2, 0))
        toggles = ctk.CTkFrame(f, fg_color="transparent"); toggles.pack(fill="x", pady=(8, 0))
        self._shadow = ctk.CTkCheckBox(toggles, text="Drop shadow", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._shadow.select(); self._shadow.pack(side="left")
        self._watermark = ctk.CTkCheckBox(toggles, text="\"Made with KS ToolBox\" watermark", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._watermark.select(); self._watermark.pack(side="left", padx=20)
        self._hero_frame = f

    def _build_ba_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")
        ctk.CTkLabel(f, text="Second folder (counterparts, matched by filename)",
                     text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        pick = ctk.CTkFrame(f, fg_color="transparent"); pick.pack(fill="x", pady=(2, 0))
        self._ba_entry = c.entry(pick); self._ba_entry.pack(side="left", fill="x", expand=True)
        c.secondary_button(pick, "Browse", self._pick_ba, width=90).pack(side="left", padx=(8, 0))
        self._ba_frame = f

    def _pick_ba(self):
        d = filedialog.askdirectory(title="Choose the counterpart (after) folder")
        if d:
            self._ba_entry.delete(0, "end"); self._ba_entry.insert(0, d)

    def _on_mode_change(self, mode: str):
        """Show only the active mode's sub-options; contact ignores the shared cell
        as a per-thumbnail box, hero/ba use cell as the canvas/panel size."""
        for frame in (self._contact_frame, self._hero_frame, self._ba_frame):
            frame.pack_forget()
        if mode == "hero":
            self._hero_frame.pack(fill="x")
        elif mode == "before_after":
            self._ba_frame.pack(fill="x")
        else:
            self._contact_frame.pack(fill="x")

    def _collect_options(self):
        mode = self._mode.get()
        try:
            cell = int(float(self._cell.get()))
            padding = int(float(self._padding.get()))
            cols = int(float(self._cols.get() or "4"))
        except ValueError:
            self._logline("Cell size, padding and columns must be numbers.", t.STATE["error"][1]); return None
        bg_color = self._parse_hex(self._bg_color.get())
        if bg_color is None:
            self._logline("Backdrop colour must be a hex like #111827.", t.STATE["error"][1]); return None

        ba_folder = None
        if mode == "before_after":
            ba = self._ba_entry.get().strip()
            if not ba:
                self._logline("Before/After needs a second folder of counterparts.", t.STATE["error"][1]); return None
            ba_folder = Path(ba)

        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ShowcaseOptions(
            mode=mode, cols=cols, cell_size=cell, padding=padding,
            bg_style=self._bg_style.get(), bg_color=bg_color,
            caption=self._caption.get().strip(), watermark=bool(self._watermark.get()),
            shadow=bool(self._shadow.get()), labels=bool(self._labels.get()),
            title=self._title.get().strip(), ba_folder=ba_folder,
            out_root=out_root, input_root=input_root, mirror=mirror,
            dry_run=bool(self._dry.get()))

    @staticmethod
    def _parse_hex(text: str):
        s = text.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return None
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None

    # -- queue submission (mode-specific work-unit granularity) ----------------

    def _build_submission(self, files: list[Path], opts: e.ShowcaseOptions) -> QueueSubmission:
        if opts.mode == "contact":
            anchors = [files[0]]
            identity_dependencies = files
            execute = lambda anchor, token: e.build_contact_sheet(files, opts)
            label = f"Showcase Contact Sheet · {len(files)} image(s)"
        else:
            anchors = files
            identity_dependencies = None
            if opts.mode == "before_after":
                identity_dependencies = list(files)
                for source in files:
                    identity_dependencies.extend(e.partner_candidates(source, opts))
            execute = lambda path, token: e.process(path, opts)
            label = f"Showcase {opts.mode.replace('_', ' ').title()} · {len(files)} image(s)"

        definition = JobDefinition.create(
            tool_id="showcase",
            tool_version="1",
            workflow_version="showcase.v2",
            inputs=anchors,
            identity_dependencies=identity_dependencies,
            settings=asdict(opts),
        )

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "rendered":
                if res.detail == "degraded":
                    return ItemOutcome.warning(data, res.reason)
                return ItemOutcome.completed(data, res.reason)
            if res.action == "dry-run":
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=label,
            execute=execute,
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_completion(report, opts),
        )

    def _prepare_completion(self, report, opts: e.ShowcaseOptions) -> QueueFinalization:
        return prepare_batch_completion(
            report,
            result_from_record=self._result_from_record,
            write_manifest=lambda results: self._write_manifest(opts, results),
            report_path=completion_report_path(
                "showcase", report.job_id,
                out_root=opts.out_root, dry_run=opts.dry_run,
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        rendered = sum(result.action == "rendered" for result in results)
        previewed = sum(result.action == "dry-run" for result in results)
        failed = len(results) - rendered - previewed
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            rendered, previewed, failed, remaining, payload.manifest,
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
            item.details or "showcase item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.ShowcaseOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "showcase_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["src", "action", "out_path", "detail", "reason"])
            for r in results:
                w.writerow([r.src, r.action, r.out_path, r.detail, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"rendered": "✓", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"rendered": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        extra = f"  → {res.out_path}" if res.action == "rendered" else f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, rendered, previewed, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"rendered {rendered} · previewed {previewed} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
