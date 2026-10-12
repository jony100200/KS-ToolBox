"""Image to PBR UI — Presentation layer over engine.py via BaseBatchPanel."""
from __future__ import annotations

import csv
import os
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e


class ImageToPbrPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.LAYERS
    RUN_LABEL = "Generate PBR Maps"

    # -- options (tool-specific) -----------------------------------------------

    def _build_options_card(self):
        card = c.Card(self, "PBR Generation Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # Row 1: Engine + Preset
        row1 = ctk.CTkFrame(b, fg_color="transparent")
        row1.pack(fill="x")

        ctk.CTkLabel(row1, text="Generator Engine", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._engine_menu = ctk.CTkOptionMenu(
            row1,
            values=[e.ENGINE_LABELS[k] for k in e.ENGINES],
            width=200,
            command=self._on_engine_change,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._engine_menu.set(e.ENGINE_LABELS["builtin"])
        self._engine_menu.grid(row=1, column=0, sticky="w", padx=(0, 20), pady=(2, 0))

        ctk.CTkLabel(row1, text="Material Preset", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        preset_labels = [p["label"] for p in e.PRESETS.values()]
        self._preset_menu = ctk.CTkOptionMenu(
            row1,
            values=preset_labels,
            width=180,
            command=self._on_preset_change,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._preset_menu.set(e.PRESETS["wood"]["label"])
        self._preset_menu.grid(row=1, column=1, sticky="w", padx=(0, 20), pady=(2, 0))

        ctk.CTkLabel(row1, text="Normal Format", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._normal_format = ctk.CTkOptionMenu(
            row1,
            values=["OpenGL (+Y)", "DirectX (-Y)"],
            width=120,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._normal_format.set("OpenGL (+Y)")
        self._normal_format.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # Row 2: Resolution + ORM Packing
        row2 = ctk.CTkFrame(b, fg_color="transparent")
        row2.pack(fill="x", pady=(10, 0))

        ctk.CTkLabel(row2, text="Output Resolution", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._resolution = ctk.CTkOptionMenu(
            row2,
            values=["Original Size", "1024 x 1024", "2048 x 2048", "4096 x 4096"],
            width=140,
            fg_color=t.BG_COLOR,
            button_color=t.CARD_BORDER,
            button_hover_color=t.NEUTRAL_HOVER,
        )
        self._resolution.set("Original Size")
        self._resolution.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))

        self._pack_orm = ctk.CTkCheckBox(
            row2,
            text="Pack ORM Map (R=AO · G=Roughness · B=Metallic)",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._pack_orm.select()
        self._pack_orm.grid(row=1, column=1, sticky="w", pady=(18, 0))

        # Row 3: Output folder
        self._build_output_row(b, "Output folder (blank = ./pbr_maps beside each source image)")

        # Row 4: Toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent")
        toggles.pack(fill="x", pady=(12, 0))

        self._dry = ctk.CTkCheckBox(
            toggles,
            text="Preview only (list outputs — no generation)",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._dry.pack(side="left")

        self._mirror = ctk.CTkCheckBox(
            toggles,
            text="Mirror input structure",
            font=t.font(11),
            fg_color=t.ACCENT_BLUE,
        )
        self._mirror.select()
        self._mirror.pack(side="left", padx=20)

        # Run row
        self._build_run_row(b)

    def _on_engine_change(self, selected_label: str):
        pass

    def _on_preset_change(self, selected_label: str):
        pass

    def _resolve_engine_key(self) -> str:
        label = self._engine_menu.get()
        for k, v in e.ENGINE_LABELS.items():
            if v == label:
                return k
        return "builtin"

    def _resolve_preset_key(self) -> str:
        label = self._preset_menu.get()
        for k, v in e.PRESETS.items():
            if v["label"] == label:
                return k
        return "wood"

    def _collect_options(self) -> e.PbrOptions | None:
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None

        res_str = self._resolution.get()
        if "1024" in res_str:
            resize_to = 1024
        elif "2048" in res_str:
            resize_to = 2048
        elif "4096" in res_str:
            resize_to = 4096
        else:
            resize_to = 0

        normal_fmt = "directx" if "DirectX" in self._normal_format.get() else "opengl"

        return e.PbrOptions(
            engine=self._resolve_engine_key(),
            preset_name=self._resolve_preset_key(),
            normal_format=normal_fmt,
            pack_orm=bool(self._pack_orm.get()),
            resize_to=resize_to,
            out_root=out_root,
            input_root=input_root,
            mirror=mirror,
            dry_run=bool(self._dry.get()),
        )

    def _pre_run_check(self, opts: e.PbrOptions) -> bool:
        if opts.engine == "sampler":
            sampler_exe = opts.sampler_exe or e.DEFAULT_SAMPLER_EXE
            if not os.path.isfile(sampler_exe):
                self._logline(
                    f"Substance Sampler not found at '{sampler_exe}'. Please verify installation.",
                    t.STATE["error"][1],
                )
                return False
        return True

    # -- batch loop fallback ---------------------------------------------------

    def _work(self, files: list[Path], opts: e.PbrOptions):
        success = failed = skipped = 0
        results: list[e.PbrItemResult] = []

        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED)
                break
            self.after(0, self._logline, f"[{i}/{len(files)}] Processing {f.name} …", t.TEXT_MUTED)
            res = e.process_single_image(f, opts, cancelled=lambda: self._stop.is_set())
            results.append(res)
            if res.status == "ok":
                if opts.dry_run:
                    skipped += 1
                else:
                    success += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))

        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, success, skipped, failed, manifest)

    def _build_submission(self, files: list[Path], opts: e.PbrOptions) -> QueueSubmission:
        definition = JobDefinition.create(
            tool_id="image_to_pbr",
            tool_version="1",
            workflow_version="pbr-generator.v1",
            inputs=files,
            settings=asdict(opts),
        )

        def classify(result: e.PbrItemResult) -> ItemOutcome:
            data = {
                "source": str(result.source_path),
                "status": result.status,
                "message": result.message,
                "files": [str(p) for p in result.generated_files],
            }
            if result.status == "ok":
                if opts.dry_run:
                    return ItemOutcome.skipped(data, result.message)
                return ItemOutcome.completed(data, result.message)
            return ItemOutcome.failed(result.message, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Image to PBR · {len(files)} image(s)",
            execute=lambda path, token: e.process_single_image(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: True,
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="image_to_pbr"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        success = sum(r.status == "ok" and not getattr(r, "dry_run", False) for r in results)
        skipped = sum(getattr(r, "dry_run", False) for r in results)
        failed = len(results) - success - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            success, skipped, failed, payload.manifest, remaining,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.PbrItemResult:
        if item.data and "source" in item.data:
            return e.PbrItemResult(
                source_path=Path(item.data["source"]),
                status=item.data.get("status", "ok"),
                generated_files=[Path(p) for p in item.data.get("files", [])],
                message=item.data.get("message", ""),
            )
        return e.PbrItemResult(
            source_path=item.input_path,
            status="error",
            message=item.details or "Item quarantined",
        )

    def _write_manifest(self, opts: e.PbrOptions, results: list[e.PbrItemResult]) -> str | None:
        if opts.dry_run or not results:
            return None
        dest_root = opts.out_root or (results[0].source_path.parent / "pbr_maps")
        manifest_path = dest_root / "pbr_manifest.csv"
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["source_image", "status", "message", "generated_files_count", "files"])
                for r in results:
                    file_list = ";".join(str(p.name) for p in r.generated_files)
                    w.writerow([str(r.source_path.name), r.status, r.message, len(r.generated_files), file_list])
            return str(manifest_path)
        except OSError:
            return None

    def _show(self, res: e.PbrItemResult, i: int, total: int):
        self._progress.set(i / total)
        icon = "✓" if res.status == "ok" else "✗"
        color = t.STATE["done"][1] if res.status == "ok" else t.STATE["error"][1]
        name = res.source_path.name
        detail = f"  → {len(res.generated_files)} maps generated" if res.status == "ok" else f"  — {res.message}"
        self._logline(f"  {icon} {name}{detail}", color)

    def _done(self, success: int, skipped: int, failed: int, manifest=None, remaining=0,
              report_path=None, job_state=JobState.COMPLETED,
              recovered=False, reused=False):
        summary = f"generated {success} · previewed {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
