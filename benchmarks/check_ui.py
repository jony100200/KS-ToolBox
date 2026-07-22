"""Real-UI construction check for every KS-ToolBox tool panel.

This does NOT merely import tool modules — it drives the *actual production UI
path*: it builds the real ToolBoxShell, then for every discovered tool it runs
the shell's own `_select` → `_safe_build` → `tool.build_panel()` → panel
`__init__` (which constructs every option widget), forcing widget realization
with `update()`.

Two failure modes are detected per tool:

  1. build failure — the panel `__init__` raised. `_safe_build` swallows that and
     substitutes an in-panel "unavailable" card (a plain CTkFrame) instead of
     re-raising, so we detect that fallback frame and treat it as a FAILURE,
     capturing the reason text it displays.
  2. settings-wiring failure — a successfully-built panel's `_collect_options`
     raises when called with its default widget values. (A returned ``None`` is
     acceptable: it means a required field like an output folder is empty — that
     is validation, not a bug.)

Exit code is non-zero if any panel failed to construct or any `_collect_options`
raised.

Run:
    $env:PYTHONPATH="D:\\KSAppDev\\KS-ToolBox"
    uv run --no-project --python 3.12 --with customtkinter --with pillow \
        --with numpy python benchmarks\\check_ui.py
"""
from __future__ import annotations

import sys
import traceback


def _iter_labels(widget):
    """Yield the text of every CTkLabel/Label in a widget subtree."""
    try:
        children = widget.winfo_children()
    except Exception:
        return
    for child in children:
        try:
            text = child.cget("text")
        except Exception:
            text = None
        if isinstance(text, str) and text:
            yield text
        yield from _iter_labels(child)


def _fallback_reason(panel) -> str | None:
    """If `panel` is the shell's error/'unavailable' fallback frame, return the
    reason text it shows; otherwise return None.

    The real panels are all CTkFrame *subclasses* (VideoCompressorPanel, etc.);
    the fallback that `_safe_build` builds is a bare `ctk.CTkFrame` containing an
    '<Title> — unavailable' card. We confirm with the label text so a genuine
    panel could never be misread as a failure.
    """
    import customtkinter as ctk

    labels = list(_iter_labels(panel))
    joined = "\n".join(labels)
    lowered = joined.lower()
    looks_fallback = "unavailable" in lowered or "could not start" in lowered
    is_bare_frame = type(panel) is ctk.CTkFrame
    if not (looks_fallback and is_bare_frame):
        # Also flag the odd case of a bare frame with no real content, defensively.
        if is_bare_frame and not any(
            type(w).__name__ not in ("CTkFrame",) for w in _walk(panel)
        ):
            return "built panel is a bare CTkFrame with no widgets (suspected silent failure)"
        return None
    # Prefer the detailed 'This tool could not start: ...' body line.
    for text in labels:
        if "could not start" in text.lower():
            return text.replace("\n", " ").strip()
    return joined.replace("\n", " ").strip() or "unavailable (no reason text)"


def _walk(widget):
    try:
        for child in widget.winfo_children():
            yield child
            yield from _walk(child)
    except Exception:
        return


def main() -> int:
    try:
        import customtkinter  # noqa: F401
    except Exception as ex:  # pragma: no cover
        print(f"FATAL: customtkinter not importable: {type(ex).__name__}: {ex}")
        return 2

    try:
        from toolbox.shell import ToolBoxShell
        from toolbox.discovery import discover
    except Exception as ex:
        print("FATAL: could not import the ToolBox shell/discovery:")
        traceback.print_exc()
        return 2

    registry = discover()
    tools = registry.all()
    tool_ids = [tl.meta.id for tl in tools]
    print(f"Discovered {len(tool_ids)} tools: {', '.join(tool_ids)}\n")

    try:
        app = ToolBoxShell(registry)
    except Exception as ex:
        print("FATAL: could not create the ToolBoxShell (no display?):")
        traceback.print_exc()
        return 2

    # Hide the window immediately so nothing flashes on screen.
    try:
        app.withdraw()
    except Exception:
        pass

    results: list[dict] = []
    try:
        for tid in tool_ids:
            rec = {"id": tid, "build": "ok", "options": "n/a", "detail": ""}
            # --- construct the panel via the real shell path -----------------
            try:
                app._select(tid)
                app.update()  # force widget realization (runs __init__ fully)
            except Exception:
                rec["build"] = "fail"
                rec["detail"] = "shell._select raised:\n" + traceback.format_exc()
                results.append(rec)
                continue

            panel = app._panels.get(tid)
            if panel is None:
                rec["build"] = "fail"
                rec["detail"] = "no panel was cached after _select (shell never built it)"
                results.append(rec)
                continue

            reason = _fallback_reason(panel)
            if reason is not None:
                rec["build"] = "fail"
                rec["detail"] = f"fallback/unavailable panel shown: {reason}"
                results.append(rec)
                continue

            # --- exercise settings wiring ------------------------------------
            collect = getattr(panel, "_collect_options", None)
            if not callable(collect):
                rec["options"] = "n/a"
            else:
                try:
                    out = collect()
                    rec["options"] = "none" if out is None else "ok"
                except Exception:
                    rec["options"] = "raised"
                    rec["detail"] = "_collect_options raised:\n" + traceback.format_exc()
            results.append(rec)
    finally:
        try:
            app.destroy()
        except Exception:
            pass

    # ---- report -------------------------------------------------------------
    print("Per-tool construction table")
    print("-" * 72)
    built_ok = 0
    any_failure = False
    for rec in results:
        build_ok = rec["build"] == "ok"
        opts_ok = rec["options"] != "raised"
        tool_ok = build_ok and opts_ok
        if build_ok:
            built_ok += 1
        if not tool_ok:
            any_failure = True
        status = "OK  " if tool_ok else "FAIL"
        print(
            f"[{status}] {rec['id']:<20} build={'ok' if build_ok else 'fail':<4}  "
            f"options={rec['options']}"
        )
    print("-" * 72)
    print(f"{built_ok}/{len(results)} panels constructed")

    # ---- failure detail -----------------------------------------------------
    failures = [r for r in results if r["build"] != "ok" or r["options"] == "raised"]
    if failures:
        print("\nFAILURES\n" + "=" * 72)
        for rec in failures:
            print(f"\n### {rec['id']}  (build={rec['build']}, options={rec['options']})")
            print(rec["detail"] or "(no detail captured)")

    return 1 if any_failure else 0


if __name__ == "__main__":
    sys.exit(main())
