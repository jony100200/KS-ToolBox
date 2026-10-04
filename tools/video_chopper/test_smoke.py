"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Generates a real sample (content · black gap · content) with ffmpeg, then runs
the engine end-to-end and asserts it cut exactly the two content clips. Proves
the tool boots and produces valid output on a real file, not a mock.

Run standalone:  python -m tools.video_chopper.test_smoke
Skips (does not fail) if ffmpeg/ffprobe aren't available on this machine.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Allow standalone `python test_smoke.py` as well as `-m`.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.video_chopper import engine as e  # noqa: E402
from toolbox.engine_common import run_cancellable_cmd  # noqa: E402


def _make_sample(dst: Path) -> bool:
    """2s content · 1s black · 2s content, in one file. False if ffmpeg absent."""
    ff = e._tool("ffmpeg")
    if not ff:
        return False
    cmd = [ff, "-y",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
           "-f", "lavfi", "-i", "color=c=black:size=320x240:rate=15:duration=1",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
           "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
           "-map", "[v]", "-pix_fmt", "yuv420p", str(dst)]
    return run_cancellable_cmd(cmd, timeout=120).returncode == 0


def _make_continuous(dst: Path) -> bool:
    ff = e._tool("ffmpeg")
    if not ff:
        return False
    command = [
        ff, "-y", "-f", "lavfi", "-i",
        "color=c=red:size=320x240:rate=15:duration=1",
        "-pix_fmt", "yuv420p", str(dst),
    ]
    return run_cancellable_cmd(command, timeout=120).returncode == 0


def main() -> int:
    if not (e._tool("ffmpeg") and e._tool("ffprobe")):
        print("SKIP: ffmpeg/ffprobe not found — cannot run smoke test here.")
        return 0

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        sample = tmp / "sample.mp4"
        if not _make_sample(sample):
            print("SKIP: could not synthesize a sample with this ffmpeg build.")
            return 0

        # 1) pure planner sanity — two content segments around one black gap.
        det = e.detect_black(sample, min_black_s=0.3)
        assert not det["error"], det["details"]
        clips = e.plan_clips(det["data"], duration=5.0, min_clip_s=0.5)
        assert len(clips) == 2, f"expected 2 planned clips, got {len(clips)}: {clips}"

        # 2) full pipeline writes exactly those two clips.
        out = tmp / "out"
        opts = e.ChopOptions(out_root=out, min_black_s=0.3, min_clip_s=0.5, dry_run=False)
        res = e.process(sample, opts)
        assert res.action == "chopped", f"expected chopped, got {res.action}: {res.reason}"
        assert res.clips == 2, f"expected 2 clips written, got {res.clips}"
        assert len(res.clip_ranges) == 2
        assert set(res.clip_paths) == set(res.clip_bytes) == set(res.clip_sha256)
        assert set(res.clip_paths) == set(res.clip_durations)
        assert e.validate_result(res, opts), "stored clip-set validation failed"
        for p in res.clip_paths:
            assert Path(p).is_file() and Path(p).stat().st_size > 0, f"empty clip: {p}"

        # A size-preserving overwrite still invalidates exact clip reuse.
        first = Path(res.clip_paths[0])
        original = first.read_bytes()
        first.write_bytes(b"X" * len(original))
        assert not e.validate_result(res, opts), "corrupt clip hash must be rejected"

        preview_opts = e.ChopOptions(
            out_root=tmp / "preview", min_black_s=0.3, min_clip_s=0.5,
            dry_run=True,
        )
        preview = e.process(sample, preview_opts)
        assert preview.action == "dry-run" and preview.clips == 2
        assert e.validate_result(preview, preview_opts)

        other = tmp / "other" / sample.name
        other.parent.mkdir()
        other.write_bytes(sample.read_bytes())
        collisions = e.find_output_collisions([sample, other], opts)
        assert len(collisions) == 1, collisions

        cancel_opts = e.ChopOptions(
            out_root=tmp / "cancel", min_black_s=0.3, dry_run=False
        )
        try:
            e.process(sample, cancel_opts, cancelled=lambda: True)
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("immediate cancellation did not propagate")
        assert not (tmp / "cancel").exists()

        continuous = tmp / "continuous.mp4"
        assert _make_continuous(continuous)
        no_gap = e.process(
            continuous,
            e.ChopOptions(out_root=tmp / "continuous_out", min_black_s=0.3, dry_run=False),
        )
        assert no_gap.action == "skipped" and "no black gaps" in no_gap.reason
        assert not (tmp / "continuous_out").exists(), "continuous source should not be copied"

        bad = e.process(sample, e.ChopOptions(min_clip_s=0, dry_run=True))
        assert bad.action == "failed" and bad.detail == "bad.options"
        malformed = e.process(sample, e.ChopOptions(container="../mp4", dry_run=True))
        assert malformed.action == "failed" and malformed.detail == "bad.container"

    print("PASS: video_chopper — planned, validated, and wrote 2 clips; corruption, "
          "collision, cancellation, and no-gap paths passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
