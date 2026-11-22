"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Generates a deliberately bloated (near-lossless) sample with ffmpeg, then runs
the full pipeline and asserts it (a) decided to compress, (b) produced a smaller
file, and (c) actually measured a VMAF score. The VMAF *floor* is set to 0 so the
test is deterministic on synthetic input — it proves the compress + measure chain
works, not a quality threshold (real-content quality was verified separately).

Run standalone:  python -m tools.video_compressor.test_smoke
Skips (does not fail) if ffmpeg/ffprobe aren't available on this machine.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.video_compressor import engine as e  # noqa: E402


def _make_bloated(dst: Path) -> bool:
    """A near-lossless native FFV1 clip + PCM audio — high bitrate, so assess() says compress."""
    ff = e._tool("ffmpeg")
    if not ff:
        return False
    cmd = [ff, "-y",
           "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=30:duration=3",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
           "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
           "-c:a", "pcm_s16le", str(dst)]
    return e._run(cmd, timeout=120).returncode == 0


def main() -> int:
    if not (e._tool("ffmpeg") and e._tool("ffprobe")):
        print("SKIP: ffmpeg/ffprobe not found — cannot run smoke test here.")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sample = tmp / "bloated.mkv"
        if not _make_bloated(sample):
            print("SKIP: could not synthesize a sample with this ffmpeg build.")
            return 0

        # assess must recognise the bloated source as worth compressing.
        pr = e.probe(sample)
        assert not pr["error"], pr["details"]
        decision = e.assess(pr["data"], e.Policy())
        assert decision.action == "compress", f"expected compress, got {decision.action}: {decision.reason}"

        try:
            e.probe(sample, cancelled=lambda: True)
            raise AssertionError("cancelled ffprobe unexpectedly completed")
        except e.CommandCancelled:
            pass

        # full pipeline; floor 0 keeps the quality gate out of the way (deterministic).
        opts = e.ProcessOptions(out_root=tmp / "out", policy=e.Policy(crf=28),
                                vmaf_floor=0.0, dry_run=False, delete_original=False)
        res = e.process(sample, opts)
        assert res.action == "compressed", f"expected compressed, got {res.action}: {res.reason}"
        assert Path(res.out_path).is_file() and Path(res.out_path).stat().st_size > 0, "no output written"
        assert res.after_mb < res.before_mb, f"not smaller: {res.before_mb} -> {res.after_mb} MB"
        assert res.vmaf is not None, "VMAF was not measured (libvmaf missing from this ffmpeg?)"
        assert e.validate_result(res), "stored output validation failed"
        assert not list((tmp / "out").glob("*.verify.*")), "uncommitted candidate remained"
        out_probe = e.probe(res.out_path)
        assert not out_probe["error"] and out_probe["data"].codec == "av1", "output is not AV1"

        # downscale + bloated PCM audio -> Opus + scale2ref VMAF + resumable skip
        opts_down = e.ProcessOptions(
            out_root=tmp / "out_down",
            policy=e.Policy(crf=28, max_height=240, max_fps=24.0, audio_mode="shrink_bloated"),
            vmaf_floor=0.0, dry_run=False, delete_original=False,
        )
        res_down = e.process(sample, opts_down)
        assert res_down.action == "compressed", f"downscale failed: {res_down.reason}"
        assert res_down.vmaf is not None, "downscale VMAF was not measured"
        down_probe = e.probe(res_down.out_path)
        assert not down_probe["error"], down_probe["details"]
        assert down_probe["data"].height == 240, f"expected 240p, got {down_probe['data'].height}"
        assert down_probe["data"].audio_codec == "opus", f"expected opus audio, got {down_probe['data'].audio_codec}"
        res_resume = e.process(sample, opts_down)
        assert res_resume.action == "skipped" and res_resume.detail == "resumable", f"expected resumable skip, got {res_resume}"

        bad_enc = e.compress(sample, tmp / "bad.mkv", encoder="not_an_encoder")
        assert bad_enc["error"] and bad_enc["error_type"] == "config.encoder"

        # Odd dimensions + PCM audio into MP4 must auto-even dimensions and auto-AAC audio
        odd_src = tmp / "odd_pcm.mkv"
        ff = e._tool("ffmpeg")
        if ff and e._run([
            ff, "-y",
            "-f", "lavfi", "-i", "testsrc2=size=321x241:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "ffv1", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(odd_src),
        ], timeout=120).returncode == 0:
            mp4_out = tmp / "out_mp4"
            res_mp4 = e.process(
                odd_src,
                e.ProcessOptions(
                    out_root=mp4_out,
                    policy=e.Policy(crf=28, audio_mode="copy"),
                    vmaf_floor=0.0,
                    dry_run=False,
                    delete_original=False,
                    container="mp4",
                ),
            )
            assert res_mp4.action == "compressed", f"odd+mp4 failed: {res_mp4.reason}"
            mp4_probe = e.probe(res_mp4.out_path)
            assert not mp4_probe["error"] and mp4_probe["data"].audio_codec == "aac"
            assert mp4_probe["data"].width % 2 == 0 and mp4_probe["data"].height % 2 == 0

        # verify_proof + cleanup_verified_originals: dry-run keeps source; execute removes bigger source
        verify_sample = tmp / "to_clean.mkv"
        verify_sample.write_bytes(sample.read_bytes())
        clean_out = tmp / "clean_out"
        res_clean = e.process(
            verify_sample,
            e.ProcessOptions(
                out_root=clean_out,
                policy=e.Policy(crf=28),
                vmaf_floor=0.0,
                dry_run=False,
                delete_original=False,
            ),
        )
        assert res_clean.action == "compressed" and verify_sample.is_file()
        dry_checks = e.cleanup_verified_originals(clean_out, vmaf_floor=0.0, dry_run=True)
        assert len(dry_checks) == 1 and dry_checks[0].status == "dry_run_ok"
        assert verify_sample.is_file(), "dry_run cleanup must keep original"
        real_checks = e.cleanup_verified_originals(
            clean_out, vmaf_floor=0.0, verify_source_sha=True, permanent_delete=True, dry_run=False
        )
        assert len(real_checks) == 1 and real_checks[0].status == "removed"
        assert not verify_sample.exists(), "verified bigger original should be removed"

        # work_dir staging + in_place replacement + resumable proof skip
        work_dir = tmp / "ssd_work"
        in_place_dir = tmp / "in_place_src"
        in_place_dir.mkdir()
        ip_sample = in_place_dir / "clip.mkv"
        ip_sample.write_bytes(sample.read_bytes())
        orig_sz = ip_sample.stat().st_size
        res_ip = e.process(
            ip_sample,
            e.ProcessOptions(
                out_root=None,
                in_place=True,
                work_dir=work_dir,
                policy=e.Policy(crf=28),
                vmaf_floor=0.0,
                dry_run=False,
                delete_original=True,
            ),
        )
        assert res_ip.action == "compressed" and res_ip.original_removed, res_ip
        assert ip_sample.is_file() and ip_sample.stat().st_size < orig_sz
        assert not list(work_dir.glob("*.verify.*")), "staged work_dir candidate left behind"
        res_ip_resume = e.process(
            ip_sample,
            e.ProcessOptions(
                out_root=None,
                in_place=True,
                work_dir=work_dir,
                policy=e.Policy(crf=28),
                vmaf_floor=0.0,
                dry_run=False,
                delete_original=True,
            ),
        )
        assert res_ip_resume.action == "skipped" and res_ip_resume.detail == "resumable", res_ip_resume
        best_enc = e.pick_best_encoder("av1")
        assert best_enc in e.ENCODERS, best_enc

    print(f"PASS: video_compressor — {res.before_mb:.1f}->{res.after_mb:.1f} MB "
          f"(-{res.saved_pct:.0f}%), VMAF {res.vmaf:.1f}; downscale+opus VMAF {res_down.vmaf:.1f}; proof cleanup ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
