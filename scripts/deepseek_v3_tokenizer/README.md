# DeepSeek V3 Tokenizer (External Asset)

## Status: Not tracked in git

`tokenizer.json` is **not tracked** in this repository because it is a large
external tokenizer vocabulary file (~7.5 MB) distributed separately by DeepSeek.

## When is it needed?

Only for **optional real-trace token accounting** in Tier 2 / Tier 3 experiments:

- `scripts/diagnose_neutral_real_llm_results.py` — per-session token diagnostics
- Any script that calls `count_tokens()` from `deepseek_tokenizer.py`

The following do **not** require this file:

- Unit tests (`go test ./...`)
- Mock-runtime experiments (Tier 1)
- PlanGate-R controlled recovery tests
- All baseline comparisons that use mock LLM

## How to obtain

Download the DeepSeek V3 tokenizer from one of the following sources:

1. **Hugging Face** — search for the official DeepSeek-V3 tokenizer repository
   and download `tokenizer.json` from the model card.
2. **DeepSeek official release** — check [https://github.com/deepseek-ai](https://github.com/deepseek-ai)
   for the corresponding model release that includes the tokenizer vocabulary.

After downloading, place the file at:

```
scripts/deepseek_v3_tokenizer/tokenizer.json
```

No other configuration is needed; `deepseek_tokenizer.py` will load it automatically.

## If unavailable

If `tokenizer.json` is not present, skip or comment out any script that imports
`deepseek_tokenizer.py`. The rest of the experiment suite will work normally.
