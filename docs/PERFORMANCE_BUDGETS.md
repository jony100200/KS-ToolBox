# Performance Budgets

Measurements below were recorded on 2026-07-22 on the current Windows machine.
They are evidence for this revision, not universal hardware guarantees.

## Current measurements

| Metric | Measured | Command |
|---|---:|---|
| Ready-to-mainloop, measured range; latest 112 ms (five-run median 113 ms) | 99–197 ms | `python benchmarks/measure_startup.py` |
| Discovery + shell import; latest 27 ms (five-run median 27 ms) | 21–36 ms | same |
| Optional AI/numeric modules at startup | none | same |
| Python source after this slice | 113 files / 19,459 lines | bounded repository scan |
| Durable runner, 1,000 no-op items | 90.05–115.59 ms / 8,651–11,106 items/s; latest 93.56 ms / 10,689 items/s | `measure_batch_core.py` |
| Job identity, 1,000 path/stat inputs | 88.22–122.26 ms; latest 96.49 ms | same |
| Completed-job reuse, 1,000 items | 5.18–6.22 ms; latest 5.47 ms / zero executor calls | same |
| SQLite checkpoint, 1,000 items | 270,336 bytes | same |
| Dataset split, 200 image-caption pairs | 722.3 → 910.4 ms (+188.0 ms, +26.0%); exact output reuse validation 508.5 ms | paired five-run in-process benchmark |
| Alpha Doctor deterministic 512² cutout | 10.6 → 11.2 ms (+0.6 ms, +5.6%) after settings/source/collision guards | paired seven-run in-process benchmark |
| Alpha Doctor model integrity | 64 MiB first verification 84.7 ms; unchanged session-cache check 0.253 ms | synthetic local model file |
| Alpha Doctor durable output | 12.3 → 15.4 ms (+3.1 ms, +25.5%) for staged decode/hash/coverage validation; exact reuse validation 2.8 ms | paired seven-run 512² cutout benchmark |
| Texture Renderer staged publication | 100 × 16 KiB outputs: direct 23.9 ms → isolated/atomic 71.5 ms (+47.6 ms, +199.0%) | paired five-run stubbed Substance export; external render time excluded |
| Texture Renderer process control | trivial child launch 35.5 → 36.0 ms (+0.4 ms); sleeping process cancelled in 297.0 ms; stdout/stderr RAM tails capped at 1 MiB each | paired nine-run process benchmark + owned-tree cancellation |
| Texture Renderer discovery | 2,000 files / 500 projects: 9.8 → 20.0 ms (+10.2 ms) for case-correct bounded/cancellable traversal | paired five-run filesystem benchmark |
| Texture Renderer durable artifacts | 100 × 16 KiB staged outputs: 71.5 → 581.3 ms (+509.8 ms, +713% or 5.10 ms/output) with SHA-256 provenance; exact reuse validation 53.0 ms; atomic manifest 1.6 ms | paired seven-run local filesystem benchmark; external render time excluded |
| To SVG validated artifact | 26,829-byte / 1,002-element stub: legacy staged write 0.47 ms → bounded XML/SHA-256 validated write 9.56 ms (+9.09 ms); exact reuse validation 1.83 ms | paired 21-run local filesystem benchmark; native trace time excluded |
| Sprite detection, 1,024² / 100 components | 124.37 → 149.61 ms (+25.24 ms, +20.3%); Python traced peak 10.16 → 2.13 MiB (−79.0%) | paired five-run runtime benchmark; one-run `tracemalloc` peak |
| Sprite GIF validation, 48 × 64² frames | 17.48 → 25.91 ms (+8.43 ms, +48.2%) with full frame decode/read-back before commit | paired five-run in-process benchmark |
| Shell queue integration | PASS for sixteen image/vector/audio/video/document/material/archive/audit/dataset/external-render/hybrid workflows, exact reuse/repair, provenance repair, and history UI | `python benchmarks/check_queue_flow.py` |
| Runtime dependency added by batch core | 0 | SQLite is Python standard library |
| Runtime dependency added by job queue | 0 | threading/heapq are Python standard library |

The original startup assertion incorrectly classified Pillow as optional-heavy;
CustomTkinter necessarily imports Pillow. The benchmark now gates actual
optional numeric/AI stacks and passes. No startup-speed improvement is claimed.

## Budgets

| Area | Gate |
|---|---|
| Ready-to-mainloop | ≤250 ms on the reference machine; investigate >10% regression |
| Discovery/shell import | ≤50 ms for the current 17 tools |
| Idle optional systems | no model, CUDA, FFmpeg, Blender, or SQLite store opened |
| Idle CPU | no polling worker or background service |
| Batch checkpoint overhead | ≥5,000 item transitions/s for trivial local work |
| Recovery reuse | zero tool-executor calls for a completed identical job |
| Persistence growth | bounded and approximately linear in item/result metadata |
| Dependencies | no addition without purpose, size, startup, license, and alternative review |

## Required before/after evidence

Record package size, startup, idle RAM/CPU/process/thread count, representative
tool throughput, peak memory, temporary disk, cache/reuse rate, recovery, and
output-quality checks for significant changes. Native or AI work also records
VRAM, transfer, model load, and inference calls. Do not infer package size from
source size; run the package benchmark on a built artifact.
