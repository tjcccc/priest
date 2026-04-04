# Changelog

All notable changes to `priest` are documented here.

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
