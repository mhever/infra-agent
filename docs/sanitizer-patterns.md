# Sanitizer Patterns

The sanitizer uses [detect-secrets](https://github.com/Yelp/detect-secrets) (by Yelp) as the primary detection engine, with custom post-processors for patterns detect-secrets does not cover.

## Detection Layers

### Layer 1: detect-secrets plugins

| Category | Plugins | Config Flag |
|----------|---------|-------------|
| AWS | `AWSKeyDetector` | `enable_aws` |
| Azure | `AzureStorageKeyDetector` | `enable_azure` |
| Private keys | `PrivateKeyDetector` | `enable_private_keys` |
| High entropy | `Base64HighEntropyString`, `HexHighEntropyString` | `enable_high_entropy` |
| Keywords | `KeywordDetector` | `enable_keywords` |
| Common tokens | `GitHubTokenDetector`, `GitLabTokenDetector`, `SlackDetector`, `StripeDetector`, `SendGridDetector`, `JwtTokenDetector`, `DiscordBotTokenDetector`, `NpmDetector`, `PypiTokenDetector`, `TelegramBotTokenDetector`, `BasicAuthDetector` | `enable_common_tokens` |

Default entropy limit: **4.5** for Base64, **3.0** for Hex (configurable via `entropy_limit`).

### Layer 2: Custom post-processors

These run after detect-secrets to catch patterns it misses:

| Pattern | Regex | Tag |
|---------|-------|-----|
| Private key blocks | `-----BEGIN...-----` through `-----END...-----` | `[REDACTED:private_key]` |
| URL passwords | `://user:password@host` | `[REDACTED:url_password]` |
| Azure SAS tokens | `sig=...` in query strings | `[REDACTED:azure_sas_token]` |
| Azure AccountKey | `AccountKey=...` in connection strings | `[REDACTED:azure_account_key]` |
| Keyword values | `password=`, `secret=`, `token=`, `api_key=`, etc. | `[REDACTED:secret_keyword]` |

## Redaction Tags

Each redacted value is replaced with `[REDACTED:type]` where `type` identifies what was caught. This preserves context for the LLM (it knows an AWS key was here, not just "something").

## Known Limitations

1. **Context-after overlap**: If a secret appears within the context-after window of a previous error, it is still sanitized (the sanitizer runs on the full context text).
2. **Entropy false positives**: High-entropy detection may flag legitimate base64 values (e.g., resource IDs, hashes). Tune `entropy_limit` if this is a problem.
3. **Novel secret formats**: Any secret format not covered by detect-secrets plugins or the custom post-processors will pass through. The entropy detectors provide a safety net for unknown formats with high randomness.
4. **Secrets split across lines**: Handled for private keys (multi-line block detection). Other multi-line secrets are not explicitly handled.

## Configuration

All categories can be enabled/disabled via `SanitizerConfig`:

```python
from infra_agent.sanitizer import SanitizerConfig, sanitize

# Default: all detectors enabled
result = sanitize(text)

# Custom: disable high entropy (too many false positives)
config = SanitizerConfig(enable_high_entropy=False)
result = sanitize(text, config=config)

# Custom: lower entropy threshold (catch more)
config = SanitizerConfig(entropy_limit=3.5)
result = sanitize(text, config=config)
```
