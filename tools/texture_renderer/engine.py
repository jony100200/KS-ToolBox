"""Texture Renderer engine — pure logic, no UI, no global state.

Drives two external, USER-PROVIDED CLI tools to batch-export textures:
  - Substance 3D Designer's `sbsrender` (renders `.sbsar` archives)
  - Material Maker's `material_maker` (exports `.ptex` projects)

Neither engine is bundled — the user points us at their own install. This layer
only *builds the argument lists* and *shells out*, then (for Material Maker)
cleans the non-PNG junk the exporter drops and optionally resizes the PNGs.

Cross-platform (Windows/Linux/macOS): the subprocess goes through
`engine_common.run_cmd` (which sets CREATE_NO_WINDOW on Windows for us), and
engine validation matches on the file *stem*, so `sbsrender`, `sbsrender.exe`,
and `sbsrender.sh` are all accepted — no hardcoded `.exe`.

Errors are values: fallible calls return the standard envelope
{error, error_type, retryable, degraded, details, data}; the per-project render
functions return a `Result` describing what happened. Pillow is imported lazily
(only when a resize is actually requested) so the tool loads on machines without
it. See AGENTS.md / CodingPrinciples.md.

Ported from M:/KS Apps/UniversalBatchRenderer (app.py) — behavior preserved,
anti-patterns fixed on the way in (see README).
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import ok as _ok, err as _err, run_cmd as _run

# Substance resolutions are set via a log2 pair ("$outputsize@10,10" == 1024²).
_LOG2_RES = {"512x512": "9,9", "1024x1024": "10,10",
             "2048x2048": "11,11", "4096x4096": "12,12"}


# ---------------------------------------------------------------------------
# 1. discovery + argument building — all pure, all testable
# ---------------------------------------------------------------------------

def find_projects(input_dir: str | Path, ext: str, recursive: bool = True) -> list[Path]:
    """Every project file of `ext` (".sbsar" or ".ptex") under `input_dir`.

    Sorted, de-duplicated, files only. Suffix match is case-insensitive so a
    `.SBSAR` on a case-sensitive filesystem still counts. Empty list if the
    directory is missing (the caller reports "none found")."""
    root = Path(input_dir)
    if not root.is_dir():
        return []
    ext = ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    it = root.rglob(f"*{ext}") if recursive else root.glob(f"*{ext}")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() == ext)


def get_log2_res(res_string: str) -> str:
    """Map a "WxH" label to Substance's log2 outputsize pair (default 1024²)."""
    return _LOG2_RES.get(res_string, "10,10")


def substance_cmd(engine: str, project: str, dest: str, res_val: str) -> list[str]:
    """`sbsrender` argv to render one archive into `dest` at `res_val` (log2)."""
    return [engine, "render",
            "--inputs", project,
            "--output-path", dest,
            "--output-name", "{inputName}_{outputNodeName}",
            "--set-value", f"$outputsize@{res_val}"]


def material_maker_cmd(engine: str, project: str, dest: str, target: str) -> list[str]:
    """`material_maker` argv to export one project into `dest` for `target`
    (e.g. "Unreal", "Godot", "Unity", "Blender")."""
    return [engine, "--export-material", "--target", target, "-o", dest, project]


def validate_engine(path: str | Path, expected_stem: str) -> dict:
    """Confirm `path` is the expected engine executable. Envelope out.

    Cross-platform: accepts the file if it exists AND its name stem equals
    `expected_stem` — so "sbsrender", "sbsrender.exe", "sbsrender.sh" all pass.
    (The source app hardcoded a Windows-only `.exe` check; dropped here.)
    Rejects a missing path or a wrongly-named file with a clear message."""
    if not path or not str(path).strip():
        return _err("engine.missing", f"no {expected_stem} executable selected")
    p = Path(path)
    if not p.is_file():
        return _err("engine.missing", f"{expected_stem} executable not found: {p}")
    if p.stem.lower() != expected_stem.lower():
        return _err("engine.wrong",
                    f"expected the '{expected_stem}' executable, got '{p.name}' — "
                    f"select the engine's CLI, not the folder or main app")
    return _ok(str(p))


# ---------------------------------------------------------------------------
# 2. post-processing — Material Maker drops non-PNG files; clean + resize them
# ---------------------------------------------------------------------------

def cleanup_and_resize(out_dir: str | Path, resize_to: int | None) -> dict:
    """Remove non-PNG engine junk from `out_dir` and optionally resize PNGs.

    Material Maker writes engine-specific sidecars (`.tres`, `.uasset`, …) next
    to the PNG maps; we keep only the PNGs. When `resize_to` is set, every PNG
    not already square-`resize_to` is resampled (LANCZOS) to `resize_to²`.

    Returns the envelope with data = {"cleaned", "resized", "errors"}. Unlike the
    source (which wrapped every op in `except: pass`), per-file failures are
    COUNTED into `errors` and reported — never silently dropped. Pillow is
    imported lazily and only when a resize is requested."""
    out = Path(out_dir)
    if not out.is_dir():
        return _err("cleanup.nodir", f"output directory not found: {out}")

    Image = None
    if resize_to is not None:
        try:
            from PIL import Image  # lazy: only pulled in when actually resizing
        except ImportError:
            return _err("dep.missing", "Pillow (PIL) required to resize but not installed")

    cleaned = resized = errors = 0
    for root_dir, _dirs, files in os.walk(out):
        for name in files:
            fp = Path(root_dir) / name
            if fp.suffix.lower() != ".png":
                # not a texture map — engine junk, remove it
                try:
                    fp.unlink()
                    cleaned += 1
                except OSError:
                    errors += 1
                continue
            if resize_to is None:
                continue
            # Load fully and CLOSE the read handle before writing back — saving
            # into a still-open file is flaky on Windows.
            try:
                with Image.open(fp) as img:
                    needs = img.width != resize_to or img.height != resize_to
                    scaled = img.resize((resize_to, resize_to), Image.Resampling.LANCZOS) if needs else None
                if scaled is not None:
                    scaled.save(fp)
                    resized += 1
            except Exception:   # noqa: BLE001 — PIL raises varied types; count, don't swallow
                errors += 1

    return _ok({"cleaned": cleaned, "resized": resized, "errors": errors},
               degraded=errors > 0,
               details=(f"{errors} file(s) could not be processed" if errors else ""))


# ---------------------------------------------------------------------------
# 3. options + result + per-project render
# ---------------------------------------------------------------------------

@dataclass
class RenderOptions:
    """One batch's settings. `resolution` is a "WxH" label (Substance / MM);
    `resize` is the MM post-export target in px (None = keep original size)."""
    engine_path: str
    input_dir: str
    output_dir: str
    resolution: str = "1024x1024"       # Substance render size / MM label
    target_engine: str = "Unreal"       # Material Maker export target
    group: bool = True                  # each project into its own subfolder
    recursive: bool = True              # scan input_dir recursively
    resize: int | None = None           # Material Maker: resize PNGs to N² (None = original)
    dry_run: bool = False               # plan only, don't shell out


@dataclass
class Result:
    """Outcome of one project. `action` ∈ {rendered, skipped, failed, dry-run}."""
    project: str
    action: str
    reason: str = ""
    out_dir: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _dest_for(project: Path, opts: RenderOptions) -> Path:
    """Where this project's maps land: a per-project subfolder when grouping,
    else the flat output directory."""
    root = Path(opts.output_dir)
    return root / project.stem if opts.group else root


def render_substance(project: str | Path, opts: RenderOptions) -> Result:
    """Render one `.sbsar` archive via `sbsrender`. Envelope-checked, dry-run aware."""
    project = Path(project)
    v = validate_engine(opts.engine_path, "sbsrender")
    if v["error"]:
        return Result(str(project), "failed", v["details"], detail=v["error_type"])

    dest = _dest_for(project, opts)
    res_val = get_log2_res(opts.resolution)
    cmd = substance_cmd(opts.engine_path, str(project), str(dest), res_val)

    if opts.dry_run:
        return Result(str(project), "dry-run", " ".join(cmd), out_dir=str(dest))

    dest.mkdir(parents=True, exist_ok=True)
    try:
        r = _run(cmd)
    except subprocess.TimeoutExpired:
        return Result(str(project), "failed", "sbsrender timed out",
                      out_dir=str(dest), detail="render.timeout")
    if r.returncode != 0:
        return Result(str(project), "failed",
                      f"sbsrender exited {r.returncode}: {(r.stderr or '').strip()[-200:]}",
                      out_dir=str(dest), detail="render.failed")
    return Result(str(project), "rendered", f"rendered at {opts.resolution}", out_dir=str(dest))


def render_material_maker(project: str | Path, opts: RenderOptions) -> Result:
    """Export one `.ptex` project via `material_maker`, then clean junk + resize.

    Cleanup runs even on a non-zero exit (the exporter may have written partial
    output worth tidying), but the render is only reported `rendered` when the
    process actually succeeded."""
    project = Path(project)
    v = validate_engine(opts.engine_path, "material_maker")
    if v["error"]:
        return Result(str(project), "failed", v["details"], detail=v["error_type"])

    dest = _dest_for(project, opts)
    cmd = material_maker_cmd(opts.engine_path, str(project), str(dest), opts.target_engine)

    if opts.dry_run:
        return Result(str(project), "dry-run", " ".join(cmd), out_dir=str(dest))

    dest.mkdir(parents=True, exist_ok=True)
    try:
        r = _run(cmd)
    except subprocess.TimeoutExpired:
        return Result(str(project), "failed", "material_maker timed out",
                      out_dir=str(dest), detail="render.timeout")

    clean = cleanup_and_resize(dest, opts.resize)
    counts = clean["data"] if not clean["error"] else {"cleaned": 0, "resized": 0, "errors": 0}

    if r.returncode != 0:
        return Result(str(project), "failed",
                      f"material_maker exited {r.returncode}: {(r.stderr or '').strip()[-200:]}",
                      out_dir=str(dest), detail="render.failed")

    reason = f"exported for {opts.target_engine}; removed {counts['cleaned']} junk file(s)"
    if opts.resize is not None:
        reason += f"; resized {counts['resized']} PNG(s) to {opts.resize}px"
    if counts["errors"]:
        reason += f"; {counts['errors']} cleanup error(s)"
    detail = clean["error_type"] if clean["error"] else ("cleanup-errors" if counts["errors"] else "")
    return Result(str(project), "rendered", reason, out_dir=str(dest), detail=detail)
