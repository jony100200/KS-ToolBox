# Seamless Checker

Interactive 3×3 wraparound texture inspection and triage workstation — **deterministic, offline, zero AI**.

Quickly inspect, judge, and triage textures or complete multi-map PBR material sets with edge-to-edge 3×3 visual wrap tiling, mouse wheel zoom/pan, keyboard shortcuts, and safe all-or-nothing Redo transfers.

## What it does

| Feature | Details |
|---|---|
| **Edge-to-Edge 3×3 Wrap** | Visualizes any texture tiled 3×3 in memory with integer box reduce so tile boundaries align without seam filtering artifacts. |
| **Interactive Canvas** | Smooth mouse wheel zoom (centered on cursor), mouse drag pan, double-click fit, and 1:1 pixel inspection. |
| **PBR & Standalone Detection** | Dual-pass scanning automatically pairs BaseColor maps with normal, roughness, metallic, etc. maps, while also recognizing standalone textures without suffixes. |
| **Clean Redo Triage** | Standalone single images are transferred directly into `redo_root/` as files (no redundant single-file subfolders); multi-file PBR sets are transferred into dedicated subfolders (`redo_root/<SetName>/...`). |
| **All-or-Nothing Transfer** | Atomic staging (`.incoming_*`), collision safety (`_2`, `_3`), and support for both Copy and Move modes. |
| **Zero Top Chrome & Max View** | Maximizes viewing area for texture review. Hit `F` or `Tab` to enter Max View (collapses sidebars) and `S` to toggle the Folders sidebar. |
| **Session Persistence** | Remembers last-used input and redo directories across launches, and resumes exactly where you left off. |

## Controls & Shortcuts

| Key | Action |
|---|---|
| **Left / Right** | Previous / Next texture set |
| **Space / Enter** | Mark OK (seamless) & advance |
| **R** | Mark Redo (transfer to Redo folder) & advance |
| **G** | Toggle seam boundary guide lines |
| **F / Tab** | Toggle Max View (focus on canvas) |
| **S** | Toggle Folders sidebar |
| **0** | Fit image to viewport |
| **1** | 100% 1:1 pixel size |
| **Mouse Wheel** | Zoom in / Zoom out |
| **Left Drag** | Pan canvas |

## Dependencies

- **customtkinter** (`pip install customtkinter`)
- **Pillow** (`pip install pillow`)

## Verify

```powershell
python -m tools.seamless_checker.test_smoke
```
