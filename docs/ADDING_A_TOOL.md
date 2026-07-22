# Adding a First-Party Tool

Add one folder under `tools/`; do not edit `main.py`, the shell, or discovery.

```text
tools/example_tool/
  engine.py
  panel.py
  tool.py
  __init__.py
  test_smoke.py
  README.md
```

## Required sequence

1. Define the smallest `ToolMeta` and lazy `build_panel` in `tool.py`.
2. Implement pure/headless processing in `engine.py`; return visible result or
   error-envelope values and use atomic outputs.
3. Extend `BaseBatchPanel` for batch tools. Keep widgets and UI formatting in
   `panel.py`; never put image, codec, archive, or AI algorithms there.
4. Start in preview/dry-run mode. Destructive actions require preview, explicit
   confirmation, recoverable behavior, and a manifest.
5. Add a real sample/output smoke test. Missing optional dependencies must print
   `SKIP` and return success; broken installed behavior must fail.
6. Run the standalone smoke, real UI construction check, and full smoke suite.

## Minimal registration example

```python
from toolbox.tool import ToolMeta

class ExampleTool:
    meta = ToolMeta(
        id="example_tool",
        title="Example Tool",
        icon="…",
        subtitle="One precise job",
    )

    def build_panel(self, parent, services):
        from .panel import ExamplePanel
        return ExamplePanel(parent, queue_service=services.queue)

TOOL = ExampleTool()
```

The panel import remains inside `build_panel`; discovery must not import heavy
or optional dependencies.

New batch tools should implement `_build_submission()` and return a
`QueueSubmission`; the shared panel submits it to `services.queue`. Keep the
legacy `_work` loop only when migrating an already verified tool incrementally.
Use `prepare_batch_completion()` for terminal-record decoding, manifest errors,
and the atomic JSON report instead of copying finalization code into the panel.

## Preset example

Presets are currently tool-owned options, not a shared file schema. Until a
shared preset store is implemented, document a reproducible preset as data:

```toml
schema_version = 1
tool_id = "image_rescale"
tool_version = "1"
name = "Marketplace thumbnails"
[settings]
mode = "fit_inside"
fit_w = 1024
fit_h = 1024
allow_upscale = false
resample = "auto"
```

Do not ship this as executable configuration until a strict parser and version
validator exist.
