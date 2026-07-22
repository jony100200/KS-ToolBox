"""Video Chopper engine — split one video into clips at black-frame gaps.

Pure logic, no UI, no global state. Cross-platform: every external tool is
resolved via a bundled `bin/` first, then PATH.

Rebuilt from the original OpenCV chopper on ffmpeg, for three reasons:
  * detection runs in C (ffmpeg `blackdetect`), not a Python per-frame loop —
    orders of magnitude faster on long sources;
  * cutting is a stream-copy by default (lossless, near-instant) instead of a
    full mpeg4 re-encode of every frame (the original's quality/size problem);
  * no OpenCV/numpy dependency in the shipped bundle.

Errors are values: every fallible call returns the standard envelope
{error, error_type, retryable, degraded, details, data}. See CodingPrinciples.md.

Public interface:
    probe_duration(path)          -> envelope(float seconds)
    detect_black(path, ...)       -> envelope([(start, end), ...])
    plan_clips(blacks, dur, min)  -> [(start, end), ...]        (pure, no I/O)
    cut_clip(src, dst, s, e, ...) -> envelope
    process(path, opts)           -> Result                     (orchestrates)
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import (
    VIDEO_EXTS, ok as _ok, err as _err,
    resolve_tool as _tool, run_cmd as _run, tools_status as _tools_status_raw,
)


def tools_status() -> dict[str, str | None]:
    """What's available on this machine."""
    return _tools_status_raw(["ffprobe", "ffmpeg"])


# ---------------------------------------------------------------------------
# 1. probe duration
# ---------------------------------------------------------------------------

def probe_duration(path: str | Path) -> dict:
    """Total duration in seconds via ffprobe. Envelope out."""
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    ff = _tool("ffprobe")
    if not ff:
        return _err("dep.missing", "ffprobe not found (bundle a bin/ or install ffmpeg)")
    cmd = [ff, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=nw=1:nk=1", str(p)]
    try:
        r = _run(cmd, timeout=60)
    except subprocess.TimeoutExpired:
        return _err("probe.timeout", f"ffprobe timed out on {p.name}", retryable=True)
    if r.returncode != 0:
        return _err("probe.failed", f"ffprobe failed: {r.stderr.strip()[:200]}")
    try:
        return _ok(float(r.stdout.strip()))
    except ValueError:
        return _err("probe.parse", f"could not read duration of {p.name}")


# ---------------------------------------------------------------------------
# 2. detect black gaps — ffmpeg blackdetect (C-level, fast)
# ---------------------------------------------------------------------------

_BLACK_RE = re.compile(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)")


def detect_black(path: str | Path, min_black_s: float = 0.10,
                 pix_th: float = 0.10, timeout: int | None = None) -> dict:
    """Black intervals [(start, end), ...] found by ffmpeg's blackdetect filter.

    min_black_s : minimum duration a stretch must be black to count as a gap.
    pix_th      : pixel blackness threshold 0..1 (lower = stricter).
    """
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found (bundle a bin/ or install ffmpeg)")
    cmd = [ff, "-hide_banner", "-i", str(p),
           "-vf", f"blackdetect=d={min_black_s}:pix_th={pix_th}",
           "-an", "-f", "null", "-"]
    try:
        r = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _err("blackdetect.timeout", f"blackdetect timed out on {p.name}", retryable=True)
    # blackdetect writes to stderr even on success; a non-zero code is a real failure.
    if r.returncode != 0:
        return _err("blackdetect.failed", f"ffmpeg failed: {(r.stderr or '')[-200:]}")
    blacks = [(float(a), float(b)) for a, b in _BLACK_RE.findall(r.stderr or "")]
    return _ok(sorted(blacks))


# ---------------------------------------------------------------------------
# 3. plan clips — pure function (the heart of the tool; trivially testable)
# ---------------------------------------------------------------------------

def plan_clips(blacks: list[tuple[float, float]], duration: float,
               min_clip_s: float = 0.5) -> list[tuple[float, float]]:
    """The content segments *between* black gaps. A clip is kept only if it runs
    at least `min_clip_s`. Boundaries land on content edges, excluding the black.
    """
    clips: list[tuple[float, float]] = []
    cursor = 0.0
    for bstart, bend in sorted(blacks):
        if bstart - cursor >= min_clip_s:
            clips.append((cursor, bstart))
        cursor = max(cursor, bend)
    if duration - cursor >= min_clip_s:
        clips.append((cursor, duration))
    return clips


# ---------------------------------------------------------------------------
# 4. cut one clip
# ---------------------------------------------------------------------------

def cut_clip(src: str | Path, dst: str | Path, start: float, end: float, *,
             reencode: bool = False, crf: int = 18, timeout: int | None = None) -> dict:
    """Extract [start, end) of src into dst. Envelope out.

    reencode=False : stream-copy — lossless and near-instant, but the cut snaps
                     to the nearest prior keyframe (fine for black-gap splits).
    reencode=True  : re-encode H.264 — frame-accurate boundaries, slower.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return _err("file.missing", f"source not found: {src}")
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Keep the real extension on the temp ("clip.part.mp4") so ffmpeg can infer
    # the muxer; a bare ".part" leaves it unable to pick a container.
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")   # atomic: write, rename on success
    dur = max(0.0, end - start)
    if reencode:
        codec = ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-c:a", "aac"]
    else:
        codec = ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd = [ff, "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
           *codec, "-map", "0", str(tmp)]
    try:
        r = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return _err("cut.timeout", f"ffmpeg timed out cutting {src.name}", retryable=True)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return _err("cut.failed", f"ffmpeg failed: {(r.stderr or '')[-200:]}")
    tmp.replace(dst)
    return _ok({"out_bytes": dst.stat().st_size})


# ---------------------------------------------------------------------------
# 5. process — the whole pipeline for one file
# ---------------------------------------------------------------------------

@dataclass
class ChopOptions:
    out_root: Path | None = None      # output root; None = <src>/<stem>_clips beside source
    input_root: Path | None = None    # mirror mode recreates paths relative to this root
    mirror: bool = False              # rebuild input folder structure under out_root
    min_black_s: float = 0.10         # min black-stretch length to treat as a cut point
    pix_th: float = 0.10              # blackdetect pixel threshold 0..1
    min_clip_s: float = 0.5           # discard clips shorter than this
    reencode: bool = False            # frame-accurate H.264 cut vs lossless stream-copy
    container: str = ".mp4"           # output container/extension
    dry_run: bool = True              # default safe: report clip plan, write nothing


def plan_output_dir(src: Path, opts: ChopOptions) -> Path:
    """Folder that receives this source's clips. Mirror mode preserves the input
    subtree under out_root; else a flat per-source folder under out_root; else a
    `<stem>_clips` folder beside the source."""
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / src.stem
            except ValueError:
                pass  # source not under input_root — fall back to flat
        return root / src.stem
    return src.parent / f"{src.stem}_clips"


@dataclass
class Result:
    src: str
    action: str                       # chopped | skipped | failed | dry-run
    reason: str
    clips: int = 0
    out_dir: str | None = None
    clip_paths: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: ChopOptions) -> Result:
    """probe -> detect black -> plan clips -> (dry-run report | cut each clip)."""
    src = Path(path)
    dur = probe_duration(src)
    if dur["error"]:
        return Result(str(src), "failed", dur["details"], detail=dur["error_type"])
    duration: float = dur["data"]

    det = detect_black(src, opts.min_black_s, opts.pix_th)
    if det["error"]:
        return Result(str(src), "failed", det["details"], detail=det["error_type"])
    clips = plan_clips(det["data"], duration, opts.min_clip_s)

    if not clips:
        return Result(str(src), "skipped", "no black-gap splits found (single continuous clip)")

    out_dir = plan_output_dir(src, opts)

    if opts.dry_run:
        preview = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in clips[:6])
        more = "" if len(clips) <= 6 else f" (+{len(clips) - 6} more)"
        return Result(str(src), "dry-run", f"would cut {len(clips)} clips: {preview}{more}",
                      clips=len(clips), out_dir=str(out_dir))

    written: list[str] = []
    for i, (start, end) in enumerate(clips, 1):
        dst = out_dir / f"{src.stem}_clip_{i:03d}{opts.container}"
        res = cut_clip(src, dst, start, end, reencode=opts.reencode)
        if res["error"]:
            return Result(str(src), "failed", f"clip {i}: {res['details']}",
                          clips=len(written), out_dir=str(out_dir),
                          clip_paths=written, detail=res["error_type"])
        written.append(str(dst))

    return Result(str(src), "chopped", f"{len(written)} clips written",
                  clips=len(written), out_dir=str(out_dir), clip_paths=written,
                  detail="re-encoded H.264" if opts.reencode else "stream-copy (lossless)")
