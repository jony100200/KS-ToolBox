"""Image Enhancer presentation layer; work runs only through the shell queue."""
from __future__ import annotations

import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path
from typing import Any

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_panel import BaseBatchPanel
from toolbox.icons import Icons
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e
from . import model_rack

_PRESET_MAP = {
    "Auto (Recommended)": "auto",
    "Natural Skin / De-Gloss": "natural_skin",
    "De-Gloss / De-Shine": "de_gloss",
    "Retinex Dehaze & Range": "retinex_dehaze",
    "Shadows & Highlights": "shadows_highlights",
    "Gentle Restore": "gentle_restore",
    "Clarity & Detail": "detail",
    "Portrait Polish": "portrait_polish",
    "Texture Cleanup": "texture_cleanup",
    "Color Recovery": "colour_restore",
    "Smooth Bilateral": "smooth_bilateral",
    "Custom (Manual)": "custom",
}
_REVERSE_PRESET_MAP = {v: k for k, v in _PRESET_MAP.items()}


class ModelRackDialog(ctk.CTkToplevel):
    """Interactive dialog displaying all specialist AI micro-models and download links."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("AI Filter Model Rack & Download Hub")
        self.geometry("780x560")
        self.configure(fg_color=t.BG_COLOR)
        self.attributes("-topmost", True)

        header = ctk.CTkFrame(self, fg_color=t.CARD_BG)
        header.pack(fill="x", padx=16, pady=(16, 8))
        ctk.CTkLabel(
            header, text="Specialist Micro-Models Rack",
            text_color=t.TEXT_MAIN, font=t.font(16, "bold")
        ).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            header,
            text="Tiny specialist models (~1–20 MB) that run beside deterministic filters. Download only what you need.",
            text_color=t.TEXT_MUTED, font=t.font(11)
        ).pack(anchor="w", padx=14, pady=(0, 10))

        # Scrollable list of models
        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=16, pady=8)

        self._refresh_list()

    def _refresh_list(self):
        for widget in self._scroll.winfo_children():
            widget.destroy()

        specs = model_rack.list_models()
        for spec in specs:
            card = ctk.CTkFrame(self._scroll, fg_color=t.CARD_BG, corner_radius=6, border_width=1, border_color=t.CARD_BORDER)
            card.pack(fill="x", pady=5)

            # Left column: Info
            info_frame = ctk.CTkFrame(card, fg_color="transparent")
            info_frame.pack(side="left", fill="both", expand=True, padx=12, pady=10)

            title_row = ctk.CTkFrame(info_frame, fg_color="transparent")
            title_row.pack(fill="x")

            # Category badge
            ctk.CTkLabel(
                title_row, text=f" {spec.category} ",
                fg_color=t.ACCENT_BLUE if spec.is_installed() else t.NEUTRAL_HOVER,
                text_color="#ffffff", font=t.font(9, "bold"), corner_radius=4
            ).pack(side="left", padx=(0, 8))

            ctk.CTkLabel(
                title_row, text=spec.name,
                text_color=t.TEXT_MAIN, font=t.font(12, "bold")
            ).pack(side="left")

            ctk.CTkLabel(
                title_row, text=f"({spec.size_mb:.1f} MB)",
                text_color=t.TEXT_MUTED, font=t.font(11)
            ).pack(side="left", padx=(6, 0))

            ctk.CTkLabel(
                info_frame, text=spec.description,
                text_color=t.TEXT_MUTED, font=t.font(11), wraplength=480, justify="left"
            ).pack(anchor="w", pady=(4, 0))

            # Right column: Status & Actions
            act_frame = ctk.CTkFrame(card, fg_color="transparent")
            act_frame.pack(side="right", padx=12, pady=10)

            if spec.is_installed():
                ctk.CTkLabel(
                    act_frame, text="✅ Installed",
                    text_color=t.STATE["done"][1], font=t.font(11, "bold")
                ).pack(pady=(0, 4))
            else:
                ctk.CTkLabel(
                    act_frame, text="⬇️ Not Installed",
                    text_color=t.TEXT_MUTED, font=t.font(11)
                ).pack(pady=(0, 4))

                dl_btn = ctk.CTkButton(
                    act_frame, text="Download", width=90, height=26,
                    fg_color=t.ACCENT_BLUE, font=t.font(10, "bold"),
                    command=lambda s=spec: self._start_download(s)
                )
                dl_btn.pack(pady=(0, 4))

            btn_row = ctk.CTkFrame(act_frame, fg_color="transparent")
            btn_row.pack()

            copy_btn = ctk.CTkButton(
                btn_row, text="Copy Link", width=70, height=22,
                fg_color=t.BG_COLOR, text_color=t.TEXT_MAIN, font=t.font(9),
                command=lambda url=spec.download_url: self._copy_link(url)
            )
            copy_btn.pack(side="left", padx=(0, 4))

            if spec.docs_url:
                docs_btn = ctk.CTkButton(
                    btn_row, text="Docs", width=50, height=22,
                    fg_color=t.BG_COLOR, text_color=t.TEXT_MAIN, font=t.font(9),
                    command=lambda url=spec.docs_url: webbrowser.open(url)
                )
                docs_btn.pack(side="left")

    def _copy_link(self, url: str):
        self.clipboard_clear()
        self.clipboard_append(url)
        self.update()

    def _start_download(self, spec: model_rack.ModelSpec):
        progress_win = ctk.CTkToplevel(self)
        progress_win.title(f"Downloading {spec.name}")
        progress_win.geometry("420x150")
        progress_win.attributes("-topmost", True)

        lbl = ctk.CTkLabel(progress_win, text=f"Downloading {spec.name}...\n({spec.size_mb:.1f} MB)", font=t.font(12))
        lbl.pack(pady=(20, 10))

        bar = ctk.CTkProgressBar(progress_win, width=320)
        bar.pack(pady=10)
        bar.set(0)

        def runner():
            def cb(frac, cur, total):
                self.after(0, lambda: bar.set(frac))

            ok, msg = model_rack.download_model(spec.id, progress_callback=cb)
            self.after(0, lambda: progress_win.destroy())
            self.after(0, self._refresh_list)

        threading.Thread(target=runner, daemon=True).start()


class ImageEnhancerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BOLT
    RUN_LABEL = "Enhance Selected"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    def _build_options_card(self):
        card = c.Card(self, "One-Button Image Enhance & Restoration", icon=Icons.BOLT)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body

        # Row 1: Primary Preset & Mode Selection
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x")

        ctk.CTkLabel(row, text="Enhancement Preset", text_color=t.TEXT_MAIN, font=t.font(11, "bold")).pack(side="left")
        self._preset_display = ctk.CTkOptionMenu(
            row, values=list(_PRESET_MAP.keys()), width=210,
            command=self._preset_changed, fg_color=t.ACCENT_BLUE,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._preset_display.pack(side="left", padx=(8, 16))
        self._preset_display.set("Auto (Recommended)")

        ctk.CTkLabel(row, text="Mode", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._mode = ctk.CTkOptionMenu(
            row, values=["Deterministic", "Hybrid", "AI only"], width=130,
            command=self._mode_changed, fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._mode.pack(side="left", padx=(8, 16))
        self._mode.set("Deterministic")

        ctk.CTkLabel(row, text="Scale", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._scale = ctk.CTkOptionMenu(
            row, values=["1", "2", "3", "4"], width=64,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._scale.pack(side="left", padx=(8, 16))
        self._scale.set("1")

        ctk.CTkLabel(row, text="Model", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._model = ctk.CTkOptionMenu(
            row, values=["realesrgan-x4plus", "realesrgan-x4plus-anime", "realesr-animevideov3-x2"], width=180,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._model.pack(side="left", padx=(8, 12))
        self._model.set("realesrgan-x4plus")

        # Model Rack Hub Button
        rack_btn = ctk.CTkButton(
            row, text="📦 Model Rack", width=110, height=28,
            fg_color=t.CARD_BORDER, hover_color=t.NEUTRAL_HOVER,
            text_color=t.TEXT_MAIN, font=t.font(11, "bold"),
            command=self._open_model_rack
        )
        rack_btn.pack(side="left")

        # Row 2: Region / Facial Detail & Hole Repair
        row2 = ctk.CTkFrame(body, fg_color="transparent")
        row2.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(row2, text="Target Region", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._region = ctk.CTkOptionMenu(
            row2, values=["none", "faces", "subject_mask", "manual_box"], width=130,
            command=self._region_changed, fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER
        )
        self._region.pack(side="left", padx=(8, 16))
        self._region.set("none")

        self._box_label = ctk.CTkLabel(row2, text="Box x,y,w,h (%)", text_color=t.TEXT_MUTED, font=t.font(11))
        self._box = c.entry(row2, width=140)
        self._box.insert(0, "25,25,50,50")

        self._face_detail = ctk.CTkCheckBox(row2, text="Face-detail Refinement", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._face_detail.pack(side="left", padx=(12, 16))

        self._repair = ctk.CTkCheckBox(row2, text="Repair Enclosed Alpha Holes", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._repair.pack(side="left", padx=12)

        self._debug = ctk.CTkCheckBox(row2, text="Save Region Masks / Debug", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._debug.pack(side="left", padx=12)

        # Row 3: Options, Dry-run & Mirroring
        row3 = ctk.CTkFrame(body, fg_color="transparent")
        row3.pack(fill="x", pady=(10, 0))

        self._auto_mode = ctk.CTkCheckBox(row3, text="Auto-detect flaw severity per image", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._auto_mode.select()
        self._auto_mode.pack(side="left")

        self._dry = ctk.CTkCheckBox(row3, text="Preview only (dry-run)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.pack(side="left", padx=20)

        self._mirror = ctk.CTkCheckBox(row3, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select()
        self._mirror.pack(side="left", padx=20)

        # Advanced Manual Stack Controls
        advanced = ctk.CTkFrame(body, fg_color="transparent")
        advanced.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(advanced, text="Manual Overrides:", text_color=t.TEXT_MUTED, font=t.font(11, "bold")).pack(side="left", padx=(0, 10))

        self._advanced: dict[str, ctk.CTkEntry] = {}
        self._advanced_entry(advanced, "Brightness", "brightness", "1.0")
        self._advanced_entry(advanced, "Contrast", "contrast", "1.0")
        self._advanced_entry(advanced, "Gamma", "gamma", "1.0")
        self._advanced_entry(advanced, "Hue°", "hue_degrees", "0")
        self._advanced_entry(advanced, "Saturation", "saturation", "1.0")
        self._advanced_entry(advanced, "De-Gloss", "de_gloss_strength", "0.0")

        advanced2 = ctk.CTkFrame(body, fg_color="transparent")
        advanced2.pack(fill="x", pady=(6, 0))
        self._advanced_entry(advanced2, "Texture", "micro_texture_amount", "0.0")
        self._advanced_entry(advanced2, "Vibrance", "vibrance", "0")
        self._advanced_entry(advanced2, "Temp", "temperature", "0")
        self._advanced_entry(advanced2, "Tint", "tint", "0")
        self._advanced_entry(advanced2, "Denoise", "denoise", "0")
        self._advanced_entry(advanced2, "Sharpen", "sharpen", "1.0")
        self._advanced_entry(advanced2, "High-pass", "high_pass", "0")
        self._advanced_entry(advanced2, "Edge", "edge_boost", "0")

        # Telemetry & Status
        self._status = ctk.CTkLabel(body, text="", text_color=t.TEXT_MUTED, font=t.font(11), anchor="w")
        self._status.pack(fill="x", pady=(10, 0))
        self._refresh_status()

        self._build_output_row(body, "Output folder (blank = ./enhanced beside each source)")
        self._build_run_row(body)

        self._region_changed("none")
        self._mode_changed("Deterministic")

    def _open_model_rack(self):
        ModelRackDialog(self)

    def _advanced_entry(self, row, label: str, key: str, default: str):
        ctk.CTkLabel(row, text=label, text_color=t.TEXT_MUTED, font=t.font(10)).pack(side="left", padx=(0, 4))
        entry = c.entry(row, width=54)
        entry.insert(0, default)
        entry.pack(side="left", padx=(0, 8))
        self._advanced[key] = entry

    def _preset_changed(self, display_name: str):
        preset_key = _PRESET_MAP.get(display_name, "auto")
        if preset_key == "de_gloss":
            self._advanced["de_gloss_strength"].delete(0, "end")
            self._advanced["de_gloss_strength"].insert(0, "0.85")
        elif preset_key == "natural_skin":
            self._advanced["de_gloss_strength"].delete(0, "end")
            self._advanced["de_gloss_strength"].insert(0, "0.75")
            self._advanced["micro_texture_amount"].delete(0, "end")
            self._advanced["micro_texture_amount"].insert(0, "0.035")
        elif preset_key == "auto":
            self._advanced["de_gloss_strength"].delete(0, "end")
            self._advanced["de_gloss_strength"].insert(0, "0.0")
            self._advanced["micro_texture_amount"].delete(0, "end")
            self._advanced["micro_texture_amount"].insert(0, "0.0")

    def _region_changed(self, mode: str):
        if mode == "manual_box":
            self._box_label.pack(side="left", padx=(0, 6))
            self._box.pack(side="left")
        else:
            self._box_label.pack_forget()
            self._box.pack_forget()
        if mode != "faces":
            self._face_detail.deselect()

    def _mode_changed(self, mode: str):
        deterministic = mode == "Deterministic"
        self._scale.configure(state="disabled" if deterministic else "normal")
        self._model.configure(state="disabled" if deterministic else "normal")
        self._face_detail.configure(state="disabled" if deterministic else "normal")
        if deterministic:
            self._scale.set("1")
            self._face_detail.deselect()
        if hasattr(self, "_status"):
            self._refresh_status()

    def _refresh_status(self):
        state = e.utility_status()
        rack = model_rack.get_rack_status()
        ready = all(bool(state[key]) for key in ("upscale_ready", "segmentation_ready", "faces_ready", "repair_ready"))
        contract = "Deterministic: Fast CPU frequency separation" if self._mode.get() == "Deterministic" else "Hybrid: Utility models on demand" if self._mode.get() == "Hybrid" else "AI-only: Model restoration pass"
        self._status.configure(
            text=f"{contract} · {state['details']} · Model Rack: {rack['installed_count']}/{rack['total']} installed",
            text_color=t.STATE["done"][1] if ready else t.STATE["waiting"][1]
        )

    def _collect_options(self):
        preset_key = _PRESET_MAP.get(self._preset_display.get(), "auto")
        region = self._region.get()
        if self._face_detail.get() and region == "none":
            region = "faces"

        box = None
        if region == "manual_box":
            try:
                box = tuple(float(value.strip()) for value in self._box.get().split(","))
                if len(box) != 4:
                    raise ValueError
            except ValueError:
                self._logline("Manual box must be x,y,width,height percentages.", t.STATE["error"][1])
                return None

        try:
            scale = int(self._scale.get())
        except ValueError:
            return None

        try:
            advanced = {key: float(entry.get().strip()) for key, entry in self._advanced.items()}
        except ValueError:
            self._logline("Stack controls must be valid numbers.", t.STATE["error"][1])
            return None

        state = e.utility_status()
        chosen_mode = {"Deterministic": "deterministic", "Hybrid": "hybrid", "AI only": "ai"}[self._mode.get()]
        may_use_model = chosen_mode == "ai" or (chosen_mode == "hybrid" and (scale > 1 or self._auto_mode.get()))

        if may_use_model and not state["upscale_ready"]:
            self._logline("AI upscale is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if region == "subject_mask" and not state["segmentation_ready"]:
            self._logline("Subject segmentation is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if (region == "faces" or self._face_detail.get()) and not state["faces_ready"]:
            self._logline("Face detail is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None
        if self._repair.get() and not state["repair_ready"]:
            self._logline("Local repair is not ready: " + str(state["details"]), t.STATE["error"][1])
            return None

        out = self._out_entry.get().strip()
        return e.EnhanceOptions(
            out_root=Path(out) if out else None,
            input_root=self._resolve_input_root() if self._mirror.get() else None,
            mirror=bool(self._mirror.get()),
            preset=preset_key,
            scale_factor=scale,
            ai_model=self._model.get(),
            mode=chosen_mode,
            auto_select_mode=bool(self._auto_mode.get()),
            region_mode=region,
            manual_box=box,
            face_detail=bool(self._face_detail.get()),
            repair_alpha_holes=bool(self._repair.get()),
            debug_outputs=bool(self._debug.get()),
            dry_run=bool(self._dry.get()),
            **advanced,
        )

    def _build_submission(self, files: list[Path], opts: e.EnhanceOptions) -> QueueSubmission:
        dependencies = []
        if opts.mode == "ai" or opts.auto_select_mode or opts.scale_factor > 1 or opts.face_detail:
            dependencies.extend([
                e._REAL_ESRGAN / "realesrgan-ncnn-vulkan.exe",
                e._REAL_ESRGAN / "models" / f"{opts.ai_model}.bin",
                e._REAL_ESRGAN / "models" / f"{opts.ai_model}.param"
            ])
        if opts.region_mode == "subject_mask":
            dependencies.append(e._U2NETP)
        if opts.region_mode == "faces" or opts.face_detail:
            dependencies.append(e._YUNET)

        definition = JobDefinition.create(
            tool_id="image_enhancer", tool_version="3", workflow_version="smart-enhance.v3",
            inputs=files, settings=asdict(opts), identity_dependencies=dependencies
        )

        def classify(result: e.Result) -> ItemOutcome:
            if result.action == "enhanced":
                return ItemOutcome.completed(result.to_dict(), result.reason)
            if result.action == "needs-review":
                return ItemOutcome.warning(result.to_dict(), result.reason)
            if result.action == "dry-run":
                return ItemOutcome.skipped(result.to_dict(), result.reason)
            return ItemOutcome.failed(result.reason, data=result.to_dict())

        return QueueSubmission(
            definition=definition, label=f"Enhance · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts, cancelled=lambda: token.is_cancelled),
            classify=classify,
            validate_stored=lambda item: e.validate_result(self._result_from_record(item)),
            finalize=lambda report: self._prepare_queue_completion(report, opts, tool_id="image_enhancer"),
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined")

    def _queue_complete(self, completion: QueueCompletion) -> None:
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        completed = sum(item.action == "enhanced" for item in results)
        review = sum(item.action == "needs-review" for item in results)
        skipped = sum(item.action == "dry-run" for item in results)
        failed = len(results) - completed - skipped - review

        self._finish_queue_ui(
            f"enhanced {completed} · review {review} · previewed {skipped} · failed {failed}",
            job_state=self._queue_completion_state(completion),
            recovered=completion.report.recovered,
            reused=completion.report.reused,
            manifest=payload.manifest,
            report_path=payload.report_path
        )
