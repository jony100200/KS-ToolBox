"""Audio Tool engine — deterministic batch audio processing, pure logic, no UI.

One job: given an audio file and a set of options, produce a processed output
with the bundled ffmpeg. No AI, no network. Every operation is a plain ffmpeg
invocation, so results are byte-reproducible for the same input + options.

Operations, any combination in one pass:
  * convert  — re-encode to a target format (mp3/wav/flac/aac/m4a/ogg/opus).
  * trim     — keep [start, end] via input-side `-ss`/`-to`.
  * fade     — fade-in / fade-out via the `afade` filter (out needs duration).
  * normalize— loudness-normalize via the `loudnorm` filter.
Multiple filters chain into a single `-af`.

Cross-platform: ffmpeg/ffprobe resolved via engine_common (bundled bin/ first,
then PATH). Errors are values: fallible calls return the standard envelope
{error, error_type, retryable, degraded, details, data}; `dep.missing` when
ffmpeg/ffprobe is absent. Writes atomically (`name.part.ext` → replace) and is
non-destructive — the source is never overwritten.

Public interface:
    tools_status()             -> {name: path|None}
    probe_duration(path)       -> envelope(float seconds)
    build_filters(opts, dur)   -> str                     (pure -af assembler)
    plan_output(path, opts)    -> Path
    process(path, opts)        -> Result
"""
from __future__ import annotations

import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled,
    find_output_collisions as _find_collisions,
    ok,
    err,
    resolve_tool,
    run_cancellable_cmd,
    sha256_file,
    tools_status as _tools_status_raw,
)

# Every extension accepted as input. Output is restricted to the seven formats
# with a known muxer/codec below.
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aiff"}

# Target format -> ffmpeg muxer (the `.part` temp hides the real extension, so
# ffmpeg can't infer the container — we always state `-f <muxer>`).
_MUXER = {".mp3": "mp3", ".wav": "wav", ".flac": "flac",
          ".aac": "adts", ".m4a": "ipod", ".ogg": "ogg", ".opus": "opus"}

# Panel builds its dropdown from this order.
TARGET_FORMATS = ["mp3", "wav", "flac", "aac", "m4a", "ogg", "opus"]

# Formats that carry a user bitrate (lossy); the rest ignore it.
LOSSY_FORMATS = {"mp3", "aac", "m4a", "ogg", "opus"}


def tools_status() -> dict[str, str | None]:
    """What's available on this machine. UI/status can show this."""
    return _tools_status_raw(["ffmpeg", "ffprobe"])


# --- codec / muxer maps -------------------------------------------------------

def _muxer(ext: str) -> str | None:
    return _MUXER.get(ext.lower())


def _audio_codec(target_ext: str, bitrate: str) -> list[str]:
    """ffmpeg audio codec args for a target extension. Mirrors Format Converter."""
    return {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", bitrate],
        ".wav": ["-c:a", "pcm_s16le"],
        ".flac": ["-c:a", "flac"],
        ".aac": ["-c:a", "aac", "-b:a", bitrate],
        ".m4a": ["-c:a", "aac", "-b:a", bitrate],
        ".ogg": ["-c:a", "libvorbis", "-b:a", bitrate],
        ".opus": ["-c:a", "libopus", "-b:a", bitrate],
    }[target_ext.lower()]


# --- time parsing -------------------------------------------------------------

def _num(x: float) -> str:
    """Compact number for a filter/CLI arg: 3.0 -> '3', 2.5 -> '2.5'."""
    return f"{float(x):g}"


def _parse_time(s: str | None) -> float | None:
    """Seconds float from '' | '12.5' | 'mm:ss' | 'hh:mm:ss'. None if blank;
    None if unparseable (caller decides — never silently treated as zero)."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        if ":" in s:
            sec = 0.0
            for part in s.split(":"):
                sec = sec * 60 + float(part)
            return sec if math.isfinite(sec) else None
        value = float(s)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


# --- probe (ffprobe) ----------------------------------------------------------

def probe_duration(
    path: str | Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Total duration in seconds. Standard envelope."""
    p = Path(path)
    if not p.is_file():
        return err("file.missing", f"not a file: {p}")
    fp = resolve_tool("ffprobe")
    if not fp:
        return err("dep.missing", "ffprobe not found (bundle a bin/ or install ffmpeg)", retryable=False)
    cmd = [fp, "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(p)]
    try:
        r = run_cancellable_cmd(cmd, timeout=60, cancelled=cancelled)
    except subprocess.TimeoutExpired:
        return err("probe.timeout", f"ffprobe timed out on {p.name}", retryable=True)
    if r.returncode != 0:
        return err("probe.failed", f"ffprobe failed: {(r.stderr or '').strip()[:200]}")
    text = (r.stdout or "").strip()
    try:
        duration = float(text)
    except ValueError:
        return err("probe.parse", f"could not read duration from ffprobe ({text!r})")
    if not math.isfinite(duration) or duration <= 0:
        return err("probe.parse", f"invalid duration from ffprobe ({text!r})")
    return ok(duration)


# --- filter chain (pure) ------------------------------------------------------

def build_filters(opts: "AudioOptions", duration: float | None) -> str:
    """Assemble the `-af` chain from the selected options. Pure — no I/O.

    `duration` is the *effective* (post-trim) length, needed to place the
    fade-out. Order: fade-in, fade-out, loudnorm — one comma-joined chain.
    Returns "" when no filter is selected (caller then omits `-af`).
    """
    parts: list[str] = []
    if opts.fade_in and opts.fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={_num(opts.fade_in)}")
    if opts.fade_out and opts.fade_out > 0:
        st = 0.0
        if duration is not None:
            st = max(0.0, float(duration) - float(opts.fade_out))
        parts.append(f"afade=t=out:st={_num(st)}:d={_num(opts.fade_out)}")
    if opts.normalize:
        parts.append("loudnorm")
    return ",".join(parts)


# --- options / result ---------------------------------------------------------

@dataclass
class AudioOptions:
    target_format: str = "mp3"          # one of TARGET_FORMATS
    bitrate: str = "192k"               # lossy formats only
    trim_start: str = ""                # "" | seconds | mm:ss
    trim_end: str = ""                  # "" | seconds | mm:ss
    fade_in: float = 0.0                # seconds
    fade_out: float = 0.0               # seconds
    normalize: bool = False
    out_root: Path | None = None        # output root; None = ./audio beside source
    input_root: Path | None = None      # mirror mode recreates paths relative to this
    mirror: bool = False
    dry_run: bool = True                # default safe: report, don't write


@dataclass
class Result:
    src: str
    action: str                         # processed | skipped | failed | dry-run
    reason: str
    before: str = ""                    # source extension
    after: str = ""                     # target format
    ops: str = ""                       # human summary of operations
    out_path: str | None = None
    detail: str = ""
    output_bytes: int = 0
    output_sha256: str = ""
    duration_seconds: float = 0.0
    retryable: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# --- output planning ----------------------------------------------------------

def plan_output(src: str | Path, opts: AudioOptions) -> Path:
    """Where the processed file goes. Mirror preserves the input subtree under
    out_root; else flat under out_root; else an `audio` folder beside source."""
    src = Path(src)
    name = f"{src.stem}.{opts.target_format.lower().lstrip('.')}"
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass  # source not under input_root — fall back to flat
        return root / name
    return src.parent / "audio" / name


def find_output_collisions(paths, opts: AudioOptions) -> dict[str, tuple[str, ...]]:
    """Find shared output names and outputs that target selected inputs."""
    return _find_collisions(paths, lambda source: (plan_output(source, opts),))


def _describe_ops(opts: AudioOptions, src: Path, tgt_ext: str) -> str:
    ops: list[str] = []
    if src.suffix.lower() != tgt_ext:
        ops.append(f"convert→{opts.target_format}")
    else:
        ops.append(f"re-encode {opts.target_format}")
    if opts.trim_start.strip() or opts.trim_end.strip():
        ops.append("trim")
    if (opts.fade_in and opts.fade_in > 0) or (opts.fade_out and opts.fade_out > 0):
        ops.append("fade")
    if opts.normalize:
        ops.append("normalize")
    return ", ".join(ops)


# --- ffmpeg run ---------------------------------------------------------------

def _run_ffmpeg(src: Path, dst: Path, opts: AudioOptions,
                start: float | None, end: float | None, af: str,
                cancelled: Callable[[], bool] | None = None) -> dict:
    """`ffmpeg -y [-ss][-to] -i SRC -vn [-af] <codec> -f <muxer> TMP` then
    atomic-replace. Envelope out."""
    ff = resolve_tool("ffmpeg")
    if not ff:
        return err("dep.missing", "ffmpeg not found (bundle a bin/ or install ffmpeg)", retryable=False)
    tgt_ext = dst.suffix.lower()
    muxer = _muxer(tgt_ext)
    if not muxer:
        return err("unsupported.target", f"no ffmpeg muxer for {tgt_ext}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return err("io.prepare", f"could not prepare output folder: {ex}")
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")

    # Input-side seek: `-ss X -to Y` yields the [X, Y] segment and resets output
    # timestamps to 0 — which is why build_filters places fade-out against the
    # trimmed (effective) duration.
    pre: list[str] = []
    if start is not None and start > 0:
        pre += ["-ss", _num(start)]
    if end is not None:
        pre += ["-to", _num(end)]
    filt = ["-af", af] if af else []
    # -vn: drop any cover-art/video stream so the output is audio-only.
    cmd = [ff, "-y", *pre, "-i", str(src), "-vn",
           *filt, *_audio_codec(tgt_ext, opts.bitrate), "-f", muxer, str(tmp)]
    try:
        r = run_cancellable_cmd(cmd, timeout=3600, cancelled=cancelled)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return err("ff.timeout", f"ffmpeg timed out on {src.name}", retryable=True)
    try:
        candidate_ready = tmp.is_file() and tmp.stat().st_size > 0
        candidate_error = ""
    except OSError as ex:
        candidate_ready = False
        candidate_error = f"; candidate inspection failed: {ex}"
    if r.returncode != 0 or not candidate_ready:
        tmp.unlink(missing_ok=True)
        return err("ff.failed", f"ffmpeg failed: {(r.stderr or '')[-300:]}{candidate_error}")

    try:
        inspected = probe_duration(tmp, cancelled=cancelled)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    if inspected["error"] or not inspected["data"] or inspected["data"] <= 0:
        tmp.unlink(missing_ok=True)
        details = inspected["details"] if inspected["error"] else "output duration is zero"
        return err(
            "output.invalid",
            f"ffmpeg candidate failed validation: {details}",
            retryable=bool(inspected.get("retryable")),
        )
    try:
        out_bytes = tmp.stat().st_size
        digest = sha256_file(tmp, cancelled=cancelled)
        if cancelled is not None and cancelled():
            raise CommandCancelled(["audio-commit", str(tmp)])
        tmp.replace(dst)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as ex:
        tmp.unlink(missing_ok=True)
        return err("io.commit", f"could not commit processed audio: {ex}", retryable=True)
    return ok({
        "out_bytes": out_bytes,
        "output_sha256": digest,
        "duration_seconds": float(inspected["data"]),
    })


# --- process ------------------------------------------------------------------

def process(
    path: str | Path,
    opts: AudioOptions,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """validate → plan → (dry-run report | probe if needed → ffmpeg), one file."""
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if src.suffix.lower() not in AUDIO_EXTS:
        return Result(str(src), "failed", f"unsupported input type: {src.suffix}",
                      detail="unsupported.source")

    tgt_ext = "." + opts.target_format.lower().lstrip(".")
    if _muxer(tgt_ext) is None:
        return Result(str(src), "failed", f"unsupported target format: {opts.target_format}",
                      before=src.suffix.lstrip("."), detail="unsupported.target")

    # Surface bad trim input instead of silently treating it as no-trim.
    start = _parse_time(opts.trim_start)
    end = _parse_time(opts.trim_end)
    if opts.trim_start.strip() and start is None:
        return Result(str(src), "failed", f"invalid trim start: {opts.trim_start!r}", detail="bad.trim")
    if opts.trim_end.strip() and end is None:
        return Result(str(src), "failed", f"invalid trim end: {opts.trim_end!r}", detail="bad.trim")
    if start is not None and end is not None and end <= start:
        return Result(str(src), "failed", f"trim end ({end:g}) must be after start ({start:g})",
                      detail="bad.trim")
    if (start is not None and start < 0) or (end is not None and end < 0):
        return Result(str(src), "failed", "trim times must be zero or positive", detail="bad.trim")
    try:
        fades_valid = (
            math.isfinite(opts.fade_in)
            and math.isfinite(opts.fade_out)
            and opts.fade_in >= 0
            and opts.fade_out >= 0
        )
    except (TypeError, ValueError):
        fades_valid = False
    if not fades_valid:
        return Result(str(src), "failed", "fade times must be zero or positive", detail="bad.fade")

    dst = plan_output(src, opts)
    before, after = src.suffix.lstrip("."), opts.target_format
    ops = _describe_ops(opts, src, tgt_ext)

    # Non-destructive: never overwrite the source in place.
    if src.resolve() == dst.resolve():
        return Result(str(src), "skipped", "output would overwrite the source — "
                      "choose a different format or output folder",
                      before=before, after=after, ops=ops, detail="would-overwrite")

    if opts.dry_run:
        return Result(str(src), "dry-run", f"would {ops}", before=before, after=after,
                      ops=ops, out_path=str(dst))

    # Fade-out needs the effective (post-trim) duration to place `st`.
    duration: float | None = None
    if opts.fade_out and opts.fade_out > 0:
        pr = probe_duration(src, cancelled=cancelled)
        if pr["error"]:
            return Result(str(src), "failed", pr["details"], before=before, after=after,
                          ops=ops, detail=pr["error_type"], retryable=pr["retryable"])
        full = pr["data"]
        s = start or 0.0
        e_end = end if end is not None else full
        if e_end > full:
            e_end = full
        duration = max(0.0, e_end - s)

    af = build_filters(opts, duration)
    res = _run_ffmpeg(src, dst, opts, start, end, af, cancelled=cancelled)
    if res["error"]:
        return Result(str(src), "failed", res["details"], before=before, after=after,
                      ops=ops, detail=res["error_type"], retryable=res["retryable"])
    data = res["data"]
    return Result(str(src), "processed", ops, before=before, after=after, ops=ops,
                  out_path=str(dst), detail=f"{data['out_bytes'] / 1024:.0f} KB",
                  output_bytes=data["out_bytes"], output_sha256=data["output_sha256"],
                  duration_seconds=data["duration_seconds"])


def validate_result(result: Result, opts: AudioOptions) -> bool:
    """Verify stored decisions and exact, pre-validated audio artifacts."""
    if result.action == "skipped":
        return True
    if result.action == "dry-run":
        if not result.out_path:
            return False
        try:
            return Path(result.out_path).resolve(strict=False) == plan_output(
                result.src, opts
            ).resolve(strict=False)
        except (OSError, TypeError, ValueError):
            return False
    try:
        metadata_valid = (
            result.action == "processed"
            and bool(result.out_path)
            and isinstance(result.output_bytes, int)
            and not isinstance(result.output_bytes, bool)
            and result.output_bytes > 0
            and isinstance(result.output_sha256, str)
            and bool(result.output_sha256)
            and isinstance(result.duration_seconds, (int, float))
            and not isinstance(result.duration_seconds, bool)
            and math.isfinite(result.duration_seconds)
            and result.duration_seconds > 0
        )
    except (TypeError, ValueError):
        return False
    if not metadata_valid:
        return False
    try:
        output = Path(result.out_path)
        if output.resolve(strict=False) != plan_output(result.src, opts).resolve(strict=False):
            return False
        return (
            output.is_file()
            and output.stat().st_size == result.output_bytes
            and sha256_file(output) == result.output_sha256
        )
    except (OSError, TypeError, ValueError):
        return False
