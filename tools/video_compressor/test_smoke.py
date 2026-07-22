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
    """A near-lossless H.264 clip — high bitrate, so assess() says 'compress'."""
    ff = e._tool("ffmpeg")
    if not ff:
        return False
    cmd = [ff, "-y", "-f", "lavfi", "-i", "testsrc=size=640x480:rate=30:duration=3",
           "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
           "-pix_fmt", "yuv420p", str(dst)]
    return e._run(cmd, timeout=120).returncode == 0


def main() -> int:
    if not (e._tool("ffmpeg") and e._tool("ffprobe")):
        print("SKIP: ffmpeg/ffprobe not found — cannot run smoke test here.")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sample = tmp / "bloated.mp4"
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

    print(f"PASS: video_compressor — {res.before_mb:.1f}->{res.after_mb:.1f} MB "
          f"(-{res.saved_pct:.0f}%), VMAF {res.vmaf:.1f}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
