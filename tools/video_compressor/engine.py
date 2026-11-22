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

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, asdict, field
from pathlib import Path

from toolbox.engine_common import (
    VIDEO_EXTS, ok as _ok, err as _err,
    resolve_tool as _tool, run_cmd as _run, tools_status as _tools_status_raw,
    run_cancellable_cmd as _run_cancellable, CommandCancelled, sha256_file,
    commit_candidate as _commit_candidate, safe_remove as _safe_remove,
)

# Codecs that are already modern/efficient. If a file already uses one of these
# AND sits at a low bits-per-pixel, re-encoding buys little and risks quality.
EFFICIENT_CODECS = {"hevc", "h265", "av1", "vp9"}
BLOATED_AUDIO_CODECS = {"flac", "alac", "wavpack"}
AUDIO_REENCODE_BITRATE_FLOOR = 256_000
_MP4_SAFE_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac"}
_ENCODER_PROBE_CACHE: dict[tuple[str, str], bool] = {}

ENCODERS: dict[str, dict] = {
    "svt_av1": {
        "codec": "libsvtav1",
        "label": "AV1 · SVT-AV1 (best size/quality)",
        "flags": lambda crf: ["-c:v", "libsvtav1", "-preset:v", "6", "-crf:v", str(crf)],
    },
    "nvenc_av1": {
        "codec": "av1_nvenc",
        "label": "AV1 · NVIDIA NVENC (fast GPU)",
        "flags": lambda crf: ["-c:v", "av1_nvenc", "-preset:v", "p5", "-rc:v", "vbr", "-cq:v", str(crf), "-b:v", "0"],
    },
    "qsv_av1": {
        "codec": "av1_qsv",
        "label": "AV1 · Intel Arc / QSV (fast GPU)",
        "flags": lambda crf: ["-c:v", "av1_qsv", "-preset:v", "medium", "-global_quality:v", str(crf)],
    },
    "nvenc_hevc": {
        "codec": "hevc_nvenc",
        "label": "HEVC · NVIDIA NVENC (compatible)",
        "flags": lambda crf: ["-c:v", "hevc_nvenc", "-preset:v", "p5", "-rc:v", "vbr", "-cq:v", str(crf), "-b:v", "0"],
    },
    "qsv_hevc": {
        "codec": "hevc_qsv",
        "label": "HEVC · Intel Arc / QSV (compatible)",
        "flags": lambda crf: ["-c:v", "hevc_qsv", "-preset:v", "medium", "-global_quality:v", str(crf)],
    },
}


def tools_status() -> dict[str, str | None]:
    """What's available on this machine. UI/status can show this."""
    return _tools_status_raw(["ffprobe", "ffmpeg"])


def probe_encoder(key: str) -> bool:
    """Return True if *key* exists in ffmpeg and initializes a real 64x64 encode."""
    spec = ENCODERS.get(key)
    ff = _tool("ffmpeg")
    if not spec or not ff:
        return False
    cache_key = (ff, key)
    if cache_key in _ENCODER_PROBE_CACHE:
        return _ENCODER_PROBE_CACHE[cache_key]
    cmd = [
        ff, "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30:d=0.08",
        *spec["flags"](24), "-pix_fmt", "yuv420p", "-f", "null", "-",
    ]
    try:
        r = _run(cmd, timeout=15)
        ok_init = r.returncode == 0
    except Exception:
        ok_init = False
    _ENCODER_PROBE_CACHE[cache_key] = ok_init
    return ok_init


def available_encoders(*, verify: bool = False) -> list[str]:
    """Return ENCODERS keys whose ffmpeg codec exists (and initializes when verify=True)."""
    ff = _tool("ffmpeg")
    if not ff:
        return []
    try:
        r = _run([ff, "-hide_banner", "-encoders"], timeout=15)
    except Exception:
        return []
    out = (r.stdout or "") + (r.stderr or "")
    listed = [k for k, spec in ENCODERS.items() if spec["codec"] in out]
    if not verify:
        return listed
    return [k for k in listed if probe_encoder(k)]


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
    audio_codec: str = ""
    audio_bitrate_bps: int = 0

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
           "-show_entries", "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,bit_rate:stream_disposition=attached_pic",
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

    streams = meta.get("streams", [])
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    # Skip embedded cover art (attached_pic) when choosing the primary video stream.
    vstream = next(
        (s for s in vstreams if not (s.get("disposition") or {}).get("attached_pic")),
        vstreams[0] if vstreams else None,
    )
    if not vstream:
        return _err("probe.novideo", f"no video stream in {p.name}")
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = meta.get("format", {})

    size = int(fmt.get("size") or p.stat().st_size)
    duration = float(fmt.get("duration") or 0.0)
    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    avg_fps = _parse_fps(vstream.get("avg_frame_rate") or "0/1")
    r_fps = _parse_fps(vstream.get("r_frame_rate") or "0/1")
    fps = avg_fps if 0 < avg_fps <= 240 else (r_fps if 0 < r_fps <= 240 else (30.0 if duration > 0 else 0.0))

    # Overall bitrate: prefer the container value, else derive from size/duration.
    bitrate = int(fmt.get("bit_rate") or 0)
    if bitrate <= 0 and duration > 0:
        bitrate = int(size * 8 / duration)

    bpp = 0.0
    if width and height and fps > 0 and bitrate > 0:
        bpp = bitrate / (width * height * fps)

    audio_codec = (astream.get("codec_name") or "").lower() if astream else ""
    audio_bitrate = int(astream.get("bit_rate") or 0) if astream else 0

    info = VideoInfo(
        path=str(p), size_bytes=size, codec=(vstream.get("codec_name") or "").lower(),
        width=width, height=height, fps=round(fps, 3), duration_s=round(duration, 2),
        bitrate_bps=bitrate, bits_per_pixel=round(bpp, 5),
        audio_codec=audio_codec, audio_bitrate_bps=audio_bitrate,
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
    crf: int = 20                        # quality target (lower = higher quality)
    encoder: str = "svt_av1"            # key from ENCODERS
    max_height: int | None = None       # optional downscale cap (e.g. 1080, 720); never upscales
    max_fps: float | None = None        # optional fps cap (e.g. 30.0); never upsamples
    audio_mode: str = "copy"            # "copy" | "shrink_bloated"


@dataclass
class Decision:
    action: str          # "compress" | "skip"
    reason: str
    est_saving: float    # 0..1 fraction, rough
    target_crf: int


def _is_bloated_audio(info: VideoInfo) -> bool:
    c = info.audio_codec
    if not c:
        return False
    if c.startswith(("pcm_", "adpcm_")) or c in BLOATED_AUDIO_CODECS:
        return True
    return info.audio_bitrate_bps > AUDIO_REENCODE_BITRATE_FLOOR


def assess(info: VideoInfo, policy: Policy) -> Decision:
    """Decide, from the probe alone, whether re-encoding earns its place."""
    will_downscale = bool(policy.max_height and info.height > policy.max_height)
    will_cap_fps = bool(policy.max_fps and info.fps > policy.max_fps + 0.5)
    will_shrink_audio = (policy.audio_mode == "shrink_bloated" and _is_bloated_audio(info))

    is_efficient_codec = info.codec in EFFICIENT_CODECS
    thresh = policy.bpp_efficient_hevc if is_efficient_codec else policy.bpp_efficient_h264

    transforms: list[str] = []
    if will_downscale:
        transforms.append(f"{info.height}p→{policy.max_height}p")
    if will_cap_fps:
        transforms.append(f"{info.fps:.0f}→{policy.max_fps:.0f}fps")
    if will_shrink_audio:
        transforms.append(f"audio {info.audio_codec or 'raw'}→compressed")

    if transforms:
        est = max(0.25, _estimate_saving(info, thresh, is_efficient_codec))
        return Decision("compress", f"compress + {', '.join(transforms)}", est, policy.crf)

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
             encoder: str = "svt_av1", max_height: int | None = None,
             max_fps: float | None = None, shrink_audio: bool = False,
             src_info: VideoInfo | None = None,
             timeout: int | None = None,
             cancelled: Callable[[], bool] | None = None) -> dict:
    """Encode src -> dst through the bundled LGPL FFmpeg build. Envelope out."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        return _err("file.missing", f"source not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")     # atomic: write .part, rename on success

    if encoder not in ENCODERS:
        return _err(
            "config.encoder",
            f"unsupported encoder {encoder!r}; choose one of {', '.join(ENCODERS)}",
        )
    ff = _tool("ffmpeg")
    if not ff:
        return _err("dep.missing", "ffmpeg not found on PATH")
    cmd = _ffmpeg_cmd(
        ff, src, tmp, crf, encoder,
        max_height=max_height, max_fps=max_fps,
        shrink_audio=shrink_audio, src_info=src_info,
    )
    tool_used = "ffmpeg"

    try:
        r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
        if (r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0) and "-map" in cmd and "0:s?" in cmd:
            # Unknown or incompatible subtitle streams (e.g. codec_name=unknown) can abort muxing; retry without subtitles.
            tmp.unlink(missing_ok=True)
            cmd_nosub = _ffmpeg_cmd(
                ff, src, tmp, crf, encoder,
                max_height=max_height, max_fps=max_fps,
                shrink_audio=shrink_audio, src_info=src_info, keep_subs=False,
            )
            r = _run_cancellable(cmd_nosub, timeout=timeout, cancelled=cancelled)
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


def _target_format(dst: Path) -> str:
    """Infer muxer even when dst is '<name>.mp4.part'."""
    suffixes = [s.lower() for s in dst.suffixes]
    return "mp4" if ".mp4" in suffixes else "matroska"


def _ffmpeg_cmd(ff: str, src: Path, dst: Path, crf: int, encoder: str, *,
                max_height: int | None = None, max_fps: float | None = None,
                shrink_audio: bool = False, src_info: VideoInfo | None = None,
                keep_subs: bool = True) -> list[str]:
    vcodec = ENCODERS[encoder]["flags"](crf)
    vf: list[str] = []
    if max_height and (src_info is None or src_info.height <= 0 or src_info.height > max_height):
        vf.append(f"scale=-2:min(ih\\,{int(max_height)})")
    elif src_info is None or (src_info.width % 2 != 0) or (src_info.height % 2 != 0):
        vf.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
    if max_fps and (src_info is None or src_info.fps <= 0 or src_info.fps > max_fps + 0.5):
        vf.append(f"fps={max_fps:g}")
    vf.append("format=yuv420p")
    vf_args = ["-vf", ",".join(vf)]

    fmt = _target_format(dst)
    mux_extra = ["-movflags", "+faststart"] if fmt == "mp4" else []
    need_audio_transcode = (
        (shrink_audio and (src_info is None or _is_bloated_audio(src_info)))
        or (fmt == "mp4" and src_info is not None and bool(src_info.audio_codec)
            and src_info.audio_codec not in _MP4_SAFE_AUDIO)
    )
    if need_audio_transcode:
        acodec = (["-c:a", "aac", "-b:a", "160k"] if fmt == "mp4"
                  else ["-c:a", "libopus", "-b:a", "128k"])
    else:
        acodec = ["-c:a", "copy"]
    # MP4 doesn't support arbitrary text/ASS/PGS subtitle stream copies cleanly;
    # keep subtitle copy for MKV. Uppercase V skips attached cover-art pictures.
    scodec = ["-c:s", "copy"] if (fmt == "matroska" and keep_subs) else ["-sn"]
    return [ff, "-y", "-i", str(src), *vf_args, *vcodec,
            *acodec, *scodec, "-map", "0:V:0", "-map", "0:a?",
            *(["-map", "0:s?"] if (fmt == "matroska" and keep_subs) else []),
            "-f", fmt, *mux_extra, str(dst)]


# ---------------------------------------------------------------------------
# 4. verify (VMAF) — quality as evidence before we delete anything
# ---------------------------------------------------------------------------

def measure_vmaf(reference: str | Path, distorted: str | Path,
                 fps: float | None = None, sample_seconds: float | None = None,
                 duration_s: float | None = None,
                 timeout: int | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict:
    """Mean VMAF of distorted vs reference (0-100). Degrades to None if libvmaf
    isn't available — caller must treat a missing score as "unproven".

    Uses scale2ref so downscaled encodes compare cleanly against the original,
    and seeks past the opening intro when sampling long videos.
    """
    ff = _tool("ffmpeg")
    if not ff:
        return _ok(None, degraded=True, details="ffmpeg absent — VMAF unavailable")
    sync = f"fps={fps}," if fps and 0 < fps <= 120 else ""
    lavfi = (f"[0:v]{sync}format=yuv420p,setpts=PTS-STARTPTS[d0];"
             f"[1:v]{sync}format=yuv420p,setpts=PTS-STARTPTS[r0];"
             f"[r0][d0]scale2ref=flags=bicubic[r][d];"
             f"[d][r]libvmaf=n_threads=6")
    seek: list[str] = []
    if sample_seconds and sample_seconds > 0 and duration_s and duration_s > sample_seconds * 1.5:
        seek = ["-ss", f"{round(duration_s * 0.25, 2)}"]
    limit = ["-t", str(sample_seconds)] if sample_seconds and sample_seconds > 0 else []
    for try_seek in (seek, []) if seek else ([],):
        cmd = [ff, *try_seek, "-i", str(distorted), *try_seek, "-i", str(reference),
               "-lavfi", lavfi, *limit, "-f", "null", "-"]
        try:
            r = _run_cancellable(cmd, timeout=timeout, cancelled=cancelled)
        except subprocess.TimeoutExpired:
            return _ok(None, degraded=True, details="VMAF timed out")
        text = (r.stderr or "") + (r.stdout or "")
        score = _parse_vmaf(text)
        if score is not None:
            return _ok(score, details=f"VMAF {score:.2f}")
    return _ok(None, degraded=True, details="libvmaf not built into this ffmpeg — quality unproven")


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
    out_root: Path | None = None      # output root; None = ./compressed beside each source (or beside src if in_place)
    input_root: Path | None = None    # mirror mode recreates paths relative to this root
    mirror: bool = False              # rebuild the input folder structure under out_root
    policy: Policy = field(default_factory=Policy)
    vmaf_floor: float = 92.0          # min VMAF to accept an encode (visually-lossless)
    vmaf_sample_seconds: float | None = 60.0  # seconds to sample for VMAF verification (None = full)
    delete_original: bool = False     # only true after user opts in; uses recycle bin when possible
    skip_existing: bool = True        # resumable batches: don't redo a file whose output exists
    dry_run: bool = True              # default safe: report, don't write/delete
    container: str = "mkv"            # output container: "mkv" (default) or "mp4" (device-friendly)
    work_dir: Path | None = None      # optional fast SSD staging folder for encode + VMAF
    in_place: bool = False            # when out_root is None, place compressed file beside src

def plan_output(src: Path, opts: ProcessOptions) -> Path:
    """Where this source's compressed file goes. Mirror mode preserves the
    input subtree under out_root; else flat under out_root; else beside src
    (when in_place=True) or ./compressed. Extension follows opts.container.
    """
    ext = ".mp4" if opts.container == "mp4" else ".mkv"
    src = Path(src)
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / (src.stem + ext)
            except ValueError:
                pass  # source not under input_root — fall back to flat
        return root / (src.stem + ext)
    if opts.in_place:
        return src.with_suffix(ext)
    return src.parent / "compressed" / (src.stem + ext)


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
    estimated: bool = False           # True when saved_pct is the unverified
                                      # assess() heuristic, not a measured result

    def to_dict(self) -> dict:
        return asdict(self)


def _has_verified_proof(dst: Path, vmaf_floor: float) -> bool:
    """True when *dst* has a matching <dst>.json proof with saved_quality_verified=True."""
    sidecar = dst.with_suffix(dst.suffix + ".json")
    if not dst.is_file() or not sidecar.is_file():
        return False
    try:
        proof = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(proof, dict) or proof.get("tool") != "video_compressor":
        return False
    vmaf_val = proof.get("vmaf")
    if not isinstance(vmaf_val, (int, float)) or isinstance(vmaf_val, bool):
        return False
    return (
        bool(proof.get("saved_quality_verified"))
        and float(vmaf_val) >= vmaf_floor
        and int(proof.get("output_bytes") or 0) == dst.stat().st_size
    )


def process(path: str | Path, opts: ProcessOptions,
            cancelled: Callable[[], bool] | None = None) -> Result:
    """probe -> assess -> (compress -> verify -> [delete]) for one file."""
    src = Path(path)
    pr = probe(src, cancelled=cancelled)
    if pr["error"]:
        return Result(str(src), "failed", pr["details"], detail=pr["error_type"])
    info: VideoInfo = pr["data"]

    dst = plan_output(src, opts)
    same_target = dst.resolve(strict=False) == src.resolve(strict=False)

    # Resumable: if an in-place same-ext output already has a verified proof, skip before assessing.
    if same_target and opts.skip_existing and _has_verified_proof(dst, opts.vmaf_floor):
        return Result(str(src), "skipped", "already compressed (verified proof exists)",
                      before_mb=round(info.megabytes, 1), after_mb=round(info.megabytes, 1),
                      out_path=str(dst), detail="resumable")

    decision = assess(info, opts.policy)
    if decision.action == "skip":
        return Result(str(src), "skipped", decision.reason, before_mb=round(info.megabytes, 1))

    if opts.dry_run:
        # saved_pct here is the assess() heuristic ONLY — the real encode may
        # come out larger (see "no-gain" skip) or smaller. Mark it estimated so
        # any UI/log never presents it as a promised saving. (Issue #1)
        return Result(str(src), "dry-run",
                      f"would compress (est. saving, unverified): {decision.reason}",
                      before_mb=round(info.megabytes, 1),
                      saved_pct=round(decision.est_saving * 100, 0),
                      out_path=str(dst), estimated=True,
                      detail="estimated — real encode is measured per file and may differ")

    if same_target and not opts.delete_original:
        return Result(str(src), "failed",
                      "in-place same-extension replacement requires delete_original=True",
                      before_mb=round(info.megabytes, 1), out_path=str(dst),
                      detail="config.conflict")

    # Resumable: a finished output means this file is already done.
    if not same_target and opts.skip_existing and dst.is_file() and dst.stat().st_size > 0:
        existing = probe(dst, cancelled=cancelled)
        if existing["error"]:
            return Result(str(src), "failed",
                          f"existing output is invalid: {existing['details']}",
                          before_mb=round(info.megabytes, 1), out_path=str(dst),
                          detail="output.invalid")
        existing_info: VideoInfo = existing["data"]
        duration_tolerance = max(0.5, 2.0 / info.fps) if info.fps > 0 else 0.5
        expected_w = info.width - (info.width % 2)
        expected_h = info.height - (info.height % 2)
        geom_ok = (
            (existing_info.height <= opts.policy.max_height and existing_info.width > 0)
            if opts.policy.max_height and info.height > opts.policy.max_height
            else (existing_info.width, existing_info.height) == (expected_w, expected_h)
        )
        if not geom_ok or abs(existing_info.duration_s - info.duration_s) > duration_tolerance:
            return Result(str(src), "failed",
                          "existing output does not match source geometry/duration",
                          before_mb=round(info.megabytes, 1), out_path=str(dst),
                          detail="output.mismatch")
        return Result(str(src), "skipped", "already compressed (output exists)",
                      before_mb=round(info.megabytes, 1), after_mb=round(dst.stat().st_size / 1e6, 1),
                      out_path=str(dst), detail="resumable")

    # The final path must never become visible before quality validation. When
    # work_dir is set (e.g. fast NVMe SSD), encode + VMAF run there so an HDD
    # source does not suffer simultaneous read/write head thrashing.
    if opts.work_dir:
        work_root = Path(opts.work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
        tag = hashlib.sha1(str(src).encode("utf-8", errors="replace")).hexdigest()[:10]
        candidate = work_root / f"{dst.stem}.{tag}.verify{dst.suffix}"
    else:
        candidate = dst.with_name(f"{dst.stem}.verify{dst.suffix}")
    candidate.unlink(missing_ok=True)
    enc = compress(
        src, candidate, crf=decision.target_crf, encoder=opts.policy.encoder,
        max_height=opts.policy.max_height, max_fps=opts.policy.max_fps,
        shrink_audio=(opts.policy.audio_mode == "shrink_bloated"),
        src_info=info, cancelled=cancelled,
    )
    if enc["error"]:
        return Result(str(src), "failed", enc["details"], before_mb=round(info.megabytes, 1),
                      detail=enc["error_type"])

    after = candidate.stat().st_size
    saved = 1.0 - (after / info.size_bytes) if info.size_bytes else 0.0

    # Quality gate — measure before trusting (sync to target/source fps).
    eval_fps = min(info.fps, opts.policy.max_fps) if (opts.policy.max_fps and info.fps > opts.policy.max_fps) else info.fps
    try:
        vm = measure_vmaf(src, candidate, fps=eval_fps,
                          sample_seconds=opts.vmaf_sample_seconds,
                          duration_s=info.duration_s, cancelled=cancelled)
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
    if same_target and (vmaf is None or vmaf < opts.vmaf_floor):
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped", "VMAF unproven — kept original rather than replacing in-place",
                      before_mb=round(info.megabytes, 1), after_mb=round(after / 1e6, 1),
                      vmaf=vmaf, detail="quality-unproven")

    if same_target:
        try:
            src_sha = sha256_file(src, chunk_size=4 * 1024 * 1024, cancelled=cancelled)
        except CommandCancelled:
            candidate.unlink(missing_ok=True)
            raise
        except Exception:
            src_sha = None
        removed = _safe_remove(src)
        if not removed:
            candidate.unlink(missing_ok=True)
            return Result(str(src), "failed", "could not remove original before in-place replacement",
                          before_mb=round(info.megabytes, 1), detail="delete.failed")
        _commit_candidate(candidate, dst, cancelled=cancelled)
        _write_proof(src, dst, info, after, vmaf, decision, opts,
                     precomputed_sha=src_sha, original_removed=True)
    else:
        _commit_candidate(candidate, dst, cancelled=cancelled)
        removed = False
        if opts.delete_original and vmaf is not None and vmaf >= opts.vmaf_floor:
            removed = _safe_remove(src)
        # Proof sidecar: the user's "encode -> compare -> delete OG" workflow needs a
        # durable, re-auditable record next to each output WITHOUT re-running VMAF.
        # We write <output>.json with the measured quality + source fingerprint so a
        # later compare step is instant and provable. (Improvement #3)
        _write_proof(src, dst, info, after, vmaf, decision, opts,
                     original_removed=removed)

    return Result(
        str(src), "compressed", decision.reason,
        before_mb=round(info.megabytes, 1), after_mb=round(after / 1e6, 1),
        saved_pct=round(saved * 100, 1), vmaf=vmaf, out_path=str(dst),
        original_removed=removed,
        detail=("original removed" if removed else "original kept") +
               ("" if vmaf is not None else " (VMAF unproven)"),
    )




def _write_proof(src: Path, dst: Path, info: VideoInfo, after: int,
                 vmaf: float | None, decision: Decision, opts: ProcessOptions,
                 *, precomputed_sha: str | None = None,
                 original_removed: bool = False) -> None:
    """Write a durable proof sidecar next to the output: <dst>.json.

    Contains the measured VMAF, a SHA-256 of the ORIGINAL (so a later compare can
    prove the output corresponds to a specific source without re-encoding), the
    before/after sizes, and the exact encode settings. A downstream compare/delete
    step reads this instead of re-running VMAF on every file. Best-effort: if the
    source hash can't be read we still write the rest and flag src_sha256=None.
    """
    src_p = Path(src)
    if precomputed_sha is not None:
        src_sha = precomputed_sha
    else:
        try:
            src_sha = sha256_file(src_p, chunk_size=4 * 1024 * 1024)
        except Exception:
            src_sha = None
    proof = {
        "tool": "video_compressor",
        "version": "compress.v4",
        "source": str(src_p),
        "source_sha256": src_sha,
        "output": str(dst),
        "output_bytes": dst.stat().st_size,
        "before_bytes": info.size_bytes,
        "after_bytes": int(after),
        "saved_pct": round(100.0 * (1.0 - after / info.size_bytes), 1) if info.size_bytes else 0.0,
        "vmaf": round(vmaf, 3) if vmaf is not None else None,
        "vmaf_floor": opts.vmaf_floor,
        "encoder": opts.policy.encoder,
        "crf": decision.target_crf,
        "max_height": opts.policy.max_height,
        "max_fps": opts.policy.max_fps,
        "audio_mode": opts.policy.audio_mode,
        "container": opts.container,
        "assess_reason": decision.reason,
        "saved_quality_verified": (vmaf is not None and vmaf >= opts.vmaf_floor),
        "original_removed": bool(original_removed),
    }
    sidecar = dst.with_suffix(dst.suffix + ".json")
    tmp_sidecar = sidecar.with_name(sidecar.name + ".part")
    try:
        tmp_sidecar.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        tmp_sidecar.replace(sidecar)
    except OSError:
        tmp_sidecar.unlink(missing_ok=True)  # sidecar is a nicety; never fail the encode because of it


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


@dataclass
class ProofVerification:
    proof_path: str
    src: str
    out_path: str
    status: str                  # verified | dry_run_ok | removed | already_removed | rejected
    reason: str
    before_bytes: int = 0
    after_bytes: int = 0
    saved_bytes: int = 0
    vmaf: float | None = None
    original_removed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def verify_proof(
    proof_path: str | Path,
    *,
    vmaf_floor: float = 92.0,
    min_saved_bytes: int = 1,
    verify_source_sha: bool = False,
    check_duration: bool = True,
    cancelled: Callable[[], bool] | None = None,
) -> ProofVerification:
    """Independently verify a <output>.json proof sidecar before touching its original."""
    pp = Path(proof_path)
    try:
        proof = json.loads(pp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        return ProofVerification(str(pp), "", "", "rejected", f"unreadable proof: {ex}")
    if not isinstance(proof, dict) or proof.get("tool") != "video_compressor":
        return ProofVerification(str(pp), "", "", "rejected", "not a video_compressor proof")

    src = Path(str(proof.get("source") or ""))
    out = Path(str(proof.get("output") or pp.with_suffix("")))
    vmaf_val = proof.get("vmaf")
    before_bytes = int(proof.get("before_bytes") or 0)
    after_bytes = int(proof.get("after_bytes") or 0)

    if not out.is_file():
        return ProofVerification(str(pp), str(src), str(out), "rejected", "compressed output missing",
                                 before_bytes=before_bytes, after_bytes=after_bytes, vmaf=vmaf_val)
    actual_out = out.stat().st_size
    if actual_out <= 0:
        return ProofVerification(str(pp), str(src), str(out), "rejected", "compressed output is empty",
                                 before_bytes=before_bytes, after_bytes=actual_out, vmaf=vmaf_val)
    expected_out = int(proof.get("output_bytes") or actual_out)
    if actual_out != expected_out:
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 f"output size mismatch ({actual_out} != {expected_out})",
                                 before_bytes=before_bytes, after_bytes=actual_out, vmaf=vmaf_val)
    if vmaf_val is None or not isinstance(vmaf_val, (int, float)) or isinstance(vmaf_val, bool):
        return ProofVerification(str(pp), str(src), str(out), "rejected", "VMAF score missing",
                                 before_bytes=before_bytes, after_bytes=actual_out)
    if float(vmaf_val) < vmaf_floor or not proof.get("saved_quality_verified"):
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 f"VMAF {float(vmaf_val):.2f} below floor {vmaf_floor:.1f}",
                                 before_bytes=before_bytes, after_bytes=actual_out, vmaf=float(vmaf_val))

    out_probe = probe(out, cancelled=cancelled)
    if out_probe["error"]:
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 f"output ffprobe failed: {out_probe['details']}",
                                 before_bytes=before_bytes, after_bytes=actual_out, vmaf=float(vmaf_val))
    out_info: VideoInfo = out_probe["data"]
    if out_info.width <= 0 or out_info.height <= 0 or out_info.duration_s <= 0:
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 "output video has invalid geometry or duration",
                                 before_bytes=before_bytes, after_bytes=actual_out, vmaf=float(vmaf_val))

    if proof.get("original_removed") or not src.is_file() or src.resolve(strict=False) == out.resolve(strict=False):
        return ProofVerification(str(pp), str(src), str(out), "already_removed",
                                 "output verified; original already removed",
                                 before_bytes=before_bytes, after_bytes=actual_out,
                                 saved_bytes=max(0, before_bytes - actual_out), vmaf=float(vmaf_val))

    actual_src = src.stat().st_size
    if before_bytes and actual_src != before_bytes:
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 f"original size changed ({actual_src} != {before_bytes})",
                                 before_bytes=actual_src, after_bytes=actual_out, vmaf=float(vmaf_val))
    saved = actual_src - actual_out
    if saved < min_saved_bytes:
        return ProofVerification(str(pp), str(src), str(out), "rejected",
                                 f"output not sufficiently smaller (saved {saved} bytes < {min_saved_bytes})",
                                 before_bytes=actual_src, after_bytes=actual_out,
                                 saved_bytes=saved, vmaf=float(vmaf_val))

    if check_duration:
        src_probe = probe(src, cancelled=cancelled)
        if src_probe["error"]:
            return ProofVerification(str(pp), str(src), str(out), "rejected",
                                     f"source ffprobe failed: {src_probe['details']}",
                                     before_bytes=actual_src, after_bytes=actual_out,
                                     saved_bytes=saved, vmaf=float(vmaf_val))
        src_info: VideoInfo = src_probe["data"]
        tol = max(1.5, 0.02 * src_info.duration_s)
        if abs(out_info.duration_s - src_info.duration_s) > tol:
            return ProofVerification(str(pp), str(src), str(out), "rejected",
                                     f"duration mismatch ({out_info.duration_s:.1f}s vs {src_info.duration_s:.1f}s)",
                                     before_bytes=actual_src, after_bytes=actual_out,
                                     saved_bytes=saved, vmaf=float(vmaf_val))

    if verify_source_sha and proof.get("source_sha256"):
        actual_sha = sha256_file(src, chunk_size=4 * 1024 * 1024, cancelled=cancelled)
        if actual_sha != proof["source_sha256"]:
            return ProofVerification(str(pp), str(src), str(out), "rejected",
                                     "source SHA-256 mismatch",
                                     before_bytes=actual_src, after_bytes=actual_out,
                                     saved_bytes=saved, vmaf=float(vmaf_val))

    return ProofVerification(str(pp), str(src), str(out), "verified",
                             f"verified smaller (-{100.0 * saved / actual_src:.1f}%) & VMAF {float(vmaf_val):.2f}",
                             before_bytes=actual_src, after_bytes=actual_out,
                             saved_bytes=saved, vmaf=float(vmaf_val))


def cleanup_verified_originals(
    proof_root: str | Path,
    *,
    vmaf_floor: float = 92.0,
    min_saved_bytes: int = 1,
    verify_source_sha: bool = False,
    check_duration: bool = True,
    permanent_delete: bool = False,
    dry_run: bool = True,
    progress: Callable[[int, int, ProofVerification], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[ProofVerification]:
    """Audit <out>.json proofs sequentially and safely remove verified-bigger originals."""
    root = Path(proof_root)
    proofs = sorted(
        p for ext in (".mkv.json", ".mp4.json")
        for p in root.rglob(f"*{ext}")
        if p.is_file()
    )
    results: list[ProofVerification] = []
    total = len(proofs)
    for idx, pp in enumerate(proofs, 1):
        if cancelled is not None and cancelled():
            raise CommandCancelled(["cleanup_verified_originals", str(pp)])
        vr = verify_proof(
            pp,
            vmaf_floor=vmaf_floor,
            min_saved_bytes=min_saved_bytes,
            verify_source_sha=verify_source_sha,
            check_duration=check_duration,
            cancelled=cancelled,
        )
        if vr.status == "verified" and not dry_run:
            if _safe_remove(Path(vr.src), permanent=permanent_delete):
                vr.status = "removed"
                vr.original_removed = True
                try:
                    payload = json.loads(pp.read_text(encoding="utf-8"))
                    payload["original_removed"] = True
                    tmp_p = pp.with_name(pp.name + ".part")
                    tmp_p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    tmp_p.replace(pp)
                except OSError:
                    pass
            else:
                vr.status = "rejected"
                vr.reason = "failed to remove original file"
        elif vr.status == "verified" and dry_run:
            vr.status = "dry_run_ok"
        results.append(vr)
        if progress is not None:
            progress(idx, total, vr)
    return results
