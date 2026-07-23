"""Alpha Doctor — the tool's UI. Thin over engine.py. Deterministic methods by
default; the AI method is just one more choice in the dropdown.
"""
from __future__ import annotations

import csv
import importlib.util
from dataclasses import asdict
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from toolbox.batch_core import ItemOutcome, ItemRecord, JobDefinition, JobState
from toolbox.job_queue import QueueCompletion, QueueSubmission
from . import engine as e

_METHOD_LABELS = {
    "solid": "Auto solid background",
    "chroma": "Chroma key (pick colour)",
    "edge_flood": "Edge flood-fill",
    "ai": "AI matte (u2net — model optional)",
}

_AI_MODEL_LABELS = {
    "u2netp": "Compact U2NetP (about 5 MB — recommended)",
    "u2net": "Full U2Net (about 176 MB — higher detail)",
}


class AlphaDoctorPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "image"
    RESULTS_ICON = Icons.BROOM
    RUN_LABEL = "Remove Background & Save"

    def __init__(self, parent, queue_service):
        super().__init__(parent, queue_service=queue_service)
        self._on_method(self._method.get())

    # -- options ---------------------------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Method", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._method = ctk.CTkOptionMenu(row, values=[_METHOD_LABELS[m] for m in e.METHODS], width=240,
                                         command=self._on_method, fg_color=t.BG_COLOR,
                                         button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._method.set(_METHOD_LABELS["solid"]); self._method.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        ctk.CTkLabel(row, text="Tolerance", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        self._tol = c.entry(row, width=70); self._tol.insert(0, "100")
        self._tol.grid(row=1, column=1, sticky="w", padx=(0, 24), pady=(2, 0))
        # chroma key colour (shown for chroma method)
        self._key_label = ctk.CTkLabel(row, text="Key colour", text_color=t.TEXT_MUTED, font=t.font(11))
        self._key_label.grid(row=0, column=2, sticky="w")
        self._key = ctk.CTkOptionMenu(row, values=list(e.KEY_PRESETS) + ["custom"], width=90,
                                      fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._key.set("green"); self._key.grid(row=1, column=2, sticky="w", padx=(0, 8), pady=(2, 0))
        self._key_hex = c.entry(row, width=90); self._key_hex.insert(0, "#00FF00")
        self._key_hex.grid(row=1, column=3, sticky="w", pady=(2, 0))
        # AI model choice (shown only for AI matte).  Keep the small model first:
        # this tool is intended to stay useful on ordinary CPU-only installs.
        self._model_label = ctk.CTkLabel(row, text="AI model", text_color=t.TEXT_MUTED, font=t.font(11))
        self._model_label.grid(row=0, column=4, sticky="w", padx=(24, 0))
        self._model = ctk.CTkOptionMenu(
            row, values=[_AI_MODEL_LABELS[name] for name in e.AI_MODELS], width=275,
            fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER,
            command=lambda _label: self._on_method(self._method.get()),
        )
        self._model.set(_AI_MODEL_LABELS["u2netp"])
        self._model.grid(row=1, column=4, sticky="w", padx=(24, 0), pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./cutouts beside each source)")
        # post-op toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(
            toggles, text="Plan only (no cut is calculated or saved)",
            font=t.font(11), fg_color=t.ACCENT_BLUE, command=self._refresh_run_action,
        )
        self._dry.pack(side="left")
        self._defringe = ctk.CTkCheckBox(toggles, text="Defringe", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._defringe.select(); self._defringe.pack(side="left", padx=16)
        self._green = ctk.CTkCheckBox(toggles, text="Green despill", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._green.pack(side="left", padx=16)
        self._premul = ctk.CTkCheckBox(toggles, text="Premultiply", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._premul.pack(side="left", padx=16)
        row2 = ctk.CTkFrame(b, fg_color="transparent"); row2.pack(fill="x", pady=(6, 0))
        self._mirror = ctk.CTkCheckBox(row2, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left")
        self._hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10)); self._hint.pack(anchor="w", pady=(6, 0))
        self._build_run_row(b)

    def _on_method(self, _label: str):
        method = self._current_method()
        chroma = method == "chroma"
        for wdg in (self._key_label, self._key, self._key_hex):
            (wdg.grid() if chroma else wdg.grid_remove())
        for wdg in (self._model_label, self._model):
            (wdg.grid() if method == "ai" else wdg.grid_remove())
        hints = {"solid": "Deterministic — keys out the auto-detected flat background. No model.",
                 "chroma": "Deterministic — keys the chosen colour. Great for green/blue screen, flat logos.",
                 "edge_flood": "Deterministic — removes background regions touching the image border.",
                 "ai": self._ai_hint()}
        self._hint.configure(text=hints.get(method, ""))

    def _ai_hint(self) -> str:
        model = self._current_model()
        if importlib.util.find_spec("onnxruntime") is None:
            return "AI matte needs optional onnxruntime. Install requirements-optional.txt, then reopen ToolBox."
        if e.cached_model_path(model) is None:
            return f"{_AI_MODEL_LABELS[model]} will be downloaded only after you approve it. CPU-only."
        return f"{_AI_MODEL_LABELS[model]} is installed and ready. CPU-only."

    def _refresh_run_action(self) -> None:
        if not hasattr(self, "_run_btn"):
            return
        if bool(self._dry.get()):
            self._run_btn.configure(text="Plan Outputs (No Files)")
        else:
            self._run_btn.configure(text=self.RUN_LABEL)

    def _current_method(self) -> str:
        label = self._method.get()
        return next(m for m, lbl in _METHOD_LABELS.items() if lbl == label)

    def _current_model(self) -> str:
        label = self._model.get()
        return next(name for name, value in _AI_MODEL_LABELS.items() if value == label)

    def _collect_options(self):
        try:
            tol = float(self._tol.get())
        except ValueError:
            self._logline("Tolerance must be a number.", t.STATE["error"][1]); return None
        key_hex = self._key_hex.get().strip() if self._key.get() == "custom" else e.KEY_PRESETS.get(self._key.get(), "#00FF00")
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        return e.AlphaOptions(out_root=out_root, input_root=self._resolve_input_root() if mirror else None,
                              mirror=mirror, method=self._current_method(), key_color=key_hex, tolerance=tol,
                              model=self._current_model(),
                              do_defringe=bool(self._defringe.get()), green_despill=bool(self._green.get()),
                              do_premultiply=bool(self._premul.get()), dry_run=bool(self._dry.get()))

    def _pre_run_check(self, opts: e.AlphaOptions) -> bool:
        normalized, options_error = e.normalized_options(opts)
        if normalized is None:
            self._logline(options_error, t.STATE["error"][1])
            return False
        if not self._check_output_collisions(
            e.find_output_collisions(self._files, normalized)
        ):
            return False
        if normalized.method != "ai":
            return True
        if importlib.util.find_spec("onnxruntime") is None:
            self._logline(
                "AI matte needs onnxruntime. Run: python -m pip install -r requirements-optional.txt, then reopen ToolBox.",
                t.STATE["error"][1],
            )
            return False
        if e.cached_model_path(normalized.model) is not None:
            return True
        approved = messagebox.askyesno(
            "Download AI matte model?",
            f"AI matte needs {_AI_MODEL_LABELS[normalized.model]}. It will be "
            "downloaded from the rembg GitHub release and stored in the "
            "ToolBox model cache.\n\nAllow this network download?",
            icon="question",
            parent=self,
        )
        if not approved:
            self._logline(
                "AI model download not approved; no network request was made.",
                t.TEXT_MUTED,
            )
            return False
        opts.allow_model_download = True
        return True

    # -- batch loop ------------------------------------------------------------
    def _work(self, files: list[Path], opts: e.AlphaOptions):
        cut = skipped = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(
                f, opts, cancelled=lambda: self._stop.is_set()
            ); results.append(res)
            if res.action == "cut":
                cut += 1
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, cut, skipped, failed, manifest)

    def _build_submission(
        self, files: list[Path], opts: e.AlphaOptions
    ) -> QueueSubmission:
        settings = asdict(opts)
        settings.pop("allow_model_download")
        definition = JobDefinition.create(
            tool_id="alpha_doctor",
            tool_version="1",
            workflow_version="alpha-cutout.v2",
            inputs=files,
            settings=settings,
            max_retries=1,
        )

        def classify(result: e.Result) -> ItemOutcome:
            data = result.to_dict()
            if result.action == "cut":
                if result.degraded:
                    return ItemOutcome.warning(data, result.reason)
                return ItemOutcome.completed(data, result.reason)
            if result.action in {"skipped", "dry-run"}:
                return ItemOutcome.skipped(data, result.reason)
            return ItemOutcome.failed(
                result.reason, retryable=result.retryable, data=data
            )

        return QueueSubmission(
            definition=definition,
            label=f"Alpha Doctor {opts.method.replace('_', ' ').title()} · {len(files)} image(s)",
            execute=lambda path, token: e.process(
                path, opts, cancelled=lambda: token.is_cancelled
            ),
            classify=classify,
            validate_stored=lambda item: e.validate_result(
                self._result_from_record(item), opts, expected_src=item.input_path
            ),
            finalize=lambda report: self._prepare_queue_completion(
                report, opts, tool_id="alpha_doctor"
            ),
        )

    def _queue_complete(self, completion: QueueCompletion) -> None:
        report = completion.report
        payload = self._consume_queue_completion(completion)
        if payload is None:
            return
        results = payload.results
        cut = sum(result.action == "cut" for result in results)
        skipped = sum(result.action in {"skipped", "dry-run"} for result in results)
        failed = len(results) - cut - skipped
        remaining = len(report.items) - len(payload.finished_items)
        self._done(
            cut, skipped, failed, payload.manifest, remaining,
            payload.report_path, self._queue_completion_state(completion),
            report.recovered, report.reused,
        )

    @staticmethod
    def _result_from_record(item: ItemRecord) -> e.Result:
        if item.data and {"src", "action", "reason"}.issubset(item.data):
            return e.Result(**item.data)
        return e.Result(
            item.input_path, "failed", item.details or "item quarantined",
            detail="batch.quarantined",
        )

    def _write_manifest(self, opts: e.AlphaOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "cutout_manifest.csv"
        candidate = root / "cutout_manifest.part.csv"
        try:
            with open(candidate, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "src", "action", "coverage", "out_path", "detail", "reason"
                ])
                for result in results:
                    writer.writerow([
                        result.src, result.action, f"{result.coverage:.3f}",
                        result.out_path, result.detail, result.reason,
                    ])
            candidate.replace(path)
        except BaseException as ex:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup:
                raise OSError(
                    f"manifest failed and staged cleanup failed: {cleanup}"
                ) from ex
            raise
        return str(path)

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"cut": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"cut": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        extra = (f"  {res.coverage*100:.0f}% kept  ({res.detail})  → {res.out_path}"
                 if res.action == "cut" else f"  — {res.reason}")
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, cut, skipped, failed, manifest=None, remaining=0,
              report_path=None, job_state=JobState.COMPLETED,
              recovered=False, reused=False):
        summary = f"cut {cut} · skipped {skipped} · failed {failed}"
        if remaining:
            summary += f" · remaining {remaining}"
        self._finish_queue_ui(
            summary, job_state=job_state, recovered=recovered, reused=reused,
            manifest=manifest, report_path=report_path,
        )
