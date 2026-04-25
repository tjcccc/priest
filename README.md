# priest

Pure core library for AI orchestration. Transport-agnostic, host-agnostic, async-first.

`priest` handles single run execution and session continuation. It is designed to be embedded into other applications — CLI apps, web apps, bots, games, etc. The separate `priests` repo provides the CLI and service layer built on top.

## What it does

- Executes a single AI request against a configured provider
- Loads behavior profiles from disk (identity, rules, custom context, memories)
- Persists and continues conversation sessions (SQLite-backed)
- Returns structured responses with usage, latency, and error info

## What it does not do

- No CLI, no HTTP server, no config files required
- No multi-step orchestration or workflow chaining (that belongs in `priests`)
- No hardcoded paths or model preferences
- No response parsing — `response.text` is always the raw string from the model

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

This creates a `.venv` and installs all dependencies in isolation. Dependencies: `pydantic>=2`, `httpx`, `aiosqlite`, `openai>=1.0`, `anyio[trio]`.

## Quick start

```python
import asyncio
from pathlib import Path
from priest import PriestConfig, PriestEngine, PriestRequest
from priest.profile.loader import FilesystemProfileLoader
from priest.providers.ollama_provider import OllamaProvider

async def main():
    engine = PriestEngine(
        profile_loader=FilesystemProfileLoader(Path("profiles/")),
        adapters={"ollama": OllamaProvider()},
    )
    response = await engine.run(PriestRequest(
        config=PriestConfig(provider="ollama", model="qwen3.5:9b"),
        profile="default",
        prompt="Hello.",
    ))
    print(response.text)  # always a raw string — parse it yourself if needed

asyncio.run(main())
```

## Profiles

Profiles live in a directory and define behavior context — identity, rules, custom overrides, and memories. They are model-agnostic.

```
profiles/
  default/
    PROFILE.md    # identity and behavior
    RULES.md      # strict constraints
    CUSTOM.md     # user customization layer
    memories/     # optional memory files (.md or .txt)
    profile.toml  # optional machine-readable metadata
```

A built-in `default` profile is included. Host apps can override it by providing their own `default/` folder.

## Sessions

Sessions persist conversation turns to SQLite. Pass a `SessionRef` with your chosen ID to start or continue a conversation. The ID you provide is canonical — the session is created with it if it does not exist yet.

```python
from priest import SessionRef
from priest.session.sqlite_store import SqliteSessionStore

async with SqliteSessionStore(db_path=Path("sessions.db")) as store:
    engine = PriestEngine(..., session_store=store)

    # First turn — session created with ID "my-session"
    r1 = await engine.run(PriestRequest(
        ...,
        prompt="Remember this number: 7.",
        session=SessionRef(id="my-session", create_if_missing=True),
    ))

    # Second turn — session continued by the same ID
    r2 = await engine.run(PriestRequest(
        ...,
        prompt="What number did I ask you to remember?",
        session=SessionRef(id="my-session"),
    ))
```

## Output format hints

`priest` never parses the response. `response.text` is always the raw string returned by the model — format handling is the app layer's responsibility.

Three independent mechanisms are available to hint the model's output format:

```python
from priest.schema.request import OutputSpec

# Activate provider-native JSON mode (e.g. Ollama's format field, OpenAI's json_object mode)
output=OutputSpec(provider_format="json")

# Inject a natural-language instruction into the system prompt (works with any provider)
output=OutputSpec(prompt_format="json")   # also: "xml", "code"

# JSON Schema structured output — preferred for strict schema compliance
output=OutputSpec(
    json_schema={
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
    },
    json_schema_name="person",    # optional, defaults to "response"
    json_schema_strict=False,     # True requires additionalProperties:false on all objects
)
```

`json_schema` wires to provider-native structured output when available (OpenAI `json_schema` mode, Ollama `format` field). For Anthropic, the schema is injected into the system message. `json_schema` takes precedence over `provider_format` when both are set. Either, both, or none of the three mechanisms can be set independently.

## System context

App-layer policy can be injected at the top of the system prompt — above profile rules — via `system_context`:

```python
PriestRequest(
    ...,
    system_context=["Today is 2026-04-01.", "Running inside priests CLI."],
)
```

## Providers

| Provider | Class | Notes |
|----------|-------|-------|
| Ollama | `OllamaProvider` | Local models. Default base URL: `http://localhost:11434` |
| OpenAI-compatible | `OpenAICompatProvider` | OpenAI, Gemini, Bailian, MiniMax, DeepSeek, Kimi, Groq, OpenRouter, and any custom `/v1/chat/completions` endpoint |
| Anthropic | `AnthropicProvider` | Anthropic Claude. Uses the native `/v1/messages` API |

### OpenAI-compatible adapter

```python
from priest.providers.openai_compat_provider import OpenAICompatProvider

adapter = OpenAICompatProvider(
    name="bailian",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    api_key="sk-...",
    proxy="http://127.0.0.1:7890",  # optional
)
```

The adapter runs via the sync `openai` SDK in a worker thread to avoid Python 3.14+ compatibility issues with httpcore's anyio async TLS backend.

### Anthropic adapter

```python
from priest.providers.anthropic_provider import AnthropicProvider

adapter = AnthropicProvider(
    api_key="sk-ant-...",
    proxy="http://127.0.0.1:7890",  # optional
)
```

### Provider options

Pass provider-specific options via `PriestConfig.provider_options`. These are forwarded as extra fields in the request body (OpenAI: `extra_body`; Ollama/Anthropic: merged into the payload).

```python
# Enable/disable thinking mode on Qwen3 models (Bailian / Ollama)
PriestConfig(provider="bailian", model="qwen3-32b", provider_options={"think": True})
```

Not all providers accept the same options — pass only what the target provider supports.

## Testing

```bash
# Unit tests (no Ollama required)
uv run pytest tests/ -m "not integration" -v

# Integration tests (requires running Ollama)
uv run pytest tests/ -v

# Single prompt against Ollama
uv run python scripts/try_run.py --model qwen3.5:9b --prompt "hello"

# Interactive chat
uv run python scripts/try_run.py --model qwen3.5:9b --chat

# Full smoke test
uv run python scripts/try_run.py --model qwen3.5:9b
```

## Package structure

```
priest/
├── errors.py              # error codes and exception hierarchy
├── engine.py              # PriestEngine — single run orchestration
├── schema/
│   ├── request.py         # PriestRequest, PriestConfig, SessionRef, OutputSpec
│   └── response.py        # PriestResponse, ExecutionInfo, UsageInfo, PriestError
├── profile/
│   ├── default_profile.py # built-in fallback default profile
│   ├── loader.py          # FilesystemProfileLoader, ProfileLoader protocol
│   ├── model.py           # Profile dataclass
│   └── context_builder.py # message assembly
├── session/
│   ├── store.py           # SessionStore ABC
│   ├── sqlite_store.py    # SqliteSessionStore (default)
│   ├── memory_store.py    # InMemorySessionStore (tests/ephemeral)
│   └── model.py           # Session, Turn dataclasses
└── providers/
    ├── base.py                    # ProviderAdapter ABC, AdapterResult
    ├── ollama_provider.py         # OllamaProvider
    ├── openai_compat_provider.py  # OpenAICompatProvider (OpenAI SDK, sync-in-thread)
    └── anthropic_provider.py      # AnthropicProvider (httpx async)
```
