"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Pure legs (no deps): `is_within` rejects `../escape` and accepts a normal child;
`safe_name` sanitises. Full legs (stdlib only — no ffmpeg/Pillow needed):

  (1) Build a real .zip containing good files PLUS a crafted `../evil.txt`
      traversal entry -> extract -> assert the good files land under out_root and
      the traversal entry is REJECTED (recorded, never written outside).
  (2) Build a minimal fake .unitypackage (gzipped tar) with one
      `<guid>/pathname` (= Assets/foo/bar.txt) + `<guid>/asset` (bytes) ->
      extract -> assert Assets/foo/bar.txt is reconstructed with the right bytes.
  (3) Extract and recursively validate a nested zip, then exercise declared-byte,
      entry-count, same-destination, strict-option, corruption, and cancellation
      rollback guards without touching a pre-existing collision target.

The real extraction legs assert atomic per-archive reports. Everything is stdlib, so
nothing is skipped. customtkinter is needed only to import the package.

Run standalone:  python -m tools.package_extractor.test_smoke
"""
from __future__ import annotations

import gzip  # noqa: F401  (documents the .unitypackage compression; tarfile uses it)
import io
import json
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.package_extractor import engine as e  # noqa: E402


# ---------------------------------------------------------------------------
# pure legs
# ---------------------------------------------------------------------------

def test_safe_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert e.is_within(root, root / "child" / "x.txt"), "normal child must be inside"
        assert not e.is_within(root, root / ".." / "escape.txt"), "../escape must be rejected"
        assert not e.is_within(root, root.parent / "sibling.txt"), "sibling must be rejected"
    # safe_name strips separators / bad chars / reserved names, never empty.
    assert "/" not in e.safe_name("a/b") and "\\" not in e.safe_name("a\\b")
    assert "<" not in e.safe_name("a<b>c") and ">" not in e.safe_name("a<b>c")
    assert e.safe_name("") == "_"
    assert e.safe_name("CON").upper() != "CON", "Windows-reserved name must be defused"
    # a traversal component is rejected outright by _safe_relpath.
    assert e._safe_relpath("../evil.txt") is None
    assert e._safe_relpath("good/a.txt") == Path("good") / "a.txt"
    print("PASS: safe-path — is_within rejects ../escape & sibling, accepts child; safe_name sanitises.")


# ---------------------------------------------------------------------------
# full legs (stdlib)
# ---------------------------------------------------------------------------

def _build_malicious_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("good/a.txt", b"hello")
        zf.writestr("b.txt", b"world")
        zf.writestr("../evil.txt", b"pwned")          # zip-slip traversal


def test_zip_slip_rejected() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        zpath = tmp / "malicious.zip"
        _build_malicious_zip(zpath)
        out = tmp / "out"
        res = e.process(zpath, e.ExtractOptions(out_root=out, collision="rename", dry_run=False))

        dest = out / "malicious"
        assert res.action == "extracted", f"expected extracted, got {res.action}: {res.reason}"
        assert res.degraded, "security rejection must produce a warning-bearing result"
        assert (dest / "good" / "a.txt").read_bytes() == b"hello", "good/a.txt missing or wrong"
        assert (dest / "b.txt").read_bytes() == b"world", "b.txt missing or wrong"
        # the traversal entry must NOT exist anywhere in or above out_root.
        assert not (dest / "evil.txt").exists(), "evil.txt leaked into out_root"
        assert not (out / "evil.txt").exists(), "evil.txt leaked beside out_root"
        assert not (tmp / "evil.txt").exists(), "evil.txt ESCAPED the output root!"
        assert res.rejected >= 1, "zip-slip entry was not recorded as rejected"
        # report written and it names the rejection.
        assert res.report_path and Path(res.report_path).is_file(), "report not written"
        assert res.csv_report_path and Path(res.csv_report_path).is_file()
        assert e.validate_result(res, e.ExtractOptions(out_root=out, collision="rename", dry_run=False))
        assert not list(dest.glob("*.part.*")), "staged report leaked"
        report = json.loads(Path(res.report_path).read_text(encoding="utf-8"))
        assert any("evil" in r["name"] for r in report["rejected"]), "rejection not in report"

        # A same-size overwrite must invalidate exact artifact reuse.
        artifact = dest / "b.txt"
        original = artifact.read_bytes()
        artifact.write_bytes(b"X" * len(original))
        assert not e.validate_result(
            res, e.ExtractOptions(out_root=out, collision="rename", dry_run=False)
        ), "corrupt extracted bytes must invalidate the stored result"

        preview_opts = e.ExtractOptions(out_root=tmp / "preview", dry_run=True)
        preview = e.process(zpath, preview_opts)
        assert preview.action == "dry-run" and preview.listed == 2
        assert preview.degraded and e.validate_result(preview, preview_opts)
    print(f"PASS: zip-slip — good files extracted, '../evil.txt' REJECTED "
          f"({res.rejected} rejected, not written outside).")


def _build_unitypackage(path: Path, guid: str, unity_path: str, payload: bytes) -> None:
    with tarfile.open(path, "w:gz") as tf:               # a .unitypackage IS a gzipped tar
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name); info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        add(f"{guid}/pathname", (unity_path + "\n").encode("utf-8"))
        add(f"{guid}/asset", payload)
        add(f"{guid}/asset.meta", b"fileFormatVersion: 2\n")   # ignored during rebuild


def test_unitypackage_reconstructed() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        upkg = tmp / "pkg.unitypackage"
        payload = b"the-real-asset-bytes-123"
        _build_unitypackage(upkg, "0123456789abcdef", "Assets/foo/bar.txt", payload)
        out = tmp / "out"
        res = e.process(upkg, e.ExtractOptions(out_root=out, dry_run=False))

        dest = out / "pkg"
        rebuilt = dest / "Assets" / "foo" / "bar.txt"
        assert res.action == "extracted", f"expected extracted, got {res.action}: {res.reason}"
        assert rebuilt.is_file(), f"reconstructed path missing: {rebuilt}"
        assert rebuilt.read_bytes() == payload, "reconstructed asset bytes wrong"
        assert res.written == 1, f"expected 1 written, got {res.written}"
        assert res.report_path and Path(res.report_path).is_file(), "report not written"
        assert e.validate_result(res, e.ExtractOptions(out_root=out, dry_run=False))
    print("PASS: unitypackage — 'Assets/foo/bar.txt' reconstructed from pathname with exact bytes.")


def _nested_zip_bytes() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("inside.txt", b"nested payload")
    return data.getvalue()


def test_nested_validation_and_guards() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        outer = tmp / "outer.zip"
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("nested/inner.zip", _nested_zip_bytes())
        out = tmp / "out"
        opts = e.ExtractOptions(out_root=out, nested_depth=1, dry_run=False)
        result = e.process(outer, opts)
        nested_file = out / "outer" / "nested" / "inner_unpacked" / "inside.txt"
        assert result.action == "extracted", result.reason
        assert nested_file.read_bytes() == b"nested payload"
        assert e.validate_result(result, opts), "nested artifact tree did not validate"
        nested_file.write_bytes(b"X" * nested_file.stat().st_size)
        assert not e.validate_result(result, opts), "nested corruption must invalidate reuse"

        # Preview enforces declared-size budgets without creating output.
        preview_out = tmp / "preview"
        preview = e.process(
            outer,
            e.ExtractOptions(
                out_root=preview_out, max_bytes=8, max_file_bytes=8, dry_run=True
            ),
        )
        assert preview.action == "failed" and "bomb guard" in preview.reason
        assert not preview_out.exists(), "dry-run budget check must not write"

        many = tmp / "many.zip"
        with zipfile.ZipFile(many, "w", zipfile.ZIP_STORED) as zf:
            for index in range(3):
                zf.writestr(f"{index}.txt", b"")
        entry_guard = e.process(
            many,
            e.ExtractOptions(
                out_root=preview_out, max_entries=2, dry_run=True
            ),
        )
        assert entry_guard.action == "failed" and "member-count cap" in entry_guard.reason

        # Cancellation after one member commits rolls back only this attempt.
        large = tmp / "large.zip"
        with zipfile.ZipFile(large, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("first.bin", b"first")
            zf.writestr("second.bin", b"A" * (2 * 1024 * 1024))
        cancel_out = tmp / "cancel"
        first_output = cancel_out / "large" / "first.bin"
        first_output.parent.mkdir(parents=True)
        first_output.write_bytes(b"pre-existing")
        attempted_output = cancel_out / "large" / "first_001.bin"

        def cancelled() -> bool:
            return attempted_output.exists()

        try:
            e.process(
                large,
                e.ExtractOptions(out_root=cancel_out, dry_run=False),
                cancelled=cancelled,
            )
        except e.CommandCancelled:
            pass
        else:
            raise AssertionError("streaming cancellation did not propagate")
        assert not list(cancel_out.rglob("*.part")), "cancelled member left a staged file"
        assert not attempted_output.exists(), "cancelled archive left a committed partial member"
        assert first_output.read_bytes() == b"pre-existing", "rollback touched an old collision target"

        other = tmp / "other" / outer.name
        other.parent.mkdir()
        other.write_bytes(outer.read_bytes())
        assert len(e.find_output_collisions([outer, other], opts)) == 1
        invalid = e.process(
            outer, e.ExtractOptions(out_root=out, collision="overwrite", dry_run=True)
        )
        assert invalid.action == "failed" and invalid.detail == "bad.options"
    print("PASS: nested/caps/cancel — recursive artifacts validated; preview budget, "
          "collision, option, and cancellation guards passed.")


def main() -> int:
    test_safe_path()
    test_zip_slip_rejected()
    test_unitypackage_reconstructed()
    test_nested_validation_and_guards()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
