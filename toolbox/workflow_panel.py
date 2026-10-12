"""Unified Workflows panel for KS ToolBox shell."""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import CancellationToken
from toolbox.icons import Icons, get_icon_font
from toolbox.workflow import (
    BUILTIN_PRESETS,
    ImageDeliveryWorkflow,
    WorkflowPreset,
    WorkflowReport,
)


class WorkflowPanel(ctk.CTkScrollableFrame):
    """Interactive workspace panel for Unified Workflows."""

    def __init__(self, parent: ctk.CTkBaseClass):
        super().__init__(
            parent,
            fg_color=t.BG_COLOR,
            corner_radius=0,
        )
        self.grid_columnconfigure(0, weight=1)

        self._current_token: CancellationToken | None = None
        self._is_running = False
        self._preset_keys = list(BUILTIN_PRESETS.keys())
        self._selected_preset_key = "web_delivery"
        self._workflow = ImageDeliveryWorkflow(BUILTIN_PRESETS[self._selected_preset_key])

        self._build_header()
        self._build_preset_card()
        self._build_folder_card()
        self._build_actions()
        self._build_results()

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 16))
        header.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(anchor="w")

        ctk.CTkLabel(
            title_row,
            text=Icons.BOLT,
            font=get_icon_font(18),
            text_color=t.ACCENT_SOFT,
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            title_row,
            text="Unified Workflows",
            font=t.font(20, bold=True),
            text_color=t.TEXT_MAIN,
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text="Chain multiple tools into automated 1-click pipelines with non-destructive staging and manifests.",
            font=t.font(11),
            text_color=t.TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 0))

    def _build_preset_card(self) -> None:
        card = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        card.grid(row=1, column=0, sticky="ew", padx=28, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="1. Select Pipeline Preset",
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8))

        preset_names = [BUILTIN_PRESETS[k].name for k in self._preset_keys]
        self._preset_dropdown = ctk.CTkOptionMenu(
            card,
            values=preset_names,
            command=self._on_preset_change,
            fg_color=t.ENTRY_BG,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
            dropdown_fg_color=t.CARD_BG,
            dropdown_hover_color=t.NEUTRAL_HOVER,
            font=t.font(11),
            width=280,
            height=34,
        )
        self._preset_dropdown.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 6))

        self._preset_desc = ctk.CTkLabel(
            card,
            text=self._workflow.preset.description,
            font=t.font(11),
            text_color=t.TEXT_MUTED,
            justify="left",
            wraplength=700,
        )
        self._preset_desc.grid(row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))

        # Visual pipeline stages diagram
        stages_frame = ctk.CTkFrame(card, fg_color=t.BG_COLOR, corner_radius=6)
        stages_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 16))
        self._stages_label = ctk.CTkLabel(
            stages_frame,
            text=self._get_stages_text(),
            font=t.font(10, bold=True),
            text_color=t.ACCENT_SOFT,
            padx=12,
            pady=8,
        )
        self._stages_label.pack(anchor="w")

    def _build_folder_card(self) -> None:
        card = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        card.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card,
            text="2. Select Folders (Non-Destructive)",
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(14, 8))

        # Input folder row
        ctk.CTkLabel(
            card, text="Input Folder:", font=t.font(11), text_color=t.TEXT_MAIN, width=90, anchor="w"
        ).grid(row=1, column=0, sticky="w", padx=(16, 6), pady=(0, 8))
        self._input_entry = c.entry(card, placeholder_text="Select source images folder...", height=34)
        self._input_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))
        c.secondary_button(card, "Browse...", self._browse_input, width=85, height=34).grid(
            row=1, column=2, sticky="e", padx=(0, 16), pady=(0, 8)
        )

        # Output folder row
        ctk.CTkLabel(
            card, text="Output Folder:", font=t.font(11), text_color=t.TEXT_MAIN, width=90, anchor="w"
        ).grid(row=2, column=0, sticky="w", padx=(16, 6), pady=(0, 14))
        self._output_entry = c.entry(card, placeholder_text="Select delivery folder (will be created if missing)...", height=34)
        self._output_entry.grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=(0, 14))
        c.secondary_button(card, "Browse...", self._browse_output, width=85, height=34).grid(
            row=2, column=2, sticky="e", padx=(0, 16), pady=(0, 14)
        )

    def _build_actions(self) -> None:
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.grid(row=3, column=0, sticky="ew", padx=28, pady=(0, 14))
        action_frame.grid_columnconfigure(2, weight=1)

        self._preview_btn = c.secondary_button(
            action_frame, "Preview Pipeline", self._preview, width=150, height=38
        )
        self._preview_btn.pack(side="left", padx=(0, 10))

        self._run_btn = c.primary_button(
            action_frame, "Execute Pipeline", self._run, width=170, height=38
        )
        self._run_btn.pack(side="left", padx=(0, 10))

        self._cancel_btn = c.danger_button(
            action_frame, "Cancel", self._cancel, width=100, height=38
        )
        self._cancel_btn.pack(side="left")
        self._cancel_btn.configure(state="disabled")

        self._open_out_btn = c.ghost_button(
            action_frame, "Open Output Folder", self._open_output, width=160, height=38
        )
        self._open_out_btn.pack(side="right")
        self._open_out_btn.configure(state="disabled")

    def _build_results(self) -> None:
        self._result_card = ctk.CTkFrame(
            self,
            fg_color=t.CARD_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=t.RADIUS_CARD,
        )
        self._result_card.grid(row=4, column=0, sticky="ew", padx=28, pady=(0, 24))
        self._result_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._result_card,
            text="3. Execution Progress & Audit",
            font=t.font(13, bold=True),
            text_color=t.TEXT_MAIN,
        ).pack(anchor="w", padx=16, pady=(14, 8))

        self._progress_bar = ctk.CTkProgressBar(self._result_card, fg_color=t.ENTRY_BG, progress_color=t.ACCENT)
        self._progress_bar.pack(fill="x", padx=16, pady=(0, 6))
        self._progress_bar.set(0.0)

        self._status_label = ctk.CTkLabel(
            self._result_card,
            text="Ready. Select input/output folders and click 'Preview' or 'Execute'.",
            font=t.font(11),
            text_color=t.TEXT_MUTED,
            anchor="w",
        )
        self._status_label.pack(anchor="w", padx=16, pady=(0, 8))

        self._log_box = ctk.CTkTextbox(
            self._result_card,
            height=130,
            font=t.font(10),
            fg_color=t.ENTRY_BG,
            border_color=t.CARD_BORDER,
            border_width=1,
            corner_radius=6,
        )
        self._log_box.pack(fill="x", padx=16, pady=(0, 16))
        self._log_box.configure(state="disabled")

    def _get_stages_text(self) -> str:
        p = self._workflow.preset
        steps = []
        if p.enable_rescale:
            steps.append(f"[1. Rescale to {p.rescale_longest_side}px]")
        if p.enable_alpha:
            steps.append(f"[2. Alpha Doctor ({p.alpha_method})]")
        if p.enable_scrub:
            steps.append("[3. Metadata Scrubber]")
        steps.append("[4. Manifest Audit Report]")
        return "  ──►  ".join(steps)

    def _on_preset_change(self, choice: str) -> None:
        for k, v in BUILTIN_PRESETS.items():
            if v.name == choice:
                self._selected_preset_key = k
                self._workflow = ImageDeliveryWorkflow(v)
                self._preset_desc.configure(text=v.description)
                self._stages_label.configure(text=self._get_stages_text())
                break

    def _browse_input(self) -> None:
        folder = filedialog.askdirectory(title="Select Input Image Folder")
        if folder:
            self._input_entry.delete(0, "end")
            self._input_entry.insert(0, folder)
            if not self._output_entry.get().strip():
                # Default output beside input
                self._output_entry.delete(0, "end")
                self._output_entry.insert(0, str(Path(folder).parent / f"{Path(folder).name}_delivery"))

    def _browse_output(self) -> None:
        folder = filedialog.askdirectory(title="Select Destination Folder")
        if folder:
            self._output_entry.delete(0, "end")
            self._output_entry.insert(0, folder)

    def _log(self, text: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _preview(self) -> None:
        in_dir = self._input_entry.get().strip()
        out_dir = self._output_entry.get().strip()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning("KS ToolBox", "Please select a valid input folder.")
            return
        if not out_dir:
            messagebox.showwarning("KS ToolBox", "Please specify an output folder.")
            return

        prev = self._workflow.preview(in_dir, out_dir)
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

        self._log(f"=== WORKFLOW PREVIEW: {prev['workflow_name']} ===")
        self._log(f"Preset: {prev['preset_name']}")
        self._log(f"Input files discovered: {prev['input_count']}")
        self._log(f"Destination: {prev['destination_dir']}")
        self._log("Stages planned:")
        for idx, st in enumerate(prev["stages"], 1):
            self._log(f"  {idx}. {st['stage']}: {st['settings']}")
        self._log(f"Safety check: {'PASSED (Non-destructive)' if prev['safe'] else 'FAILED (Same directory)'}")
        self._status_label.configure(
            text=f"Preview ready: {prev['input_count']} images ready for processing."
        )

    def _run(self) -> None:
        if self._is_running:
            return
        in_dir = self._input_entry.get().strip()
        out_dir = self._output_entry.get().strip()
        if not in_dir or not Path(in_dir).is_dir():
            messagebox.showwarning("KS ToolBox", "Please select a valid input folder.")
            return
        if not out_dir:
            messagebox.showwarning("KS ToolBox", "Please specify an output folder.")
            return

        self._is_running = True
        self._current_token = CancellationToken()
        self._run_btn.configure(state="disabled")
        self._preview_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._open_out_btn.configure(state="disabled")
        self._progress_bar.set(0.0)

        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log(f"Starting pipeline execution on '{in_dir}'...")

        threading.Thread(target=self._worker_execute, args=(in_dir, out_dir), daemon=True).start()

    def _worker_execute(self, in_dir: str, out_dir: str) -> None:
        def on_progress(msg: str, frac: float):
            self.after(0, lambda: self._update_progress(msg, frac))

        report = self._workflow.execute(
            in_dir,
            out_dir,
            cancellation=self._current_token,
            on_progress=on_progress,
        )
        self.after(0, lambda: self._finish_execution(report))

    def _update_progress(self, msg: str, frac: float) -> None:
        self._status_label.configure(text=msg)
        self._progress_bar.set(frac)
        self._log(f"[{int(frac*100)}%] {msg}")

    def _finish_execution(self, report: WorkflowReport) -> None:
        self._is_running = False
        self._run_btn.configure(state="normal")
        self._preview_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")

        if report.success:
            self._open_out_btn.configure(state="normal")
            self._status_label.configure(
                text=f"Pipeline Completed! {report.total_outputs} files generated in {report.duration_s:.2f}s."
            )
            self._log(f"=== PIPELINE SUCCESSFUL in {report.duration_s:.2f}s ===")
            self._log(f"Total delivered: {report.total_outputs} files")
            self._log(f"Audit manifest: {report.manifest_path}")
            messagebox.showinfo(
                "Pipeline Complete",
                f"Successfully delivered {report.total_outputs} assets to:\n{report.output_folder}",
            )
        elif report.cancelled:
            self._status_label.configure(text="Pipeline cancelled by user.")
            self._log("=== PIPELINE CANCELLED ===")
        else:
            self._status_label.configure(text=f"Error: {report.error}")
            self._log(f"=== PIPELINE FAILED: {report.error} ===")
            messagebox.showerror("Pipeline Failed", report.error)

    def _cancel(self) -> None:
        if self._current_token and not self._current_token.is_cancelled:
            self._status_label.configure(text="Cancelling pipeline...")
            self._current_token.cancel()
            self._cancel_btn.configure(state="disabled")

    def _open_output(self) -> None:
        out_dir = self._output_entry.get().strip()
        if out_dir and Path(out_dir).is_dir():
            if os.name == "nt":
                os.startfile(out_dir)
            else:
                subprocess.run(["xdg-open", out_dir], check=False)
