"""Format Converter — the tool's UI. Thin over engine.py. The one twist: the
"Convert to" targets depend on the kinds of files loaded, so the dropdown
refreshes whenever the file list changes.
"""
from __future__ import annotations

import csv
from pathlib import Path

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from toolbox.batch_panel import BaseBatchPanel
from . import engine as e

_ALL_TARGETS = sorted({tgt for (_k, tgt) in e.DISPATCH})


class FormatConverterPanel(BaseBatchPanel):
    FILE_EXTS = e.ALL_EXTS
    FILE_LABEL = "file"
    RESULTS_ICON = Icons.ARROW
    RUN_LABEL = "Preview & Convert"

    # -- options (tool-specific) ----------------------------------------------
    def _build_options_card(self):
        card = c.Card(self, "Options", icon=Icons.GEAR)
        card.grid(row=1, column=0, sticky="nsew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        b = card.body
        row = ctk.CTkFrame(b, fg_color="transparent"); row.pack(fill="x")
        ctk.CTkLabel(row, text="Convert to", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._target = ctk.CTkOptionMenu(row, values=_ALL_TARGETS, width=140,
                                         fg_color=t.BG_COLOR, button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        self._target.set("png"); self._target.grid(row=1, column=0, sticky="w", padx=(0, 24), pady=(2, 0))
        self._kinds_hint = ctk.CTkLabel(row, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._kinds_hint.grid(row=1, column=1, sticky="w")
        # advanced (only relevant options apply per conversion; harmless otherwise)
        adv = ctk.CTkFrame(b, fg_color="transparent"); adv.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(adv, text="JPEG/WebP quality", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=0, sticky="w")
        self._quality = c.entry(adv, width=60); self._quality.insert(0, "90")
        self._quality.grid(row=1, column=0, sticky="w", padx=(0, 20), pady=(2, 0))
        ctk.CTkLabel(adv, text="GIF width / fps", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=1, sticky="w")
        gifrow = ctk.CTkFrame(adv, fg_color="transparent"); gifrow.grid(row=1, column=1, sticky="w", padx=(0, 20), pady=(2, 0))
        self._gifw = c.entry(gifrow, width=60); self._gifw.insert(0, "480"); self._gifw.pack(side="left")
        self._fps = c.entry(gifrow, width=50); self._fps.insert(0, "12"); self._fps.pack(side="left", padx=(6, 0))
        ctk.CTkLabel(adv, text="PDF→image DPI", text_color=t.TEXT_MUTED, font=t.font(11)).grid(row=0, column=2, sticky="w")
        self._dpi = c.entry(adv, width=60); self._dpi.insert(0, "150")
        self._dpi.grid(row=1, column=2, sticky="w", pady=(2, 0))
        # output folder
        self._build_output_row(b, "Output folder (blank = ./converted beside each source)")
        # toggles
        toggles = ctk.CTkFrame(b, fg_color="transparent"); toggles.pack(fill="x", pady=(12, 0))
        self._dry = ctk.CTkCheckBox(toggles, text="Preview only (list conversions — no writes)", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._dry.select(); self._dry.pack(side="left")
        self._mirror = ctk.CTkCheckBox(toggles, text="Mirror input structure", font=t.font(11), fg_color=t.ACCENT_BLUE)
        self._mirror.select(); self._mirror.pack(side="left", padx=20)
        # run row
        self._build_run_row(b)
        self._tools_hint = ctk.CTkLabel(b, text="", text_color=t.TEXT_MUTED, font=t.font(10))
        self._tools_hint.pack(anchor="w", pady=(6, 0))
        self._refresh_tools_hint()

    def _refresh_tools_hint(self):
        have_ff = bool(e.resolve_tool("ffmpeg"))
        av = "ffmpeg ✓" if have_ff else "ffmpeg ✗ (audio/video needs it)"
        self._tools_hint.configure(text=f"images: Pillow · audio/video: {av} · docs: optional (see README)")

    def _render_files(self):
        """Base file rendering + refresh the target dropdown from the loaded kinds."""
        super()._render_files()
        kinds = sorted({e.source_kind(p.suffix) for p in self._files} - {""})
        if not kinds:
            self._kinds_hint.configure(text=""); return
        targets: set[str] = set()
        for k in kinds:
            targets.update(e.targets_for(k))
        values = sorted(targets) or _ALL_TARGETS
        self._target.configure(values=values)
        if self._target.get() not in values:
            self._target.set(values[0])
        self._kinds_hint.configure(text=f"loaded: {', '.join(kinds)}")

    # -- option collection -----------------------------------------------------
    def _collect_options(self):
        try:
            quality = int(self._quality.get()); gifw = int(self._gifw.get())
            fps = int(self._fps.get()); dpi = int(self._dpi.get())
        except ValueError:
            self._logline("Quality / GIF / DPI must be integers.", t.STATE["error"][1]); return None
        out = self._out_entry.get().strip()
        out_root = Path(out) if out else None
        mirror = bool(self._mirror.get())
        input_root = self._resolve_input_root() if mirror else None
        return e.ConvertOptions(target=self._target.get(), out_root=out_root, input_root=input_root,
                                mirror=mirror, dry_run=bool(self._dry.get()), quality=quality,
                                gif_width=gifw, fps=fps, dpi=dpi)

    # -- batch loop ------------------------------------------------------------
    def _work(self, files: list[Path], opts: e.ConvertOptions):
        converted = skipped = failed = 0; results = []
        for i, f in enumerate(files, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{len(files)}] {f.name} …", t.TEXT_MUTED)
            res = e.process(f, opts); results.append(res)
            if res.action == "converted":
                converted += 1
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, len(files))
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, converted, skipped, failed, manifest)

    def _write_manifest(self, opts: e.ConvertOptions, results: list) -> str | None:
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "convert_manifest.csv"
            new = not path.exists()
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["src", "action", "before", "after", "out_path", "detail", "reason"])
                for r in results:
                    w.writerow([r.src, r.action, r.before, r.after, r.out_path, r.detail, r.reason])
            return str(path)
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"converted": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        name = Path(res.src).name
        if res.action in ("converted", "dry-run"):
            extra = f"  {res.before} → {res.after}"
            if res.detail:
                extra += f"  ({res.detail})"
            if res.action == "converted" and res.out_path:
                extra += f"  → {res.out_path}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {name}{extra}", color)

    def _done(self, converted, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"converted {converted} · skipped {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)
