# KS ToolBox — benchmarks

Reproducible baseline measurements backing `docs/KS_TOOLBOX_OPTIMIZATION_AUDIT.md`.
Re-run after each optimization and record before/after in the plan doc. **No
improvement is claimed without the paired measurement.**

Run from the repo root. `$py` = the app venv's python (`.venv\Scripts\python.exe`);
`$uv` = uv on PATH (dependency measurements use an isolated temp target so nothing
touches the app venv).

Run package-aware Python checks with module syntax, for example
`$py -m benchmarks.check_ui`; direct script syntax does not place the repository
root on `sys.path` for every verifier.

| Script | Measures | Notes |
|---|---|---|
| `measure_repo.ps1` | file count, LOC, source/asset/bin sizes | pure filesystem |
| `measure_startup.py` | import + discovery time, models-at-startup | headless (no window) |
| `measure_batch_core.py` | durable item transitions, checkpoint size, completed-job reuse | 1,000 no-op local items |
| `check_queue_flow.py` | real CustomTkinter submission across fifteen durable image/audio/video/document/material/archive/audit/dataset/external-render/hybrid workflows, exact reuse/repair, provenance repair, and history rendering | uses temporary state and generated media |
| `check_ui.py` | construction of every discovered tool panel | no processing |
| `run_all_smoke.py` | every tool's real standalone smoke contract | optional dependencies skip cleanly |
| `measure_deps.ps1` | installed footprint of base vs Clean Cutout stacks | uv `--target` into temp, then du |
| `measure_ai_calls.ps1` | every AI / network call site in the source | static sweep |
| `measure_package.ps1` | built PyInstaller dist size + file count | run after a build |

## Original baseline (2026-07-22)

| Metric | Value |
|---|---|
| Python files / LOC | 34 / 2,908 |
| Startup (headless-ready) | 232 ms (ctk 198 + discover/shell 34) |
| Base install footprint | 1.3 MB |
| Clean Cutout install footprint | 358.6 MB (≈237 MB recoverable) |
| Bundled ffmpeg + ffprobe | 462 MB |
| AI call sites | 1 (specialist ONNX, `clean_cutout`) |
| Network call sites | 0 |
| Models loaded at startup | 0 |

The original table predates the 17-tool audit and durable batch-core slice. Use
`docs/PERFORMANCE_BUDGETS.md` for current measured ranges and rerun the scripts
before making an optimization claim.
