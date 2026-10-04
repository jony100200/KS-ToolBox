"""Unity Packager UI — thin over engine.py.

Three steps on one screen: pick the Unity project, tick the folders to ship, press
Build. Build always previews first and asks before writing. The build runs on a
cancellable worker thread; the UI thread only polls a result queue.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import TclError, filedialog, messagebox

import customtkinter as ctk

from toolbox import components as c
from toolbox import theme as t
from toolbox.icons import Icons
from . import engine as e

_COLLISION_LABELS = {"Create a copy": "copy", "Overwrite": "overwrite", "Stop if it exists": "error"}


class UnityPackagerPanel(ctk.CTkFrame):
    def __init__(self, parent, services=None):
        super().__init__(parent, fg_color=t.BG_COLOR)
        self._services = services
        self._checks: dict[str, ctk.CTkCheckBox] = {}
        self._cancel = threading.Event()
        self._results: queue.SimpleQueue = queue.SimpleQueue()
        self._worker: threading.Thread | None = None
        self._poll_job = None
        self._destroying = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_project_card()
        self._build_folders_card()
        self._build_output_card()
        self._build_log_card()
        self._set_status("Pick your Unity project folder.", "idle")

    # -- cards -----------------------------------------------------------------

    def _build_project_card(self):
        card = c.Card(self, "1 · Unity project", icon=Icons.FOLDER)
        card.grid(row=0, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        card.body.grid_columnconfigure(0, weight=1)
        self._project = c.entry(card.body, placeholder_text="Folder that contains Assets/")
        self._project.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        c.secondary_button(card.body, "Browse", self._browse_project, width=90).grid(row=0, column=1)

    def _build_folders_card(self):
        card = c.Card(self, "2 · Folders to include", icon=Icons.LAYERS)
        card.grid(row=1, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))

        # Actions toolbar
        actions = ctk.CTkFrame(card.body, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 6))
        c.ghost_button(actions, "Select All", self._select_all_folders, width=88).pack(side="left", padx=(0, 6))
        c.ghost_button(actions, "Deselect All", self._deselect_all_folders, width=94).pack(side="left", padx=(0, 10))
        c.ghost_button(actions, "Add another folder…", self._add_folder, width=160).pack(side="left")

        self._list = ctk.CTkScrollableFrame(card.body, height=130, fg_color=t.BG_COLOR)
        self._list.pack(fill="x")

    def _build_output_card(self):
        card = c.Card(self, "3 · Package", icon=Icons.TOOLBOX)
        card.grid(row=2, column=0, sticky="ew", padx=t.PAD_GRID, pady=(t.PAD_GRID, 0))
        body = card.body
        body.grid_columnconfigure(0, weight=1)
        self._output = c.entry(body, placeholder_text="Where to save the .unitypackage")
        self._output.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        c.secondary_button(body, "Save as…", self._browse_output, width=90).grid(row=0, column=1)

        opts = ctk.CTkFrame(body, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._level = self._menu(opts, "Compression", list(e.LEVELS), "Balanced")
        self._collision = self._menu(opts, "If the file exists", list(_COLLISION_LABELS), "Create a copy")

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self._status = c.Pill(row)
        self._status.pack(side="left")
        self._cancel_btn = c.danger_button(row, "Cancel", self._cancel_build, width=90, state="disabled")
        self._cancel_btn.pack(side="right")
        self._build_btn = c.primary_button(row, "Build package", self._build, width=140)
        self._build_btn.pack(side="right", padx=(0, 8))
        self._preview_btn = c.secondary_button(row, "Preview", self._preview, width=100)
        self._preview_btn.pack(side="right", padx=(0, 8))

    def _build_log_card(self):
        card = c.Card(self, "Report", icon=Icons.CHECK)
        card.grid(row=3, column=0, sticky="nsew", padx=t.PAD_GRID, pady=t.PAD_GRID)
        self._log = ctk.CTkTextbox(card.body, fg_color=t.BG_COLOR, text_color=t.TEXT_MUTED,
                                   font=t.mono(11), state="disabled", height=120)
        self._log.pack(fill="both", expand=True)

    @staticmethod
    def _menu(parent, label: str, values: list[str], default: str) -> ctk.CTkOptionMenu:
        ctk.CTkLabel(parent, text=label, text_color=t.TEXT_MUTED, font=t.font(11)).pack(side="left", padx=(0, 6))
        menu = ctk.CTkOptionMenu(parent, values=values, width=140, fg_color=t.BG_COLOR,
                                 button_color=t.CARD_BORDER, button_hover_color=t.NEUTRAL_HOVER)
        menu.set(default)
        menu.pack(side="left", padx=(0, 18))
        return menu

    # -- inputs ----------------------------------------------------------------

    def _browse_project(self):
        try:
            top = self.winfo_toplevel()
        except Exception:
            top = None
        try:
            chosen = filedialog.askdirectory(title="Select the Unity project folder", parent=top)
        except Exception:
            chosen = filedialog.askdirectory(title="Select the Unity project folder")
        if not chosen:
            return
        root = Path(chosen)
        if not (root / "Assets").is_dir():
            self._set_status("That folder has no Assets/ folder.", "error")
            return
        self._project.delete(0, "end")
        self._project.insert(0, str(root))
        self._load_folders(root)
        self._set_status("Tick the folders to ship, then Build.", "idle")

    def _load_folders(self, root: Path):
        for check in list(self._checks.values()):
            try:
                check.destroy()
            except Exception:
                pass
        self._checks.clear()
        assets = root / "Assets"
        if not assets.is_dir():
            return
        try:
            first_dirs = sorted(p for p in assets.iterdir() if p.is_dir() and not e._ignored(p.name))
        except OSError as exc:
            self._set_status(f"Error reading Assets: {exc}", "error")
            return

        found = 0
        for first in first_dirs:
            try:
                subs = sorted(p for p in first.iterdir() if p.is_dir() and not e._ignored(p.name))
            except OSError:
                subs = []
            for folder in subs or [first]:
                try:
                    self._add_check(folder.relative_to(root).as_posix(), checked=True)
                    found += 1
                except Exception:
                    pass
        if found == 0:
            self._set_status("No exportable folders found in Assets/.", "waiting")

    def _select_all_folders(self):
        for box in self._checks.values():
            try:
                box.select()
            except Exception:
                pass

    def _deselect_all_folders(self):
        for box in self._checks.values():
            try:
                box.deselect()
            except Exception:
                pass

    def _add_check(self, rel: str, checked: bool = False):
        if rel in self._checks:
            return
        box = ctk.CTkCheckBox(self._list, text=rel, font=t.font(11), fg_color=t.ACCENT_BLUE)
        if checked:
            box.select()
        box.pack(anchor="w", pady=2)
        self._checks[rel] = box

    def _add_folder(self):
        root = self._root()
        if root is None:
            return
        try:
            top = self.winfo_toplevel()
        except Exception:
            top = None
        initial = str(root / "Assets") if (root / "Assets").is_dir() else str(root)
        try:
            chosen = filedialog.askdirectory(title="Pick a folder inside Assets", initialdir=initial, parent=top)
        except Exception:
            chosen = filedialog.askdirectory(title="Pick a folder inside Assets", initialdir=initial)
        if not chosen:
            return
        try:
            rel = Path(chosen).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            self._set_status("That folder is outside the project.", "error")
            return
        self._add_check(rel, checked=True)

    def _browse_output(self):
        selected = self._selected()
        first_name = Path(selected[0]).name if selected else "package"
        name = (first_name or "package") + ".unitypackage"
        try:
            top = self.winfo_toplevel()
        except Exception:
            top = None
        try:
            path = filedialog.asksaveasfilename(
                title="Save package as",
                defaultextension=".unitypackage",
                initialfile=name,
                filetypes=[("Unity package", "*.unitypackage")],
                parent=top,
            )
        except Exception:
            path = filedialog.asksaveasfilename(
                title="Save package as",
                defaultextension=".unitypackage",
                initialfile=name,
                filetypes=[("Unity package", "*.unitypackage")],
            )
        if path:
            self._output.delete(0, "end")
            self._output.insert(0, path)

    def _root(self) -> Path | None:
        text = self._project.get().strip()
        if not text:
            self._set_status("Pick your Unity project folder first.", "error")
            return None
        return Path(text)

    def _selected(self) -> list[str]:
        return [rel for rel, box in self._checks.items() if box.get()]

    def _package_options(self) -> e.PackageOptions | None:
        root = self._root()
        if root is None:
            return None
        selected = self._selected()
        output = self._output.get().strip()
        if not output and selected:
            output = str(root.parent / (Path(selected[0]).name + ".unitypackage"))
            self._output.delete(0, "end")
            self._output.insert(0, output)
        level_val = e.LEVELS.get(self._level.get(), 6)
        collision_val = _COLLISION_LABELS.get(self._collision.get(), "copy")
        return e.PackageOptions(
            project_root=root,
            includes=tuple(selected),
            output=Path(output or "package.unitypackage"),
            compress_level=level_val,
            collision=collision_val,
        )

    # -- actions ---------------------------------------------------------------

    def _preview(self):
        opts = self._package_options()
        if opts is None:
            return
        result = e.plan(opts)
        if result["error"]:
            self._report(f"✗ {result['details']}")
            self._set_status(result["details"], "error")
            return
        built = result["data"]
        lines = [f"Would package {built.files} file(s) and {built.folders} folder(s)  "
                 f"({built.raw_bytes / 1048576:.2f} MB before compression)"]
        lines += [f"  skipped — {reason}" for reason in built.skipped]
        self._report("\n".join(lines))
        self._set_status("Preview ready." if not built.skipped else "Preview ready (with skipped items).",
                         "done" if not built.skipped else "waiting")

    def _build(self):
        opts = self._package_options()
        if opts is None:
            return
        result = e.plan(opts)
        if result["error"]:
            self._report(f"✗ {result['details']}")
            self._set_status(result["details"], "error")
            return
        built = result["data"]
        message = (f"Create {opts.output.name}?\n\n{built.files} file(s), {built.folders} folder(s)"
                   + (f"\n{len(built.skipped)} item(s) will be skipped (no .meta)." if built.skipped else ""))
        try:
            top = self.winfo_toplevel()
        except Exception:
            top = self
        if not messagebox.askyesno("Build Unity package", message, parent=top):
            return
        self._cancel.clear()
        self._set_busy(True)
        self._set_status("Building…", "running")
        self._worker = threading.Thread(target=self._run_build, args=(opts,), daemon=True, name="unity-packager")
        self._worker.start()
        self._poll_job = self.after(30, self._poll)

    def _run_build(self, opts: e.PackageOptions):
        self._results.put(e.build(opts, cancelled=self._cancel.is_set))

    def _cancel_build(self):
        self._cancel.set()
        self._set_status("Cancelling…", "waiting")

    def _poll(self):
        self._poll_job = None
        if self._destroying:
            return
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            if not self._destroying and self._worker is not None and self._worker.is_alive():
                self._poll_job = self.after(30, self._poll)
            return
        except Exception:
            return
        self._set_busy(False)
        if result["error"]:
            self._report(f"✗ {result['details']}")
            self._set_status(result["details"], "waiting" if result.get("error_type") == "cancelled" else "error")
            return
        data = result["data"]
        lines = [f"✓ {data['path']}",
                 f"  {data['files']} file(s), {data['folders']} folder(s) · "
                 f"{data['package_bytes'] / 1048576:.2f} MB · sha256 {data['sha256'][:16]}…"]
        lines += [f"  skipped — {reason}" for reason in data.get("skipped", [])]
        self._report("\n".join(lines))
        self._set_status("Package created.", "done")

    # -- helpers ---------------------------------------------------------------

    def _set_busy(self, busy: bool):
        try:
            self._build_btn.configure(state="disabled" if busy else "normal")
            self._preview_btn.configure(state="disabled" if busy else "normal")
            self._cancel_btn.configure(state="normal" if busy else "disabled")
        except Exception:
            pass

    def _report(self, text: str):
        try:
            self._log.configure(state="normal")
            self._log.delete("1.0", "end")
            self._log.insert("1.0", text)
            self._log.configure(state="disabled")
        except Exception:
            pass

    def _set_status(self, text: str, state: str):
        if hasattr(self, "_status") and self._status is not None:
            try:
                self._status.set_state((text if len(text) <= 60 else text[:57] + "...").upper(), state)
            except Exception:
                pass

    def destroy(self):
        self._destroying = True
        self._cancel.set()
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except (TclError, Exception):
                pass
        super().destroy()
