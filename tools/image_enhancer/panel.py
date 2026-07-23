"""Image Enhancer presentation layer; work runs only through the shell queue."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_panel import BaseBatchPanel
from toolbox.icons import Icons
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class ImageEnhancerPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BOLT
    RUN_LABEL = "Enhance Selected"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    def _build_options_card(self):
        card = c.Card(self, "Local enhancement", icon=Icons.BOLT)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body
        row = ctk.CTkFrame(body, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Restore preset", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._preset = ctk.CTkOptionMenu(row, values=list(e.filter_stack.preset_names()), width=160,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._preset.pack(side="left", padx=(8, 22)); self._preset.set("gentle_restore")
        ctk.CTkLabel(row, text="AI scale", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._scale = ctk.CTkOptionMenu(row, values=["1", "2", "3", "4"], width=72,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._scale.pack(side="left", padx=(8, 22)); self._scale.set("1")
        ctk.CTkLabel(row, text="AI model", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._model = ctk.CTkOptionMenu(row, values=["realesrgan-x4plus", "realesrgan-x4plus-anime"], width=205,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._model.pack(side="left", padx=(8, 0)); self._model.set("realesrgan-x4plus")

        row2 = ctk.CTkFrame(body, fg_color="transparent"); row2.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row2, text="Target region", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left")
        self._region = ctk.CTkOptionMenu(row2, values=["none", "subject_mask", "faces", "manual_box"], width=150,
                                          command=self._region_changed, fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._region.pack(side="left", padx=(8, 18)); self._region.set("none")
        self._box_label = ctk.CTkLabel(row2, text="Box x,y,w,h (%)", text_color=t.TEXT_MUTED, font=t.font(11))
        self._box = c.entry(row2, width=160); self._box.insert(0, "25,25,50,50")
        self._face_detail = ctk.CTkCheckBox(row2, text="Face-detail AI", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._face_detail.pack(side="left", padx=(18, 0))

        row3 = ctk.CTkFrame(body, fg_color="transparent"); row3.pack(fill="x", pady=(10, 0))
        self._repair = ctk.CTkCheckBox(row3, text="Repair enclosed transparent holes", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._repair.pack(side="left")
        self._debug = ctk.CTkCheckBox(row3, text="Save region masks / BBox previews", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._debug.pack(side="left", padx=20)
        self._dry = ctk.CTkCheckBox(row3, text="Preview only (no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.pack(side="left", padx=20)
        self._mirror = ctk.CTkCheckBox(row3, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)

        advanced = ctk.CTkFrame(body, fg_color="transparent"); advanced.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(advanced, text="Stack controls (applied after profile)", text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 14))
        self._advanced: dict[str, ctk.CTkEntry] = {}
        self._advanced_entry(advanced, "Brightness", "brightness", "1.0")
        self._advanced_entry(advanced, "Contrast", "contrast", "1.0")
        self._advanced_entry(advanced, "Gamma", "gamma", "1.0")
        self._advanced_entry(advanced, "Hue°", "hue_degrees", "0")
        self._advanced_entry(advanced, "Saturation", "saturation", "1.0")
        advanced2 = ctk.CTkFrame(body, fg_color="transparent"); advanced2.pack(fill="x", pady=(6, 0))
        self._advanced_entry(advanced2, "Vibrance", "vibrance", "0")
        self._advanced_entry(advanced2, "Temp", "temperature", "0")
        self._advanced_entry(advanced2, "Tint", "tint", "0")
        self._advanced_entry(advanced2, "Denoise", "denoise", "0")
        self._advanced_entry(advanced2, "Sharpen", "sharpen", "1.0")
        self._advanced_entry(advanced2, "High-pass", "high_pass", "0")
        self._advanced_entry(advanced2, "Edge", "edge_boost", "0")

        self._status = ctk.CTkLabel(body, text="", text_color=t.TEXT_MUTED, font=t.font(11), anchor="w")
        self._status.pack(fill="x", pady=(10, 0)); self._refresh_status()
        self._build_output_row(body, "Output folder (blank = ./enhanced beside each source)")
        self._build_run_row(body)
        self._region_changed("none")

    def _advanced_entry(self, row, label: str, key: str, default: str):
        ctk.CTkLabel(row, text=label, text_color=t.TEXT_MUTED, font=t.font(10)).pack(side="left", padx=(0, 4))
        entry = c.entry(row, width=58); entry.insert(0, default); entry.pack(side="left", padx=(0, 10))
        self._advanced[key] = entry

    def _region_changed(self, mode: str):
        if mode == "manual_box":
            self._box_label.pack(side="left", padx=(0, 6)); self._box.pack(side="left")
        else:
            self._box_label.pack_forget(); self._box.pack_forget()
        if mode != "faces":
            self._face_detail.deselect()

    def _refresh_status(self):
        state = e.utility_status()
        ready = all(bool(state[key]) for key in ("upscale_ready", "segmentation_ready", "faces_ready", "repair_ready"))
        self._status.configure(text=str(state["details"]), text_color=t.STATE["done"][1] if ready else t.STATE["waiting"][1])

    def _collect_options(self):
        region = self._region.get()
        if self._face_detail.get() and region == "none":
            region = "faces"
        box = None
        if region == "manual_box":
            try:
                box = tuple(float(value.strip()) for value in self._box.get().split(","))
                if len(box) != 4: raise ValueError
            except ValueError:
                self._logline("Manual box must be x,y,width,height percentages.", t.STATE["error"][1]); return None
        try:
            scale = int(self._scale.get())
        except ValueError:
            return None
        try:
            advanced = {key: float(entry.get().strip()) for key, entry in self._advanced.items()}
        except ValueError:
            self._logline("Stack controls must be valid numbers.", t.STATE["error"][1]); return None
        state = e.utility_status()
        if scale > 1 and not state["upscale_ready"]:
            self._logline("AI upscale is not ready: " + str(state["details"]), t.STATE["error"][1]); return None
        if region == "subject_mask" and not state["segmentation_ready"]:
            self._logline("Subject segmentation is not ready: " + str(state["details"]), t.STATE["error"][1]); return None
        if (region == "faces" or self._face_detail.get()) and not state["faces_ready"]:
            self._logline("Face detail is not ready: " + str(state["details"]), t.STATE["error"][1]); return None
        if self._repair.get() and not state["repair_ready"]:
            self._logline("Local repair is not ready: " + str(state["details"]), t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        return e.EnhanceOptions(
            out_root=Path(out) if out else None, input_root=self._resolve_input_root() if self._mirror.get() else None,
            mirror=bool(self._mirror.get()), preset=self._preset.get(), scale_factor=scale, ai_model=self._model.get(),
            region_mode=region, manual_box=box, face_detail=bool(self._face_detail.get()),
            repair_alpha_holes=bool(self._repair.get()), debug_outputs=bool(self._debug.get()), dry_run=bool(self._dry.get()),
            **advanced,
        )

    def _build_submission(self, files: list[Path], opts: e.EnhanceOptions) -> QueueSubmission:
        dependencies = []
        if opts.scale_factor > 1 or opts.face_detail:
            dependencies.extend([e._REAL_ESRGAN / "realesrgan-ncnn-vulkan.exe", e._REAL_ESRGAN / "models" / f"{opts.ai_model}.bin", e._REAL_ESRGAN / "models" / f"{opts.ai_model}.param"])
        if opts.region_mode == "subject_mask": dependencies.append(e._U2NETP)
        if opts.region_mode == "faces" or opts.face_detail: dependencies.append(e._YUNET)
        definition = JobDefinition.create(tool_id="image_enhancer", tool_version="1", workflow_version="enhance.v1",
                                          inputs=files, settings=asdict(opts), identity_dependencies=dependencies)
        def classify(result: e.Result) -> ItemOutcome:
            if result.action == "enhanced": return ItemOutcome.completed(result.to_dict(), result.reason)
            if result.action == "dry-run": return ItemOutcome.skipped(result.to_dict(), result.reason)
            return ItemOutcome.failed(result.reason, data=result.to_dict())
        return QueueSubmission(
            definition=definition, label=f"Image Enhancer · {len(files)} file(s)",
            execute=lambda path, token: e.process(path, opts, cancelled=lambda: token.is_cancelled), classify=classify,
            validate_stored=lambda item: e.validate_result(self._result_from_record(item)),
            finalize=lambda report: self._prepare_queue_completion(report, opts, tool_id="image_enhancer"),
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data): return e.Result(**item.data)
        return e.Result(item.input_path, "failed", item.details or "item quarantined")

    def _queue_complete(self, completion: QueueCompletion) -> None:
        payload = self._consume_queue_completion(completion)
        if payload is None: return
        results = payload.results; completed = sum(item.action == "enhanced" for item in results)
        skipped = sum(item.action == "dry-run" for item in results); failed = len(results) - completed - skipped
        self._finish_queue_ui(f"enhanced {completed} · previewed {skipped} · failed {failed}",
                              job_state=self._queue_completion_state(completion), recovered=completion.report.recovered,
                              reused=completion.report.reused, manifest=payload.manifest, report_path=payload.report_path)
