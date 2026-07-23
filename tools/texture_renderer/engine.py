"""Texture Renderer engine — pure logic, no UI, no global state.

Drives two external, USER-PROVIDED CLI tools to batch-export textures:
  - Substance 3D Designer's `sbsrender` (renders `.sbsar` archives)
  - Material Maker's `material_maker` (exports `.ptex` projects)

Neither engine is bundled — the user points us at their own install. This layer
only *builds the argument lists* and *shells out*, then (for Material Maker)
cleans the non-PNG junk the exporter drops and optionally resizes the PNGs.

Cross-platform (Windows/Linux/macOS): the subprocess goes through the shared
cancellable runner (which sets CREATE_NO_WINDOW on Windows for us), and engine
validation matches on the file *stem*, so `sbsrender`, `sbsrender.exe`, and
`sbsrender.sh` are all accepted — no hardcoded `.exe`.

Errors are values: fallible calls return the standard envelope
{error, error_type, retryable, degraded, details, data}; the per-project render
functions return a `Result` describing what happened. Pillow is imported lazily
(only when a resize is actually requested) so the tool loads on machines without
it. See AGENTS.md / CodingPrinciples.md.

Ported from M:/KS Apps/UniversalBatchRenderer (app.py) — behavior preserved,
anti-patterns fixed on the way in (see README).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict, replace
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    ok as _ok,
    err as _err,
    run_cancellable_cmd as _run,
)

# Substance resolutions are set via a log2 pair ("$outputsize@10,10" == 1024²).
_LOG2_RES = {"512x512": "9,9", "1024x1024": "10,10",
             "2048x2048": "11,11", "4096x4096": "12,12"}
_STAGE_MARKER = ".ks-texture-renderer-stage-v1"
_STAGE_MARKER_CONTENT = "owned by KS ToolBox Texture Renderer v1\n"
_TARGET_ENGINES = {"Unreal", "Godot", "Unity", "Blender"}
_MIN_RESIZE = 16
_MAX_RESIZE = 16_384
MAX_RENDER_PROJECTS = 10_000
MAX_RENDER_TIMEOUT = 24 * 60 * 60
MAX_RENDER_LOG_BYTES = 1024 * 1024
Cancelled = Callable[[], bool] | None


def _cancelled(cancelled: Cancelled, stage: str, path: str | Path = "") -> None:
    if cancelled is not None and cancelled():
        command = [stage]
        if path:
            command.append(str(path))
        raise CommandCancelled(command)


# ---------------------------------------------------------------------------
# 1. discovery + argument building — all pure, all testable
# ---------------------------------------------------------------------------

def find_projects(
    input_dir: str | Path,
    ext: str,
    recursive: bool = True,
    cancelled: Cancelled = None,
    exclude_dirs=(),
) -> list[Path]:
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
    excluded = {
        Path(directory).resolve(strict=False) for directory in exclude_dirs
    }
    projects: list[Path] = []

    def include(path: Path) -> None:
        _cancelled(cancelled, "texture-project-discovery", path)
        if path.suffix.lower() != ext:
            return
        if path.is_symlink() or not path.is_file():
            return
        projects.append(path)
        if len(projects) > MAX_RENDER_PROJECTS:
            raise ValueError(
                f"project scan exceeds the {MAX_RENDER_PROJECTS} file limit"
            )

    if recursive:
        def raise_walk_error(error: OSError) -> None:
            raise error

        for current, directories, filenames in os.walk(
            root, topdown=True, onerror=raise_walk_error, followlinks=False
        ):
            current_path = Path(current)
            _cancelled(cancelled, "texture-project-discovery", current_path)
            kept: list[str] = []
            for name in sorted(directories, key=str.casefold):
                candidate = current_path / name
                resolved = candidate.resolve(strict=False)
                if candidate.is_symlink() or any(
                    resolved.is_relative_to(directory) for directory in excluded
                ):
                    continue
                kept.append(name)
            directories[:] = kept
            for name in sorted(filenames, key=str.casefold):
                include(current_path / name)
    else:
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            include(path)
    return sorted(projects, key=lambda path: str(path).casefold())


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

def cleanup_and_resize(
    out_dir: str | Path,
    resize_to: int | None,
    cancelled: Cancelled = None,
) -> dict:
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
            _cancelled(cancelled, "texture-stage-postprocess", fp)
            if fp.name == _STAGE_MARKER:
                continue
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
                _cancelled(cancelled, "texture-stage-resize", fp)
                if scaled is not None:
                    scaled.save(fp)
                    resized += 1
            except CommandCancelled:
                raise
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
    protected_paths: tuple[str, ...] = ()  # selected projects; never publish over these
    timeout_seconds: int = 60 * 60       # one external project render


def normalized_options(
    opts: RenderOptions, kind: str
) -> tuple[RenderOptions | None, str]:
    if kind not in {"sbs", "mm"}:
        return None, f"unknown renderer kind: {kind!r}"
    engine_path = str(opts.engine_path).strip()
    input_dir = str(opts.input_dir).strip()
    output_dir = str(opts.output_dir).strip()
    if not engine_path:
        return None, "engine path is required"
    if not input_dir:
        return None, "input directory is required"
    if not output_dir:
        return None, "output directory is required"
    if kind == "sbs" and opts.resolution not in _LOG2_RES:
        return None, f"unsupported Substance resolution: {opts.resolution!r}"
    if kind == "mm" and opts.target_engine not in _TARGET_ENGINES:
        return None, f"unsupported Material Maker target: {opts.target_engine!r}"
    if opts.resize is not None:
        if isinstance(opts.resize, bool):
            return None, f"resize must be from {_MIN_RESIZE} to {_MAX_RESIZE} pixels"
        try:
            resize = int(opts.resize)
        except (TypeError, ValueError):
            return None, f"resize must be from {_MIN_RESIZE} to {_MAX_RESIZE} pixels"
        if resize != opts.resize or not _MIN_RESIZE <= resize <= _MAX_RESIZE:
            return None, f"resize must be from {_MIN_RESIZE} to {_MAX_RESIZE} pixels"
    else:
        resize = None
    if isinstance(opts.timeout_seconds, bool):
        return None, f"timeout must be from 1 to {MAX_RENDER_TIMEOUT} seconds"
    try:
        timeout = int(opts.timeout_seconds)
    except (TypeError, ValueError):
        return None, f"timeout must be from 1 to {MAX_RENDER_TIMEOUT} seconds"
    if timeout != opts.timeout_seconds or not 1 <= timeout <= MAX_RENDER_TIMEOUT:
        return None, f"timeout must be from 1 to {MAX_RENDER_TIMEOUT} seconds"
    try:
        protected = tuple(str(Path(path)) for path in opts.protected_paths)
    except TypeError:
        return None, "protected project paths must be filesystem paths"
    return replace(
        opts,
        engine_path=engine_path,
        input_dir=input_dir,
        output_dir=output_dir,
        resize=resize,
        timeout_seconds=timeout,
        protected_paths=protected,
        group=bool(opts.group),
        recursive=bool(opts.recursive),
        dry_run=bool(opts.dry_run),
    ), ""


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


def _stage_for(project: Path, opts: RenderOptions) -> Path:
    identity = hashlib.sha256(
        str(project.resolve(strict=False)).encode("utf-8")
    ).hexdigest()[:16]
    return Path(opts.output_dir) / ".ks-render-stage" / identity


def _assert_owned_stage(stage: Path, output_root: Path) -> None:
    root = output_root.resolve(strict=False)
    resolved = stage.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise OSError(f"refusing unsafe renderer stage path: {stage}")


def _reset_stage(
    stage: Path, output_root: Path, cancelled: Cancelled = None
) -> None:
    _cancelled(cancelled, "texture-stage-reset", stage)
    _assert_owned_stage(stage, output_root)
    if stage.exists():
        if stage.is_symlink():
            raise OSError(f"renderer stage is a symlink: {stage}")
        marker = stage / _STAGE_MARKER
        try:
            owned = marker.read_text(encoding="utf-8") == _STAGE_MARKER_CONTENT
        except (OSError, UnicodeError):
            owned = False
        if not owned:
            raise OSError(f"existing renderer stage is not KS-owned: {stage}")
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=False)
    (stage / _STAGE_MARKER).write_text(
        _STAGE_MARKER_CONTENT, encoding="utf-8"
    )
    _cancelled(cancelled, "texture-stage-reset", stage)


def _remove_stage(stage: Path, output_root: Path) -> None:
    _assert_owned_stage(stage, output_root)
    if stage.is_symlink():
        raise OSError(f"renderer stage is a symlink: {stage}")
    if stage.exists():
        marker = stage / _STAGE_MARKER
        if marker.read_text(encoding="utf-8") != _STAGE_MARKER_CONTENT:
            raise OSError(f"renderer stage ownership marker is invalid: {stage}")
        shutil.rmtree(stage)
    parent = stage.parent
    try:
        parent.rmdir()
    except FileNotFoundError:
        pass
    except OSError:
        if not any(parent.iterdir()):
            raise


def _stage_files(
    stage: Path,
    suffix: str | None = None,
    cancelled: Cancelled = None,
) -> list[Path]:
    files: list[Path] = []
    for path in sorted(stage.rglob("*"), key=lambda item: str(item).casefold()):
        _cancelled(cancelled, "texture-output-enumeration", path)
        if path.is_symlink():
            raise OSError(f"renderer produced a symlink: {path}")
        if not path.is_file():
            continue
        if path.name == _STAGE_MARKER:
            continue
        if suffix is None or path.suffix.lower() == suffix:
            if path.stat().st_size <= 0:
                raise OSError(f"renderer produced an empty file: {path.name}")
            files.append(path)
    return files


def _publish_stage(
    stage: Path,
    destination: Path,
    files: list[Path],
    protected_paths=(),
    cancelled: Cancelled = None,
) -> int:
    if not files:
        raise OSError("renderer produced no output files")
    protected = {
        Path(path).resolve(strict=False) for path in protected_paths
    }
    destination_root = destination.resolve(strict=False)
    planned: list[tuple[Path, Path, int]] = []
    targets: set[Path] = set()
    for source in files:
        _cancelled(cancelled, "texture-output-planning", source)
        relative = source.relative_to(stage)
        target = destination / relative
        resolved_target = target.resolve(strict=False)
        if not resolved_target.is_relative_to(destination_root):
            raise OSError(f"render output escapes its destination: {target}")
        if resolved_target in protected:
            raise OSError(f"render output would replace a protected file: {target}")
        if resolved_target in targets:
            raise OSError(f"renderer produced duplicate output: {target}")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise OSError(f"refusing non-file publication target: {target}")
        targets.add(resolved_target)
        planned.append((source, target, source.stat().st_size))

    backup_root = stage / ".ks-publish-backup"
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for index, (_source, target, _size) in enumerate(planned):
            _cancelled(cancelled, "texture-output-backup", target)
            if not target.exists():
                continue
            backup = backup_root / f"{index:08d}.bak"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            if backup.stat().st_size != target.stat().st_size:
                raise OSError(f"could not verify publication backup: {target}")
            backups[target] = backup
        for source, target, source_size in planned:
            _cancelled(cancelled, "texture-output-publish", target)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            published.append(target)
            if target.stat().st_size != source_size:
                raise OSError(f"published file size changed: {target}")
    except BaseException as ex:
        rollback_errors: list[str] = []
        for target in reversed(published):
            backup = backups.get(target)
            try:
                if backup is not None and backup.exists():
                    backup.replace(target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as rollback:
                rollback_errors.append(f"{target}: {rollback}")
        detail = (
            f"; rollback failed for {len(rollback_errors)} output(s): "
            + " | ".join(rollback_errors[:3])
            if rollback_errors else ""
        )
        if isinstance(ex, CommandCancelled) and not rollback_errors:
            raise
        raise OSError(f"atomic publication failed: {ex}{detail}") from ex
    return len(published)


def _discard_stage(stage: Path, output_root: Path) -> str:
    try:
        _remove_stage(stage, output_root)
        return ""
    except OSError as ex:
        return f"; renderer stage cleanup failed: {ex}"


def _validate_render_request(
    project: str | Path,
    opts: RenderOptions,
    *,
    kind: str,
    extension: str,
    engine_name: str,
) -> tuple[Path, RenderOptions | None, Result | None]:
    source = Path(project)
    normalized, options_error = normalized_options(opts, kind)
    if normalized is None:
        return source, None, Result(
            str(source), "failed", options_error, detail="options.invalid"
        )
    if not source.is_file() or source.suffix.lower() != extension:
        return source, None, Result(
            str(source), "failed", f"not a {extension} project file: {source}",
            detail="project.invalid",
        )
    input_root = Path(normalized.input_dir)
    if not input_root.is_dir():
        return source, None, Result(
            str(source), "failed", f"input directory not found: {input_root}",
            detail="input.missing",
        )
    if not source.resolve(strict=False).is_relative_to(input_root.resolve(strict=False)):
        return source, None, Result(
            str(source), "failed", "project is outside the selected input directory",
            detail="project.outside_input",
        )
    validated_engine = validate_engine(normalized.engine_path, engine_name)
    if validated_engine["error"]:
        return source, None, Result(
            str(source), "failed", validated_engine["details"],
            detail=validated_engine["error_type"],
        )
    return source, normalized, None


def _propagate_cancel(
    error: CommandCancelled, stage: Path, output_root: Path
) -> None:
    cleanup = _discard_stage(stage, output_root)
    if cleanup:
        raise OSError(f"render cancelled{cleanup}") from error
    raise error


def render_substance(
    project: str | Path,
    opts: RenderOptions,
    cancelled: Cancelled = None,
) -> Result:
    """Render one `.sbsar` archive via `sbsrender`. Envelope-checked, dry-run aware."""
    project, normalized, failure = _validate_render_request(
        project, opts, kind="sbs", extension=".sbsar", engine_name="sbsrender"
    )
    if failure is not None:
        return failure
    assert normalized is not None
    opts = normalized

    dest = _dest_for(project, opts)
    output_root = Path(opts.output_dir)
    stage = _stage_for(project, opts)
    res_val = get_log2_res(opts.resolution)
    cmd = substance_cmd(opts.engine_path, str(project), str(stage), res_val)

    if opts.dry_run:
        return Result(str(project), "dry-run", " ".join(cmd), out_dir=str(dest))

    try:
        _reset_stage(stage, output_root, cancelled)
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except OSError as ex:
        return Result(
            str(project), "failed", f"could not prepare isolated output: {ex}",
            out_dir=str(dest), detail="output.stage",
        )
    try:
        r = _run(
            cmd, timeout=opts.timeout_seconds, cancelled=cancelled,
            capture_limit_bytes=MAX_RENDER_LOG_BYTES,
        )
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except subprocess.TimeoutExpired:
        cleanup = _discard_stage(stage, output_root)
        return Result(str(project), "failed", f"sbsrender timed out{cleanup}",
                      out_dir=str(dest), detail="render.timeout")
    except OSError as ex:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed", f"could not start sbsrender: {ex}{cleanup}",
            out_dir=str(dest), detail="render.start",
        )
    if r.returncode != 0:
        cleanup = _discard_stage(stage, output_root)
        return Result(str(project), "failed",
                      f"sbsrender exited {r.returncode}: "
                      f"{(r.stderr or '').strip()[-200:]}{cleanup}",
                      out_dir=str(dest), detail="render.failed")
    try:
        published = _publish_stage(
            stage, dest, _stage_files(stage, cancelled=cancelled),
            [project, opts.engine_path, *opts.protected_paths],
            cancelled,
        )
        _remove_stage(stage, output_root)
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except OSError as ex:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed", f"could not publish render: {ex}{cleanup}",
            out_dir=str(dest), detail="output.publish",
        )
    return Result(
        str(project), "rendered",
        f"rendered {published} file(s) at {opts.resolution}", out_dir=str(dest),
    )


def render_material_maker(
    project: str | Path,
    opts: RenderOptions,
    cancelled: Cancelled = None,
) -> Result:
    """Export one `.ptex` project via `material_maker`, then clean junk + resize.

    Generated sidecars are cleaned only inside an owned stage, and nothing is
    published unless the process exits successfully."""
    project, normalized, failure = _validate_render_request(
        project, opts, kind="mm", extension=".ptex",
        engine_name="material_maker",
    )
    if failure is not None:
        return failure
    assert normalized is not None
    opts = normalized

    dest = _dest_for(project, opts)
    output_root = Path(opts.output_dir)
    stage = _stage_for(project, opts)
    cmd = material_maker_cmd(
        opts.engine_path, str(project), str(stage), opts.target_engine
    )

    if opts.dry_run:
        return Result(str(project), "dry-run", " ".join(cmd), out_dir=str(dest))

    try:
        _reset_stage(stage, output_root, cancelled)
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except OSError as ex:
        return Result(
            str(project), "failed", f"could not prepare isolated output: {ex}",
            out_dir=str(dest), detail="output.stage",
        )
    try:
        r = _run(
            cmd, timeout=opts.timeout_seconds, cancelled=cancelled,
            capture_limit_bytes=MAX_RENDER_LOG_BYTES,
        )
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except subprocess.TimeoutExpired:
        cleanup = _discard_stage(stage, output_root)
        return Result(str(project), "failed", f"material_maker timed out{cleanup}",
                      out_dir=str(dest), detail="render.timeout")
    except OSError as ex:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed",
            f"could not start material_maker: {ex}{cleanup}",
            out_dir=str(dest), detail="render.start",
        )

    if r.returncode != 0:
        cleanup = _discard_stage(stage, output_root)
        return Result(str(project), "failed",
                      f"material_maker exited {r.returncode}: "
                      f"{(r.stderr or '').strip()[-200:]}{cleanup}",
                      out_dir=str(dest), detail="render.failed")

    try:
        clean = cleanup_and_resize(stage, opts.resize, cancelled)
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    if clean["error"]:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed", f"{clean['details']}{cleanup}",
            out_dir=str(dest), detail=clean["error_type"],
        )
    counts = clean["data"]
    if counts["errors"]:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed",
            f"{counts['errors']} staged output file(s) failed validation{cleanup}",
            out_dir=str(dest), detail="cleanup-errors",
        )
    try:
        published = _publish_stage(
            stage, dest, _stage_files(stage, ".png", cancelled),
            [project, opts.engine_path, *opts.protected_paths],
            cancelled,
        )
        _remove_stage(stage, output_root)
    except CommandCancelled as ex:
        _propagate_cancel(ex, stage, output_root)
    except OSError as ex:
        cleanup = _discard_stage(stage, output_root)
        return Result(
            str(project), "failed", f"could not publish export: {ex}{cleanup}",
            out_dir=str(dest), detail="output.publish",
        )
    reason = f"exported for {opts.target_engine}; removed {counts['cleaned']} junk file(s)"
    if opts.resize is not None:
        reason += f"; resized {counts['resized']} PNG(s) to {opts.resize}px"
    reason += f"; published {published} PNG(s)"
    return Result(str(project), "rendered", reason, out_dir=str(dest))
