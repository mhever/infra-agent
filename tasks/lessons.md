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

---

## Phase 2 — Sanitizer

- **Use battle-tested libraries for security-critical code.** Original plan was homegrown regex — switched to `detect-secrets` (Yelp). 28+ detectors, entropy-based detection, maintained by a security team. Don't reinvent this.
- **detect-secrets `secret_value` is often just a prefix.** GitHub tokens return `"ghp"`, not the full token. Using `str.replace(secret_value, tag)` leaves the actual secret visible. Must extend to the full token boundary with `re.escape(prefix) + r"\S*"`.
- **Private keys are multi-line.** detect-secrets only flags the `BEGIN` line. Post-processing needed to redact the entire BEGIN-through-END block including the key body.
- **detect-secrets doesn't catch everything.** Missing: URL passwords (`://user:pass@host`), Azure SAS tokens (`sig=...`), Azure AccountKey in connection strings. Custom post-processors needed on top.
- **`secret_value=None` is a real code path.** Some detectors don't expose the matched value. Must handle this — either redact conservatively or skip (don't emit a false Redaction).
- **Temp files need private directories.** `NamedTemporaryFile` in `/tmp` is readable by other users on some systems. Use `tempfile.mkdtemp()` + cleanup in `finally`.
- **Frozen dataclasses with mutable fields are misleading.** `frozen=True` prevents attribute reassignment but doesn't prevent mutating a `list`. Use `tuple` for truly immutable collections.
- **Tests must verify full secret removal, not just partial.** A test checking `"ghp_ABC..." not in output` passes even when 95% of the token leaks. Check the body/suffix too.

---

## Phase 3 — Analyzer (LLM Integration)

- **`httpx.AsyncClient` should be reused across calls.** Creating a new client per request wastes connections. Make the class an async context manager that owns a single client. Tracked for Phase 4 integration.
- **`httpx.MockTransport` is the clean way to test async HTTP.** No patching needed — create a mock handler function and pass it as the transport. Works naturally with async/await.
- **Don't use `assert` for runtime validation in production code.** Python strips `assert` with `-O` flag. Use explicit `if not ...: raise` instead. Not a bug here (exceptions still caught), but a code smell.
- **Provider abstraction with Protocol works well.** OpenRouter (OpenAI-compatible) and Anthropic have different request/response formats. Protocol class + two implementations keeps it clean without inheritance.
- **Response parsing from LLMs is inherently fuzzy.** Heuristic section detection with graceful fallback (sections -> paragraphs -> single blob) is the right approach for MVP. Don't over-engineer structured output parsing.

---

## Phase 4 — TUI

- **`RichLog` needs `max_lines` to prevent unbounded memory.** Without it, a long-running pipeline (millions of lines) will eat all available memory. Set `max_lines=10_000` or similar.
- **Don't re-append lines that were already displayed.** When every line is yielded for log display AND error context is re-appended on error detection, you get duplicate lines. The error handler should trigger analysis, not re-display already-visible lines.
- **Textual workers are the correct pattern for background I/O.** `run_worker()` for the stdin pipeline, separate non-exclusive workers for each LLM analysis call. Post custom `Message` subclasses back to the main thread to update widgets.
- **`read_stdin=False` flag for testing.** The TUI constructor should accept a flag to disable the stdin pipeline so `App.run_test()` works without real stdin.
- **Analyzer as async context manager works well.** Single `httpx.AsyncClient` created in `__aenter__`, closed in `__aexit__`, reused across retries.

---

## Phase 5 — Polish + Release

- **README should lead with the problem, not the tech.** "No copy-pasting logs into a browser" resonates more than "split-pane TUI built with Textual."
- **Configuration table in README is essential.** Every env var, its default, and a one-line description. Users will copy-paste from this.
- **Document how to add new tools.** Five concrete steps in supported-tools.md. Makes the project feel extensible, not finished.
- **Token cost estimates matter for portfolio pieces.** Showing "$0.0001 per analysis" demonstrates that you thought about operational costs, not just features.
