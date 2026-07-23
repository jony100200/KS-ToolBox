"""Texture Renderer — the tool's UI. Thin over engine.py.

A *custom* panel (not BaseBatchPanel): this tool is directory + engine-exe based
with two sub-modes, so it uses a CTkTabview ("Substance" / "Material Maker"),
each tab carrying its own engine/input/output pickers and options, over a shared
console + progress bar + Start/Stop. All rendering runs on a worker thread — the
UI loop is never blocked. No render logic lives here (that's engine.py).

Nothing is persisted to disk (no settings.json).
"""
from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e


class TextureRendererPanel(ctk.CTkFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color=t.BG_COLOR)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)   # console stretches

        # --- tabview -----------------------------------------------------------
        self._tabs = ctk.CTkTabview(
            self, fg_color=t.CARD_BG, corner_radius=t.RADIUS_CARD,
            segmented_button_selected_color=t.ACCENT_BLUE,
            segmented_button_selected_hover_color=t.ACCENT_HOVER,
            segmented_button_unselected_color=t.BG_COLOR,
            segmented_button_unselected_hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN)
        self._tabs.grid(row=0, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        self._tab_sbs = self._tabs.add("Substance (.sbsar)")
        self._tab_mm = self._tabs.add("Material Maker (.ptex)")
        self._build_substance_tab(self._tab_sbs)
        self._build_material_maker_tab(self._tab_mm)

        # --- shared console ----------------------------------------------------
        console = c.Card(self, "Console", icon=Icons.CHART)
        console.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        console.body.grid_rowconfigure(0, weight=1)
        console.body.grid_columnconfigure(0, weight=1)
        self._log = ctk.CTkTextbox(console.body, fg_color=t.BG_COLOR, border_color=t.CARD_BORDER,
                                   border_width=1, font=t.mono(11), text_color=t.TEXT_MAIN)
        self._log.grid(row=0, column=0, sticky="nsew"); self._log.configure(state="disabled")
        self._summary = ctk.CTkLabel(console.body, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._summary.grid(row=1, column=0, sticky="w", pady=(8, 0))

        # --- shared footer: progress + run/stop -------------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=t.PAD_GRID, pady=(0, t.PAD_GRID))
        footer.grid_columnconfigure(0, weight=1)
        self._progress = ctk.CTkProgressBar(footer, height=7, fg_color=t.CARD_BORDER,
                                            progress_color=t.ACCENT_BLUE)
        self._progress.set(0); self._progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self._run_btn = c.primary_button(footer, "Start Batch Render", self._start, width=180)
        self._run_btn.grid(row=0, column=1)
        self._stop_btn = c.danger_button(footer, "Stop", self._request_stop, width=90)
        self._stop_btn.grid(row=0, column=2, padx=(10, 0)); self._stop_btn.configure(state="disabled")
        self._status = c.Pill(footer, "IDLE", "idle"); self._status.grid(row=0, column=3, padx=(10, 0))

        self._logline("System ready.", t.TEXT_MUTED)

    # -- tab builders ----------------------------------------------------------

    def _path_row(self, tab, row: int, label: str, browse):
        """One label + entry + Browse row; returns the entry."""
        ctk.CTkLabel(tab, text=label, text_color=t.TEXT_MAIN, font=t.font(12)
                     ).grid(row=row, column=0, padx=(t.PAD_CARD, 10), pady=8, sticky="w")
        ent = c.entry(tab)
        ent.grid(row=row, column=1, padx=(0, 10), pady=8, sticky="ew")
        c.secondary_button(tab, "Browse", lambda: browse(ent), width=90
                           ).grid(row=row, column=2, padx=(0, t.PAD_CARD), pady=8)
        return ent

    def _build_substance_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._sbs_engine = self._path_row(tab, 0, "sbsrender path:", self._browse_file)
        self._sbs_input = self._path_row(tab, 1, "Input dir (.sbsar):",
                                         lambda ent: self._browse_dir(ent, self._sbs_output))
        self._sbs_output = self._path_row(tab, 2, "Output dir:", lambda ent: self._browse_dir(ent, None))

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=3, column=0, columnspan=3, padx=t.PAD_CARD, pady=(6, t.PAD_CARD), sticky="w")
        ctk.CTkLabel(opts, text="Resolution", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 8))
        self._sbs_res = ctk.CTkOptionMenu(opts, values=["512x512", "1024x1024", "2048x2048", "4096x4096"],
                                          width=120, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                          button_hover_color=t.NEUTRAL_HOVER)
        self._sbs_res.set("1024x1024"); self._sbs_res.pack(side="left", padx=(0, 20))
        self._sbs_group = ctk.CTkCheckBox(opts, text="Group into subfolders", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._sbs_group.select(); self._sbs_group.pack(side="left", padx=(0, 16))
        self._sbs_rec = ctk.CTkCheckBox(opts, text="Recursive", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._sbs_rec.select(); self._sbs_rec.pack(side="left", padx=(0, 16))
        self._sbs_dry = ctk.CTkCheckBox(opts, text="Dry run (preview commands)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._sbs_dry.pack(side="left")

    def _build_material_maker_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        self._mm_engine = self._path_row(tab, 0, "material_maker path:", self._browse_file)
        self._mm_input = self._path_row(tab, 1, "Input dir (.ptex):",
                                        lambda ent: self._browse_dir(ent, self._mm_output))
        self._mm_output = self._path_row(tab, 2, "Output dir:", lambda ent: self._browse_dir(ent, None))

        opts = ctk.CTkFrame(tab, fg_color="transparent")
        opts.grid(row=3, column=0, columnspan=3, padx=t.PAD_CARD, pady=(6, t.PAD_CARD), sticky="w")
        ctk.CTkLabel(opts, text="Target", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 8))
        self._mm_target = ctk.CTkOptionMenu(opts, values=["Unreal", "Godot", "Unity", "Blender"], width=100,
                                            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                            button_hover_color=t.NEUTRAL_HOVER)
        self._mm_target.set("Unreal"); self._mm_target.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(opts, text="Resize", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 8))
        self._mm_res = ctk.CTkOptionMenu(opts, values=["Original", "512x512", "1024x1024", "2048x2048", "4096x4096"],
                                         width=110, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                         button_hover_color=t.NEUTRAL_HOVER)
        self._mm_res.set("Original"); self._mm_res.pack(side="left", padx=(0, 20))
        self._mm_group = ctk.CTkCheckBox(opts, text="Group into subfolders", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mm_group.select(); self._mm_group.pack(side="left", padx=(0, 16))
        self._mm_rec = ctk.CTkCheckBox(opts, text="Recursive", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mm_rec.select(); self._mm_rec.pack(side="left", padx=(0, 16))
        self._mm_dry = ctk.CTkCheckBox(opts, text="Dry run (preview commands)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mm_dry.pack(side="left")

    # -- browse helpers --------------------------------------------------------

    def _browse_file(self, entry):
        # No filetype filter forced to *.exe — cross-platform (Linux/macOS engines
        # have no extension). validate_engine checks the name for us.
        path = filedialog.askopenfilename(title="Select engine executable", parent=self)
        if path:
            entry.delete(0, "end"); entry.insert(0, path)

    def _browse_dir(self, entry, autofill):
        path = filedialog.askdirectory(title="Select directory", parent=self)
        if not path:
            return
        entry.delete(0, "end"); entry.insert(0, path)
        # Suggest an output dir next to the chosen input, if output is still blank.
        if autofill is not None and not autofill.get().strip():
            suggested = str(Path(path) / "Exported_Maps")
            autofill.delete(0, "end"); autofill.insert(0, suggested)

    # -- console ---------------------------------------------------------------

    def _logline(self, text: str, color: str = t.TEXT_MAIN):
        self._log.configure(state="normal")
        self._log.insert("end", text + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    # -- option collection -----------------------------------------------------

    def _collect(self):
        """Read the active tab into ("sbs"|"mm", RenderOptions) or None on error."""
        active = self._tabs.get()
        if active == "Substance (.sbsar)":
            opts = e.RenderOptions(
                engine_path=self._sbs_engine.get().strip(),
                input_dir=self._sbs_input.get().strip(),
                output_dir=self._sbs_output.get().strip(),
                resolution=self._sbs_res.get(),
                group=bool(self._sbs_group.get()),
                recursive=bool(self._sbs_rec.get()),
                dry_run=bool(self._sbs_dry.get()))
            return "sbs", opts
        # Material Maker
        res = self._mm_res.get()
        opts = e.RenderOptions(
            engine_path=self._mm_engine.get().strip(),
            input_dir=self._mm_input.get().strip(),
            output_dir=self._mm_output.get().strip(),
            resolution=res,
            target_engine=self._mm_target.get(),
            group=bool(self._mm_group.get()),
            recursive=bool(self._mm_rec.get()),
            resize=None if res == "Original" else int(res.split("x")[0]),
            dry_run=bool(self._mm_dry.get()))
        return "mm", opts

    # -- run orchestration -----------------------------------------------------

    def _start(self):
        if self._worker and self._worker.is_alive():
            return
        kind, opts = self._collect()
        ext = ".sbsar" if kind == "sbs" else ".ptex"

        if not opts.output_dir:
            self._logline("Error: choose an output directory.", t.STATE["error"][1]); return
        v = e.validate_engine(opts.engine_path, "sbsrender" if kind == "sbs" else "material_maker")
        if v["error"]:
            self._logline(f"Error: {v['details']}", t.STATE["error"][1]); return
        if not Path(opts.input_dir).is_dir():
            self._logline("Error: input directory does not exist.", t.STATE["error"][1]); return

        projects = e.find_projects(opts.input_dir, ext, opts.recursive)
        if not projects:
            self._logline(f"No {ext} files found in {opts.input_dir}", t.TEXT_MUTED); return
        opts.protected_paths = tuple(str(project) for project in projects)

        # Preview + Confirm + Logging: generated files may replace same-name maps.
        # Engine sidecars are discarded only inside the KS-owned staging folder.
        if not opts.dry_run:
            mode = "Material Maker" if kind == "mm" else "Substance"
            postprocess = (
                "Generated .tres/.uasset sidecars are discarded from staging"
                + (f" and PNGs resized to {opts.resize}px." if opts.resize else ".")
                if kind == "mm"
                else "All nonempty files generated by Substance are published."
            )
            proceed = messagebox.askyesno(
                f"Confirm {mode} export",
                f"{len(projects)} project(s) will be exported to:\n{opts.output_dir}\n\n"
                f"Each export runs in an isolated KS staging folder. Generated "
                f"files are then published and may replace same-name files. "
                + postprocess
                + "\n\nOther existing files in the destination are left untouched. Continue?",
                icon="warning", parent=self)
            if not proceed:
                self._logline("Cancelled — nothing was written.", t.TEXT_MUTED); return

        self._stop.clear()
        self._run_btn.configure(state="disabled"); self._stop_btn.configure(state="normal")
        self._status.set_state("RUNNING", "running"); self._progress.set(0); self._summary.configure(text="")
        self._log.configure(state="normal"); self._log.delete("1.0", "end"); self._log.configure(state="disabled")
        self._logline(f"Found {len(projects)} {ext} project(s)."
                      + (" [DRY RUN]" if opts.dry_run else ""), t.TEXT_MUTED)
        self._worker = threading.Thread(target=self._work, args=(kind, opts, projects), daemon=True)
        self._worker.start()

    def _request_stop(self):
        # Takes effect between projects — run_cmd blocks inside one project and
        # doesn't expose the child process to terminate mid-render.
        self._stop.set(); self._status.set_state("STOPPING", "waiting")

    def _work(self, kind: str, opts, projects):
        render = e.render_substance if kind == "sbs" else e.render_material_maker
        total = len(projects)
        rendered = skipped = failed = 0
        for i, proj in enumerate(projects, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "--- stopped by user ---", t.TEXT_MUTED)
                break
            self.after(0, self._logline, f"[{i}/{total}] {proj.name} ...", t.TEXT_MUTED)
            res = render(proj, opts)
            if res.action == "rendered":
                rendered += 1
            elif res.action in ("dry-run", "skipped"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, total)
        self.after(0, self._done, rendered, skipped, failed, total)

    def _show(self, res, i: int, total: int):
        self._progress.set(i / total)
        icon = {"rendered": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"rendered": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.project).name
        self._logline(f"  {icon} {name} — {res.reason}", color)

    def _done(self, rendered: int, skipped: int, failed: int, total: int):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE" if not failed else "ERROR", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"rendered {rendered} · skipped/preview {skipped} · failed {failed} "
                                     f"· {total} total")
