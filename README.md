# infra-agent

AI-powered infrastructure error diagnosis in your terminal. Pipe any infrastructure tool's output through `infra-agent` and get real-time error detection, automatic secret sanitization, and LLM-powered root cause analysis in a split-pane TUI.

```
terraform apply 2>&1 | infra-agent
```

No copy-pasting logs into a browser. No context switching. The tool watches, catches errors, strips secrets, asks the AI, and shows the answer --- all in one screen.

## Features

- **Real-time error detection** -- regex-based pattern matching for Terraform, Packer, Ansible, and generic error formats
- **Automatic secret sanitization** -- powered by [detect-secrets](https://github.com/Yelp/detect-secrets) with custom post-processors for URL passwords, Azure SAS tokens, private key blocks, and more. Secrets are stripped *before* any data leaves your machine.
- **LLM-powered diagnosis** -- sends sanitized error context to an LLM for root cause analysis and fix suggestions
- **Split-pane TUI** -- logs scroll on the left (60%), analysis renders on the right (40%), with keybindings for quit and save
- **Provider-agnostic** -- supports DeepSeek via OpenRouter (default) and Anthropic as LLM providers

## Architecture

```
stdin (piped) --> Reader --> Error Detector --> Sanitizer --> Analyzer (LLM) --> TUI
                    |                                                            |
                    +---------------> TUI (log pane, real-time) <---------------+
```

| Component | Module | Purpose |
|-----------|--------|---------|
| Reader | `reader.py` | Async stdin reader with rolling context buffer |
| Patterns | `patterns.py` | Error detection regex per tool type |
| Sanitizer | `sanitizer.py` | Secret/PII stripping via detect-secrets + custom patterns |
| Analyzer | `analyzer.py` | LLM API client with retry logic and response parsing |
| TUI | `ui/` | Textual split-pane app with log and analysis panes |
| CLI | `cli.py` | Click entry point with pipe detection |
| Config | `config.py` | pydantic-settings for environment variable configuration |

## Installation

Requires Python 3.12+.

```bash
git clone https://github.com/mhever/infra-agent.git
cd infra-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development (includes test/lint tooling):

```bash
pip install -e ".[dev]"
```

## Usage

Pipe any infrastructure command's output:

```bash
# Terraform
terraform plan 2>&1 | infra-agent
terraform apply 2>&1 | infra-agent

# Packer
packer build template.pkr.hcl 2>&1 | infra-agent

# Ansible
ansible-playbook site.yml 2>&1 | infra-agent

# Any command
kubectl apply -f deployment.yaml 2>&1 | infra-agent
```

### Keybindings

| Key | Action |
|-----|--------|
| `q` | Quit |
| `s` | Save latest analysis to `infra-agent-analysis.txt` |

### Version

```bash
infra-agent --version
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `INFRA_AGENT_PROVIDER` | `openrouter` | LLM provider (`openrouter` or `anthropic`) |
| `INFRA_AGENT_API_KEY` | *(required)* | API key for the configured provider |
| `INFRA_AGENT_MODEL` | `deepseek/deepseek-chat` | Model identifier |
| `INFRA_AGENT_CONTEXT_LINES` | `20` | Number of context lines around detected errors |
| `INFRA_AGENT_TIMEOUT` | `30` | LLM API timeout in seconds |

### Example: DeepSeek via OpenRouter

```bash
export INFRA_AGENT_API_KEY="sk-or-..."
export INFRA_AGENT_PROVIDER="openrouter"
export INFRA_AGENT_MODEL="deepseek/deepseek-chat"
terraform apply 2>&1 | infra-agent
```

### Example: Anthropic

```bash
export INFRA_AGENT_API_KEY="sk-ant-..."
export INFRA_AGENT_PROVIDER="anthropic"
export INFRA_AGENT_MODEL="claude-haiku-4-5-20251001"
terraform apply 2>&1 | infra-agent
```

## Sanitizer

The sanitizer runs **before** any data leaves your machine. It uses [detect-secrets](https://github.com/Yelp/detect-secrets) (28+ detectors) plus custom post-processors for:

- AWS access keys and secret keys
- Azure storage keys, connection strings, and SAS tokens
- Private key blocks (BEGIN through END)
- Passwords in URLs (`://user:pass@host`)
- GitHub/GitLab/Slack/Stripe tokens, JWTs
- High-entropy strings (configurable threshold)
- Keyword-based detection (`password=`, `secret=`, `token=`, etc.)

Each redacted value is tagged: `[REDACTED:aws_access_key]`, `[REDACTED:private_key]`, etc., preserving context for the LLM.

See [docs/sanitizer-patterns.md](docs/sanitizer-patterns.md) for full details.

## Supported Tools

| Tool | Detection Method | Patterns |
|------|-----------------|----------|
| Terraform | Version string (`Terraform v...`) | `Error:`, `Failed to`, `on *.tf line`, timeout, HTTP errors |
| Packer | Output prefix (`==> packer`) | `errored`, `Error launching`, `Timeout waiting` |
| Ansible | Play header (`PLAY [`, `TASK [`) | `fatal:`, `FAILED!`, `ERROR!`, `UNREACHABLE!` |
| Generic | Fallback | `FATAL`, `ERROR`, `panic:`, `Traceback`, `Exception` |

See [docs/supported-tools.md](docs/supported-tools.md) for details on adding new tool support.

## Development

```bash
# Run tests
pytest -v

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/

# Type check
mypy
```

All three gates must pass before merging.

## Tech Stack

| Component | Library |
|-----------|---------|
| TUI | [Textual](https://textual.textualize.io/) |
| Terminal styling | [Rich](https://rich.readthedocs.io/) |
| HTTP client | [httpx](https://www.python-httpx.org/) |
| CLI | [Click](https://click.palletsprojects.com/) |
| Config | [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) |
| Secret detection | [detect-secrets](https://github.com/Yelp/detect-secrets) |
| Testing | [pytest](https://docs.pytest.org/) + pytest-asyncio |
| Linting | [Ruff](https://docs.astral.sh/ruff/) |
| Type checking | [mypy](https://mypy-lang.org/) |

## License

MIT
