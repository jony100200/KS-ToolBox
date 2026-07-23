# Performance Budgets

Measurements below were recorded on 2026-07-22 on the current Windows machine.
They are evidence for this revision, not universal hardware guarantees.

## Current measurements

| Metric | Measured | Command |
|---|---:|---|
| Ready-to-mainloop, measured range; latest 157 ms | 99–197 ms | `python benchmarks/measure_startup.py` |
| Discovery + shell import; latest 28 ms | 21–36 ms | same |
| Optional AI/numeric modules at startup | none | same |
| Python source after this slice | 112 files / 16,490 lines | bounded repository scan |
| Durable runner, 1,000 no-op items | 90.05–115.59 ms / 8,651–11,106 items/s; latest 92.97 ms / 10,756 items/s | `measure_batch_core.py` |
| Job identity, 1,000 path/stat inputs | 95.43–122.26 ms | same |
| Completed-job reuse, 1,000 items | 5.19–6.22 ms / zero executor calls | same |
| SQLite checkpoint, 1,000 items | 270,336 bytes | same |
| Dataset split, 200 image-caption pairs | 722.3 → 910.4 ms (+188.0 ms, +26.0%); exact output reuse validation 508.5 ms | paired five-run in-process benchmark |
| Shell queue integration | PASS for thirteen image/audio/video/document/material/archive/audit/dataset workflows, grouped reuse/repair, and history UI | `python benchmarks/check_queue_flow.py` |
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
