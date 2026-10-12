"""Unified Workflows engine for KS ToolBox.

Provides a lightweight, non-destructive pipeline orchestrator that chains
existing tool processing engines (e.g., Image Rescale -> Alpha Doctor -> Metadata Scrubber)
without introducing a new scheduler, graph editor, or foreign framework.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from toolbox.batch_core import CancellationToken
from toolbox.engine_common import IMAGE_EXTS

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    stage_id: str
    stage_name: str
    input_count: int
    output_files: list[Path] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    duration_s: float = 0.0


@dataclass
class WorkflowReport:
    workflow_id: str
    workflow_name: str
    input_folder: str
    output_folder: str
    preset_name: str
    total_inputs: int
    total_outputs: int
    stages: list[StageResult] = field(default_factory=list)
    manifest_path: str = ""
    summary_path: str = ""
    success: bool = True
    cancelled: bool = False
    duration_s: float = 0.0
    error: str = ""


@dataclass
class WorkflowPreset:
    id: str
    name: str
    description: str
    enable_rescale: bool = True
    rescale_longest_side: int = 1200
    rescale_mode: str = "longest_side"  # longest_side, fit_inside, scale_factor
    enable_alpha: bool = True
    alpha_method: str = "solid"         # solid, chroma, edge_flood
    alpha_defringe: bool = True
    alpha_despill: bool = False
    enable_scrub: bool = True
    scrub_keep_icc: bool = True


# Standard pre-configured presets
BUILTIN_PRESETS: dict[str, WorkflowPreset] = {
    "web_delivery": WorkflowPreset(
        id="web_delivery",
        name="Web & Portfolio Delivery",
        description="Resize to max 1200px, remove flat background, and strip all EXIF/GPS/AI metadata.",
        enable_rescale=True,
        rescale_longest_side=1200,
        enable_alpha=True,
        alpha_method="solid",
        alpha_defringe=True,
        enable_scrub=True,
        scrub_keep_icc=True,
    ),
    "transparent_cutout": WorkflowPreset(
        id="transparent_cutout",
        name="Transparent Asset Generator",
        description="Key out solid background, defringe alpha halo, and scrub metadata without resizing.",
        enable_rescale=False,
        enable_alpha=True,
        alpha_method="solid",
        alpha_defringe=True,
        enable_scrub=True,
        scrub_keep_icc=True,
    ),
    "clean_rescale": WorkflowPreset(
        id="clean_rescale",
        name="Clean Rescale & Privacy Scrub",
        description="Resize to 1920px longest side and strip all EXIF/GPS metadata (keeps original background).",
        enable_rescale=True,
        rescale_longest_side=1920,
        enable_alpha=False,
        enable_scrub=True,
        scrub_keep_icc=True,
    ),
    "privacy_only": WorkflowPreset(
        id="privacy_only",
        name="Metadata Privacy Scrub Only",
        description="Strip ComfyUI workflows, Stable Diffusion parameters, and camera GPS info losslessly.",
        enable_rescale=False,
        enable_alpha=False,
        enable_scrub=True,
        scrub_keep_icc=True,
    ),
}


class ImageDeliveryWorkflow:
    """Workflow B: Image & Web Delivery Pipeline.
    
    Chains:
      Stage 1 (optional): Image Rescale
      Stage 2 (optional): Alpha Doctor (Background Removal)
      Stage 3 (optional): Metadata Scrubber
      Stage 4: Atomic Commit & Manifest Generation
    """

    ID = "workflow_image_delivery"
    NAME = "Image & Web Delivery Pipeline"
    DESCRIPTION = "Automate multi-step image preparation: resize, background removal, and metadata scrubbing."

    def __init__(self, preset: WorkflowPreset | None = None) -> None:
        self.preset = preset or BUILTIN_PRESETS["web_delivery"]

    def discover_inputs(self, input_dir: Path | str) -> list[Path]:
        """Find valid image files in input directory."""
        path = Path(input_dir)
        if not path.is_dir():
            return []
        files = []
        for p in sorted(path.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                files.append(p)
        return files

    def preview(self, input_dir: Path | str, output_dir: Path | str) -> dict[str, Any]:
        """Dry-run preview of planned stages and outputs without writing files."""
        in_path = Path(input_dir)
        out_path = Path(output_dir)
        inputs = self.discover_inputs(in_path)

        stages_planned = []
        if self.preset.enable_rescale:
            stages_planned.append({
                "stage": "Image Rescale",
                "settings": f"Mode: {self.preset.rescale_mode}, Longest Side: {self.preset.rescale_longest_side}px",
            })
        if self.preset.enable_alpha:
            stages_planned.append({
                "stage": "Alpha Doctor",
                "settings": f"Method: {self.preset.alpha_method}, Defringe: {self.preset.alpha_defringe}",
            })
        if self.preset.enable_scrub:
            stages_planned.append({
                "stage": "Metadata Scrubber",
                "settings": f"Keep ICC: {self.preset.scrub_keep_icc}, Strip EXIF/GPS/AI-recipes",
            })

        planned_files = []
        for f in inputs:
            ext = ".png" if self.preset.enable_alpha else f.suffix
            planned_files.append(str(out_path / f"{f.stem}{ext}"))

        return {
            "workflow_name": self.NAME,
            "preset_name": self.preset.name,
            "input_count": len(inputs),
            "stages": stages_planned,
            "planned_outputs": planned_files,
            "destination_dir": str(out_path),
            "safe": str(in_path.resolve()) != str(out_path.resolve()),
        }

    def execute(
        self,
        input_dir: Path | str,
        output_dir: Path | str,
        *,
        cancellation: CancellationToken | None = None,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> WorkflowReport:
        """Execute the multi-stage pipeline non-destructively with staging isolation."""
        token = cancellation or CancellationToken()
        t0 = time.perf_counter()
        in_path = Path(input_dir).resolve()
        out_path = Path(output_dir).resolve()

        if in_path == out_path:
            return WorkflowReport(
                workflow_id=self.ID,
                workflow_name=self.NAME,
                input_folder=str(in_path),
                output_folder=str(out_path),
                preset_name=self.preset.name,
                total_inputs=0,
                total_outputs=0,
                success=False,
                error="Output folder cannot be the same as input folder (non-destructive safety).",
            )

        inputs = self.discover_inputs(in_path)
        if not inputs:
            return WorkflowReport(
                workflow_id=self.ID,
                workflow_name=self.NAME,
                input_folder=str(in_path),
                output_folder=str(out_path),
                preset_name=self.preset.name,
                total_inputs=0,
                total_outputs=0,
                success=False,
                error="No supported image files found in input directory.",
            )

        out_path.mkdir(parents=True, exist_ok=True)
        staging_dir = out_path / ".workflow_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)

        current_files = list(inputs)
        stages_results: list[StageResult] = []
        manifest_rows: list[dict[str, str]] = []

        try:
            # -------------------------------------------------------------
            # STAGE 1: Rescale
            # -------------------------------------------------------------
            if self.preset.enable_rescale and not token.is_cancelled:
                if on_progress:
                    on_progress("Rescaling images...", 0.1)
                stage_dir = staging_dir / "step1_rescale"
                stage_dir.mkdir(parents=True, exist_ok=True)
                s_res = self._run_stage_rescale(current_files, stage_dir, token)
                stages_results.append(s_res)
                if s_res.output_files:
                    current_files = s_res.output_files

            # -------------------------------------------------------------
            # STAGE 2: Alpha Doctor
            # -------------------------------------------------------------
            if self.preset.enable_alpha and not token.is_cancelled:
                if on_progress:
                    on_progress("Removing backgrounds (Alpha Doctor)...", 0.4)
                stage_dir = staging_dir / "step2_alpha"
                stage_dir.mkdir(parents=True, exist_ok=True)
                s_res = self._run_stage_alpha(current_files, stage_dir, token)
                stages_results.append(s_res)
                if s_res.output_files:
                    current_files = s_res.output_files

            # -------------------------------------------------------------
            # STAGE 3: Metadata Scrubber
            # -------------------------------------------------------------
            if self.preset.enable_scrub and not token.is_cancelled:
                if on_progress:
                    on_progress("Scrubbing metadata & EXIF...", 0.7)
                stage_dir = staging_dir / "step3_scrub"
                stage_dir.mkdir(parents=True, exist_ok=True)
                s_res = self._run_stage_scrub(current_files, stage_dir, token)
                stages_results.append(s_res)
                if s_res.output_files:
                    current_files = s_res.output_files

            if token.is_cancelled:
                return WorkflowReport(
                    workflow_id=self.ID,
                    workflow_name=self.NAME,
                    input_folder=str(in_path),
                    output_folder=str(out_path),
                    preset_name=self.preset.name,
                    total_inputs=len(inputs),
                    total_outputs=0,
                    stages=stages_results,
                    success=False,
                    cancelled=True,
                    duration_s=time.perf_counter() - t0,
                    error="Workflow cancelled by user.",
                )

            # -------------------------------------------------------------
            # STAGE 4: Commit to final output & build manifest
            # -------------------------------------------------------------
            if on_progress:
                on_progress("Publishing final outputs...", 0.9)

            final_files: list[Path] = []
            for src_file in current_files:
                dest = out_path / src_file.name
                shutil.copy2(src_file, dest)
                final_files.append(dest)
                manifest_rows.append({
                    "original_source": str(src_file.name),
                    "final_output": str(dest.name),
                    "status": "success",
                    "size_bytes": str(dest.stat().st_size),
                })

            # Write workflow_manifest.csv
            manifest_csv = out_path / "workflow_manifest.csv"
            with manifest_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["original_source", "final_output", "status", "size_bytes"]
                )
                writer.writeheader()
                writer.writerows(manifest_rows)

            # Write workflow_summary.json
            summary_json = out_path / "workflow_summary.json"
            summary_data = {
                "workflow_id": self.ID,
                "workflow_name": self.NAME,
                "preset": asdict(self.preset),
                "timestamp": time.time(),
                "inputs_processed": len(inputs),
                "outputs_delivered": len(final_files),
                "duration_seconds": round(time.perf_counter() - t0, 3),
            }
            summary_json.write_text(json.dumps(summary_data, indent=2), encoding="utf-8")

            # Clean up staging directory
            shutil.rmtree(staging_dir, ignore_errors=True)

            if on_progress:
                on_progress("Complete!", 1.0)

            return WorkflowReport(
                workflow_id=self.ID,
                workflow_name=self.NAME,
                input_folder=str(in_path),
                output_folder=str(out_path),
                preset_name=self.preset.name,
                total_inputs=len(inputs),
                total_outputs=len(final_files),
                stages=stages_results,
                manifest_path=str(manifest_csv),
                summary_path=str(summary_json),
                success=True,
                cancelled=False,
                duration_s=time.perf_counter() - t0,
            )

        except Exception as ex:
            logger.exception("Workflow failed with unhandled exception")
            shutil.rmtree(staging_dir, ignore_errors=True)
            return WorkflowReport(
                workflow_id=self.ID,
                workflow_name=self.NAME,
                input_folder=str(in_path),
                output_folder=str(out_path),
                preset_name=self.preset.name,
                total_inputs=len(inputs),
                total_outputs=0,
                stages=stages_results,
                success=False,
                error=str(ex),
                duration_s=time.perf_counter() - t0,
            )

    def _run_stage_rescale(
        self, files: list[Path], stage_dir: Path, token: CancellationToken
    ) -> StageResult:
        from tools.image_rescale.engine import ResizeOptions, process

        t0 = time.perf_counter()
        opts = ResizeOptions(
            out_root=stage_dir,
            mode=self.preset.rescale_mode,
            longest_side=self.preset.rescale_longest_side,
            keep_format=True,
            dry_run=False,
        )

        outputs: list[Path] = []
        errors = []
        success = 0
        failed = 0

        for f in files:
            if token.is_cancelled:
                break
            res = process(f, opts)
            if res.action == "resized" and res.out_path and Path(res.out_path).is_file():
                outputs.append(Path(res.out_path))
                success += 1
            elif res.action == "skipped":
                # Already at target dimensions; copy to stage_dir
                dest = stage_dir / f.name
                shutil.copy2(f, dest)
                outputs.append(dest)
                success += 1
            else:
                failed += 1
                errors.append({"file": str(f), "error": res.reason or "rescale failed"})

        return StageResult(
            stage_id="stage_rescale",
            stage_name="Image Rescale",
            input_count=len(files),
            output_files=outputs,
            success_count=success,
            failure_count=failed,
            errors=errors,
            duration_s=time.perf_counter() - t0,
        )

    def _run_stage_alpha(
        self, files: list[Path], stage_dir: Path, token: CancellationToken
    ) -> StageResult:
        from tools.alpha_doctor.engine import AlphaOptions, process

        t0 = time.perf_counter()
        opts = AlphaOptions(
            out_root=stage_dir,
            method=self.preset.alpha_method,
            do_defringe=self.preset.alpha_defringe,
            green_despill=self.preset.alpha_despill,
            dry_run=False,
        )

        outputs: list[Path] = []
        errors = []
        success = 0
        failed = 0

        for f in files:
            if token.is_cancelled:
                break
            res = process(f, opts)
            if res.action == "cut" and res.out_path and Path(res.out_path).is_file():
                outputs.append(Path(res.out_path))
                success += 1
            elif res.action == "skipped":
                # Passthrough
                dest = stage_dir / f.name
                shutil.copy2(f, dest)
                outputs.append(dest)
                success += 1
            else:
                failed += 1
                errors.append({"file": str(f), "error": res.reason or "alpha keying failed"})

        return StageResult(
            stage_id="stage_alpha",
            stage_name="Alpha Doctor",
            input_count=len(files),
            output_files=outputs,
            success_count=success,
            failure_count=failed,
            errors=errors,
            duration_s=time.perf_counter() - t0,
        )

    def _run_stage_scrub(
        self, files: list[Path], stage_dir: Path, token: CancellationToken
    ) -> StageResult:
        from tools.metadata_scrubber.engine import ScrubOptions, process

        t0 = time.perf_counter()
        opts = ScrubOptions(
            out_root=stage_dir,
            keep_icc=self.preset.scrub_keep_icc,
            dry_run=False,
        )

        outputs: list[Path] = []
        errors = []
        success = 0
        failed = 0

        for f in files:
            if token.is_cancelled:
                break
            res = process(f, opts)
            if res.action in ("scrubbed", "copied") and res.out_path and Path(res.out_path).is_file():
                outputs.append(Path(res.out_path))
                success += 1
            else:
                failed += 1
                errors.append({"file": str(f), "error": res.reason or "scrub failed"})

        return StageResult(
            stage_id="stage_scrub",
            stage_name="Metadata Scrubber",
            input_count=len(files),
            output_files=outputs,
            success_count=success,
            failure_count=failed,
            errors=errors,
            duration_s=time.perf_counter() - t0,
        )
