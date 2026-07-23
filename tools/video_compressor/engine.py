"""Smart video-compression engine — pure logic, no UI, no global state.

One job: given a video, decide whether re-encoding is worth it, and if so
produce a smaller file at visually-lossless quality, proven by VMAF before the
original is ever removed. Cross-platform (Windows/Linux/macOS): all external
tools resolved via PATH.

LEGO block. Public interface:
    probe(path)            -> VideoInfo
    assess(info, policy)   -> Decision            (skip vs compress + why)
    compress(src, dst, ..) -> envelope            (runs FFmpeg)
    measure_vmaf(src, dst) -> float | None
    process(path, opts)    -> Result              (orchestrates all of the above)

Errors are values: every fallible call returns the standard envelope
{error, error_type, retryable, degraded, details, data} — never raises for
expected failures (missing tool, bad file). See CodingPrinciples.md.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import (
    VIDEO_EXTS, ok as _ok, err as _err,
    resolve_tool as _tool, run_cmd as _run, tools_status as _tools_status_raw,
    run_cancellable_cmd as _run_cancellable, CommandCancelled,
)

# Codecs that are already modern/efficient. If a file already uses one of these
# AND sits at a low bits-per-pixel, re-encoding buys little and risks quality.
EFFICIENT_CODECS = {"hevc", "h265", "av1", "vp9"}


def tools_status() -> dict[str, str | None]:
    """What's available on this machine. UI/status can show this."""
    return _tools_status_raw(["ffprobe", "ffmpeg"])


# ---------------------------------------------------------------------------
# 1. probe
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VideoInfo:
    path: str
    size_bytes: int
    codec: str
    width: int
    height: int
    fps: float
    duration_s: float
    bitrate_bps: int          # overall file bitrate (video+audio); good enough for the heuristic
    bits_per_pixel: float     # bitrate / (w*h*fps) — the efficiency signal

    @property
    def megabytes(self) -> float:
        return self.size_bytes / 1_000_000


def probe(path: str | Path, cancelled: Callable[[], bool] | None = None) -> dict:
    """ffprobe a file into a VideoInfo. Returns the standard envelope."""
    p = Path(path)
    if not p.is_file():
        return _err("file.missing", f"not a file: {p}")
    ff = _tool("ffprobe")
    if not ff:
        return _err("dep.missing", "ffprobe not found on PATH", retryable=False)
    cmd = [ff, "-v", "error", "-print_format", "json",
           "-show_entries", "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
           str(p)]
    try:
        r = _run_cancellable(cmd, timeout=60, cancelled=cancelled)
    except subprocess.TimeoutExpired:
        return _err("probe.timeout", f"ffprobe timed out on {p.name}", retryable=True)
    if r.returncode != 0:
        return _err("probe.failed", f"ffprobe failed: {r.stderr.strip()[:200]}")
    try:
        meta = json.loads(r.stdout)
    except json.JSONDecodeError:
        return _err("probe.parse", "could not parse ffprobe output")

    vstream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vstream:
        return _err("probe.novideo", f"no video stream in {p.name}")
    fmt = meta.get("format", {})

    size = int(fmt.get("size") or p.stat().st_size)
    duration = float(fmt.get("duration") or 0.0)
    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    fps = _parse_fps(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "0/1")

    # Overall bitrate: prefer the container value, else derive from size/duration.
    bitrate = int(fmt.get("bit_rate") or 0)
    if bitrate <= 0 and duration > 0:
        bitrate = int(size * 8 / duration)

    bpp = 0.0
    if width and height and fps > 0 and bitrate > 0:
        bpp = bitrate / (width * height * fps)

    info = VideoInfo(
        path=str(p), size_bytes=size, codec=(vstream.get("codec_name") or "").lower(),
        width=width, height=height, fps=round(fps, 3), duration_s=round(duration, 2),
        bitrate_bps=bitrate, bits_per_pixel=round(bpp, 5),
    )
    return _ok(info)


def _parse_fps(rate: str) -> float:
    try:
        if "/" in rate:
            num, den = rate.split("/")
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


# ---------------------------------------------------------------------------
# 2. assess — "is it already well compressed? does it need more?"
# ---------------------------------------------------------------------------

@dataclass
class Policy:
    """Thresholds that decide skip-vs-compress. Config over code."""
    # bits-per-pixel below which a file is considered already-efficient.
    # (Reference: visually-clean H.264 ~0.08-0.15 bpp; efficient HEVC ~0.04-0.08.)
    bpp_efficient_hevc: float = 0.045   # already-HEVC/AV1 at/below this -> skip
    bpp_efficient_h264: float = 0.070   # already-H.264 at/below this -> already lean, skip
    min_expected_saving: float = 0.15   # skip if we don't expect >=15% smaller
    crf: int = 20                        # SVT-AV1 visually-lossless target
    encoder: str = "svt_av1"            # SVT-AV1 (quality/size) | NVENC AV1 (fast)


@dataclass
class Decision:
    action: str          # "compress" | "skip"
    reason: str
    est_saving: float    # 0..1 fraction, rough
    target_crf: int


def assess(info: VideoInfo, policy: Policy) -> Decision:
    """Decide, from the probe alone, whether re-encoding earns its place."""
    is_efficient_codec = info.codec in EFFICIENT_CODECS
    thresh = policy.bpp_efficient_hevc if is_efficient_codec else policy.bpp_efficient_h264

    # Already an efficient codec sitting at a lean bitrate -> leave it alone.
    if is_efficient_codec and info.bits_per_pixel > 0 and info.bits_per_pixel <= thresh:
        return Decision("skip", f"already {info.codec.upper()} at {info.bits_per_pixel:.3f} bpp "
                        f"(<= {thresh:.3f}) — already efficient", 0.0, policy.crf)

    # A non-efficient codec that is nonetheless very lean: little to gain.
    if not is_efficient_codec and info.bits_per_pixel > 0 and info.bits_per_pixel <= thresh:
        return Decision("skip", f"{info.codec.upper()} but already lean at "
                        f"{info.bits_per_pixel:.3f} bpp — not worth re-encoding", 0.0, policy.crf)

    if info.bits_per_pixel <= 0:
        # Unknown bitrate (e.g. odd container) — try, but flag low confidence.
        return Decision("compress", "bitrate unknown — attempting compression (verified by VMAF)",
                        0.30, policy.crf)

    # Rough expected saving grows with how far above the efficient bpp we are.
    est = _estimate_saving(info, thresh, is_efficient_codec)
    if est < policy.min_expected_saving:
        return Decision("skip", f"expected saving ~{est*100:.0f}% below "
                        f"{policy.min_expected_saving*100:.0f}% floor — not worth it", est, policy.crf)
    return Decision("compress", f"{info.codec.upper()} at {info.bits_per_pixel:.3f} bpp "
                    f"(> {thresh:.3f}) — ~{est*100:.0f}% expected saving", est, policy.crf)


def _estimate_saving(info: VideoInfo, thresh: float, is_efficient_codec: bool) -> float:
    """Very rough: how much smaller a CRF-20 HEVC pass is likely to land.
    Capped and conservative — the real saving is measured, not trusted (rule #22)."""
    if info.bits_per_pixel <= 0:
        return 0.30
    ratio = thresh / info.bits_per_pixel          # <1 when bloated
    base = max(0.0, 1.0 - ratio)                  # bloat headroom
    # H.264 -> AV1 gives an extra structural ~25% at equal quality.
    codec_bonus = 0.0 if is_efficient_codec else 0.20
    return round(min(0.85, base * 0.9 + codec_bonus), 3)


# ---------------------------------------------------------------------------
# 3. compress
# ---------------------------------------------------------------------------

def compress(src: str | Path, dst: str | Path, *, crf: int = 20,
             encoder: str = "svt_av1", timeout: int | None = None,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """Encode src -> dst through the bundled LGPL FFmpeg build. Envelope out.

    encoder: 'svt_av1' (software AV1, best quality/size) or 'nvenc_av1' (GPU, fast).
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return _err("file.missing", f"source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")     # atomic: write .part, rename on success

    if encoder not in {"svt_av1", "nvenc_av1"}:
        return _err(
            "config.encoder",
            f"unsupported encoder {encoder!r}; choose svt_av1 or nvenc_av1",
        )
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found on PATH")
    cmd = _ffmpeg_cmd(ff, src, tmp, crf, encoder)
    tool_used = "ffmpeg"

    try:
        r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
    except CommandCancelled:
        tmp.unlink(missing_ok=True)
        raise
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return _err("encode.timeout", f"{tool_used} timed out on {src.name}", retryable=True)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return _err("encode.failed", f"{tool_used} failed: {(r.stderr or '')[-300:]}")

    tmp.replace(dst)
    return _ok({"tool": tool_used, "encoder": encoder, "crf": crf,
                "out_bytes": dst.stat().st_size}, details=f"encoded via {tool_used}")


def _ffmpeg_cmd(ff: str, src: Path, dst: Path, crf: int, encoder: str) -> list[str]:
    if encoder == "nvenc_av1":
        vcodec = ["-c:v", "av1_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(crf)]
    else:
        # SVT-AV1 is BSD-2-Clause and is included by the verified LGPL FFmpeg
        # bundle. Preset 6 is a quality/speed balance for unattended batches.
        vcodec = ["-c:v", "libsvtav1", "-preset", "6", "-crf", str(crf)]
    # -f matroska: the temp file is "<name>.mkv.part", so ffmpeg can't infer the
    # muxer from the extension — state it. The pipeline always targets MKV.
    return [ff, "-y", "-i", str(src), *vcodec,
            "-c:a", "copy", "-c:s", "copy", "-map", "0", "-f", "matroska", str(dst)]


# ---------------------------------------------------------------------------
# 4. verify (VMAF) — quality as evidence before we delete anything
# ---------------------------------------------------------------------------

def measure_vmaf(reference: str | Path, distorted: str | Path,
                 fps: float | None = None, timeout: int | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict:
    """Mean VMAF of distorted vs reference (0-100). Degrades to None if libvmaf
    isn't available — caller must treat a missing score as "unproven".

    Both streams MUST be frame-synced or the score is garbage: normalise
    frame rate (to the reference's fps), pixel format, and timestamps before
    libvmaf. Verified: without this a CRF-20 near-lossless encode reads ~74;
    with it, ~98 (the true value). Link order is [distorted][reference];
    input 0 = distorted.
    """
    ff = _tool("ffmpeg")
    if not ff:
        return _ok(None, degraded=True, details="ffmpeg absent — VMAF unavailable")
    sync = f"fps={fps}," if fps and fps > 0 else ""
    lavfi = (f"[0:v]{sync}format=yuv420p,setpts=PTS-STARTPTS[d];"
             f"[1:v]{sync}format=yuv420p,setpts=PTS-STARTPTS[r];"
             f"[d][r]libvmaf=n_threads=6")
    cmd = [ff, "-i", str(distorted), "-i", str(reference), "-lavfi", lavfi, "-f", "null", "-"]
    try:
        r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
    except subprocess.TimeoutExpired:
        return _ok(None, degraded=True, details="VMAF timed out")
    text = (r.stderr or "") + (r.stdout or "")
    score = _parse_vmaf(text)
    if score is None:
        return _ok(None, degraded=True, details="libvmaf not built into this ffmpeg — quality unproven")
    return _ok(score, details=f"VMAF {score:.2f}")


def _parse_vmaf(text: str) -> float | None:
    import re
    m = re.search(r"VMAF score:\s*([0-9.]+)", text) or re.search(r"vmaf.*?([0-9]{2}\.[0-9]+)", text)
    try:
        return float(m.group(1)) if m else None
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 5. process — the whole safe pipeline for one file
# ---------------------------------------------------------------------------

@dataclass
class ProcessOptions:
    out_root: Path | None = None      # output root; None = ./compressed beside each source
    input_root: Path | None = None    # mirror mode recreates paths relative to this root
    mirror: bool = False              # rebuild the input folder structure under out_root
    policy: Policy = field(default_factory=Policy)
    vmaf_floor: float = 92.0          # min VMAF to accept an encode (visually-lossless)
    delete_original: bool = False     # only true after user opts in; uses recycle bin when possible
    skip_existing: bool = True        # resumable batches: don't redo a file whose output exists
    dry_run: bool = True              # default safe: report, don't write/delete


def plan_output(src: Path, opts: ProcessOptions) -> Path:
    """Where this source's compressed file goes. Mirror mode preserves the
    input subtree under out_root; else flat under out_root; else ./compressed."""
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / (src.stem + ".mkv")
            except ValueError:
                pass  # source not under input_root — fall back to flat
        return root / (src.stem + ".mkv")
    return src.parent / "compressed" / (src.stem + ".mkv")


@dataclass
class Result:
    src: str
    action: str                       # skipped | compressed | failed | dry-run
    reason: str
    before_mb: float = 0.0
    after_mb: float = 0.0
    saved_pct: float = 0.0
    vmaf: float | None = None
    out_path: str | None = None
    original_removed: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def process(path: str | Path, opts: ProcessOptions,
            cancelled: Callable[[], bool] | None = None) -> Result:
    """probe -> assess -> (compress -> verify -> [delete]) for one file."""
    src = Path(path)
    pr = probe(src, cancelled=cancelled)
    if pr["error"]:
        return Result(str(src), "failed", pr["details"], detail=pr["error_type"])
    info: VideoInfo = pr["data"]

    decision = assess(info, opts.policy)
    if decision.action == "skip":
        return Result(str(src), "skipped", decision.reason, before_mb=round(info.megabytes, 1))

    dst = plan_output(src, opts)

    if opts.dry_run:
        return Result(str(src), "dry-run", f"would compress: {decision.reason}",
                      before_mb=round(info.megabytes, 1), saved_pct=round(decision.est_saving * 100, 0),
                      out_path=str(dst))

    # Resumable: a finished output means this file is already done.
    if opts.skip_existing and dst.is_file() and dst.stat().st_size > 0:
        existing = probe(dst, cancelled=cancelled)
        if existing["error"]:
            return Result(str(src), "failed",
                          f"existing output is invalid: {existing['details']}",
                          before_mb=round(info.megabytes, 1), out_path=str(dst),
                          detail="output.invalid")
        existing_info: VideoInfo = existing["data"]
        duration_tolerance = max(0.5, 2.0 / info.fps) if info.fps > 0 else 0.5
        if ((existing_info.width, existing_info.height) != (info.width, info.height)
                or abs(existing_info.duration_s - info.duration_s) > duration_tolerance):
            return Result(str(src), "failed",
                          "existing output does not match source geometry/duration",
                          before_mb=round(info.megabytes, 1), out_path=str(dst),
                          detail="output.mismatch")
        return Result(str(src), "skipped", "already compressed (output exists)",
                      before_mb=round(info.megabytes, 1), after_mb=round(dst.stat().st_size / 1e6, 1),
                      out_path=str(dst), detail="resumable")

    # The final path must never become visible before quality validation. A
    # crash/cancel during VMAF leaves only this disposable candidate.
    candidate = dst.with_name(f"{dst.stem}.verify{dst.suffix}")
    candidate.unlink(missing_ok=True)
    enc = compress(src, candidate, crf=decision.target_crf,
                   encoder=opts.policy.encoder, cancelled=cancelled)
    if enc["error"]:
        return Result(str(src), "failed", enc["details"], before_mb=round(info.megabytes, 1),
                      detail=enc["error_type"])

    after = candidate.stat().st_size
    saved = 1.0 - (after / info.size_bytes) if info.size_bytes else 0.0

    # Quality gate — measure before trusting (sync to source fps).
    try:
        vm = measure_vmaf(src, candidate, fps=info.fps, cancelled=cancelled)
    except Exception:
        candidate.unlink(missing_ok=True)
        raise
    vmaf = vm["data"]

    # Reject a bad encode: bigger than source, or measurably below the VMAF floor.
    if after >= info.size_bytes:
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped", "re-encode was not smaller — kept original",
                      before_mb=round(info.megabytes, 1), after_mb=round(after / 1e6, 1),
                      vmaf=vmaf, detail="no-gain")
    if vmaf is not None and vmaf < opts.vmaf_floor:
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped", f"VMAF {vmaf:.1f} < {opts.vmaf_floor:.0f} floor — "
                      f"would lose quality, kept original", before_mb=round(info.megabytes, 1),
                      vmaf=vmaf, detail="quality-floor")

    dst.parent.mkdir(parents=True, exist_ok=True)
    candidate.replace(dst)

    removed = False
    if opts.delete_original and vmaf is not None and vmaf >= opts.vmaf_floor:
        removed = _safe_remove(src)

    return Result(
        str(src), "compressed", decision.reason,
        before_mb=round(info.megabytes, 1), after_mb=round(after / 1e6, 1),
        saved_pct=round(saved * 100, 1), vmaf=vmaf, out_path=str(dst),
        original_removed=removed,
        detail=("original removed" if removed else "original kept") +
               ("" if vmaf is not None else " (VMAF unproven)"),
    )


def validate_result(result: Result) -> bool:
    """Verify a stored completed video before the batch core reuses it."""
    if result.action != "compressed" or not result.out_path:
        return result.action in {"skipped", "dry-run"}
    output = Path(result.out_path)
    if not output.is_file() or output.stat().st_size <= 0:
        return False
    inspected = probe(output)
    if inspected["error"]:
        return False
    info: VideoInfo = inspected["data"]
    return info.width > 0 and info.height > 0 and info.duration_s > 0


def _safe_remove(path: Path) -> bool:
    """Delete to Recycle Bin if send2trash is available (recoverable), else hard delete."""
    try:
        import send2trash  # optional dep; recoverable delete
        send2trash.send2trash(str(path))
        return True
    except ImportError:
        try:
            path.unlink()
            return True
        except OSError:
            return False
