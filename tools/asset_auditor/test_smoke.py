"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Verifies the pure filename-safety rules (no deps), then — when numpy + Pillow
are present — the perceptual dHash, the corrupt/dimension check, and the full
aggregate audit + report writing on a temp folder built to contain one of every
issue class. Skips cleanly (prints SKIP, returns 0) when numpy/Pillow are absent.

Run standalone:  python -m tools.asset_auditor.test_smoke
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.asset_auditor import engine as e  # noqa: E402


def _have_deps() -> bool:
    return (importlib.util.find_spec("numpy") is not None
            and importlib.util.find_spec("PIL") is not None)


# --- pure (no deps) -----------------------------------------------------------

def test_unsafe_names() -> None:
    assert e.is_unsafe_name("CON") is True            # Windows reserved device
    assert e.is_unsafe_name("CON.png") is True        # reserved stem, any ext
    assert e.is_unsafe_name("a<b.png") is True         # illegal char
    assert e.is_unsafe_name("trailing.png ") is True   # trailing space
    assert e.is_unsafe_name("dotend.") is True         # trailing dot
    assert e.is_unsafe_name("normal_asset.png") is False
    assert e.is_unsafe_name("CONSTANTS.png") is False  # stem not exactly reserved
    print("PASS: is_unsafe_name — reserved names, illegal chars, trailing dot/space.")


# --- deterministic image primitives (numpy + Pillow) --------------------------

def test_dhash_hamming() -> None:
    from PIL import Image
    import numpy as np
    rng = np.random.default_rng(7)
    base = (rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8))
    a = Image.fromarray(base)
    # near-identical: nudge a handful of pixels
    tweaked = base.copy(); tweaked[0, 0] = 0; tweaked[1, 1] = 255
    b = Image.fromarray(tweaked)
    c = Image.fromarray(np.roll(base, 32, axis=1))     # clearly different (shifted)

    ha, hb, hc = e.dhash(a), e.dhash(b), e.dhash(c)
    assert len(ha) == 64, f"dhash length {len(ha)} != 64"
    near = e.hamming(ha, hb)
    far = e.hamming(ha, hc)
    assert near <= 8, f"near-identical hamming too large: {near}"
    assert far > near, f"different image not farther: far={far} near={near}"
    print(f"PASS: dhash/hamming — near={near} bits, far={far} bits.")


def test_near_duplicate_index() -> None:
    """Indexed grouping must remain exactly equivalent to all-pairs union."""
    rng = random.Random(20260722)
    records = [
        e.FileRecord(
            path=f"image_{index:03d}.png",
            size_bytes=1,
            dhash=f"{rng.getrandbits(64):064b}",
        )
        for index in range(120)
    ]
    # Force an exact duplicate and a transitive A~B~C chain.
    records[1].dhash = records[0].dhash
    records[2].dhash = "0" * 64
    records[3].dhash = "1" + "0" * 63
    records[4].dhash = "11" + "0" * 62

    def brute_force(threshold: int) -> list[list[str]]:
        parent = list(range(len(records)))
        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index
        for left in range(len(records)):
            for right in range(left + 1, len(records)):
                if e.hamming(records[left].dhash, records[right].dhash) <= threshold:
                    parent[find(left)] = find(right)
        groups: dict[int, list[str]] = {}
        for index, record in enumerate(records):
            groups.setdefault(find(index), []).append(record.path)
        return sorted(sorted(group) for group in groups.values() if len(group) > 1)

    for threshold in (0, 1, 2, 8, 64):
        assert e._group_near_dups(records, threshold) == brute_force(threshold)
    print("PASS: near-duplicate index — exact parity at five Hamming thresholds.")


def test_check_image() -> None:
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        good = tmp / "good.png"
        Image.new("RGB", (200, 120), (40, 90, 160)).save(good)
        rec = e.check_image(good)
        assert rec["corrupt"] is False, f"good image flagged corrupt: {rec['reason']}"
        assert (rec["width"], rec["height"]) == (200, 120), f"dims wrong: {rec}"

        garbage = tmp / "garbage.png"
        garbage.write_bytes(b"this is not a PNG file at all")
        bad = e.check_image(garbage)
        assert bad["corrupt"] is True, "garbage .png not flagged corrupt"
    print("PASS: check_image — valid dims correct, garbage flagged corrupt.")


def test_full_audit() -> None:
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # two byte-identical PNGs (exact-dup group)
        dup_bytes = None
        Image.new("RGB", (128, 128), (200, 30, 30)).save(tmp / "dupe_a.png")
        dup_bytes = (tmp / "dupe_a.png").read_bytes()
        (tmp / "dupe_b.png").write_bytes(dup_bytes)
        # a unique image
        Image.new("RGB", (256, 256), (10, 200, 60)).save(tmp / "unique.png")
        # a zero-byte file
        (tmp / "empty.png").write_bytes(b"")
        # a garbage .png
        (tmp / "garbage.png").write_bytes(b"\x00\x01\x02 not an image \x03")
        (tmp / "garbage_copy.png").write_bytes((tmp / "garbage.png").read_bytes())
        # a tiny image
        Image.new("RGB", (8, 8), (0, 0, 0)).save(tmp / "tiny.png")
        # an empty sub-folder
        (tmp / "empty_folder").mkdir()

        # an unsafe-named file — but Windows won't let us *create* one, so try a
        # few and only assert detection if the OS accepted it (honest, not faked).
        unsafe_created = None
        for candidate in ("CON.png", "bad<name>.png", "trailing.png "):
            try:
                p = tmp / candidate
                Image.new("RGB", (16, 16), (100, 100, 100)).save(p)
                if p.exists() and e.is_unsafe_name(p.name):
                    unsafe_created = candidate
                    break
            except (OSError, ValueError):
                continue

        paths = sorted(p for p in tmp.rglob("*")
                       if p.is_file() and p.suffix.lower() in e.IMAGE_EXTS)
        report = e.audit(paths, e.AuditOptions(scan_root=tmp, min_dimension=32))

        # exact-dup group of the two identical PNGs
        dup_group = [g for g in report.exact_dups if len(g) == 2
                     and all("dupe_" in Path(x).name for x in g)]
        assert dup_group, f"exact-dup group not found: {report.exact_dups}"
        # corrupt: garbage.png (and the empty file) must be flagged
        corrupt_names = {Path(c["path"]).name for c in report.corrupt}
        assert "garbage.png" in corrupt_names, f"garbage not corrupt: {corrupt_names}"
        # empty file
        empty_names = {Path(p).name for p in report.empty_files}
        assert "empty.png" in empty_names, f"empty file not found: {empty_names}"
        # tiny image
        tiny_names = {Path(t["path"]).name for t in report.tiny_images}
        assert "tiny.png" in tiny_names, f"tiny image not found: {tiny_names}"
        # empty folder
        assert any(Path(d).name == "empty_folder" for d in report.empty_folders), \
            f"empty folder not found: {report.empty_folders}"
        # unsafe name — only assertable if the OS let us create one
        if unsafe_created:
            unsafe_names = {Path(p).name for p in report.unsafe_names}
            assert unsafe_created in unsafe_names, \
                f"unsafe name {unsafe_created!r} not detected: {unsafe_names}"
            unsafe_note = f"unsafe='{unsafe_created}'"
        else:
            unsafe_note = "unsafe=skipped (OS blocked creating an unsafe filename)"

        # reports write out non-empty .html and .json (atomic, self-contained)
        out = tmp / "report_out"
        res = e.write_reports(report, out)
        assert not res["error"], f"write_reports failed: {res['details']}"
        html_path = Path(res["data"]["html"]); json_path = Path(res["data"]["json"])
        assert html_path.stat().st_size > 0, "audit.html is empty"
        assert json_path.stat().st_size > 0, "audit.json is empty"
        assert "<html" in html_path.read_text(encoding="utf-8").lower()

        # Durable process result: exact artifacts, completion marker, corruption
        # rejection, strict options, and active cancellation cleanup.
        durable_out = tmp / "durable_report"
        opts = e.AuditOptions(
            out_root=durable_out, scan_root=tmp, min_dimension=32
        )
        result = e.process(paths, opts)
        assert result.action == "audited", result.reason
        assert len(result.artifacts) == 3 and e.validate_result(result, opts)
        assert result.warnings, "corrupt duplicate thumbnails must be announced"
        marker = durable_out / "audit.json"
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        assert marker_payload["report_schema"] == 2
        assert set(marker_payload["report_artifacts"]) == {"csv", "html"}

        html_bytes = (durable_out / "audit.html").read_bytes()
        (durable_out / "audit.html").write_bytes(b"X" * len(html_bytes))
        assert not e.validate_result(result, opts), "same-size report corruption was reused"
        (durable_out / "audit.html").write_bytes(html_bytes)
        assert e.validate_result(result, opts)

        invalid = e.process(
            paths, e.AuditOptions(out_root=tmp / "invalid", near_dup_hamming=65)
        )
        assert invalid.action == "failed" and invalid.detail == "bad.options"
        invalid_size = e.process(
            paths, e.AuditOptions(out_root=tmp / "invalid_size", oversized_mb=float("nan"))
        )
        assert invalid_size.action == "failed" and invalid_size.detail == "bad.options"
        too_many = e.process(
            [paths[0]] * (e.MAX_AUDIT_FILES + 1),
            e.AuditOptions(out_root=tmp / "too_many"),
        )
        assert too_many.action == "failed" and too_many.detail == "resource.limit"
        try:
            e.process(
                paths, e.AuditOptions(out_root=tmp / "cancelled"),
                cancelled=lambda: True,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("audit cancellation did not propagate")
        assert not list(tmp.rglob("*.part")), "audit left staged report files"

    print(f"PASS: full audit — dup group, corrupt, empty, tiny, empty-folder; "
          f"durable reports validated/cancelled; {unsafe_note}.")


def test_exact_dup_three_stage() -> None:
    """Verify HDD-gentle 3-stage dedupe skips unique sizes without reading them."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # 1. Exact duplicate (> 192 KB so Stage 2 partial hash is exercised separately from Stage 3)
        payload_a = (b"HEADER_MATCH_" * 6000) + b"TAIL_DIFF_A" + (b"0" * 180_000)
        payload_b = (b"HEADER_MATCH_" * 6000) + b"TAIL_DIFF_B" + (b"0" * 180_000)
        (tmp / "dup_1.bin").write_bytes(payload_a)
        (tmp / "sub").mkdir()
        (tmp / "sub" / "dup_2.bin").write_bytes(payload_a)
        # Same size, same head+mid+tail, differs only at byte offset 100_000 -> caught at Stage 3!
        diff_mid = bytearray(payload_a)
        diff_mid[100_000] ^= 0xFF
        (tmp / "same_size_diff_inner.bin").write_bytes(bytes(diff_mid))
        # Unique size -> skipped at Stage 1 without opening
        (tmp / "unique.bin").write_bytes(payload_b + b"EXTRA")
        # 0-byte file
        (tmp / "empty.bin").write_bytes(b"")

        out = tmp / "dup_report"
        rep = e.find_exact_duplicates([tmp], e.DupScanOptions(out_root=out))
        assert rep.total_files == 5, rep
        assert rep.unique_size_skipped == 1, rep
        assert len(rep.empty_files) == 1
        assert len(rep.groups) == 1, rep.groups
        assert len(rep.groups[0].paths) == 2
        assert rep.groups[0].reclaimable_bytes == len(payload_a)
        assert (out / "dupes.json").is_file() and (out / "dupes.csv").is_file() and (out / "dupes.html").is_file()

        # Verify cleanup_exact_duplicates: dry-run keeps both, execute removes 1 and keeps preferred
        dry_items = e.cleanup_exact_duplicates(out / "dupes.json", dry_run=True)
        assert len(dry_items) == 1 and dry_items[0].status == "dry_run_ok", dry_items
        assert (tmp / "dup_1.bin").is_file() and (tmp / "sub" / "dup_2.bin").is_file()
        real_items = e.cleanup_exact_duplicates(
            out / "dupes.json",
            prefer_prefixes=(str(tmp / "sub"),),
            permanent_delete=True,
            dry_run=False,
        )
        assert len(real_items) == 1 and real_items[0].status == "removed", real_items
        assert Path(real_items[0].keep_path) == tmp / "sub" / "dup_2.bin"
        assert Path(real_items[0].keep_path).is_file()
        assert not Path(real_items[0].dup_path).exists()

        # Verify hardlinks to the same file are skipped rather than reported as duplicates
        hl_src = tmp / "hardlink_orig.bin"
        hl_src.write_bytes(b"HARDLINK_TEST_" * 500)
        hl_dst = tmp / "hardlink_alias.bin"
        try:
            os.link(hl_src, hl_dst)
            rep_hl = e.find_exact_duplicates([tmp])
            assert rep_hl.hardlinks_skipped >= 1, rep_hl
            assert not any(str(hl_dst) in g.paths and str(hl_src) in g.paths for g in rep_hl.groups), rep_hl.groups
        except OSError:
            pass

        # Verify bounded-depth drive finder index skips system dirs and writes atomic HTML + MD
        (tmp / "$RECYCLE.BIN").mkdir()
        (tmp / "$RECYCLE.BIN" / "ignored.zip").write_bytes(b"X" * 2048)
        idx_res = e.build_drive_index(tmp, max_depth=2, include_files_depth=1, min_file_bytes=1024)
        assert (tmp / "00_FIND_EVERYTHING.html").is_file() and (tmp / "00_DRIVE_MAP.md").is_file()
        assert not list(tmp.glob("*.part")), "drive index left stale .part file"
        html_txt = (tmp / "00_FIND_EVERYTHING.html").read_text(encoding="utf-8")
        assert "$RECYCLE.BIN" not in html_txt and idx_res["dirs"] >= 1, idx_res
    print("PASS: exact_dup_three_stage — size filter, partial hash, full sha256, cleanup, and reports ok.")


def main() -> int:
    test_unsafe_names()
    test_exact_dup_three_stage()
    if not _have_deps():
        print("SKIP: numpy/Pillow not installed — image-dependent legs skipped.")
        return 0
    test_dhash_hamming()
    test_near_duplicate_index()
    test_check_image()
    test_full_audit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
