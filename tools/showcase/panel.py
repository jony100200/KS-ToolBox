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
from toolbox.job_queue import QueueCompletion, QueueSubmission
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
        self._build_spritesheet_frame()

        library_row = ctk.CTkFrame(b, fg_color="transparent")
        library_row.pack(fill="x", pady=(12, 0))
        c.secondary_button(
            library_row,
            "Load presentation library",
            self._load_presentation_library,
            width=190,
        ).pack(side="left")
        ctk.CTkLabel(
            library_row,
            text="Loads Hero Renders and prepares paginated 2×2 sheets.",
            text_color=t.TEXT_MUTED,
            font=t.font(11),
        ).pack(side="left", padx=10)

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

    def _build_spritesheet_frame(self):
        f = ctk.CTkFrame(self._mode_box, fg_color="transparent")

        # Row 1: Grid preset + Dimensions
        row1 = ctk.CTkFrame(f, fg_color="transparent"); row1.pack(fill="x")
        ctk.CTkLabel(row1, text="Grid preset", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._grid_preset = ctk.CTkOptionMenu(
            row1, values=list(e.GRID_PRESETS), width=130, command=self._on_grid_preset_change,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._grid_preset.set("4x4")
        self._grid_preset.grid(row=1, column=0, sticky="w", padx=(0, 20), pady=(2, 0))

        ctk.CTkLabel(row1, text="Columns", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._sheet_cols = c.entry(row1, width=65); self._sheet_cols.insert(0, "4")
        self._sheet_cols.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(row1, text="Rows", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._sheet_rows = c.entry(row1, width=65); self._sheet_rows.insert(0, "4")
        self._sheet_rows.grid(row=1, column=2, sticky="w", padx=(0, 16), pady=(2, 0))

        # Row 2: Backdrop style + Color + Sheet Backdrop
        row2 = ctk.CTkFrame(f, fg_color="transparent"); row2.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row2, text="Icon backdrop", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._icon_bg_style = ctk.CTkOptionMenu(
            row2, values=list(e.BG_STYLES), width=130,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._icon_bg_style.set("solid"); self._icon_bg_style.grid(row=1, column=0, sticky="w", padx=(0, 20), pady=(2, 0))

        ctk.CTkLabel(row2, text="Backdrop colour (hex)", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._icon_bg_color = c.entry(row2, width=110); self._icon_bg_color.insert(0, "#111827")
        self._icon_bg_color.grid(row=1, column=1, sticky="w", padx=(0, 20), pady=(2, 0))

        ctk.CTkLabel(row2, text="Sheet backdrop", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._sheet_bg_style = ctk.CTkOptionMenu(
            row2, values=list(e.SHEET_BG_STYLES), width=130,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._sheet_bg_style.set("transparent"); self._sheet_bg_style.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # Row 3: Shadow & Repeat toggles
        row3 = ctk.CTkFrame(f, fg_color="transparent"); row3.pack(fill="x", pady=(8, 0))
        self._sheet_shadow = ctk.CTkCheckBox(row3, text="Drop shadow on icon", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._sheet_shadow.select(); self._sheet_shadow.pack(side="left")

        self._sheet_repeat = ctk.CTkCheckBox(row3, text="Tile single input across grid", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._sheet_repeat.select(); self._sheet_repeat.pack(side="left", padx=20)

        self._paginate_sheet = ctk.CTkCheckBox(
            row3,
            text="Paginate all inputs",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._paginate_sheet.pack(side="left")

        # Output Selection Box: User can export ONE or BOTH
        out_box = ctk.CTkFrame(f, fg_color=t.BG_COLOR, corner_radius=6); out_box.pack(fill="x", pady=(10, 0))
        out_pad = ctk.CTkFrame(out_box, fg_color="transparent"); out_pad.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(out_pad, text="Outputs (choose one or both):", text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")

        out_checks = ctk.CTkFrame(out_pad, fg_color="transparent"); out_checks.pack(fill="x", pady=(4, 0))
        self._export_sheet = ctk.CTkCheckBox(out_checks, text="Spritesheet / Grid presentation sheet (PNG)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._export_sheet.select(); self._export_sheet.pack(side="left")

        self._export_icons = ctk.CTkCheckBox(out_checks, text="Individual framed icons (PNG)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._export_icons.select(); self._export_icons.pack(side="left", padx=20)

        self._spritesheet_frame = f

    def _on_grid_preset_change(self, preset: str):
        if preset == "4x4":
            self._sheet_cols.delete(0, "end"); self._sheet_cols.insert(0, "4")
            self._sheet_rows.delete(0, "end"); self._sheet_rows.insert(0, "4")
        elif preset == "3x3":
            self._sheet_cols.delete(0, "end"); self._sheet_cols.insert(0, "3")
            self._sheet_rows.delete(0, "end"); self._sheet_rows.insert(0, "3")
        elif preset == "2x2":
            self._sheet_cols.delete(0, "end"); self._sheet_cols.insert(0, "2")
            self._sheet_rows.delete(0, "end"); self._sheet_rows.insert(0, "2")

    def _pick_ba(self):
        d = filedialog.askdirectory(title="Choose the counterpart (after) folder")
        if d:
            self._ba_entry.delete(0, "end"); self._ba_entry.insert(0, d)

    def _load_presentation_library(self):
        selected = filedialog.askdirectory(title="Choose a volume presentation library")
        if not selected:
            return
        try:
            library = e.discover_presentation_library(selected)
        except ValueError as error:
            self._logline(str(error), t.STATE["error"][1])
            return
        self._clear()
        self._add(list(library["heroes"]))
        self._mode.set("spritesheet")
        self._on_mode_change("spritesheet")
        self._grid_preset.set("2x2")
        self._on_grid_preset_change("2x2")
        self._cell.delete(0, "end"); self._cell.insert(0, "512")
        self._export_sheet.select()
        self._export_icons.deselect()
        self._paginate_sheet.select()
        self._out_entry.delete(0, "end")
        self._out_entry.insert(0, str(library["sheets_dir"]))
        self._logline(
            f"Loaded {library['hero_count']} hero renders · "
            f"{library['gif_count']} GIF · {library['mp4_count']} MP4",
            t.STATE["done"][1],
        )

    def _on_mode_change(self, mode: str):
        """Show only the active mode's sub-options; contact ignores the shared cell
        as a per-thumbnail box, hero/ba use cell as the canvas/panel size."""
        for frame in (self._contact_frame, self._hero_frame, self._ba_frame, self._spritesheet_frame):
            frame.pack_forget()
        if mode == "hero":
            self._hero_frame.pack(fill="x")
        elif mode == "before_after":
            self._ba_frame.pack(fill="x")
        elif mode == "spritesheet":
            self._spritesheet_frame.pack(fill="x")
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

        rows = cols
        grid_preset = "4x4"
        export_sheet = True
        export_icons = True
        sheet_bg_style = "transparent"
        repeat_single = True
        paginate_sheet = False
        bg_style = self._bg_style.get()
        shadow = bool(self._shadow.get())

        if mode == "spritesheet":
            grid_preset = self._grid_preset.get()
            try:
                cols = max(1, int(float(self._sheet_cols.get() or "4")))
                rows = max(1, int(float(self._sheet_rows.get() or "4")))
            except ValueError:
                self._logline("Spritesheet columns and rows must be integers.", t.STATE["error"][1]); return None
            bg_style = self._icon_bg_style.get()
            icon_hex = self._parse_hex(self._icon_bg_color.get())
            if icon_hex is None:
                self._logline("Icon backdrop colour must be a hex like #111827.", t.STATE["error"][1]); return None
            bg_color = icon_hex
            sheet_bg_style = self._sheet_bg_style.get()
            shadow = bool(self._sheet_shadow.get())
            repeat_single = bool(self._sheet_repeat.get())
            paginate_sheet = bool(self._paginate_sheet.get())
            export_sheet = bool(self._export_sheet.get())
            export_icons = bool(self._export_icons.get())
            if not export_sheet and not export_icons:
                self._logline("Select at least one deliverable: Spritesheet or Individual Icons.", t.STATE["error"][1]); return None

        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ShowcaseOptions(
            mode=mode, cols=cols, rows=rows, grid_preset=grid_preset,
            cell_size=cell, padding=padding,
            bg_style=bg_style, bg_color=bg_color,
            caption=self._caption.get().strip(), watermark=bool(self._watermark.get()),
            shadow=shadow, labels=bool(self._labels.get()),
            title=self._title.get().strip(), ba_folder=ba_folder,
            export_sheet=export_sheet, export_icons=export_icons,
            sheet_bg_style=sheet_bg_style, repeat_single=repeat_single,
            paginate_sheet=paginate_sheet,
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
        if opts.mode == "spritesheet":
            anchors = [files[0]]
            identity_dependencies = files
            execute = lambda anchor, token: e.build_spritesheet(files, opts)
            label = f"Showcase Spritesheet ({opts.cols}x{opts.rows}) · {len(files)} image(s)"
        elif opts.mode == "contact":
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
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="showcase"
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
