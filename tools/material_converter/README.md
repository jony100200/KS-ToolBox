# Material Converter

Batch-process folders of loose PBR texture maps: detect texture sets by
filename, pack/unpack channels, convert normal conventions, invert
gloss↔roughness, resize consistently, and rename to an engine's naming
convention — with a per-set material manifest.

**100% deterministic. No AI, no network, no GPU** — just numpy + Pillow.

## What it does

Point it at a folder of maps. It groups files that share a base name into
**texture sets**, classifying each by its suffix keyword:

| Role | Recognized suffixes |
|---|---|
| basecolor | `_BaseColor` `_albedo` `_diffuse` `_color` `_col` |
| normal | `_Normal` `_nrm` `_norm` |
| roughness | `_Roughness` `_rough` `_rgh` |
| gloss | `_Gloss` `_glossiness` |
| metallic | `_Metallic` `_metalness` `_metal` `_mtl` |
| ao | `_AO` `_occlusion` `_AmbientOcclusion` |
| height | `_Height` `_disp` `_displacement` `_bump` |
| orm | `_ORM` `_RMA` `_ARM` `_MaskMap` |

CamelCase and trailing tags (`_DX`, `_GL`, `_2k`, `_4096`) are handled, so
`rock_Normal_DX_2k.png` still resolves to the `rock` set as a normal map.

## Operations

| Operation | Meaning |
|---|---|
| **Pack ORM** | build a packed map — R=AO, G=Roughness, B=Metallic |
| **Pack Unity MOS** | RGBA metallic/smoothness — R=Metallic, G=AO, B=1, A=1−Roughness |
| **Unpack ORM** | split an existing ORM back into separate AO / Roughness / Metallic |
| **Normal flip (DX↔GL)** | convert a normal map between DirectX and OpenGL (invert green) |
| **Gloss → Roughness** | invert a gloss map (255 − x) into a roughness map |
| **Resize longest side** | scale every map in the set to the same longest edge (Lanczos) |
| **Target engine** | rename outputs to an engine's convention (below) |

Missing pack channels degrade to sensible defaults (no AO → white, no metallic
→ black/dielectric, no roughness → mid-grey).

## Engine presets (rename)

| Role | Unity | Unreal | Godot | Blender | glTF |
|---|---|---|---|---|---|
| basecolor | BaseColor | BaseColor | Albedo | BaseColor | baseColor |
| normal | Normal | Normal | Normal | Normal | normal |
| ao | Occlusion | AO | AO | AO | occlusion |
| orm | ORM | ORM | ORM | ORM | ORM |

(roughness / metallic / height keep their standard names across engines.)

## Output

Each set writes its renamed maps, any packed/unpacked maps, and a
`<base>_Material.json` manifest (source maps + operations + outputs). A run into
an explicit output folder also appends a top-level `material_manifest.csv`.
**Preview only** lists the planned files without writing anything.

The shared durable queue treats each detected texture set as one work item while
every member map participates in its checkpoint identity. Changing any channel
invalidates the set. Stored results are reused only after every output image and
the versioned per-set provenance manifest pass deterministic validation.

## Dependencies

- **numpy** and **Pillow** (`pip install numpy pillow`).

## Verify

```
python -m tools.material_converter.test_smoke
```

Checks filename classification and set detection (no deps), the channel-op
algebra (pack/unpack round-trip, normal-flip involution, invert), then converts
a real fake texture set end-to-end and asserts the packed ORM channels, the
Unity-renamed outputs, the flipped normal, and the manifest.

## Credits

Channel-pack (ORM), per-map naming, and 8/16-bit PNG writing distilled from
KS ChobiEngine's `pbr_map_generator` exporters and KS-RupayanFlow's `pbr/export`
(MOS pack, `MAP_SPECS`, `_Material.json`). The DirectX↔OpenGL normal flip mirrors
ChobiEngine's `normal_generation` (`if directx: ny *= -1`). The filename
auto-detection, ORM unpack/split, standalone gloss↔roughness invert, and the
Blender/glTF presets were built for this tool.
