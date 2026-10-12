# Image to PBR

Batch-convert 2D raster images into complete, production-grade sets of PBR texture maps (BaseColor, Normal, Height, Roughness, Metallic, AO, and ORM). A KS ToolBox plugin.

**100% standalone. No Upadon dependency.**

---

## What it does

Point it at a single texture or a folder of images (PNG, JPEG, WebP, TGA, BMP, TIFF). It processes each image and generates:

1. **BaseColor**: Delighted or calibrated albedo.
2. **Normal Map**: Tangent-space surface vectors with choice of **OpenGL (+Y)** or **DirectX (-Y)** coordinate conventions.
3. **Height Map**: Multi-scale frequency-separated displacement map.
4. **Roughness Map**: High-frequency micro-contrast variance blended with material preset base values.
5. **Metallic Map**: Accurate dielectric vs. conductor mask.
6. **Ambient Occlusion (AO)**: Cavity and valley occlusion shading.
7. **Packed ORM Map**: Single channel-packed RGB texture ready for game engines:
   - **Red**: Ambient Occlusion ($AO$)
   - **Green**: Roughness ($R$)
   - **Blue**: Metallic ($M$)

---

## Supported Engines

The tool supports three generator backends:

| Engine | Description | Requirements |
| :--- | :--- | :--- |
| **Built-in (Deterministic)** | Fast, offline CPU processing using multi-scale gradient and frequency analysis. | Built into KS ToolBox (NumPy + Pillow). Zero external setup. |
| **Adobe Substance 3D Sampler** | High-end satellite processing via headless Sampler automation (`--run-script-silent`) and Designer baking (`sbsrender`). | User's local installation of Adobe Substance 3D Sampler. |
| **Material Maker** | Procedural `.ptex` graph synthesis and CLI export for Godot, Unity, and Unreal. | User's local installation of RodZilla Material Maker. |

---

## Material Presets

| Preset | Roughness | Metallic | Normal Strength | Height Depth | AO Strength | Typical Use |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wood / Planks** | 0.72 | 0.0 | 0.75 | 0.40 | 0.50 | Timber, flooring, crates, barrels |
| **Stone / Masonry** | 0.85 | 0.0 | 0.95 | 0.65 | 0.70 | Castle walls, cobblestone, rocks |
| **Brick / Tiles** | 0.80 | 0.0 | 0.85 | 0.55 | 0.65 | Ceramic tiles, brickwork, pavers |
| **Metal (Clean / Polished)** | 0.25 | 1.0 | 0.30 | 0.05 | 0.20 | Chrome, polished steel, brass |
| **Metal (Rusted / Worn)** | 0.60 | 0.85 | 0.70 | 0.25 | 0.50 | Corroded iron, scratched bronze |
| **Fabric / Leather** | 0.85 | 0.0 | 0.50 | 0.15 | 0.40 | Cloth, canvas, leather armor |
| **Ground / Dirt** | 0.90 | 0.0 | 0.80 | 0.55 | 0.65 | Soil, mud, terrain, gravel |
| **Plaster / Concrete** | 0.75 | 0.0 | 0.45 | 0.20 | 0.35 | Walls, ceilings, sidewalks |
| **Custom** | Manual | Manual | Manual | Manual | Manual | Custom parameter configuration |

---

## Batch Features

- **Preview Only**: Dry-run mode plans all output filenames and sets without writing files.
- **Mirror Mode**: Preserves source directory tree structure in destination output.
- **Cancellable**: Non-blocking queue processing with graceful process termination.
- **Manifest**: Generates a versioned `pbr_manifest.csv` recording source inputs, execution status, and generated files.
