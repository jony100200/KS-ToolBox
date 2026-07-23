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
    validate_result(result, opts) -> bool                       (reuse gate)
"""
from __future__ import annotations

import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    VIDEO_EXTS,
    err as _err,
    find_output_collisions as _find_collisions,
    ok as _ok,
    probe_media_duration,
    resolve_tool as _tool,
    run_cancellable_cmd as _run_cancellable,
    sha256_file,
    tools_status as _tools_status_raw,
)

_FFMPEG_TIMEOUT = 6 * 60 * 60


def _duration_plausible(actual: float, expected: float) -> bool:
    """Wide guard for stream-copy keyframe variance while rejecting wrong cuts."""
    lower = max(0.01, min(expected * 0.25, 0.25))
    upper = expected + max(1.0, expected * 0.5)
    return math.isfinite(actual) and lower <= actual <= upper


def tools_status() -> dict[str, str | None]:
    """What's available on this machine."""
    return _tools_status_raw(["ffprobe", "ffmpeg"])


# ---------------------------------------------------------------------------
# 1. probe duration
# ---------------------------------------------------------------------------

def probe_duration(
    path: str | Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Compatibility wrapper around the shared cancellable ffprobe adapter."""
    return probe_media_duration(path, cancelled=cancelled)


# ---------------------------------------------------------------------------
# 2. detect black gaps — ffmpeg blackdetect (C-level, fast)
# ---------------------------------------------------------------------------

_BLACK_RE = re.compile(r"black_start:([0-9.]+)\s+black_end:([0-9.]+)")


def detect_black(path: str | Path, min_black_s: float = 0.10,
                 pix_th: float = 0.10, timeout: int | None = _FFMPEG_TIMEOUT,
                 cancelled: Callable[[], bool] | None = None) -> dict:
    """Black intervals [(start, end), ...] found by ffmpeg's blackdetect filter.

    min_black_s : minimum duration a stretch must be black to count as a gap.
    pix_th      : pixel blackness threshold 0..1 (lower = stricter).
    """
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    try:
        min_black_s = float(min_black_s)
        pix_th = float(pix_th)
    except (TypeError, ValueError):
        return _err("bad.options", "black-gap settings must be numbers")
    if not math.isfinite(min_black_s) or min_black_s <= 0:
        return _err("bad.options", "minimum black gap must be positive")
    if not math.isfinite(pix_th) or not 0.0 <= pix_th <= 1.0:
        return _err("bad.options", "black threshold must be between 0 and 1")
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found (bundle a bin/ or install ffmpeg)")
    cmd = [ff, "-hide_banner", "-i", str(p),
           "-vf", f"blackdetect=d={min_black_s}:pix_th={pix_th}",
           "-an", "-f", "null", "-"]
    try:
        r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
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
             reencode: bool = False, crf: int = 18,
             timeout: int | None = _FFMPEG_TIMEOUT,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """Extract [start, end) of src into dst. Envelope out.

    reencode=False : stream-copy — lossless and near-instant, but the cut snaps
                     to the nearest prior keyframe (fine for black-gap splits).
    reencode=True  : re-encode H.264 — frame-accurate boundaries, slower.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return _err("file.missing", f"source not found: {src}")
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return _err("bad.range", "clip boundaries must be numbers")
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        return _err("bad.range", "clip end must be after a non-negative start")
    try:
        crf_value = int(crf)
    except (TypeError, ValueError):
        return _err("bad.crf", "H.264 CRF must be an integer between 0 and 51")
    if reencode and not 0 <= crf_value <= 51:
        return _err("bad.crf", "H.264 CRF must be between 0 and 51")
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return _err("io.prepare", f"could not prepare clip folder: {ex}")
    # Keep the real extension on the temp ("clip.part.mp4") so ffmpeg can infer
    # the muxer; a bare ".part" leaves it unable to pick a container.
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")   # atomic: write, rename on success
    dur = max(0.0, end - start)
    if reencode:
        codec = ["-c:v", "libx264", "-crf", str(crf_value),
                 "-preset", "medium", "-c:a", "aac"]
    else:
        codec = ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    cmd = [ff, "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
           *codec, "-map", "0", str(tmp)]
    try:
        r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return _err("cut.timeout", f"ffmpeg timed out cutting {src.name}", retryable=True)
    try:
        candidate_ready = tmp.is_file() and tmp.stat().st_size > 0
        candidate_error = ""
    except OSError as ex:
        candidate_ready = False
        candidate_error = f"; candidate inspection failed: {ex}"
    if r.returncode != 0 or not candidate_ready:
        tmp.unlink(missing_ok=True)
        return _err("cut.failed", f"ffmpeg failed: {(r.stderr or '')[-200:]}{candidate_error}")

    try:
        inspected = probe_duration(tmp, cancelled=cancelled)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    if inspected["error"]:
        tmp.unlink(missing_ok=True)
        return _err(
            "output.invalid",
            f"clip candidate failed validation: {inspected['details']}",
            retryable=bool(inspected.get("retryable")),
        )
    if not _duration_plausible(float(inspected["data"]), dur):
        tmp.unlink(missing_ok=True)
        return _err(
            "output.duration",
            f"clip duration {inspected['data']:.3f}s does not match requested {dur:.3f}s",
        )
    try:
        out_bytes = tmp.stat().st_size
        digest = sha256_file(tmp, cancelled=cancelled)
        if cancelled is not None and cancelled():
            raise CommandCancelled(["clip-commit", str(tmp)])
        tmp.replace(dst)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as ex:
        tmp.unlink(missing_ok=True)
        return _err("io.commit", f"could not commit clip: {ex}", retryable=True)
    return _ok({
        "out_bytes": out_bytes,
        "output_sha256": digest,
        "duration_seconds": float(inspected["data"]),
    })


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


def find_output_collisions(paths, opts: ChopOptions) -> dict[str, tuple[str, ...]]:
    """Detect sources that would share a generated clip directory."""
    return _find_collisions(
        paths,
        lambda source: (plan_output_dir(source, opts) / ".ks_video_chopper_owner",),
    )


def planned_clip_paths(src: str | Path, opts: ChopOptions, count: int) -> list[str]:
    source = Path(src)
    out_dir = plan_output_dir(source, opts)
    return [
        str(out_dir / f"{source.stem}_clip_{index:03d}{opts.container}")
        for index in range(1, count + 1)
    ]


@dataclass
class Result:
    src: str
    action: str                       # chopped | skipped | failed | dry-run
    reason: str
    clips: int = 0
    out_dir: str | None = None
    clip_paths: list[str] = field(default_factory=list)
    clip_ranges: list[tuple[float, float]] = field(default_factory=list)
    clip_bytes: dict[str, int] = field(default_factory=dict)
    clip_sha256: dict[str, str] = field(default_factory=dict)
    clip_durations: dict[str, float] = field(default_factory=dict)
    detail: str = ""
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def process(
    path: str | Path,
    opts: ChopOptions,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """probe -> detect black -> plan clips -> (dry-run report | cut each clip)."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if src.suffix.lower() not in VIDEO_EXTS:
        return Result(
            str(src), "failed", f"unsupported input type: {src.suffix}",
            detail="unsupported.source",
        )
    try:
        min_clip_s = float(opts.min_clip_s)
        min_black_s = float(opts.min_black_s)
        pix_th = float(opts.pix_th)
    except (TypeError, ValueError):
        min_clip_s = float("nan")
        min_black_s = float("nan")
        pix_th = float("nan")
    if (
        not math.isfinite(min_clip_s)
        or min_clip_s <= 0
        or not math.isfinite(min_black_s)
        or min_black_s <= 0
        or not math.isfinite(pix_th)
        or not 0.0 <= pix_th <= 1.0
    ):
        return Result(
            str(src), "failed",
            "gap and clip length must be positive; threshold must be between 0 and 1",
            detail="bad.options",
        )
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", str(opts.container)):
        return Result(
            str(src), "failed", "output container must be a simple extension",
            detail="bad.container",
        )

    dur = probe_duration(src, cancelled=cancelled)
    if dur["error"]:
        return Result(
            str(src), "failed", dur["details"], detail=dur["error_type"],
            retryable=dur["retryable"],
        )
    duration: float = dur["data"]

    det = detect_black(src, min_black_s, pix_th, cancelled=cancelled)
    if det["error"]:
        return Result(
            str(src), "failed", det["details"], detail=det["error_type"],
            retryable=det["retryable"],
        )
    if not det["data"]:
        return Result(
            str(src), "skipped", "no black gaps found; source left unchanged"
        )
    clips = plan_clips(det["data"], duration, min_clip_s)

    if not clips:
        return Result(str(src), "skipped", "no content clips met the minimum length")

    out_dir = plan_output_dir(src, opts)

    if opts.dry_run:
        preview = ", ".join(f"{s:.1f}-{e:.1f}s" for s, e in clips[:6])
        more = "" if len(clips) <= 6 else f" (+{len(clips) - 6} more)"
        return Result(str(src), "dry-run", f"would cut {len(clips)} clips: {preview}{more}",
                      clips=len(clips), out_dir=str(out_dir),
                      clip_paths=planned_clip_paths(src, opts, len(clips)),
                      clip_ranges=clips)

    written: list[str] = []
    clip_bytes: dict[str, int] = {}
    clip_sha256: dict[str, str] = {}
    clip_durations: dict[str, float] = {}
    for i, (start, end) in enumerate(clips, 1):
        dst = out_dir / f"{src.stem}_clip_{i:03d}{opts.container}"
        res = cut_clip(
            src, dst, start, end, reencode=opts.reencode, cancelled=cancelled
        )
        if res["error"]:
            return Result(str(src), "failed", f"clip {i}: {res['details']}",
                          clips=len(written), out_dir=str(out_dir),
                          clip_paths=written, clip_ranges=clips,
                          clip_bytes=clip_bytes, clip_sha256=clip_sha256,
                          clip_durations=clip_durations, detail=res["error_type"],
                          retryable=res["retryable"])
        output = str(dst)
        data = res["data"]
        written.append(output)
        clip_bytes[output] = data["out_bytes"]
        clip_sha256[output] = data["output_sha256"]
        clip_durations[output] = data["duration_seconds"]

    return Result(str(src), "chopped", f"{len(written)} clips written",
                  clips=len(written), out_dir=str(out_dir), clip_paths=written,
                  clip_ranges=clips, clip_bytes=clip_bytes,
                  clip_sha256=clip_sha256, clip_durations=clip_durations,
                  detail="re-encoded H.264" if opts.reencode else "stream-copy (lossless)")


def validate_result(result: Result, opts: ChopOptions) -> bool:
    """Validate a stored decision or every exact artifact in a chopped set."""
    if (
        not isinstance(result.clips, int)
        or isinstance(result.clips, bool)
        or result.clips < 0
        or not isinstance(result.clip_paths, list)
        or not isinstance(result.clip_ranges, list)
        or not isinstance(result.clip_bytes, dict)
        or not isinstance(result.clip_sha256, dict)
        or not isinstance(result.clip_durations, dict)
    ):
        return False

    def ranges_are_valid() -> bool:
        if not isinstance(result.clip_ranges, list) or len(result.clip_ranges) != result.clips:
            return False
        for pair in result.clip_ranges:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                return False
            start, end = pair
            if (
                not isinstance(start, (int, float))
                or isinstance(start, bool)
                or not isinstance(end, (int, float))
                or isinstance(end, bool)
                or not math.isfinite(start)
                or not math.isfinite(end)
                or start < 0
                or end <= start
            ):
                return False
        return True

    if result.action == "skipped":
        return True
    if result.action == "dry-run":
        try:
            return (
                result.clips > 0
                and ranges_are_valid()
                and result.clip_paths == planned_clip_paths(result.src, opts, result.clips)
                and Path(result.out_dir).resolve(strict=False)
                == plan_output_dir(Path(result.src), opts).resolve(strict=False)
            )
        except (OSError, TypeError, ValueError):
            return False
    if result.action != "chopped" or result.clips <= 0:
        return False
    try:
        if (
            len(result.clip_paths) != result.clips
            or len(result.clip_bytes) != result.clips
            or len(result.clip_sha256) != result.clips
            or len(result.clip_durations) != result.clips
        ):
            return False
        expected = planned_clip_paths(result.src, opts, result.clips)
        if (
            result.clip_paths != expected
            or not ranges_are_valid()
            or Path(result.out_dir).resolve(strict=False)
            != plan_output_dir(Path(result.src), opts).resolve(strict=False)
            or set(result.clip_bytes) != set(expected)
            or set(result.clip_sha256) != set(expected)
            or set(result.clip_durations) != set(expected)
        ):
            return False
        for output, pair in zip(expected, result.clip_ranges):
            size = result.clip_bytes[output]
            duration = result.clip_durations[output]
            if (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size <= 0
                or not isinstance(duration, (int, float))
                or isinstance(duration, bool)
                or not math.isfinite(duration)
                or duration <= 0
                or not isinstance(result.clip_sha256[output], str)
                or not result.clip_sha256[output]
                or not _duration_plausible(float(duration), float(pair[1] - pair[0]))
            ):
                return False
            artifact = Path(output)
            if (
                not artifact.is_file()
                or artifact.stat().st_size != size
                or sha256_file(artifact) != result.clip_sha256[output]
            ):
                return False
        return True
    except (OSError, TypeError, ValueError):
        return False
