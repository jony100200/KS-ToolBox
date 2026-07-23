# KS ToolBox verification and benchmarks

Run checks from the repository root with the dedicated Toolbox environment:

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py -m benchmarks.check_docs
& $py benchmarks\run_all_smoke.py
```

The documentation check is dependency-free: it validates local Markdown links,
requires one guide for each first-party tool, and rejects selected stale claims
from current operator/developer pages. The smoke suite discovers every tool;
optional dependencies may skip their optional leg, but an installed path that
breaks must fail.

| Command | Purpose |
|---|---|
| `python -m benchmarks.check_docs` | Documentation links, guide coverage, current-state wording |
| `python benchmarks/run_all_smoke.py` | Every tool's standalone real-output contract |
| `python -m benchmarks.check_ui` | Construct every discovered panel without processing files |
| `python -m benchmarks.check_shell_navigation` | Catalog/search/navigation and minimum-size shell behavior |
| `python -m benchmarks.measure_startup` | Import/discovery readiness and lazy-load boundary |
| `python -m benchmarks.measure_shell_runtime` | First paint, idle working set, CPU, thread, and lazy-panel evidence |
| `python benchmarks/measure_batch_core.py` | Queue transitions, checkpoint size, and reuse evidence |
| `powershell -File benchmarks/measure_package.ps1` | Built portable-artifact size and file count |

The dated figures in [Performance Budgets](../docs/PERFORMANCE_BUDGETS.md) are
baseline evidence, not release claims. Re-run the relevant measurement after a
meaningful change and record the machine, command, and result before claiming
an improvement.
