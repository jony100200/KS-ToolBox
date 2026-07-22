"""Smoke test — the release bar for this tool (AGENTS.md §Verification).

Pure leg (no deps): filename classification, set detection, and the algebra of
the channel ops (pack/unpack round-trip, normal-flip involution, invert). Full
leg (numpy + Pillow): a real fake texture set converted end-to-end, asserting
the packed ORM has the right channels and the renamed outputs + manifest exist.
Skips cleanly when deps are missing.

Run standalone:  python -m tools.material_converter.test_smoke
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.material_converter import engine as e  # noqa: E402


def _has(pkg: str) -> bool:
    return importlib.util.find_spec(pkg) is not None


def test_classify() -> None:
    cases = {
        "rock_BaseColor.png": e.BASECOLOR, "rock_albedo.png": e.BASECOLOR,
        "rock_diffuse.png": e.BASECOLOR, "rock_Normal.png": e.NORMAL,
        "rock_nrm.png": e.NORMAL, "rock_Normal_DX.png": e.NORMAL,
        "rock_Roughness.png": e.ROUGHNESS, "rock_rough.png": e.ROUGHNESS,
        "rock_Gloss.png": e.GLOSS, "rock_Metallic.png": e.METALLIC,
        "rock_metal.png": e.METALLIC, "rock_AO.png": e.AO,
        "rock_occlusion.png": e.AO, "wall_AmbientOcclusion.png": e.AO,
        "rock_Height.png": e.HEIGHT, "rock_disp.png": e.HEIGHT,
        "rock_ORM.png": e.ORM, "some_random_texture.png": e.UNKNOWN,
    }
    for name, want in cases.items():
        got = e.classify_map(name)
        assert got == want, f"classify {name!r}: want {want}, got {got}"
    print(f"PASS: classify_map — {len(cases)} names to roles (incl. variants + qualifier tags).")


def test_detect_sets() -> None:
    files = [
        "tex/rock_BaseColor.png", "tex/rock_Normal.png", "tex/rock_Roughness.png",
        "tex/rock_Metallic.png", "tex/rock_AO.png",
        "tex/brick_albedo.png", "tex/brick_nrm.png",
        "tex/loose_thing.png",
    ]
    sets = e.detect_sets(files)
    by_base = {s.base.lower(): s for s in sets}
    assert "rock" in by_base, f"no rock set: {[s.base for s in sets]}"
    assert set(by_base["rock"].roles) == {e.BASECOLOR, e.NORMAL, e.ROUGHNESS, e.METALLIC, e.AO}, \
        f"rock roles wrong: {by_base['rock'].roles}"
    assert "brick" in by_base and set(by_base["brick"].roles) == {e.BASECOLOR, e.NORMAL}, \
        f"brick roles wrong: {by_base.get('brick')}"
    # the unrecognized file forms its own single unknown-role set
    assert any(s.roles == [e.UNKNOWN] for s in sets), "loose file not isolated as unknown"
    print(f"PASS: detect_sets — grouped {len(files)} files into {len(sets)} sets (rock/brick/unknown).")


def test_channel_algebra() -> None:
    if not (_has("PIL") and _has("numpy")):
        print("SKIP: numpy/Pillow not installed — channel-algebra leg skipped.")
        return
    from PIL import Image
    import numpy as np

    ao = Image.new("L", (8, 8), 40)
    rough = Image.new("L", (8, 8), 130)
    metal = Image.new("L", (8, 8), 220)
    orm = e.pack_orm(ao, rough, metal)
    arr = np.asarray(orm)
    assert arr.shape == (8, 8, 3), f"orm shape {arr.shape}"
    assert (arr[..., 0] == 40).all() and (arr[..., 1] == 130).all() and (arr[..., 2] == 220).all(), \
        "pack_orm channel order wrong (want R=AO,G=Rough,B=Metal)"
    ao2, rough2, metal2 = e.unpack_orm(orm)
    assert np.asarray(ao2).mean() == 40 and np.asarray(rough2).mean() == 130 and np.asarray(metal2).mean() == 220, \
        "unpack_orm did not round-trip the channels"

    # normal flip is its own inverse
    nrm = Image.fromarray((np.random.default_rng(0).integers(0, 256, (8, 8, 3))).astype(np.uint8), "RGB")
    twice = e.flip_normal_y(e.flip_normal_y(nrm))
    assert (np.asarray(twice) == np.asarray(nrm)).all(), "flip_normal_y twice != identity"
    once = e.flip_normal_y(nrm)
    assert (np.asarray(once)[..., 1] == 255 - np.asarray(nrm)[..., 1]).all(), "flip did not invert green"

    # invert_channel: 0 -> 255
    inv = e.invert_channel(Image.new("L", (4, 4), 0))
    assert np.asarray(inv).mean() == 255, "invert_channel(0) != 255"
    print("PASS: channel algebra — pack/unpack round-trip, flip involution, invert 0->255.")


def test_full_pipeline() -> None:
    if not (_has("PIL") and _has("numpy")):
        print("SKIP: numpy/Pillow not installed — full-pipeline leg skipped.")
        return
    from PIL import Image
    import numpy as np

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td); src = tmp / "src"; src.mkdir()
        # a fake solid-color texture set
        Image.new("RGB", (16, 16), (180, 120, 60)).save(src / "rock_BaseColor.png")
        Image.new("RGB", (16, 16), (128, 128, 255)).save(src / "rock_Normal.png")
        Image.new("L", (16, 16), 130).save(src / "rock_Roughness.png")
        Image.new("L", (16, 16), 220).save(src / "rock_Metallic.png")
        Image.new("L", (16, 16), 40).save(src / "rock_AO.png")

        files = sorted(src.glob("*.png"))
        sets = e.detect_sets(files)
        assert len(sets) == 1 and sets[0].base.lower() == "rock", f"detect: {[s.base for s in sets]}"

        out = tmp / "out"
        opts = e.MaterialOptions(out_root=out, pack_orm=True, normal_flip="dx2gl",
                                 target_engine="unity", dry_run=False)
        res = e.process_set(sets[0], opts)
        assert res.action == "converted", f"expected converted, got {res.action}: {res.reason}"
        assert e.validate_result(res, opts), "fresh material set did not pass artifact validation"

        # ORM written with correct packed channels (R=AO=40, G=Rough=130, B=Metal=220)
        orm_path = out / "rock_ORM.png"
        assert orm_path.is_file(), f"no ORM at {orm_path}; outputs={res.outputs}"
        with Image.open(orm_path) as orm_im:
            a = np.asarray(orm_im.convert("RGB"))
        assert (a[..., 0] == 40).all() and (a[..., 1] == 130).all() and (a[..., 2] == 220).all(), \
            f"ORM channels wrong: R={a[...,0].mean()} G={a[...,1].mean()} B={a[...,2].mean()}"

        # engine-renamed outputs exist (Unity: BaseColor/Normal/Roughness/Metallic/Occlusion)
        for expect in ("rock_BaseColor.png", "rock_Normal.png", "rock_Roughness.png",
                       "rock_Metallic.png", "rock_Occlusion.png"):
            assert (out / expect).is_file(), f"missing renamed output: {expect}"

        # normal green channel got flipped
        with Image.open(out / "rock_Normal.png") as nrm_im:
            g = np.asarray(nrm_im.convert("RGB"))[..., 1]
        assert (g == 255 - 128).all(), f"normal green not flipped: {g.mean()}"

        # per-set manifest JSON written and well-formed
        man = out / "rock_Material.json"
        assert man.is_file(), "no rock_Material.json manifest"
        data = json.loads(man.read_text(encoding="utf-8"))
        assert data["schema"] == "ks_material_converter.v1" and data["base"].lower() == "rock", \
            f"bad manifest: {data}"

        original_manifest_writer = e._write_set_manifest

        def fail_manifest(*args, **kwargs):
            raise OSError("simulated read-only manifest destination")

        e._write_set_manifest = fail_manifest
        try:
            manifest_failure = e.process_set(sets[0], opts)
        finally:
            e._write_set_manifest = original_manifest_writer
        assert manifest_failure.action == "failed", "manifest failure must fail the set visibly"
        assert manifest_failure.outputs, "already committed outputs must remain in failure provenance"
        assert "already committed" in manifest_failure.reason, manifest_failure.reason

        orm_path.write_bytes(b"not a png")
        assert not e.validate_result(res, opts), "corrupt set member must invalidate stored result"
    print("PASS: full pipeline — set detected, ORM packed, normal flipped, Unity-renamed, manifest written.")


def main() -> int:
    test_classify()
    test_detect_sets()
    test_channel_algebra()
    test_full_pipeline()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
