# prompt-coach

**Your personal prompting analyst. Runs locally. Zero data export.**

A CLI tool that reads your conversation history from local session stores
(Hermes SQLite, OpenWebUI, etc.), analyses your personal prompting patterns
via a local LLM, and produces coaching insights. Zero data ever leaves your
machine.

## Quick start

```bash
uv sync
uv run prompt-coach discover
uv run prompt-coach report
```

## Commands

| Command | Description |
|---------|-------------|
| `discover` | Find all available session stores on your machine |
| `report` | Generate a coaching report from recent sessions |
| `stats` | Quick overview of your prompting metrics |
| `query` | Ask a natural-language question about your prompt history |
| `import` | Import external session data (JSON) |
| `serve` | Start a read-only HTTP API |

## How it works

1. **Discover** — finds your Hermes session store, OpenWebUI chats, etc.
2. **Extract** — reads your actual prompts (not templates, not responses)
3. **Analyse** — uses a local LLM (Ollama) to cluster topics, detect patterns,
   and compute style metrics
4. **Report** — produces a markdown coaching report with insights

## Integration

Adjacent module to [llm-api](https://github.com/aawobdev/llm-api). Can
ingest usage logs from the gateway for enriched analytics, and can route
analysis through the gateway's model catalog.

## Configuration

See `~/.config/prompt-coach/config.toml` after first run, or set:
- `PROMPT_COACH_API_BASE` — local LLM endpoint (default: `http://192.168.1.123:11434/v1`)
- `PROMPT_COACH_MODEL` — analysis model (default: `qwen3-coder:30b`)