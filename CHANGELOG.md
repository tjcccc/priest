# Changelog

All notable changes to `priest` are documented here.

## [2.2.0] — 2026-04-25

### Added
- `OutputSpec.json_schema: dict[str, Any] | None` — JSON Schema for structured output. When set, takes precedence over `provider_format="json"`.
- `OutputSpec.json_schema_name: str` (default `"response"`) — schema name passed to OpenAI's `json_schema.name` field.
- `OutputSpec.json_schema_strict: bool` (default `False`) — maps to OpenAI's `json_schema.strict`. Enable only when the schema fully satisfies strict mode requirements (`required` exhaustive, `additionalProperties: false`).
- **OpenAI-compat:** `response_format: {"type": "json_schema", ...}` wired in both `complete` and `stream`.
- **Ollama (v0.5+):** `format: <schema_dict>` (schema dict passed directly) wired in both `complete` and `stream`.
- **Anthropic:** adapter-level system message injection with the schema description, wired in both `complete` and `stream`. Anthropic has no native JSON Schema API; the injected block uses XML tags (`<schema>`) for clarity.

---

## [2.0.0] — 2026-04-18

### Breaking
- `PriestRequest.system_context` and `PriestRequest.memory_context` are replaced with `PriestRequest.context` (raw, untouched) and `PriestRequest.memory` (deduped, trimmable).
- `PriestRequest.extra_context` renamed to `PriestRequest.user_context` (same semantics).

### Added
- `PriestConfig.max_system_chars: int | None` — optional system prompt size budget. When set, the library trims `request.memory` and then `profile.memories` from the tail until the budget is met. `context`, rules, identity, custom, and format instructions are never trimmed. Defaults to `None` (no trimming).
- Deduplication: `request.memory` entries whose stripped content matches another memory entry or any `profile.memories` entry are dropped during assembly.
- `## Memory` section: dynamic memory entries from `request.memory` are rendered under a dedicated `## Memory\n\n` heading, after the `## Loaded Memories` block.
- `FilesystemProfileLoader` now caches loaded `Profile` objects per-instance keyed on the max mtime and file count across `PROFILE.md`, `RULES.md`, `CUSTOM.md`, `profile.toml`, and files under `memories/`. Any edit, add, or remove invalidates the cache.

### Migration
```python
# v1
PriestRequest(
    ...,
    system_context=[guide, "Running inside priests CLI."],
    memory_context=[memory_block],
    extra_context=[search_result],
)
# v2
PriestRequest(
    ...,
    context=[guide, "Running inside priests CLI."],
    memory=[memory_block],
    user_context=[search_result],
)
```

### Protocol spec
- Spec version bumped to 2.0.0 (see `spec/CHANGELOG.md`).

---

## [1.0.0] — 2026-04-12

First stable release. Established the public API surface, the protocol spec, and streaming support.

---

## [0.2.1] — 2026-04-04

### Fixed
- Profile memories not recalled by the model — memory file contents were injected as
  unlabeled raw text blocks in the system prompt; now grouped under a
  `## Loaded Memories` heading so models correctly identify and use saved facts

---

## [0.2.0] — 2026-04-04

### Added
- `OpenAICompatProvider` — covers any OpenAI-compatible `/v1/chat/completions`
  endpoint: OpenAI, Google Gemini, Alibaba Bailian, MiniMax, DeepSeek, Kimi,
  Groq, OpenRouter, and custom endpoints
- `AnthropicProvider` — native Anthropic `/v1/messages` API with system prompt
  extraction and `x-api-key` auth
- `engine.stream()` — async generator yielding text chunks as they arrive;
  session is saved automatically after the stream completes
- `ProviderAdapter.stream()` — default implementation falls back to `complete()`
  so adapters without native streaming still work
- `OllamaProvider.stream()` — async httpx streaming of `/api/chat`
- `OpenAICompatProvider.stream()` — sync OpenAI SDK `create(stream=True)` in a
  worker thread; chunks delivered via `asyncio.Queue` + `call_soon_threadsafe`
- `AnthropicProvider.stream()` — sync httpx SSE streaming in a worker thread,
  same queue pattern
- `proxy: str | None` constructor argument on `OpenAICompatProvider` and
  `AnthropicProvider`; passed as `httpx.Client(proxy=...)` for mainland China users

### Fixed
- `OpenAICompatProvider` uses the synchronous OpenAI SDK in a worker thread
  to avoid a Python 3.14 incompatibility in httpcore's anyio async TLS backend

### Changed
- Dependency: replaced `httpx>=0.27` with `openai>=1.0` as a direct dependency
  (`httpx` remains present as a transitive dependency)

---

## [0.1.0] — 2026-03-31

### Added
- `PriestEngine` — single-run AI orchestration with profile loading, session
  continuation, and structured error handling
- `PriestRequest` / `PriestConfig` / `PriestResponse` schema
- `SessionRef` — caller-controlled session IDs; `create_if_missing` for
  idempotent session creation
- `SqliteSessionStore` — async SQLite-backed session persistence (`aiosqlite`)
- `InMemorySessionStore` — ephemeral store for tests
- `FilesystemProfileLoader` — loads `PROFILE.md`, `RULES.md`, `CUSTOM.md`,
  and `memories/` from a directory tree
- Built-in default profile with identity and behavior rules
- `OllamaProvider` — async httpx adapter for Ollama `/api/chat`
- `OutputSpec` — `provider_format` and `prompt_format` hints (json, xml, code)
- `system_context` on `PriestRequest` for app-layer policy injection
- Full error hierarchy: `ProviderError`, `ProviderTimeoutError`,
  `ProviderNotRegisteredError`, `SessionNotFoundError`
