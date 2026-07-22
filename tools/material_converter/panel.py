"""Material Converter — the tool's UI. Thin over engine.py: collect maps +
operation options, run on a worker thread, stream results back via after().
No image math here — detection, packing, flipping all live in the engine.
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


class MaterialConverterPanel(BaseBatchPanel):
    FILE_EXTS = e.IMAGE_EXTS
    FILE_LABEL = "map"
    RESULTS_ICON = Icons.LAYERS
    RUN_LABEL = "Preview & Convert"

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

    # -- batch loop (tool-specific) --------------------------------------------
    def _work(self, files: list[Path], opts: e.MaterialOptions):
        sets = e.detect_sets(files)
        self.after(0, self._logline, f"detected {len(sets)} texture set(s)", t.TEXT_MUTED)
        converted = skipped = failed = 0
        results = []
        total = len(sets)
        for i, tset in enumerate(sets, 1):
            if self._stop.is_set():
                self.after(0, self._logline, "— stopped —", t.TEXT_MUTED); break
            self.after(0, self._logline, f"[{i}/{total}] {tset.base} ({','.join(tset.roles)}) …", t.TEXT_MUTED)
            res = e.process_set(tset, opts); results.append(res)
            if res.action == "converted":
                converted += 1
            elif res.action in ("skipped", "dry-run"):
                skipped += 1
            else:
                failed += 1
            self.after(0, self._show, res, i, total)
        manifest = self._write_manifest(opts, results)
        self.after(0, self._done, converted, skipped, failed, manifest)

    def _write_manifest(self, opts: e.MaterialOptions, results: list) -> str | None:
        """Aggregate CSV across all sets (the per-set JSON manifests are written
        by the engine). Only on a real run into an explicit output folder."""
        if opts.dry_run or not results or not opts.out_root:
            return None
        root = Path(opts.out_root)
        try:
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
        except OSError:
            return None

    def _show(self, res: e.Result, i: int, total: int):
        self._progress.set(i / total)
        icon = {"converted": "✓", "skipped": "–", "dry-run": "?", "failed": "✗"}.get(res.action, "•")
        color = {"converted": t.STATE["done"][1], "failed": t.STATE["error"][1]}.get(res.action, t.TEXT_MUTED)
        if res.action in ("converted", "dry-run"):
            extra = f"  {res.reason}"
        else:
            extra = f"  — {res.reason}"
        self._logline(f"  {icon} {res.base}{extra}", color)

    def _done(self, converted, skipped, failed, manifest=None):
        self._run_btn.configure(state="normal"); self._stop_btn.configure(state="disabled")
        self._status.set_state("DONE", "done" if not failed else "error")
        self._progress.set(1)
        self._summary.configure(text=f"converted {converted} · skipped {skipped} · failed {failed}")
        if manifest:
            self._logline(f"  manifest: {manifest}", t.TEXT_MUTED)
