"""Base batch panel — shared file picker, run/stop orchestration, results log.

Every batch tool panel extends this. The base owns file management, controls,
queue polling, results, output selection, and the compatibility worker path for
unmigrated tools. New durable panels submit to the shell-owned queue.
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.engine_common import sweep_part_files

if TYPE_CHECKING:
    from toolbox.batch_reporting import BatchCompletionArtifacts
    from toolbox.job_queue import JobQueue, QueueSubmission


class BaseBatchPanel(ctk.CTkFrame):
    """Shared batch-processing panel.

    Subclasses MUST set: FILE_EXTS, FILE_LABEL, RESULTS_ICON.
    Subclasses MUST override: _build_options_card, _collect_options, _work, _write_manifest.
    """

    FILE_EXTS: set[str] = set()
    FILE_LABEL: str = "file"
    FILES_ICON: str = Icons.FOLDER
    RESULTS_ICON: str = Icons.CHART
    RUN_LABEL: str = "Run"

    def __init__(self, parent, queue_service: "JobQueue | None" = None):
        super().__init__(parent, fg_color=t.BG_COLOR)
        self._queue_service = queue_service
        self._active_job_id: str | None = None
        self._queue_shown: set[int] = set()
        self._files: list[Path] = []
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_files_card()
        self._build_options_card()
        self._build_results_card()

    # --- files card -----------------------------------------------------------

    def _build_files_card(self):
        plural = self.FILE_LABEL + "s"
        card = c.Card(self, plural.capitalize(), icon=self.FILES_ICON)
        card.grid(row=0, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        row = ctk.CTkFrame(card.body, fg_color="transparent"); row.pack(fill="x")
        c.secondary_button(row, "Add files", self._add_files, width=110).pack(side="left")
        c.secondary_button(row, "Add folder", self._add_folder, width=120).pack(side="left", padx=8)
        c.ghost_button(row, "Clear", self._clear, width=70).pack(side="left")
        self._count = ctk.CTkLabel(row, text=f"0 {plural}", text_color=t.TEXT_MUTED, font=t.font(11))
        self._count.pack(side="right")
        self._file_list = ctk.CTkTextbox(card.body, height=90, fg_color=t.BG_COLOR,
                                         border_color=t.CARD_BORDER, border_width=1,
                                         font=t.mono(11), text_color=t.TEXT_MUTED)
        self._file_list.pack(fill="x", pady=(10, 0)); self._file_list.configure(state="disabled")

    def _add_files(self):
        plural = self.FILE_LABEL + "s"
        paths = filedialog.askopenfilenames(
            title=f"Choose {plural}",
            filetypes=[(plural.capitalize(), " ".join(f"*{x}" for x in sorted(self.FILE_EXTS))),
                       ("All", "*.*")])
        self._add([Path(p) for p in paths])

    def _add_folder(self):
        plural = self.FILE_LABEL + "s"
        d = filedialog.askdirectory(title=f"Choose a folder (recursively scans for {plural})")
        if not d:
            return
        self._add([p for p in Path(d).rglob("*") if p.suffix.lower() in self.FILE_EXTS])

    def _add(self, paths: list[Path]):
        seen = set(self._files)
        for p in paths:
            if p not in seen and p.suffix.lower() in self.FILE_EXTS:
                self._files.append(p); seen.add(p)
        self._render_files()

    def _clear(self):
        self._files.clear(); self._render_files()

    def _render_files(self):
        n = len(self._files)
        label = self.FILE_LABEL if n == 1 else self.FILE_LABEL + "s"
        self._count.configure(text=f"{n} {label}")
        self._file_list.configure(state="normal"); self._file_list.delete("1.0", "end")
        self._file_list.insert("1.0", "\n".join(p.name for p in self._files) or "(no files yet)")
        self._file_list.configure(state="disabled")

    # --- run row (called by subclass at end of _build_options_card) -----------

    def _build_run_row(self, parent: ctk.CTkFrame):
        runrow = ctk.CTkFrame(parent, fg_color="transparent"); runrow.pack(fill="x", pady=(14, 0))
        self._run_btn = c.primary_button(runrow, self.RUN_LABEL, self._run, width=200)
        self._run_btn.pack(side="left")
        self._stop_btn = c.danger_button(runrow, "Stop", self._request_stop, width=90)
        self._stop_btn.pack(side="left", padx=10); self._stop_btn.configure(state="disabled")
        if self._queue_service is not None:
            self._pause_btn = c.secondary_button(runrow, "Pause", self._request_pause, width=80)
            self._pause_btn.pack(side="left"); self._pause_btn.configure(state="disabled")
        self._status = c.Pill(runrow, "IDLE", "idle"); self._status.pack(side="right")
        self._progress = ctk.CTkProgressBar(parent, height=7, fg_color=t.CARD_BORDER,
                                            progress_color=t.ACCENT_BLUE)
        self._progress.set(0); self._progress.pack(fill="x", pady=(12, 0))

    # --- output folder row helper ---------------------------------------------

    def _build_output_row(self, parent: ctk.CTkFrame, hint: str):
        outrow = ctk.CTkFrame(parent, fg_color="transparent"); outrow.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(outrow, text=hint, text_color=t.TEXT_MUTED, font=t.font(11)).pack(anchor="w")
        pick = ctk.CTkFrame(outrow, fg_color="transparent"); pick.pack(fill="x", pady=(2, 0))
        self._out_entry = c.entry(pick); self._out_entry.pack(side="left", fill="x", expand=True)
        c.secondary_button(pick, "Browse", self._pick_output, width=90).pack(side="left", padx=(8, 0))

    # --- results card ---------------------------------------------------------

    def _build_results_card(self):
        card = c.Card(self, "Results", icon=self.RESULTS_ICON)
        card.grid(row=2, column=0, sticky="nsew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        card.body.grid_rowconfigure(0, weight=1); card.body.grid_columnconfigure(0, weight=1)
        self._log = ctk.CTkTextbox(card.body, fg_color=t.BG_COLOR, border_color=t.CARD_BORDER,
                                   border_width=1, font=t.mono(11), text_color=t.TEXT_MAIN)
        self._log.grid(row=0, column=0, sticky="nsew"); self._log.configure(state="disabled")
        self._summary = ctk.CTkLabel(card.body, text="", text_color=t.TEXT_MUTED, font=t.font(11))
        self._summary.grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _logline(self, text: str, color: str = t.TEXT_MAIN):
        self._log.configure(state="normal"); self._log.insert("end", text + "\n")
        self._log.see("end"); self._log.configure(state="disabled")

    # --- run orchestration ----------------------------------------------------

    def _run(self):
        if self._worker and self._worker.is_alive():
            return
        if not self._files:
            self._logline(f"No {self.FILE_LABEL}s added.", t.TEXT_MUTED); return
        opts = self._collect_options()
        if opts is None:
            return
        if not self._pre_run_check(opts):
            return
        self._sweep_stale(opts)
        self._stop.clear()
        self._run_btn.configure(state="disabled"); self._stop_btn.configure(state="normal")
        self._status.set_state("RUNNING", "running"); self._progress.set(0)
        self._log.configure(state="normal"); self._log.delete("1.0", "end"); self._log.configure(state="disabled")
        files = list(self._files)
        if self._queue_service is not None:
            try:
                self._queue_shown.clear()
                submission = self._build_submission(files, opts)
                self._active_job_id = self._queue_service.submit(submission)
            except Exception as ex:  # noqa: BLE001 - visible queue submission failure
                self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
                self._status.set_state("FAILED", "error")
                self._logline(f"queue submission failed: {type(ex).__name__}: {ex}", t.STATE["error"][1])
                return
            self._status.set_state("QUEUED", "waiting")
            self._pause_btn.configure(state="normal", text="Pause")
            self.after(50, self._poll_queue_job, self._active_job_id)
        else:
            self._worker = threading.Thread(target=self._work, args=(files, opts), daemon=True)
            self._worker.start()

    def _pre_run_check(self, opts) -> bool:
        """Override for a confirmation dialog before running. Return True to proceed."""
        return True

    def _check_output_collisions(self, collisions) -> bool:
        """Report an unsafe multi-item output plan; return whether it may run."""
        if not collisions:
            return True
        self._logline(
            f"Cannot start: {len(collisions)} output path collision(s). "
            "Choose mirror mode, a different output folder, or rename the inputs.",
            t.STATE["error"][1],
        )
        for output, owners in list(collisions.items())[:5]:
            self._logline(f"  {output}", t.STATE["error"][1])
            for owner in owners:
                self._logline(f"    ← {owner}", t.TEXT_MUTED)
        return False

    def _sweep_stale(self, opts) -> None:
        """Clear orphaned `.part` temps in the output root before a run (a prior
        hard kill can leave them). Only an explicit out_root is swept; beside-source
        temps are self-healed by the next atomic write. Best-effort, never blocks."""
        root = getattr(opts, "out_root", None)
        if root and not getattr(opts, "dry_run", False):
            n = sweep_part_files(Path(root))
            if n:
                self._logline(f"cleaned {n} stale .part temp file(s)", t.TEXT_MUTED)

    def _request_stop(self):
        if self._queue_service is not None and self._active_job_id:
            self._queue_service.cancel(self._active_job_id)
        else:
            self._stop.set()
        self._status.set_state("STOPPING", "waiting")

    def _request_pause(self):
        if self._queue_service is None or not self._active_job_id:
            return
        snapshot = self._queue_service.snapshot(self._active_job_id)
        if snapshot and snapshot.state.value == "paused":
            if self._queue_service.resume(self._active_job_id):
                self._pause_btn.configure(text="Pause")
                self._status.set_state("RUNNING", "running")
        elif self._queue_service.pause(self._active_job_id):
            self._pause_btn.configure(text="Resume")
            self._status.set_state("PAUSED", "paused")

    def _queue_cancelled(self) -> None:
        self._run_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._status.set_state("CANCELLED", "waiting")
        self._active_job_id = None

    def _poll_queue_job(self, job_id: str) -> None:
        if self._queue_service is None or self._active_job_id != job_id:
            return
        snapshot = self._queue_service.snapshot(job_id)
        if snapshot is None:
            self._batch_failed("queue record disappeared")
            return
        self._on_queue_snapshot(snapshot)
        if snapshot.state.value in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            completion = self._queue_service.completion(job_id)
            if completion is not None:
                self._queue_complete(completion)
            elif snapshot.state.value == "cancelled":
                self._queue_cancelled()
            else:
                self._batch_failed(snapshot.detail or snapshot.state.value)
            return
        self.after(100, self._poll_queue_job, job_id)

    def _on_queue_snapshot(self, snapshot) -> None:
        if snapshot.total_items:
            self._progress.set(snapshot.completed_items / snapshot.total_items)
        if snapshot.state.value == "paused":
            self._status.set_state("PAUSED", "paused")
            self._pause_btn.configure(text="Resume")
        elif snapshot.state.value == "running":
            self._status.set_state("RUNNING", "running")
            self._pause_btn.configure(text="Pause")
        elif snapshot.state.value in {"queued", "preparing"}:
            self._status.set_state("QUEUED", "waiting")
        elif snapshot.state.value == "cancelling":
            self._status.set_state("STOPPING", "waiting")
        item = snapshot.last_item
        if item is not None and item.position not in self._queue_shown:
            self._queue_shown.add(item.position)
            self._show(self._result_from_record(item), snapshot.completed_items, snapshot.total_items)

    def _consume_queue_completion(self, completion) -> "BatchCompletionArtifacts | None":
        from toolbox.batch_reporting import BatchCompletionArtifacts

        payload = completion.value
        if not isinstance(payload, BatchCompletionArtifacts):
            self._batch_failed(completion.error or "completion payload missing")
            return None
        for item, result in zip(payload.finished_items, payload.results):
            if item.position not in self._queue_shown:
                self._show(result, item.position + 1, len(completion.report.items))
        for warning in completion.warnings:
            self._logline(warning, t.STATE["error"][1])
        return payload

    def _prepare_queue_completion(self, report, opts, *, tool_id: str):
        """Finalize a durable tool with its manifest and atomic morning report."""
        from toolbox.batch_reporting import completion_report_path, prepare_batch_completion

        return prepare_batch_completion(
            report,
            result_from_record=self._result_from_record,
            write_manifest=lambda results: self._write_manifest(opts, results),
            report_path=completion_report_path(
                tool_id,
                report.job_id,
                out_root=getattr(opts, "out_root", None),
                dry_run=bool(getattr(opts, "dry_run", False)),
            ),
        )

    @staticmethod
    def _queue_completion_state(completion):
        from toolbox.batch_core import JobState

        state = completion.report.state
        if completion.warnings and state is JobState.COMPLETED:
            return JobState.COMPLETED_WITH_WARNINGS
        return state

    def _finish_queue_ui(
        self,
        summary: str,
        *,
        job_state,
        recovered: bool = False,
        reused: bool = False,
        manifest: str | None = None,
        report_path: str | None = None,
    ) -> None:
        from toolbox.batch_core import JobState

        self._run_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._active_job_id = None
        if job_state is JobState.CANCELLED:
            self._status.set_state("CANCELLED", "waiting")
        elif job_state is JobState.COMPLETED_WITH_WARNINGS:
            self._status.set_state("WARNINGS", "waiting")
            self._progress.set(1)
        elif job_state is JobState.FAILED:
            self._status.set_state("FAILED", "error")
            self._progress.set(1)
        else:
            self._status.set_state("DONE", "done")
            self._progress.set(1)
        self._summary.configure(text=summary)
        if recovered:
            self._logline("  recovered unfinished work from the previous run", t.TEXT_MUTED)
        if reused:
            self._logline("  reused valid outputs and prior item decisions", t.TEXT_MUTED)
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)
        if report_path:
            self._logline(f"  completion report: {report_path}", t.TEXT_MUTED)

    def _batch_failed(self, details: str) -> None:
        self._run_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._pause_btn.configure(state="disabled", text="Pause")
        self._active_job_id = None
        self._status.set_state("FAILED", "error")
        self._logline(f"batch core failed: {details}", t.STATE["error"][1])

    def _queue_complete(self, completion) -> None:
        raise NotImplementedError

    # --- shared helpers -------------------------------------------------------

    def _pick_output(self):
        d = filedialog.askdirectory(title="Choose output folder")
        if d:
            self._out_entry.delete(0, "end"); self._out_entry.insert(0, d)

    def _resolve_input_root(self) -> Path | None:
        """Common ancestor of all source folders for mirror mode."""
        if self._files:
            try:
                return Path(os.path.commonpath([str(p.parent) for p in self._files]))
            except ValueError:
                pass
        return None

    # --- subclass MUST implement ----------------------------------------------

    def _build_options_card(self):
        raise NotImplementedError

    def _collect_options(self):
        raise NotImplementedError

    def _work(self, files: list[Path], opts):
        raise NotImplementedError

    def _build_submission(self, files: list[Path], opts) -> "QueueSubmission":
        raise NotImplementedError

    def _write_manifest(self, opts, results: list) -> str | None:
        raise NotImplementedError

    def _result_from_record(self, item):
        raise NotImplementedError

    def _show(self, result, position: int, total: int):
        raise NotImplementedError
