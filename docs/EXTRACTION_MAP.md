# Extraction Map — deterministic modules to lift for the public tool line

Folded from two source-engine inventories (RupayanFlow `D:\KSAppDev\KS-RupayanFlow`,
ChobiEngine `M:\KS Apps\KS ChobiEngine`). Only **deterministic, CPU-only** targets
are listed here (numpy / Pillow / OpenCV-with-fallback / ffmpeg). AI/GPU/network
modules stay in the engine. `R:` = RupayanFlow, `C:` = ChobiEngine.

The clean lift targets are the inner `services/` + `engine/` functions — **not** the
`runtime.py`/`worker.py` job shells (those wrap the pure logic in a job envelope).

---

## Asset Auditor  *(highest leverage; ~80% liftable — build first)*

| Need | Lift from | Notes |
|---|---|---|
| perceptual + exact dup detection | **C: `texture_similarity_guard/image_fingerprint.py`** (`pHash/dHash/aHash/wHash`, histograms, `ssim_similarity`, `hash_similarity`) | works on **numpy+Pillow alone** (imagehash/skimage/cv2 optional). Prime engine. |
| dup grouping + reports + contact sheets | C: `texture_similarity_guard/{similarity_guard,similarity_report}.py` | weighted score, KEEP/WARN/SKIP, JSON. |
| near-dup (alt) | R: `tools/ks_image_similarity_audit.py` (dHash + union-find) | pure Pillow CLI. |
| corrupt detect, resolution, seam, edge-density | **C: `image_analyzer/services/analysis_cv.py`** (`collect_preflight_metrics`) | cv2-optional fallbacks. |
| sha256 dedup, unsafe-name, escaping-path, collision | **R: `packaging/pack_core.py`** (`sha256_file`, `validate_pack_name`, safe-path checks) | stdlib only. |
| image health (exposure/blur/contrast) | R: `analysis/heuristics.py` (`ImageHeuristicsAnalyzer`) | numpy+Pillow. |
| folder scan / natural-sort / manifest | R: `batch/manifest.py` (`scan_files`, `write_manifests`) | stdlib. |
| **build (gap):** magic-byte corrupt detection, **HTML** report, empty/oversized/resolution-mismatch batch auditor (primitives exist, not assembled) |

## Material Converter  *(nearly turnkey)*

| Need | Lift from | Notes |
|---|---|---|
| ORM/MOS channel pack, per-map naming presets, 8/16-bit PNG | **C: `pbr_map_generator/engine/exporters.py`** (`_create_packed_orm`, `_save_pbr_maps`, `_write_gray16_png`) + **R: `pbr/export.py`** (`export_pbr_map_set`, MOS pack) | numpy+Pillow. |
| **DirectX↔OpenGL normal flip** | **C: `pbr_map_generator/engine/normal_generation.py`** (`if directx: ny*=-1`) + R: `pbr/export.py` (`normal[...,1]=1-...`) | cv2+numpy. |
| engine/material presets (Unity/Unreal/Godot + roughness min/max) | C: `pbr_map_generator/profiles.py`; R: `pbr/contracts.py` (`MAP_SPECS`, `NORMAL_CONVENTIONS`) | pure dataclasses. |
| texture math (srgb↔linear, CLAHE, luminance) | C: `pbr_map_generator/engine/signal_ops.py` | general toolkit. |
| material manifest | R: `pbr/export.py` (`_Material.json`); C: `services/asset_manifest_service` | — |
| **build (gap):** texture-set auto-detect by filename (`_albedo/_normal/_orm` grouping), standalone **gloss↔roughness invert**, **ORM unpack/split**, Blender/glTF presets, resize-across-set (all small) |

## Sprite Doctor  *(very rich; highly liftable)*

| Need | Lift from | Notes |
|---|---|---|
| split / combine / bounds / align / dedupe / metadata | **C: `sprite_processor/services/autosprite_service.py`** (`split_sprite_sheet`, `join_frames_to_sheet`, `detect_frame_bounds`, `align_frames`, `dedupe_frames`, `export_animation_metadata`) | Pillow/numpy; cv2 for video. |
| connected-component sprite detection | C: `sprite_processor/services/splitter_service.py` (`find_sprite_boxes`, alpha BFS) | Pillow. |
| atlas pack (median-scale, baseline, POT, extrude) | C: `sprite_processor/services/sheet_maker_service.py`; R: `video/native/sheet.py` (`slice_pose_sheet`) | Pillow. |
| trim/pad/align/dedupe/blank-detect/GIF-MP4/loop-QA | R: `video/cleanup.py` (`crop_to_bounds`, `pad_to_size`, `align_baseline`, `dedupe_frames`, `detect_broken_frames`, `export_sprite_sheet`, `export_preview`) | numpy+cv2+Pillow (+imageio). |
| **build (gap):** reverse/ping-pong sequence, missing-frame-# detection, Godot `.tres`/Unity metadata emitter (only generic JSON exists) |

## Alpha Doctor  *(enhance the shipped tool)*

| Need | Lift from | Notes |
|---|---|---|
| HSV chroma key (green/blue/hex) + despill | **C: `motion_forge/autosprite/bg_cleanup.py`** (`remove_chroma_key`) | validates + upgrades my RGB chroma method to HSV. |
| multipass defringe / RGB bleed into transparent | **C: `batch_cleanup/worker.py`** (`_apply_multipass_defringe`, `_apply_fringe_fix`, `_refine_alpha_channel`) | full defringe/unmatte/morphology kit. |
| transparent-RGB neighbor fill (clean premult edges) | R: `image/fill.py` (`prefill_masked_region`); C: `services/masked_prefill_service` (Telea) | cv2-optional. |
| solid-color / border-distance matte (validates my `solid`) | R+C: `character_layer_split/masking.py` (`_mask_from_border_distance`, `_refine_binary_mask`) | numpy+Pillow. |
| alpha grow/shrink morphology | C: `ks_spriter/services/fringe_fix_service.py` (`run_fringe_cleanup`) | pure Pillow. |

## Package Extractor  *(mostly greenfield)*

Both engines **lack** archive **extraction** — they only *build* ZIPs. Confirmed gap.
- Build on the user's **`M:\KS Apps\KS UnityExtractor`** (Unity `.unitypackage`) + stdlib `zipfile`/`tarfile`.
- Reuse R: `packaging/pack_core.py` safe-path + collision + sha256 helpers for the *extract* side.
- ChobiEngine `grounded_asset_extractor` is crop-extraction (needs an AI-produced index) — not applicable.

## Sprite / Animation Viewer  *(NEW — 2D viewer, low complexity, high traffic)*

Interactive viewer (custom `ctk.CTkFrame` panel, not BaseBatchPanel): load a sheet /
frame folder / GIF / WebP; play at N fps, step frames, scrub timeline, show the slice
grid, alpha checkerboard. Lift slicing/metadata from C: `sprite_processor/services/
{autosprite_service,splitter_service}.py`. Free/offline answer to Sorceress's paid
"Sprite Analyzer". *(Interactive 3D viewer deliberately skipped — Tkinter can't host a
GL viewport; revisit a batch 3D-thumbnail/turntable tool later.)*

## Showcase  *(NEW — "present your work"; deterministic Pillow)*

Contact sheet (labelled thumbnail grid), framed hero (asset on gradient/checkerboard +
caption + "Made with KS ToolBox" watermark → funnel), before/after side-by-side. Pure
Pillow compositing. Output naturally carries the brand → traffic.

## Tileset / Seamless Checker  *(NEW — deterministic)*

Tile preview (N×N), seam-continuity score, wrap-offset, edge-diff heatmap. Lift R:
`seamless/tile_tools.py` (`offset_wrap`, `tile_preview`, `seam_score`) + C: `seamless_checker/
engine/tile_metrics.py` (`axis_metrics`: edge diff/RMSE/correlation/Canny mismatch;
`read/save_image_unicode_safe`). Free answer to Sorceress's paid "Tileset Forge" (they
*generate*; we *check + make seamless*).

## Audio Tool  *(NEW — deterministic, bundled ffmpeg)*

Batch trim / fade in-out / normalize (loudnorm) / convert (mp3/wav/flac/aac/ogg/opus).
Uses the ffmpeg already bundled (`engine_common.resolve_tool`/`run_cmd`). Free/offline
answer to Sorceress's paid "SFX Editor"; rounds out Media Converter.

## Dataset File Manager  *(greenfield; primitives only)*

Both engines: **no** image/caption pairing, missing-caption, resolution-bucketing, or
train/val splitter as assembled tools (captioning is AI/VLM in both). Primitives to reuse:
R: `batch/manifest.py`, `dataset_library.py` (`_SPLITS` enum, sha256); C: `image_pack_export`
(variant sizing, bundling), `services/asset_manifest_service`.

---

## Cross-cutting reuse

- **Windows-path-safe image IO**: C: `seamless_checker/engine/tile_metrics.py`
  (`read_image_unicode_safe` / `save_image_unicode_safe`) — use wherever cv2 touches paths.
- **Exclude from deterministic tools** (AI/GPU/network): R: `asset2d/bg_remove_batch/*`,
  `segmentation/service.py`, `pbr/{learned,estimator,...}`; C: `bg_remove_batch`,
  `generative_fill`, `comfyui_engine`, `image_analyzer` VLM services, LMStudio/Comfy bridges.
- The **`ai_refine`/`enable_*` gates** in both engines are clean seams — the deterministic
  path always runs first; the model branch is opt-in and removable.
</content>
