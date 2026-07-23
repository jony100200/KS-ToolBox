"""Texture Renderer — the tool's UI. Thin over engine.py.

A custom `BaseBatchPanel` layout: this tool is directory + engine-exe based with
two sub-modes, so it keeps a CTkTabview ("Substance" / "Material Maker") rather
than the standard file-picker layout. Each tab carries its own engine/input/
output controls over a shared console and Start/Stop/Pause footer. Rendering
runs through the shell-owned durable queue, so the UI loop never blocks and
interrupted jobs can recover. No render logic lives here (that's engine.py).
"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_reporting import completion_report_path, prepare_batch_completion
from toolbox.icons import Icons
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class TextureRendererPanel(BaseBatchPanel):
    """Custom two-mode screen reusing BaseBatchPanel's durable queue lifecycle."""

    def __init__(self, parent, queue_service):
        ctk.CTkFrame.__init__(self, parent, fg_color=t.BG_COLOR)
        self._queue_service = queue_service
        self._active_job_id: str | None = None
        self._queue_shown: set[int] = set()

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
        self._pause_btn = c.secondary_button(footer, "Pause", self._request_pause, width=80)
        self._pause_btn.grid(row=0, column=3, padx=(10, 0))
        self._pause_btn.configure(state="disabled")
        self._status = c.Pill(footer, "IDLE", "idle"); self._status.grid(row=0, column=4, padx=(10, 0))

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
        self._sbs_dry.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(opts, text="Timeout min", text_color=t.TEXT_MUTED,
                     font=t.font(11)).pack(side="left", padx=(0, 6))
        self._sbs_timeout = c.entry(opts, width=60)
        self._sbs_timeout.insert(0, "60"); self._sbs_timeout.pack(side="left")

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
        self._mm_dry.pack(side="left", padx=(0, 16))
        ctk.CTkLabel(opts, text="Timeout min", text_color=t.TEXT_MUTED,
                     font=t.font(11)).pack(side="left", padx=(0, 6))
        self._mm_timeout = c.entry(opts, width=60)
        self._mm_timeout.insert(0, "60"); self._mm_timeout.pack(side="left")

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
            try:
                timeout = int(self._sbs_timeout.get()) * 60
            except ValueError:
                self._logline("Timeout must be a whole number of minutes.",
                              t.STATE["error"][1])
                return None
            opts = e.RenderOptions(
                engine_path=self._sbs_engine.get().strip(),
                input_dir=self._sbs_input.get().strip(),
                output_dir=self._sbs_output.get().strip(),
                resolution=self._sbs_res.get(),
                group=bool(self._sbs_group.get()),
                recursive=bool(self._sbs_rec.get()),
                dry_run=bool(self._sbs_dry.get()),
                timeout_seconds=timeout)
            return "sbs", opts
        # Material Maker
        try:
            timeout = int(self._mm_timeout.get()) * 60
        except ValueError:
            self._logline("Timeout must be a whole number of minutes.",
                          t.STATE["error"][1])
            return None
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
            dry_run=bool(self._mm_dry.get()),
            timeout_seconds=timeout)
        return "mm", opts

    def _collect_options(self):
        """BaseBatchPanel compatibility hook; this custom screen returns mode + options."""
        return self._collect()

    # -- run orchestration -----------------------------------------------------

    def _start(self):
        if self._active_job_id:
            return
        collected = self._collect()
        if collected is None:
            return
        kind, opts = collected
        ext = ".sbsar" if kind == "sbs" else ".ptex"

        normalized, options_error = e.normalized_options(opts, kind)
        if normalized is None:
            self._logline(f"Error: {options_error}", t.STATE["error"][1])
            return
        opts = normalized
        v = e.validate_engine(opts.engine_path, "sbsrender" if kind == "sbs" else "material_maker")
        if v["error"]:
            self._logline(f"Error: {v['details']}", t.STATE["error"][1]); return
        if not Path(opts.input_dir).is_dir():
            self._logline("Error: input directory does not exist.", t.STATE["error"][1]); return

        try:
            projects = e.find_projects(
                opts.input_dir, ext, opts.recursive,
                exclude_dirs=[opts.output_dir],
            )
        except (OSError, ValueError) as ex:
            self._logline(f"Project scan failed: {ex}", t.STATE["error"][1])
            return
        if not projects:
            self._logline(f"No {ext} files found in {opts.input_dir}", t.TEXT_MUTED); return

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

        self._run_btn.configure(state="disabled"); self._stop_btn.configure(state="normal")
        self._pause_btn.configure(state="normal", text="Pause")
        self._status.set_state("QUEUED", "waiting"); self._progress.set(0)
        self._summary.configure(text="")
        self._log.configure(state="normal"); self._log.delete("1.0", "end"); self._log.configure(state="disabled")
        self._logline(f"Found {len(projects)} {ext} project(s)."
                      + (" [DRY RUN]" if opts.dry_run else ""), t.TEXT_MUTED)
        try:
            self._queue_shown.clear()
            submission = self._build_submission(kind, opts, projects)
            self._active_job_id = self._queue_service.submit(submission)
        except Exception as ex:  # noqa: BLE001 — submission failure remains visible
            self._run_btn.configure(state="normal")
            self._stop_btn.configure(state="disabled")
            self._pause_btn.configure(state="disabled")
            self._status.set_state("FAILED", "error")
            self._logline(
                f"queue submission failed: {type(ex).__name__}: {ex}",
                t.STATE["error"][1],
            )
            return
        self.after(50, self._poll_queue_job, self._active_job_id)

    def _build_submission(
        self, kind: str, opts: e.RenderOptions, projects: list[Path]
    ) -> QueueSubmission:
        render = e.render_substance if kind == "sbs" else e.render_material_maker
        settings = asdict(opts)
        settings.pop("protected_paths", None)
        definition = JobDefinition.create(
            tool_id="texture_renderer",
            tool_version="1",
            workflow_version="external-render.v1",
            inputs=projects,
            identity_dependencies=[opts.engine_path],
            settings={"renderer": kind, **settings},
            max_retries=1,
        )
        destinations = [
            os.path.normcase(str(
                (Path(opts.output_dir) / project.stem if opts.group
                 else Path(opts.output_dir)).resolve(strict=False)
            ))
            for project in projects
        ]
        exact_reuse = len(projects) == 1 or (
            opts.group and len(destinations) == len(set(destinations))
        )
        if not exact_reuse:
            self._logline(
                "Exact completed-job reuse is disabled because projects share "
                "a flat or duplicate destination; sequential export behavior is preserved.",
                t.TEXT_MUTED,
            )

        def classify(result: e.Result) -> ItemOutcome:
            data = result.to_dict()
            if result.action == "rendered":
                return ItemOutcome.completed(data, result.reason)
            if result.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, result.reason)
            return ItemOutcome.failed(
                result.reason, retryable=result.retryable, data=data
            )

        return QueueSubmission(
            definition=definition,
            label=(
                f"Texture Renderer {'Substance' if kind == 'sbs' else 'Material Maker'}"
                f" · {len(projects)} project(s)"
            ),
            execute=lambda path, token: render(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: exact_reuse and e.validate_result(
                self._result_from_record(item), opts, kind,
                expected_project=item.input_path,
            ),
            finalize=lambda report: prepare_batch_completion(
                report,
                result_from_record=self._result_from_record,
                write_manifest=lambda results: e.write_manifest(
                    list(results), opts, kind, report.job_id
                ),
                report_path=completion_report_path(
                    "texture_renderer",
                    report.job_id,
                    out_root=Path(opts.output_dir),
                    dry_run=opts.dry_run,
                ),
                write_manifest_on_reuse=True,
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        rendered = sum(result.action == "rendered" for result in results)
        skipped = sum(result.action in {"dry-run", "skipped"} for result in results)
        failed = len(results) - rendered - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            rendered, skipped, failed, remaining,
            payload.manifest, payload.report_path,
            self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"project", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            item.input_path, "failed",
            item.details or "render project quarantined",
            detail="batch.quarantined",
        )

    def _show(self, res, i: int, total: int):
        self._progress.set(i / total)
        icon = {"rendered": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"rendered": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.project).name
        self._logline(f"  {icon} {name} — {res.reason}", color)

    def _done(
        self, rendered: int, skipped: int, failed: int, remaining: int = 0,
        manifest: str | None = None, report_path: str | None = None,
        job_state=JobState.COMPLETED, recovered: bool = False,
        reused: bool = False,
    ):
        summary = f"rendered {rendered} · skipped/preview {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
