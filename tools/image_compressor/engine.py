"""Size-gated, quality-verified image compression engine — pure logic, no UI.

Supports lossless (exact-pixel verified) and visually-lossless (PSNR-gated)
compression with atomic staging, optional SSD work_dir for HDD sources,
<output>.img.json proof sidecars, and safe Recycle-Bin cleanup.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from toolbox.engine_common import (
    CommandCancelled, IMAGE_EXTS, commit_candidate as _commit_candidate,
    safe_remove as _safe_remove, sha256_file,
)

MODES = ("lossless_webp", "lossless_same", "visually_lossless")


@dataclass
class CompressOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    mode: str = "lossless_webp"          # lossless_webp | lossless_same | visually_lossless
    quality: int = 90                    # WebP/JPEG quality for visually_lossless
    psnr_floor: float = 42.0             # min PSNR (dB) for lossy modes
    min_saving: float = 0.05             # skip unless at least 5% smaller
    min_bytes: int = 8192                # skip tiny icons (<8 KB) where headers dominate
    delete_original: bool = False
    skip_existing: bool = True
    dry_run: bool = True
    work_dir: Path | None = None
    in_place: bool = False


@dataclass
class Result:
    src: str
    action: str                          # compressed | skipped | failed | dry-run
    reason: str
    before_kb: float = 0.0
    after_kb: float = 0.0
    saved_pct: float = 0.0
    psnr: float | None = None
    lossless_exact: bool = False
    out_path: str | None = None
    original_removed: bool = False
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProofVerification:
    proof_path: str
    src: str
    out_path: str
    status: str                          # verified | dry_run_ok | removed | already_removed | rejected
    reason: str
    before_bytes: int = 0
    after_bytes: int = 0
    saved_bytes: int = 0
    psnr: float | None = None
    original_removed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _target_ext(src: Path, mode: str) -> str:
    ext = src.suffix.lower()
    if mode == "lossless_webp":
        return ".webp"
    if mode == "visually_lossless":
        return ".jpg" if ext in (".jpg", ".jpeg") else ".webp"
    # lossless_same: keep PNG/WebP/TIFF; convert uncompressed BMP to PNG
    if ext == ".bmp":
        return ".png"
    return ext


def plan_output(src: Path, opts: CompressOptions) -> Path:
    src = Path(src)
    ext = _target_ext(src, opts.mode)
    name = src.stem + ext
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = src.relative_to(opts.input_root)
                return root / rel.parent / name
            except ValueError:
                pass
        return root / name
    if opts.in_place:
        return src.with_suffix(ext)
    return src.parent / "compressed" / name


def _measure_quality(ref_im, out_path: Path) -> tuple[bool, float | None, tuple[int, int]]:
    """Return (exact_pixel_match, psnr_db_or_none, out_size) by reopening *out_path*."""
    from PIL import Image, ImageChops
    import numpy as np

    with Image.open(out_path) as got:
        got.load()
        out_size = got.size
        if out_size != ref_im.size:
            return False, 0.0, out_size
        mode = "RGBA" if ("A" in ref_im.mode or "A" in got.mode) else "RGB"
        a = ref_im.convert(mode)
        b = got.convert(mode)
        diff_bbox = ImageChops.difference(a, b).getbbox()
        if diff_bbox is None:
            return True, None, out_size
        arr_a = np.asarray(a, dtype=np.float64)
        arr_b = np.asarray(b, dtype=np.float64)
        mse = float(np.mean((arr_a - arr_b) ** 2))
        if mse <= 0.0:
            return True, None, out_size
        psnr = 10.0 * math.log10((255.0 * 255.0) / mse)
        return False, round(psnr, 2), out_size


def _has_verified_proof(dst: Path) -> bool:
    sidecar = dst.with_suffix(dst.suffix + ".img.json")
    if not dst.is_file() or not sidecar.is_file():
        return False
    try:
        proof = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(proof, dict) or proof.get("tool") != "image_compressor":
        return False
    return (
        bool(proof.get("saved_quality_verified"))
        and int(proof.get("output_bytes") or 0) == dst.stat().st_size
    )


def _write_proof(
    src: Path,
    dst: Path,
    before_bytes: int,
    after_bytes: int,
    exact: bool,
    psnr: float | None,
    opts: CompressOptions,
    *,
    precomputed_sha: str | None = None,
    original_removed: bool = False,
) -> None:
    if precomputed_sha is not None:
        src_sha = precomputed_sha
    else:
        try:
            src_sha = sha256_file(src, chunk_size=4 * 1024 * 1024)
        except Exception:
            src_sha = None
    proof = {
        "tool": "image_compressor",
        "version": "img_compress.v1",
        "source": str(src),
        "source_sha256": src_sha,
        "output": str(dst),
        "output_bytes": dst.stat().st_size,
        "before_bytes": int(before_bytes),
        "after_bytes": int(after_bytes),
        "saved_pct": round(100.0 * (1.0 - after_bytes / before_bytes), 1) if before_bytes else 0.0,
        "lossless_exact": bool(exact),
        "psnr": psnr,
        "psnr_floor": opts.psnr_floor,
        "mode": opts.mode,
        "quality": opts.quality,
        "saved_quality_verified": bool(exact or (psnr is not None and psnr >= opts.psnr_floor)),
        "original_removed": bool(original_removed),
    }
    sidecar = dst.with_suffix(dst.suffix + ".img.json")
    tmp = sidecar.with_name(sidecar.name + ".part")
    try:
        tmp.write_text(json.dumps(proof, indent=2), encoding="utf-8")
        tmp.replace(sidecar)
    except OSError:
        tmp.unlink(missing_ok=True)


def process(
    path: str | Path,
    opts: CompressOptions,
    cancelled: Callable[[], bool] | None = None,
) -> Result:
    """Assess -> encode -> pixel/PSNR verify -> size-gate -> commit -> [delete]."""
    if cancelled is not None and cancelled():
        raise CommandCancelled(["image_compressor", str(path)])
    src = Path(path)
    if not src.is_file():
        return Result(str(src), "failed", f"not a file: {src}", detail="file.missing")
    if src.suffix.lower() not in IMAGE_EXTS:
        return Result(str(src), "skipped", f"unsupported extension: {src.suffix}", detail="unsupported")

    before_bytes = src.stat().st_size
    if before_bytes < opts.min_bytes:
        return Result(str(src), "skipped", f"smaller than {opts.min_bytes} B threshold",
                      before_kb=round(before_bytes / 1024, 1), detail="too-small")

    if opts.mode not in MODES:
        return Result(str(src), "failed", f"unsupported mode {opts.mode!r}", detail="bad.mode")
    if opts.mode == "lossless_same" and src.suffix.lower() in (".jpg", ".jpeg"):
        return Result(str(src), "skipped", "JPEG is already lossy; lossless_same skips JPEG",
                      before_kb=round(before_bytes / 1024, 1), detail="already-lossy")

    dst = plan_output(src, opts)
    same_target = dst.resolve(strict=False) == src.resolve(strict=False)

    if same_target and opts.skip_existing and _has_verified_proof(dst):
        return Result(str(src), "skipped", "already compressed (verified proof exists)",
                      before_kb=round(before_bytes / 1024, 1), after_kb=round(before_bytes / 1024, 1),
                      out_path=str(dst), detail="resumable")

    if opts.dry_run:
        return Result(str(src), "dry-run", f"would compress ({opts.mode}) -> {dst.name}",
                      before_kb=round(before_bytes / 1024, 1), out_path=str(dst))

    if same_target and not opts.delete_original:
        return Result(str(src), "failed",
                      "in-place same-extension replacement requires delete_original=True",
                      before_kb=round(before_bytes / 1024, 1), out_path=str(dst), detail="config.conflict")

    if not same_target and opts.skip_existing and dst.is_file() and dst.stat().st_size > 0:
        return Result(str(src), "skipped", "already compressed (output exists)",
                      before_kb=round(before_bytes / 1024, 1),
                      after_kb=round(dst.stat().st_size / 1024, 1),
                      out_path=str(dst), detail="resumable")

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return Result(str(src), "failed", "Pillow not installed — pip install pillow", detail="dep.missing")

    if opts.work_dir:
        work_root = Path(opts.work_dir)
        work_root.mkdir(parents=True, exist_ok=True)
        tag = hashlib.sha1(str(src).encode("utf-8", errors="replace")).hexdigest()[:10]
        candidate = work_root / f"{dst.stem}.{tag}.verify.part{dst.suffix}"
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        candidate = dst.with_name(f"{dst.stem}.verify.part{dst.suffix}")
    candidate.unlink(missing_ok=True)

    try:
        with Image.open(src) as opened:
            if int(getattr(opened, "n_frames", 1)) > 1:
                return Result(str(src), "skipped", "multi-frame animated image skipped",
                              before_kb=round(before_bytes / 1024, 1), detail="animated")
            icc = opened.info.get("icc_profile")
            ref = ImageOps.exif_transpose(opened) or opened
            if ref.mode in ("P", "LA", "PA"):
                ref = ref.convert("RGBA")
            elif ref.mode == "1":
                ref = ref.convert("L")
            elif ref.mode not in ("RGB", "RGBA", "L"):
                ref = ref.convert("RGBA" if "A" in ref.mode else "RGB")

            tgt_ext = dst.suffix.lower()
            save_kw: dict = {}
            if icc and tgt_ext in (".png", ".webp", ".jpg", ".jpeg", ".tif", ".tiff"):
                save_kw["icc_profile"] = icc

            out_img = ref
            if tgt_ext == ".webp":
                if opts.mode in ("lossless_webp", "lossless_same"):
                    save_kw.update({"lossless": True, "quality": 100, "method": 6})
                else:
                    save_kw.update({"lossless": False, "quality": max(1, min(100, int(opts.quality))), "method": 6})
            elif tgt_ext == ".png":
                save_kw.update({"optimize": True, "compress_level": 9})
            elif tgt_ext in (".jpg", ".jpeg"):
                if out_img.mode == "RGBA":
                    bg = Image.new("RGB", out_img.size, (255, 255, 255))
                    bg.paste(out_img, mask=out_img.split()[-1])
                    out_img = bg
                elif out_img.mode != "RGB":
                    out_img = out_img.convert("RGB")
                save_kw.update({"quality": max(1, min(100, int(opts.quality))), "optimize": True})
            elif tgt_ext in (".tif", ".tiff"):
                save_kw.update({"compression": "tiff_deflate"})

            if cancelled is not None and cancelled():
                raise CommandCancelled(["image-save", str(src)])
            out_img.save(candidate, **save_kw)
            exact, psnr, _ = _measure_quality(ref, candidate)
    except CommandCancelled:
        candidate.unlink(missing_ok=True)
        raise
    except Exception as ex:
        candidate.unlink(missing_ok=True)
        return Result(str(src), "failed", f"image compression failed: {ex}",
                      before_kb=round(before_bytes / 1024, 1), detail="encode.failed")

    after_bytes = candidate.stat().st_size
    saved_frac = (1.0 - after_bytes / before_bytes) if before_bytes else 0.0
    if after_bytes >= before_bytes or saved_frac < opts.min_saving:
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped",
                      f"not sufficiently smaller ({saved_frac * 100:.1f}% < {opts.min_saving * 100:.0f}% floor) — kept original",
                      before_kb=round(before_bytes / 1024, 1),
                      after_kb=round(after_bytes / 1024, 1),
                      psnr=psnr, lossless_exact=exact, detail="no-gain")

    if opts.mode in ("lossless_webp", "lossless_same") and not exact:
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped", "lossless pixel verification failed — kept original",
                      before_kb=round(before_bytes / 1024, 1), psnr=psnr, detail="pixel-mismatch")

    if opts.mode == "visually_lossless" and not exact and (psnr is None or psnr < opts.psnr_floor):
        candidate.unlink(missing_ok=True)
        return Result(str(src), "skipped",
                      f"PSNR {psnr} dB < {opts.psnr_floor:.1f} dB floor — kept original",
                      before_kb=round(before_bytes / 1024, 1), psnr=psnr, detail="quality-floor")

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
                          before_kb=round(before_bytes / 1024, 1), detail="delete.failed")
        _commit_candidate(candidate, dst, cancelled=cancelled)
        _write_proof(src, dst, before_bytes, after_bytes, exact, psnr, opts,
                     precomputed_sha=src_sha, original_removed=True)
    else:
        _commit_candidate(candidate, dst, cancelled=cancelled)
        removed = False
        if opts.delete_original:
            removed = _safe_remove(src)
        _write_proof(src, dst, before_bytes, after_bytes, exact, psnr, opts,
                     original_removed=removed)

    return Result(
        str(src), "compressed",
        "lossless pixel-verified" if exact else f"PSNR {psnr:.1f} dB",
        before_kb=round(before_bytes / 1024, 1),
        after_kb=round(after_bytes / 1024, 1),
        saved_pct=round(saved_frac * 100, 1),
        psnr=psnr,
        lossless_exact=exact,
        out_path=str(dst),
        original_removed=removed,
        detail="original removed" if removed else "original kept",
    )


def validate_result(result: Result) -> bool:
    if result.action != "compressed" or not result.out_path:
        return result.action in {"skipped", "dry-run"}
    out = Path(result.out_path)
    if not out.is_file() or out.stat().st_size <= 0:
        return False
    try:
        from PIL import Image
        with Image.open(out) as im:
            im.verify()
        return True
    except Exception:
        return False


def verify_proof(
    proof_path: str | Path,
    *,
    psnr_floor: float = 42.0,
    min_saved_bytes: int = 1,
    cancelled: Callable[[], bool] | None = None,
) -> ProofVerification:
    if cancelled is not None and cancelled():
        raise CommandCancelled(["verify_img_proof", str(proof_path)])
    pp = Path(proof_path)
    try:
        proof = json.loads(pp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        return ProofVerification(str(pp), "", "", "rejected", f"unreadable proof: {ex}")
    if not isinstance(proof, dict) or proof.get("tool") != "image_compressor":
        return ProofVerification(str(pp), "", "", "rejected", "not an image_compressor proof")

    src = Path(str(proof.get("source") or ""))
    out = Path(str(proof.get("output") or ""))
    before_bytes = int(proof.get("before_bytes") or 0)
    after_bytes = int(proof.get("after_bytes") or 0)
    psnr_val = proof.get("psnr")
    exact = bool(proof.get("lossless_exact"))

    if not out.is_file() or out.stat().st_size <= 0:
        return ProofVerification(str(pp), str(src), str(out), "rejected", "compressed output missing/empty",
                                 before_bytes=before_bytes, after_bytes=after_bytes, psnr=psnr_val)
    actual_out = out.stat().st_size
    if int(proof.get("output_bytes") or actual_out) != actual_out:
        return ProofVerification(str(pp), str(src), str(out), "rejected", "output size mismatch",
                                 before_bytes=before_bytes, after_bytes=actual_out, psnr=psnr_val)
    if not proof.get("saved_quality_verified"):
        return ProofVerification(str(pp), str(src), str(out), "rejected", "quality not verified",
                                 before_bytes=before_bytes, after_bytes=actual_out, psnr=psnr_val)
    if not exact and (psnr_val is None or float(psnr_val) < psnr_floor):
        return ProofVerification(str(pp), str(src), str(out), "rejected", "PSNR below floor",
                                 before_bytes=before_bytes, after_bytes=actual_out, psnr=psnr_val)

    try:
        from PIL import Image
        with Image.open(out) as im:
            im.verify()
    except Exception as ex:
        return ProofVerification(str(pp), str(src), str(out), "rejected", f"output corrupt: {ex}",
                                 before_bytes=before_bytes, after_bytes=actual_out, psnr=psnr_val)

    if proof.get("original_removed") or not src.is_file() or src.resolve(strict=False) == out.resolve(strict=False):
        return ProofVerification(str(pp), str(src), str(out), "already_removed",
                                 "output verified; original already removed",
                                 before_bytes=before_bytes, after_bytes=actual_out,
                                 saved_bytes=max(0, before_bytes - actual_out), psnr=psnr_val)

    actual_src = src.stat().st_size
    if before_bytes and actual_src != before_bytes:
        return ProofVerification(str(pp), str(src), str(out), "rejected", "original size changed",
                                 before_bytes=actual_src, after_bytes=actual_out, psnr=psnr_val)
    saved = actual_src - actual_out
    if saved < min_saved_bytes:
        return ProofVerification(str(pp), str(src), str(out), "rejected", "output not smaller",
                                 before_bytes=actual_src, after_bytes=actual_out, saved_bytes=saved, psnr=psnr_val)
    return ProofVerification(str(pp), str(src), str(out), "verified",
                             "lossless exact & smaller" if exact else f"PSNR {psnr_val} dB & smaller",
                             before_bytes=actual_src, after_bytes=actual_out, saved_bytes=saved, psnr=psnr_val)


def cleanup_verified_originals(
    proof_root: str | Path,
    *,
    psnr_floor: float = 42.0,
    min_saved_bytes: int = 1,
    permanent_delete: bool = False,
    dry_run: bool = True,
    progress: Callable[[int, int, ProofVerification], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[ProofVerification]:
    root = Path(proof_root)
    proofs = sorted(p for p in root.rglob("*.img.json") if p.is_file())
    results: list[ProofVerification] = []
    total = len(proofs)
    for idx, pp in enumerate(proofs, 1):
        if cancelled is not None and cancelled():
            raise CommandCancelled(["cleanup_verified_originals", str(pp)])
        vr = verify_proof(pp, psnr_floor=psnr_floor, min_saved_bytes=min_saved_bytes, cancelled=cancelled)
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
                vr.reason = "failed to remove original"
        elif vr.status == "verified" and dry_run:
            vr.status = "dry_run_ok"
        results.append(vr)
        if progress is not None:
            progress(idx, total, vr)
    return results
