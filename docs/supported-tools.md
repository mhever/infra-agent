# Supported Tools

infra-agent detects errors from the following infrastructure tools. Tool type is auto-detected from early stdin lines.

## Terraform

**Detection:** Version string like `Terraform v1.x.x` in output.

| Pattern | Matches |
|---------|---------|
| `terraform_error` | `Error:` (case-sensitive, with colon) |
| `terraform_pipe_error` | `\| Error:` (piped through Terraform) |
| `terraform_on_line` | `on *.tf line` (HCL source references) |
| `terraform_failed` | `Failed to` (provider/resource failures) |
| `terraform_timeout` | `timed out`, `timeout exceeded/waiting/error` |
| `terraform_403` | HTTP 403 with context (`Forbidden`, status codes) |
| `terraform_404` | HTTP 404 with context (`Not Found`, status codes) |
| `terraform_quota` | `quota exceeded` |

## Packer

**Detection:** Output lines starting with `==> ` (Packer builder prefix) or `packer v` version string.

| Pattern | Matches |
|---------|---------|
| `packer_errored` | `==> .* errored` |
| `packer_build_errored` | `Build .* errored` |
| `packer_error_launching` | `Error launching source instance` |
| `packer_timeout` | `Timeout waiting for` |

## Ansible

**Detection:** Play header (`PLAY [`, `TASK [`) or `ansible-playbook` / `ansible v` in output.

| Pattern | Matches |
|---------|---------|
| `ansible_fatal` | `fatal:` |
| `ansible_failed` | `FAILED!` |
| `ansible_error` | `ERROR!` |
| `ansible_unreachable` | `UNREACHABLE!` |

## Generic (Fallback)

Used when no specific tool is detected, or as a fallback after tool-specific patterns.

| Pattern | Matches |
|---------|---------|
| `generic_fatal` | `FATAL` (uppercase) |
| `generic_error` | `ERROR` (uppercase, standalone word) |
| `generic_panic` | `panic:` (Go-style panics) |
| `generic_traceback` | `Traceback` (Python tracebacks) |
| `generic_exception` | `Exception` (Java/Python exceptions) |

## Adding New Tools

To add support for a new tool (e.g., `kubectl`, `helm`):

1. Add a new variant to the `ToolType` enum in `src/infra_agent/patterns.py`
2. Add detection patterns (regex) to the tool patterns dict
3. Add auto-detection logic in `detect_tool_type()` (match version strings or characteristic output)
4. Add tests in `tests/test_patterns.py` (positive and negative cases)
5. Add a test fixture in `tests/fixtures/`
