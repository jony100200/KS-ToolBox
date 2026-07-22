"""Material Converter UI; texture sets run through the shell-owned queue."""
from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.batch_reporting import completion_report_path, prepare_batch_completion
from toolbox.job_queue import QueueCompletion, QueueFinalization, QueueSubmission
from . import engine as e


class MaterialConverterPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "map"
    RESULTS_ICON = Icons.LAYERS
    RUN_LABEL = "Preview & Convert"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body

        # operation checkboxes
        ops = ctk.CTkFrame(b, fg_color="transparent"); ops.pack(fill="x")
        self._pack_orm = ctk.CTkCheckBox(ops, text="Pack ORM (R=AO · G=Rough · B=Metal)",
                                         font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._pack_orm.select(); self._pack_orm.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self._pack_mos = ctk.CTkCheckBox(ops, text="Pack Unity MOS (metallic/smoothness)",
                                         font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._pack_mos.grid(row=0, column=1, sticky="w", padx=(24, 0), pady=(0, 6))
        self._unpack = ctk.CTkCheckBox(ops, text="Unpack existing ORM → AO/Rough/Metal",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._unpack.grid(row=1, column=0, sticky="w", pady=(0, 6))
        self._gloss = ctk.CTkCheckBox(ops, text="Gloss → Roughness (invert)",
                                      font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._gloss.grid(row=1, column=1, sticky="w", padx=(24, 0), pady=(0, 6))

        # normal flip + target engine + resize
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(row2, text="Normal flip (DX↔GL)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._normal_flip = ctk.CTkOptionMenu(row2, values=["none", "dx2gl", "gl2dx"], width=120,
                                              fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                              button_hover_color=t.NEUTRAL_HOVER)
        self._normal_flip.set("none")
        self._normal_flip.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))

        ctk.CTkLabel(row2, text="Target engine", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._engine = ctk.CTkOptionMenu(row2, values=list(e.ENGINES), width=130,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER,
                                         button_hover_color=t.NEUTRAL_HOVER)
        self._engine.set("none")
        self._engine.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))

        ctk.CTkLabel(row2, text="Resize longest side (0=off)", text_color=t.TEXT_MUTED,
                     font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._resize = c.entry(row2, width=90); self._resize.insert(0, "0")
        self._resize.grid(row=1, column=2, sticky="w", pady=(2, 0))

        # output folder
        self._build_output_row(b, "Output folder (blank = ./converted beside each source)")

        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list sets — no writes)",
                                    font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure",
                                       font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)

        self._build_run_row(b)

    def _collect_options(self):
        try:
            resize_to = int(self._resize.get() or "0")
        except ValueError:
            self._logline("Resize value must be a whole number.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.MaterialOptions(
            out_root=out_root, input_root=input_root, mirror=mirror,
            pack_orm=bool(self._pack_orm.get()), pack_mos=bool(self._pack_mos.get()),
            unpack_orm=bool(self._unpack.get()), normal_flip=self._normal_flip.get(),
            gloss_to_rough=bool(self._gloss.get()), target_engine=self._engine.get(),
            resize_to=max(0, resize_to), dry_run=bool(self._dry.get()),
        )

    # -- queue submission (one coherent texture set per durable item) ----------
    def _build_submission(self, files: list[Path], opts: e.MaterialOptions) -> QueueSubmission:
        sets = e.detect_sets(files)
        self._logline(f"detected {len(sets)} texture set(s)", t.TEXT_MUTED)
        anchors: list[Path] = []
        sets_by_anchor: dict[str, e.TextureSet] = {}
        for texture_set in sets:
            anchor = next(iter(texture_set.maps.values())).expanduser().resolve(strict=False)
            anchors.append(anchor)
            sets_by_anchor[str(anchor)] = texture_set

        definition = JobDefinition.create(
            tool_id="material_converter",
            tool_version="1",
            workflow_version="material-set.v1",
            inputs=anchors,
            identity_dependencies=files,
            settings=asdict(opts),
        )

        def execute(anchor: Path, token) -> e.Result:
            return e.process_set(sets_by_anchor[str(anchor.resolve(strict=False))], opts)

        def classify(res: e.Result) -> ItemOutcome:
            data = res.to_dict()
            if res.action == "converted":
                return ItemOutcome.completed(data, res.reason)
            if res.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, res.reason)
            return ItemOutcome.failed(res.reason, data=data)

        return QueueSubmission(
            definition=definition,
            label=f"Material Converter · {len(sets)} set(s)",
            execute=execute,
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts
            ),
            finalize=lambda report: self._prepare_completion(report, opts),
        )

    def _prepare_completion(self, report, opts: e.MaterialOptions) -> QueueFinalization:
        return prepare_batch_completion(
            report,
            result_from_record=self._result_from_record,
            write_manifest=lambda results: self._write_manifest(opts, results),
            report_path=completion_report_path(
                "material_converter", report.job_id,
                out_root=opts.out_root, dry_run=opts.dry_run,
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        converted = sum(result.action == "converted" for result in results)
        skipped = sum(result.action in {"skipped", "dry-run"} for result in results)
        failed = len(results) - converted - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            converted, skipped, failed, remaining, payload.manifest,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"base", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            Path(item.input_path).stem,
            "failed",
            item.details or "texture set quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.MaterialOptions, results: list) -> str | None:
        """Aggregate CSV across all sets (the per-set JSON manifests are written
        by the engine). Only on a real run into an explicit output folder."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "material_manifest.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["base", "action", "roles", "outputs", "manifest", "reason"])
            for r in results:
                w.writerow([r.base, r.action, r.roles, len(r.outputs), r.manifest, r.reason])
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"converted": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        if res.action in ("converted", "dry-run"):
            extra = f"  {res.reason}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {res.base}{extra}", color)

    def _done(self, converted, skipped, failed, remaining=0, manifest=None,
              report_path=None, job_state=JobState.COMPLETED, recovered=False, reused=False):
        summary = f"converted {converted} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
