# Lessons Learned

Updated at the end of each phase with Python-specific observations, library quirks, and bugs found by reviewer.

---

## Phase 0 — Scaffold

- `pyproject.toml` replaces the old multi-file setup (`setup.py`, `requirements.txt`, etc.)
- `SettingsConfigDict` from pydantic-settings is preferred over plain dict for `model_config` (better IDE support)
- `.gitignore` was missing — must be created early to avoid committing `.venv/`, `__pycache__/`, tool caches

---

## Phase 1 — Reader + Error Detection

- **Regex false positives are a real risk.** Reviewer caught that `\btimeout\b`, `\b403\b`, `\b404\b` patterns were too broad — they'd match normal config values and port numbers. Patterns must require error context (e.g., "timed out", "403 Forbidden").
- **Tool detection patterns must use word boundaries.** `r"packer"` matches "unpacker". Always use `\b` or anchor to specific output formats like `==> packer`.
- **`asyncio.StreamReader` from stdin** uses `loop.connect_read_pipe()` + `StreamReaderProtocol` pattern. Not obvious but works correctly. Transport must be closed in `finally`.
- **`.rstrip("\r\n")` is idiomatic** for stripping line endings — preferred over chained `.rstrip("\n").rstrip("\r")`.
- **Stateful reader instances** should reset internal state (like `_detected_tool`) at the start of each public method call to support reuse.
- **Edge-case tests matter:** error on first line (empty context_before), error on last line (truncated context_after), and consecutive errors should all be explicitly tested.
- **Known limitation:** errors within the context-after window of a previous error are captured as context, not separate events. Acceptable for now, tracked for future improvement.
