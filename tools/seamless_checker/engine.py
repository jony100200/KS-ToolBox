"""Seamless Checker engine — pure, headless domain and application logic.

No UI, no CustomTkinter, no global state.
What it does:
    1. Scan folder: dual-pass detection of multi-map PBR sets and standalone textures.
    2. 3x3 wrap preview: integer box-reduced tiling in memory with optional seam guides.
    3. Triage / Redo transfer: atomic, safe staging transfer of either a single standalone
       file directly to redo_root/<file.name> or a full PBR set to redo_root/<SetName>/...
    4. Resumable state: records decisions (OK/Redo) and last active position per folder.
    5. Settings: remembers last-used input and redo directories across sessions.

Standard error-envelope convention (toolbox.engine_common ok/err).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from toolbox.engine_common import IMAGE_EXTS, err, ok

SEPARATORS = "_- ."

# Longest first so `base_color` matches before `color`.
BASE_TOKENS = (
    "basecolor", "base_color", "base-color", "basemap", "base_map", "albedo",
    "diffuse", "colour", "color", "diff", "col", "bc",
)
GLUED_BASE_TOKENS = ("basecolor", "basemap", "albedo", "diffuse")

MAP_TOKENS = {
    "normal", "nrm", "nor", "roughness", "rough", "height", "displacement", "disp",
    "ao", "ambientocclusion", "occlusion", "metallic", "metalness", "metal", "orm",
    "rma", "arm", "emission", "emissive", "opacity", "alpha", "specular", "gloss",
    "glossiness", "smoothness", "bump", "translucency",
}

RES_TOKENS = (
    "1k", "2k", "4k", "8k", "16k",
    "1024", "2048", "4096", "8192",
    "uhd", "hdr", "fhd",
)

STATE_DIR = Path(__file__).resolve().parent / "_local"


# ---------------------------------------------------------------------------
# pure wrap preview — in memory
# ---------------------------------------------------------------------------

def tile_preview(img, repeats_x: int = 3, repeats_y: int = 3):
    """Tile a PIL image across an NxM grid."""
    from PIL import Image
    rx = max(1, int(repeats_x))
    ry = max(1, int(repeats_y))
    w, h = img.size
    canvas = Image.new(img.mode, (w * rx, h * ry))
    for yi in range(ry):
        for xi in range(rx):
            canvas.paste(img, (xi * w, yi * h))
    return canvas


def build_wrap_preview(
    path: str | Path,
    max_tile: int = 768,
    repeats: int = 3,
    seam_guide: bool = False,
):
    """Edge-to-edge repeats x repeats wraparound view of one image in memory.

    Large maps are shrunk with integer box reduce so tile boundaries align
    without filtering artifacts at the wrap seams.
    """
    from PIL import Image
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"image not found: {src}")

    with Image.open(src) as opened:
        image = opened.convert("RGB")

    factor = max(1, -(-max(image.size) // max(64, int(max_tile))))
    if factor > 1:
        image = image.reduce(factor)

    tiled = tile_preview(image, repeats_x=repeats, repeats_y=repeats)
    if seam_guide and repeats > 1:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(tiled)
        tw, th = image.size
        for i in range(1, repeats):
            draw.line([(i * tw, 0), (i * tw, tiled.height)], fill=(220, 60, 60), width=1)
            draw.line([(0, i * th), (tiled.width, i * th)], fill=(220, 60, 60), width=1)
    return tiled


# ---------------------------------------------------------------------------
# stem & token analysis
# ---------------------------------------------------------------------------

def strip_res_suffix(stem: str) -> tuple[str, str]:
    """Return (stem_without_resolution, resolution_token_or_empty)."""
    lowered = stem.lower()
    for res in RES_TOKENS:
        if lowered.endswith(res):
            prefix = stem[:-len(res)]
            if prefix and prefix[-1] in SEPARATORS:
                return prefix.rstrip(SEPARATORS), res
    return stem, ""


def base_split(stem: str) -> str | None:
    """Return set name if stem ends in a base-map token, else None."""
    clean_stem, _ = strip_res_suffix(stem)
    lowered = clean_stem.lower()
    for token in BASE_TOKENS:
        if not lowered.endswith(token):
            continue
        before = clean_stem[:-len(token)]
        if not before:
            return ""
        if before[-1] in SEPARATORS:
            return before.rstrip(SEPARATORS)
        if token in GLUED_BASE_TOKENS and before[-1].isalnum():
            return before
    return None


def is_secondary_map(stem: str) -> bool:
    """True if stem ends in a recognized non-base PBR map token (normal, rough, etc.)."""
    clean_stem, _ = strip_res_suffix(stem)
    lowered = clean_stem.lower()
    for token in MAP_TOKENS:
        if lowered.endswith(token):
            before = clean_stem[:-len(token)]
            if not before or before[-1] in SEPARATORS:
                return True
    return False


def belongs_to_set(stem: str, name: str, folder_name: str | None = None) -> bool:
    """Determine if a file belongs to a material set named `name`."""
    clean_stem, _ = strip_res_suffix(stem)
    lowered, prefix = clean_stem.lower(), name.lower()
    if lowered == prefix:
        return True
    if lowered.startswith(prefix):
        rest = lowered[len(prefix):]
        if not rest or rest[0] in SEPARATORS or rest in MAP_TOKENS or rest in BASE_TOKENS:
            return True
        if base_split(stem) == name:
            return True
    if folder_name and name.lower() == folder_name.lower():
        clean_no_sep = clean_stem.strip(SEPARATORS).lower()
        if clean_no_sep in MAP_TOKENS or clean_no_sep in BASE_TOKENS:
            return True
        for m in MAP_TOKENS:
            if clean_no_sep.endswith(m) or clean_no_sep.startswith(m):
                return True
    return False


def _token_index(stem: str) -> int:
    clean_stem, _ = strip_res_suffix(stem)
    lowered = clean_stem.lower()
    for idx, token in enumerate(BASE_TOKENS):
        if lowered.endswith(token):
            return idx
    return len(BASE_TOKENS)


# ---------------------------------------------------------------------------
# dual-pass scanning
# ---------------------------------------------------------------------------

def _folders(root: Path, redo: Path | None) -> list[Path]:
    found = [root]
    for current, names, _files in os.walk(root):
        names[:] = sorted(
            name for name in names
            if not name.startswith((".", "_")) and not name.lower().endswith(".ksmat")
            and not (redo and (Path(current) / name).resolve() == redo.resolve())
        )
        found.extend(Path(current) / name for name in names)
    return sorted(set(found))


def _sets_in_folder(folder: Path, root: Path, decisions: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        files = sorted(path for path in folder.iterdir() if path.is_file())
    except OSError:
        return []

    names: dict[str, Path] = {}

    # Pass 1: explicit base map tokens (_BaseColor, _Albedo, etc.)
    for path in files:
        if path.suffix.lower() not in IMAGE_EXTS:
            continue
        name = base_split(path.stem)
        if name is None:
            continue
        name = name or folder.name
        current = names.get(name)
        if current is None or _token_index(path.stem) < _token_index(current.stem):
            names[name] = path

    # Pass 2: standalone textures without explicit PBR suffixes or secondary maps
    for path in files:
        if path.suffix.lower() not in IMAGE_EXTS or is_secondary_map(path.stem):
            continue
        if any(belongs_to_set(path.stem, explicit_name, folder_name=folder.name) for explicit_name in names):
            continue
        clean_stem, _ = strip_res_suffix(path.stem)
        name = clean_stem or path.stem
        if name not in names:
            names[name] = path

    members: dict[str, list[Path]] = {name: [] for name in names}
    if len(names) == 1 and folder != root:
        only_name = next(iter(names))
        members[only_name] = [p for p in files if p.suffix.lower() in IMAGE_EXTS]
    else:
        ranked = sorted(names, key=len, reverse=True)
        for path in files:
            if path.suffix.lower() not in IMAGE_EXTS:
                continue
            for name in ranked:
                if belongs_to_set(path.stem, name, folder_name=folder.name):
                    members[name].append(path)
                    break

    rows: list[dict[str, Any]] = []
    for name, base in names.items():
        key = str(base.relative_to(root)).replace("\\", "/")
        rows.append({
            "key": key,
            "name": name,
            "base_map": str(base),
            "folder": str(folder),
            "files": [str(p) for p in members[name]],
            "decision": str((decisions.get(key) or {}).get("decision") or "pending"),
        })
    return rows


def scan_checker_sets(root_path: str | Path, redo_path: str | Path | None = None) -> dict[str, Any]:
    """Scan root_path recursively for all texture sets and standalone textures."""
    root = Path(root_path).resolve()
    if not root.is_dir():
        return err("folder.missing", f"input folder not found: {root_path}")

    redo = Path(redo_path).resolve() if redo_path else None
    state = read_state(root)
    decisions = state.get("decisions", {})

    items: list[dict[str, Any]] = []
    for folder in _folders(root, redo):
        items.extend(_sets_in_folder(folder, root, decisions))

    items.sort(key=lambda row: row["key"].casefold())
    counts = [row["decision"] for row in items]

    data = {
        "root": str(root),
        "position_key": state.get("position", ""),
        "count": len(items),
        "items": items,
        "progress": {
            "total": len(items),
            "ok": counts.count("ok"),
            "redo": counts.count("redo"),
            "pending": counts.count("pending"),
        },
    }
    return ok(data)


# ---------------------------------------------------------------------------
# safe Redo transfer (all-or-nothing, collision safe)
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return cleaned or "set"


def _safe_file_dest(dest_dir: Path, filename: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = dest_dir / f"{stem}{suffix}"
    count = 2
    while candidate.exists():
        candidate = dest_dir / f"{stem}_{count}{suffix}"
        count += 1
    return candidate


def _safe_dir_dest(dest_dir: Path, folder_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / folder_name
    count = 2
    while candidate.exists():
        candidate = dest_dir / f"{folder_name}_{count}"
        count += 1
    return candidate


def send_set(
    root_path: str | Path,
    key: str,
    redo_path: str | Path,
    mode: str = "copy",
) -> dict[str, Any]:
    """Transfer one texture set to redo_root safely.

    If set has 1 file (standalone texture), it is copied/moved directly to
    redo_root/<filename>.
    If set has >1 files (PBR set), it is copied/moved into redo_root/<SetName>/.
    Uses atomic staging to prevent partial writes.
    """
    root = Path(root_path).resolve()
    redo = Path(redo_path).resolve()
    if not root.is_dir():
        return err("input.missing", f"input root not found: {root}")
    if redo == root:
        return err("redo.invalid", "redo folder must differ from input folder")

    redo.mkdir(parents=True, exist_ok=True)
    mode = mode.strip().lower()
    if mode not in {"copy", "move"}:
        return err("mode.invalid", f"invalid transfer mode: {mode}")

    base = root / key
    if not base.is_file():
        return err("base.missing", f"base map is missing: {base}")

    found_sets = _sets_in_folder(base.parent, root, {})
    row = next((r for r in found_sets if r["base_map"] == str(base)), None)
    if row is None:
        return err("set.missing", f"could not resolve set for {base.name}")

    sources = [Path(p) for p in row["files"] if Path(p).is_file()]
    if not sources:
        return err("files.missing", f"no files found for set {row['name']}")

    if len(sources) == 1:
        # Standalone single image -> direct file to redo_root
        source = sources[0]
        stage = redo / f".incoming_{uuid.uuid4().hex[:8]}_{source.name}"
        try:
            shutil.copy2(source, stage)
            if stage.stat().st_size != source.stat().st_size:
                raise OSError(f"size mismatch on staged copy of {source.name}")
            dest = _safe_file_dest(redo, source.name)
            os.rename(stage, dest)
            if mode == "move":
                source.unlink()
        except Exception as ex:
            if stage.exists():
                stage.unlink(missing_ok=True)
            return err("transfer.failed", f"failed transferring single image: {ex}")
        return ok({"dest": str(dest), "files": 1, "mode": mode, "is_single": True})

    # Multi-file set -> dedicated subfolder
    stage = redo / f".incoming_{uuid.uuid4().hex[:8]}"
    stage.mkdir(parents=True, exist_ok=True)
    try:
        for source in sources:
            target = stage / source.name
            shutil.copy2(source, target)
            if target.stat().st_size != source.stat().st_size:
                raise OSError(f"size mismatch on staged copy of {source.name}")
        final_dir = _safe_dir_dest(redo, _safe_name(row["name"]))
        os.rename(stage, final_dir)
        if mode == "move":
            for source in sources:
                source.unlink(missing_ok=True)
    except Exception as ex:
        shutil.rmtree(stage, ignore_errors=True)
        return err("transfer.failed", f"failed transferring set: {ex}")

    return ok({"dest": str(final_dir), "files": len(sources), "mode": mode, "is_single": False})


# ---------------------------------------------------------------------------
# state persistence (per scanned folder)
# ---------------------------------------------------------------------------

def _state_file(root: Path) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    folder_hash = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return STATE_DIR / f"state_{folder_hash}.json"


def read_state(root: Path) -> dict[str, Any]:
    file_path = _state_file(root)
    if not file_path.is_file():
        return {"decisions": {}, "position": ""}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"decisions": {}, "position": ""}


def write_state(root: Path, state: dict[str, Any]) -> None:
    file_path = _state_file(root)
    tmp = file_path.with_name(f"{file_path.stem}.part{file_path.suffix}")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(file_path)


def decide_set(
    root_path: str | Path,
    key: str,
    decision: str,
    redo_path: str | Path | None = None,
    mode: str = "copy",
) -> dict[str, Any]:
    """Record OK or Redo verdict. If Redo, execute transfer first."""
    root = Path(root_path).resolve()
    decision = decision.strip().lower()
    if decision not in {"ok", "redo"}:
        return err("decision.invalid", "decision must be 'ok' or 'redo'")

    state = read_state(root)
    prior = state.get("decisions", {}).get(key) or {}
    entry: dict[str, Any] = {"decision": decision}
    transfer_res: dict[str, Any] | None = None

    if decision == "redo":
        if not redo_path:
            return err("redo.missing", "redo folder is required for redo decision")
        dest_val = str(prior.get("dest") or "")
        if prior.get("decision") == "redo" and dest_val and Path(dest_val).exists():
            transfer_res = {"already_sent": True, "dest": dest_val, "files": prior.get("files", 1)}
        else:
            send_res = send_set(root, key, redo_path, mode=mode)
            if send_res.get("error"):
                return send_res
            transfer_res = send_res.get("data")
        if transfer_res:
            entry.update(
                dest=transfer_res.get("dest"),
                files=transfer_res.get("files", 1),
                mode=transfer_res.get("mode", mode),
            )

    state.setdefault("decisions", {})[key] = entry
    state["position"] = key
    write_state(root, state)
    return ok({"key": key, "decision": decision, "transfer": transfer_res})


def save_position(root_path: str | Path, key: str) -> dict[str, Any]:
    """Persist current review position."""
    root = Path(root_path).resolve()
    state = read_state(root)
    state["position"] = key
    write_state(root, state)
    return ok({"position_key": key})


# ---------------------------------------------------------------------------
# persistent tool settings (last used folders)
# ---------------------------------------------------------------------------

def _settings_file() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "settings.json"


def load_settings() -> dict[str, str]:
    path = _settings_file()
    if not path.is_file():
        return {"input_root": "", "redo_root": "", "mode": "copy"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "input_root": str(data.get("input_root") or ""),
                "redo_root": str(data.get("redo_root") or ""),
                "mode": str(data.get("mode") or "copy"),
            }
    except Exception:
        pass
    return {"input_root": "", "redo_root": "", "mode": "copy"}


def save_settings(input_root: str = "", redo_root: str = "", mode: str = "copy") -> None:
    path = _settings_file()
    current = load_settings()
    if input_root:
        current["input_root"] = str(input_root)
    if redo_root:
        current["redo_root"] = str(redo_root)
    if mode:
        current["mode"] = str(mode)
    tmp = path.with_name(f"{path.stem}.part{path.suffix}")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
