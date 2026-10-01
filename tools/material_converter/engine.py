"""Material Converter engine — deterministic batch processing of PBR texture-map sets.

Pure logic, no UI, no global state, no AI, no network, no GPU. numpy + Pillow,
imported lazily inside the functions that need them so discovery and the sidebar
work before those deps are installed.

What it does (all deterministic):
    - detect texture sets by filename (group by base name, classify each map by
      suffix keyword -> role)
    - channel-pack ORM (R=AO, G=Roughness, B=Metallic) and Unity MOS
    - channel-unpack an existing ORM back into AO / Roughness / Metallic
    - convert normal maps DirectX<->OpenGL (invert green channel)
    - invert gloss<->roughness (255 - x on 8-bit)
    - rename to an engine preset (Unity / Unreal / Godot / Blender / glTF)
    - resize a whole set consistently (Lanczos)
    - write a per-set material manifest JSON

Errors are values: fallible I/O returns a `Result`; the pure math functions are
pure. See CodingPrinciples.md / AGENTS.md.

Public interface (pure):
    classify_map(filename)          -> role str
    detect_sets(paths)              -> list[TextureSet]
    pack_orm(ao, rough, metal)      -> PIL.Image  (RGB)
    unpack_orm(img)                 -> (ao, rough, metal)  (three L images)
    pack_mos(metal, ao, rough)      -> PIL.Image  (RGBA Unity metallic/smoothness)
    flip_normal_y(img)              -> PIL.Image
    invert_channel(img)             -> PIL.Image  (L)
    rename_for_engine(role, engine) -> suffix str

Fallible:
    process_set(tset, opts)         -> Result     (load -> transform -> write)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from toolbox.engine_common import IMAGE_EXTS

# --- roles --------------------------------------------------------------------

BASECOLOR = "basecolor"
NORMAL = "normal"
ROUGHNESS = "roughness"
GLOSS = "gloss"
METALLIC = "metallic"
AO = "ao"
HEIGHT = "height"
ORM = "orm"
UNKNOWN = "unknown"

ROLES = (BASECOLOR, NORMAL, ROUGHNESS, GLOSS, METALLIC, AO, HEIGHT, ORM)

# suffix keyword (normalized, lowercased, no separators) -> role
_ROLE_KEYWORDS: dict[str, str] = {
    # basecolor / albedo / diffuse
    "basecolor": BASECOLOR, "base": BASECOLOR, "albedo": BASECOLOR, "alb": BASECOLOR,
    "diffuse": BASECOLOR, "diff": BASECOLOR, "color": BASECOLOR, "colour": BASECOLOR,
    "col": BASECOLOR, "basemap": BASECOLOR,
    # normal
    "normal": NORMAL, "normals": NORMAL, "nrm": NORMAL, "norm": NORMAL, "nor": NORMAL,
    "normalmap": NORMAL,
    # roughness
    "roughness": ROUGHNESS, "rough": ROUGHNESS, "rgh": ROUGHNESS, "roughnessmap": ROUGHNESS,
    # gloss (kept distinct so gloss->roughness inversion is explicit)
    "gloss": GLOSS, "glossiness": GLOSS, "glossy": GLOSS,
    # metallic
    "metallic": METALLIC, "metalness": METALLIC, "metallness": METALLIC, "metalic": METALLIC,
    "metal": METALLIC, "mtl": METALLIC, "met": METALLIC,
    # ambient occlusion
    "ao": AO, "ambientocclusion": AO, "occlusion": AO, "occ": AO, "aomap": AO,
    # height / displacement
    "height": HEIGHT, "heightmap": HEIGHT, "disp": HEIGHT, "displacement": HEIGHT,
    "bump": HEIGHT,
    # packed ORM
    "orm": ORM, "rma": ORM, "arm": ORM, "maskmap": ORM,
}

# trailing tokens that are resolution / convention tags, not roles — stripped
# before role matching so e.g. "rock_Normal_DX_2k" still resolves to `normal`.
_QUALIFIERS = {
    "dx", "gl", "ogl", "opengl", "directx", "raw", "srgb", "linear", "lin",
    "1k", "2k", "4k", "8k", "512", "1024", "2048", "4096", "hq", "lq", "map",
}

_TOKEN_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def _token_spans(stem: str) -> list[tuple[str, int, int]]:
    """Tokenize a filename stem, splitting on separators AND camelCase boundaries,
    keeping each token's char span so the original-case base can be recovered.

    `rock_BaseColor` -> [('rock',0,4), ('Base',5,9), ('Color',9,14)]
    """
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(stem)]


def _split_role(stem: str) -> tuple[str, str]:
    """(base, role) for a stem. Role is matched as the longest keyword suffix,
    after stripping trailing qualifier/resolution tags. base preserves original
    case. If nothing matches, (stem, "unknown")."""
    spans = _token_spans(stem)
    tokens = [tok.lower() for tok, _, _ in spans]
    n = len(tokens)
    # drop trailing qualifier / pure-digit tokens from the region we match against
    end = n
    while end > 1 and (tokens[end - 1] in _QUALIFIERS or tokens[end - 1].isdigit()):
        end -= 1
    # longest suffix wins: try suffixes starting as early as possible (up to 3 tokens)
    for start in range(max(0, end - 3), end):
        joined = "".join(tokens[start:end])
        role = _ROLE_KEYWORDS.get(joined)
        if role:
            base = stem[: spans[start][1]].rstrip(" _-.")   # spans[start][1] = token start char
            return (base or stem, role)
    return (stem, UNKNOWN)


def classify_map(filename: str | Path) -> str:
    """Role for one map filename, by suffix keyword. Pure.

    Handles common variants: _BaseColor/_albedo/_diffuse, _Normal/_nrm,
    _Roughness/_rough, _Metallic/_metal, _AO/_occlusion, _Height/_disp, _ORM.
    Returns "unknown" for anything unrecognized.
    """
    return _split_role(Path(filename).stem)[1]


# --- texture-set detection ----------------------------------------------------

@dataclass
class TextureSet:
    """A group of maps sharing a base name. `maps` is role -> source path."""
    base: str
    maps: dict[str, Path] = field(default_factory=dict)

    @property
    def roles(self) -> list[str]:
        return sorted(self.maps)

    def to_dict(self) -> dict:
        return {"base": self.base, "maps": {r: str(p) for r, p in self.maps.items()}}


def detect_sets(paths: list[str | Path]) -> list[TextureSet]:
    """Group loose map files into texture sets by base name. Pure.

    Files are classified by suffix; those sharing a case-insensitive base name
    form one set. First file to claim a role wins (a later duplicate role is
    ignored so the group key stays stable). Unknown-role files each form their
    own single-entry set under role "unknown"."""
    sets: dict[str, TextureSet] = {}
    order: list[str] = []
    for raw in paths:
        p = Path(raw)
        base, role = _split_role(p.stem)
        key = base.lower()
        if role == UNKNOWN:
            key = p.stem.lower()          # keep unknowns separate, one per file
        if key not in sets:
            sets[key] = TextureSet(base=base)
            order.append(key)
        tset = sets[key]
        if role not in tset.maps:         # first-wins keeps grouping deterministic
            tset.maps[role] = p
    return [sets[k] for k in order]


# --- pure image ops (numpy + Pillow, lazy) ------------------------------------

def _as_gray(img) -> "object":
    """uint8 2D array from any image (single-channel view)."""
    import numpy as np
    return np.asarray(img.convert("L"), dtype=np.uint8)


def _match_size(img, size: tuple[int, int]):
    """Resize `img` to (w, h) with Lanczos if it differs, so channels align."""
    from PIL import Image
    if img.size != size:
        return img.resize(size, Image.LANCZOS)
    return img


def pack_orm(ao, rough, metal):
    """Pack three single-channel maps into an ORM RGB image:
    R = AO, G = Roughness, B = Metallic. Channels are resized to AO's size if
    they differ. Pure."""
    from PIL import Image
    import numpy as np
    a = _as_gray(ao)
    size = (a.shape[1], a.shape[0])       # (w, h)
    r = _as_gray(_match_size(rough, size))
    m = _as_gray(_match_size(metal, size))
    packed = np.stack([a, r, m], axis=-1).astype(np.uint8)
    return Image.fromarray(packed, mode="RGB")


def unpack_orm(img):
    """Split an ORM RGB image back into (ao, rough, metal) as three L images.
    Inverse of pack_orm. Pure."""
    from PIL import Image
    import numpy as np
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    ao = Image.fromarray(arr[..., 0], mode="L")
    rough = Image.fromarray(arr[..., 1], mode="L")
    metal = Image.fromarray(arr[..., 2], mode="L")
    return ao, rough, metal


def pack_mos(metal, ao, rough):
    """Unity metallic/smoothness pack as RGBA:
    R = Metallic, G = AO (Occlusion), B = 1.0, A = Smoothness (1 - Roughness).
    (Lifted from RupayanFlow's Unity_MOS convention.) Pure."""
    from PIL import Image
    import numpy as np
    m = _as_gray(metal)
    size = (m.shape[1], m.shape[0])
    o = _as_gray(_match_size(ao, size))
    r = _as_gray(_match_size(rough, size))
    white = np.full_like(m, 255)
    smooth = (255 - r).astype(np.uint8)
    packed = np.stack([m, o, white, smooth], axis=-1).astype(np.uint8)
    return Image.fromarray(packed, mode="RGBA")


def flip_normal_y(img):
    """Convert a tangent-space normal map between DirectX and OpenGL by inverting
    the green (Y) channel (255 - G). Its own inverse: applying it twice is the
    identity. Preserves alpha. Pure."""
    from PIL import Image
    import numpy as np
    mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
    arr = np.array(img.convert(mode), dtype=np.uint8)   # np.array => writable copy
    arr[..., 1] = 255 - arr[..., 1]
    return Image.fromarray(arr, mode=mode)


def invert_channel(img):
    """Invert a single-channel map (255 - x) — gloss<->roughness. Returns an L
    image. Pure."""
    from PIL import Image
    import numpy as np
    arr = np.asarray(img.convert("L"), dtype=np.uint8)
    return Image.fromarray((255 - arr).astype(np.uint8), mode="L")


# --- engine naming presets ----------------------------------------------------

_DEFAULT_SUFFIX = {
    BASECOLOR: "BaseColor", NORMAL: "Normal", ROUGHNESS: "Roughness",
    GLOSS: "Gloss", METALLIC: "Metallic", AO: "AO", HEIGHT: "Height", ORM: "ORM",
}

ENGINES = ("none", "unity", "unreal", "godot", "blender", "gltf")

_ENGINE_SUFFIX: dict[str, dict[str, str]] = {
    "unity": {BASECOLOR: "BaseColor", NORMAL: "Normal", ROUGHNESS: "Roughness",
              GLOSS: "Gloss", METALLIC: "Metallic", AO: "Occlusion",
              HEIGHT: "Height", ORM: "ORM"},
    "unreal": {BASECOLOR: "BaseColor", NORMAL: "Normal", ROUGHNESS: "Roughness",
               GLOSS: "Gloss", METALLIC: "Metallic", AO: "AO",
               HEIGHT: "Height", ORM: "ORM"},
    "godot": {BASECOLOR: "Albedo", NORMAL: "Normal", ROUGHNESS: "Roughness",
              GLOSS: "Gloss", METALLIC: "Metallic", AO: "AO",
              HEIGHT: "Height", ORM: "ORM"},
    "blender": {BASECOLOR: "BaseColor", NORMAL: "Normal", ROUGHNESS: "Roughness",
                GLOSS: "Gloss", METALLIC: "Metallic", AO: "AO",
                HEIGHT: "Height", ORM: "ORM"},
    "gltf": {BASECOLOR: "baseColor", NORMAL: "normal", ROUGHNESS: "roughness",
             GLOSS: "gloss", METALLIC: "metallic", AO: "occlusion",
             HEIGHT: "height", ORM: "ORM"},
}


def rename_for_engine(role: str, engine: str) -> str:
    """The map-name suffix for a role under an engine preset. engine="none" (or
    unknown) yields a neutral default. Pure."""
    table = _ENGINE_SUFFIX.get(engine)
    if table and role in table:
        return table[role]
    return _DEFAULT_SUFFIX.get(role, role.capitalize())


# --- options + result ---------------------------------------------------------

@dataclass
class MaterialOptions:
    out_root: Path | None = None
    input_root: Path | None = None
    mirror: bool = False
    pack_orm: bool = False              # build R=AO,G=Rough,B=Metal
    pack_mos: bool = False              # build Unity metallic/smoothness RGBA
    unpack_orm: bool = False            # split an existing ORM into AO/Rough/Metal
    normal_flip: str = "none"           # none | dx2gl | gl2dx (both invert green)
    gloss_to_rough: bool = False        # invert a gloss map into roughness
    target_engine: str = "none"         # none | unity | unreal | godot | blender | gltf
    resize_to: int = 0                  # 0 = keep size; else longest side in px (Lanczos)
    dry_run: bool = True

    def op_summary(self) -> dict:
        return {"pack_orm": self.pack_orm, "pack_mos": self.pack_mos,
                "unpack_orm": self.unpack_orm, "normal_flip": self.normal_flip,
                "gloss_to_rough": self.gloss_to_rough,
                "target_engine": self.target_engine, "resize_to": self.resize_to}


@dataclass
class Result:
    base: str
    action: str                         # converted | skipped | failed | dry-run
    reason: str
    roles: str = ""                     # comma-joined roles found
    outputs: list[str] = field(default_factory=list)
    manifest: str | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --- per-file helpers ---------------------------------------------------------

def _open_detached(path: Path):
    """Open + detach an image so the file handle is released immediately (matters
    on Windows for large batches). Honors the `with` idiom."""
    from PIL import Image
    with Image.open(path) as im:
        return im.copy()


def _resize_long(img, target: int):
    """Scale so the longest side == target, aspect preserved (Lanczos)."""
    from PIL import Image
    w, h = img.size
    long_side = max(w, h)
    if target <= 0 or long_side == target or long_side == 0:
        return img
    scale = target / long_side
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    return img.resize((nw, nh), Image.LANCZOS)


def _save_atomic(img, dst: Path) -> None:
    """Write via a `.part` temp then replace — no half-written files on a crash."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    img.save(tmp)
    tmp.replace(dst)


def _plan_out_dir(tset: TextureSet, opts: MaterialOptions) -> Path:
    """Where a set's outputs go. Mirror preserves the input subtree under
    out_root; else flat under out_root; else a `converted` folder beside source."""
    first = next(iter(tset.maps.values()))
    if opts.out_root:
        root = Path(opts.out_root)
        if opts.mirror and opts.input_root:
            try:
                rel = first.parent.relative_to(opts.input_root)
                return root / rel
            except ValueError:
                pass
        return root
    return first.parent / "converted"


def _grayscale_solid(size: tuple[int, int], value: int):
    """A solid single-channel image (fallback for a missing pack channel)."""
    from PIL import Image
    return Image.new("L", size, value)


# --- process one set ----------------------------------------------------------

def process_set(tset: TextureSet, opts: MaterialOptions) -> Result:
    """Load a set, apply the requested operations, write the outputs and a
    per-set `<base>_Material.json` manifest. Returns a Result envelope."""
    roles_str = ",".join(tset.roles)
    work_roles = [r for r in tset.maps if r != UNKNOWN]
    if not work_roles:
        return Result(tset.base, "skipped", "no recognized PBR maps", roles=roles_str,
                      detail="set.empty")

    try:
        import numpy  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return Result(tset.base, "failed", "numpy + Pillow required — pip install numpy pillow",
                      roles=roles_str, detail="dep.missing")

    out_dir = _plan_out_dir(tset, opts)
    engine = opts.target_engine
    ext_default = ".png"

    # ---- dry run: plan filenames without loading pixels ----------------------
    if opts.dry_run:
        planned: list[str] = []
        for role, src in tset.maps.items():
            if role == UNKNOWN:
                continue
            eff = ROUGHNESS if (role == GLOSS and opts.gloss_to_rough) else role
            planned.append(str(out_dir / f"{tset.base}_{rename_for_engine(eff, engine)}{src.suffix}"))
        if opts.pack_orm:
            planned.append(str(out_dir / f"{tset.base}_{rename_for_engine(ORM, engine)}.png"))
        if opts.pack_mos:
            planned.append(str(out_dir / f"{tset.base}_MOS.png"))
        if opts.unpack_orm and ORM in tset.maps:
            for r in (AO, ROUGHNESS, METALLIC):
                planned.append(str(out_dir / f"{tset.base}_{rename_for_engine(r, engine)}{ext_default}"))
        return Result(tset.base, "dry-run", f"would write {len(planned)} file(s)",
                      roles=roles_str, outputs=planned, detail="dry-run")

    # ---- execute -------------------------------------------------------------
    outputs: list[Path] = []
    try:
        loaded = {r: _open_detached(p) for r, p in tset.maps.items() if r != UNKNOWN}

        # per-map transforms + engine rename (every recognized map is emitted)
        for role, img in loaded.items():
            out_img = img
            eff_role = role
            if role == NORMAL and opts.normal_flip in ("dx2gl", "gl2dx"):
                out_img = flip_normal_y(out_img)
            if role == GLOSS and opts.gloss_to_rough:
                out_img = invert_channel(out_img)
                eff_role = ROUGHNESS
            if opts.resize_to:
                out_img = _resize_long(out_img, opts.resize_to)
            suffix = tset.maps[role].suffix or ext_default
            dst = out_dir / f"{tset.base}_{rename_for_engine(eff_role, engine)}{suffix}"
            _save_atomic(out_img, dst)
            outputs.append(dst)

        # roughness source for packing: real roughness, else inverted gloss
        rough_src = loaded.get(ROUGHNESS)
        if rough_src is None and GLOSS in loaded:
            rough_src = invert_channel(loaded[GLOSS])

        # channel pack: ORM
        if opts.pack_orm:
            size = next(iter(loaded.values())).size
            ao_src = loaded.get(AO) or _grayscale_solid(size, 255)      # no AO -> white (unoccluded)
            r_src = rough_src or _grayscale_solid(size, 128)            # no rough -> mid
            m_src = loaded.get(METALLIC) or _grayscale_solid(size, 0)   # no metal -> dielectric
            orm_img = pack_orm(ao_src, r_src, m_src)
            if opts.resize_to:
                orm_img = _resize_long(orm_img, opts.resize_to)
            dst = out_dir / f"{tset.base}_{rename_for_engine(ORM, engine)}.png"
            _save_atomic(orm_img, dst)
            outputs.append(dst)

        # channel pack: Unity MOS
        if opts.pack_mos:
            size = next(iter(loaded.values())).size
            m_src = loaded.get(METALLIC) or _grayscale_solid(size, 0)
            ao_src = loaded.get(AO) or _grayscale_solid(size, 255)
            r_src = rough_src or _grayscale_solid(size, 128)
            mos_img = pack_mos(m_src, ao_src, r_src)
            if opts.resize_to:
                mos_img = _resize_long(mos_img, opts.resize_to)
            dst = out_dir / f"{tset.base}_MOS.png"
            _save_atomic(mos_img, dst)
            outputs.append(dst)

        # channel unpack: split an existing ORM
        if opts.unpack_orm and ORM in loaded:
            ao_i, rough_i, metal_i = unpack_orm(loaded[ORM])
            for r, split_img in ((AO, ao_i), (ROUGHNESS, rough_i), (METALLIC, metal_i)):
                si = _resize_long(split_img, opts.resize_to) if opts.resize_to else split_img
                dst = out_dir / f"{tset.base}_{rename_for_engine(r, engine)}{ext_default}"
                _save_atomic(si, dst)
                outputs.append(dst)

        manifest_path = _write_set_manifest(tset, opts, out_dir, outputs)
    except Exception as ex:               # PIL/numpy raise many types on bad data / writes
        partial = f"; {len(outputs)} output(s) already committed" if outputs else ""
        return Result(
            tset.base,
            "failed",
            f"convert failed for '{tset.base}': {ex}{partial}",
            roles=roles_str,
            outputs=[str(path) for path in outputs],
            detail="process.failed",
        )

    return Result(tset.base, "converted", f"wrote {len(outputs)} file(s)", roles=roles_str,
                  outputs=[str(p) for p in outputs], manifest=manifest_path, detail="ok")


def _write_set_manifest(tset: TextureSet, opts: MaterialOptions, out_dir: Path,
                        outputs: list[Path]) -> str:
    """Per-set `<base>_Material.json` — records source maps, operations, outputs."""
    payload = {
        "schema": "ks_material_converter.v1",
        "base": tset.base,
        "source_maps": {r: str(p) for r, p in tset.maps.items()},
        "operations": opts.op_summary(),
        "outputs": [p.name for p in outputs],
    }
    dst = out_dir / f"{tset.base}_Material.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}.part.json")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dst)
    return str(dst)


def validate_result(result: Result, opts: MaterialOptions) -> bool:
    """Verify every set output and its provenance manifest before reuse."""
    if result.action in {"skipped", "dry-run"}:
        return True
    if result.action != "converted" or not result.outputs or not result.manifest:
        return False
    try:
        from PIL import Image

        output_paths = [Path(value) for value in result.outputs]
        for output in output_paths:
            if not output.is_file() or output.stat().st_size <= 0:
                return False
            with Image.open(output) as image:
                image.load()
                if image.width <= 0 or image.height <= 0:
                    return False
                if opts.resize_to and max(image.size) != opts.resize_to:
                    return False

        manifest_path = Path(result.manifest)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_maps = payload.get("source_maps")
        return (
            payload.get("schema") == "ks_material_converter.v1"
            and str(payload.get("base", "")).lower() == result.base.lower()
            and payload.get("operations") == opts.op_summary()
            and payload.get("outputs") == [path.name for path in output_paths]
            and isinstance(source_maps, dict)
            and bool(source_maps)
            and all(Path(path).is_file() for path in source_maps.values())
        )
    except (ImportError, OSError, TypeError, ValueError):
        return False
