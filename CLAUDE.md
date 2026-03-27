# infra-agent — Orchestration Rules

## Agent Roles

- **Orchestrator (Sonnet):** Plans phases, routes tasks to subagents, runs tests, verifies integration, writes documentation. Does NOT write production code directly.
- **python-coder (Sonnet subagent):** Writes Python implementation and tests per spec. Iterates until tests pass. Never modifies sanitizer regex patterns without orchestrator approval.
- **python-reviewer (Opus subagent):** Reviews every coder output before orchestrator accepts it.

## Quality Gates

- `pytest` must pass before moving to the next phase.
- `ruff check src/ tests/` must pass before moving to the next phase.
- `mypy` must pass before moving to the next phase.

## Phase Rules

- Never modify Phase N+1 code while in Phase N.
- Sanitizer implementation (Phase 2) requires explicit reviewer approval before Phase 3 begins. This is a security boundary.
- `tasks/lessons.md` updated at end of each phase.

## Code Conventions

- Python 3.12+ (use `match` statements, `type` aliases, `X | Y` union types).
- Type hints everywhere. `mypy --strict` must pass. No `Any` without justification.
- All public functions must have docstrings.
- `dataclass` or `pydantic.BaseModel` for data structures, not plain dicts.
- `async/await` for all I/O (stdin reading, HTTP calls, TUI rendering).
- `httpx.AsyncClient` for HTTP, not `requests`.
- `pytest` with `pytest-asyncio` for async tests.
- `ruff` for linting and formatting.
- No global state. Configuration passed explicitly.
- Errors as exceptions with custom exception hierarchy, not return codes.
- Imports organized: stdlib, third-party, local (enforced by ruff isort).

## Escalation

- If `pytest` fails after reviewer approval and one fix attempt, escalate to orchestrator. Do not loop indefinitely.
