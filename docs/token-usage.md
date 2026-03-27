# Token Usage

Estimated LLM token consumption for infra-agent analysis calls.

## How It Works

When an error is detected, infra-agent sends the sanitized error context (typically 20 lines) plus a system prompt to the configured LLM. The response includes a diagnosis, root cause, and suggested fix.

## Estimated Token Counts

| Component | Tokens (approx) |
|-----------|-----------------|
| System prompt | ~100 |
| User prompt (tool type + error context, 20 lines) | 200-500 |
| LLM response (diagnosis + fix) | 300-800 |
| **Total per analysis** | **600-1400** |

## Cost Estimates

Costs depend on the configured provider and model.

### DeepSeek V3 via OpenRouter (default)

- Input: $0.14 / 1M tokens
- Output: $0.28 / 1M tokens
- **~$0.0001-0.0003 per analysis** (effectively free)

### Anthropic Claude Haiku

- Input: $0.80 / 1M tokens
- Output: $4.00 / 1M tokens
- **~$0.001-0.004 per analysis**

## Tracking

Each `Analysis` object includes `tokens_used` and `model` fields. When saving an analysis (press `s` in the TUI), token count and model are included in the output file.

## Reducing Token Usage

- Lower `INFRA_AGENT_CONTEXT_LINES` (default 20) to send fewer context lines per error
- Use a cheaper model (DeepSeek V3 is extremely cost-effective for this use case)
