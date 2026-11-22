## Summary

- What changed and why?

## Checklist

- [ ] Pure logic stays in `engine.py` (no UI imports); thin UI in `panel.py`
- [ ] Tool smoke test passes (`python -m tools.<name>.test_smoke`)
- [ ] Core unit tests pass (`python -m unittest discover -s tests`)
- [ ] No hardcoded local paths, secrets, or personal artifacts
