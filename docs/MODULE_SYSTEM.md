# First-Party Module System

KS ToolBox currently discovers first-party Python packages under `tools/`.
This proven contract remains authoritative.

```text
tools/<name>/
  engine.py       headless domain/application logic
  panel.py        thin CustomTkinter presentation
  tool.py         metadata plus lazy panel factory
  __init__.py     exports module-level TOOL
  test_smoke.py   standalone real-output release test
  README.md       formats, options, and dependencies
```

`toolbox.discovery.discover()` imports each package independently. A broken
optional module is reported to stderr and skipped; it cannot prevent startup.
Heavy dependencies must be imported inside panel construction or execution.

## Current metadata schema

`ToolMeta` is the typed, code-native manifest:

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `id` | string | yes | Stable unique slug |
| `title` | string | yes | Sidebar display name |
| `icon` | string | yes | Shared icon glyph |
| `subtitle` | string | no | One-line purpose |
| `os_support` | tuple | no | `windows`, `linux`, `macos` |

This is intentionally smaller than the proposed TOML schema because the shell
does not yet consume settings schemas, resource declarations, or validators.
Adding unused manifest fields would be speculative.

## Future data manifest gate

A typed TOML manifest may replace or supplement `ToolMeta` only after two tools
need the same new metadata. A possible compatible shape is:

```toml
schema_version = 1
id = "image.resize"
name = "Image Rescale"
category = "Images"
executor = "tools.image_rescale.engine:process"
supports_preview = true
supports_batch = true
input_types = ["image"]
output_types = ["image"]
```

It may describe metadata and configuration only. It must never become an
interpreted programming language. Unknown schema versions fail visibly.

## Compatibility and removal

- Duplicate tool IDs fail registration.
- The shell constructs panels only when selected and caches built panels.
- Removing a module folder removes the tool without a shell edit.
- Tools never import unrelated tool internals.
- Public/untrusted plugin loading is out of scope until this first-party contract
  has remained stable across several migrated modules.
