# Streaming

This document defines the `stream()` contract, the `stream_events()` contract (spec 2.4.0), and their differences from `run()`.

Reference implementation: `priest/engine.py` (`stream()` method), `priest/providers/base.py`; `stream_events()` reference: TypeScript `@priest-ai/core` v2.4.0

---

## `stream()` vs `run()`

| Aspect | `run()` | `stream()` |
|--------|---------|------------|
| Return type | `PriestResponse` | `AsyncGenerator[str]` / `AsyncThrowingStream<String, Error>` |
| Text | `response.text` (complete) | Yields chunks as they arrive |
| Usage info | `response.usage` | Not available |
| Session info | `response.session` | Not available |
| Execution info | `response.execution` | Not available |
| Session save | After provider call completes | After all chunks yielded |
| Provider error | Captured into `response.error`; `ok = false` | Thrown as exception |

**`stream()` yields only raw text chunks.** There is no final structured response. If callers need usage stats, latency, session metadata, or tool calls while streaming, they must use `run()` or `stream_events()`.

---

## `stream_events()` (spec 2.4.0)

`stream_events(request, options?)` yields typed events and terminates with a `done` event carrying the complete `PriestResponse` (text, tool_calls, usage, session info, and error state):

| Event | Payload | Meaning |
|---|---|---|
| `text_delta` | `text` | Visible text chunk |
| `tool_call_start` | `index`, `id?`, `name?` | The model began emitting a tool call |
| `tool_call_delta` | `index`, `arguments_delta` | Raw argument JSON fragment |
| `tool_call_end` | `index`, `tool_call` | Finalized `ToolCall` (arguments parsed; `{}` on parse failure) |
| `usage` | `usage` | Token usage (`input_tokens`, `output_tokens`, and — spec 2.5.0 — `cached_input_tokens` when the provider reports prompt-cache hits); possibly emitted more than once with refinements |
| `done` | `response` | Terminal event; always last |

Rules:

- **Error semantics match `run()`, not `stream()`:** provider errors are captured into `done.response.error` (`ok = false`) rather than thrown. `PROVIDER_NOT_REGISTERED` and `SESSION_NOT_FOUND` still throw.
- **Adapter fallback:** adapters MAY implement a native `stream_events`. When absent, the engine MUST wrap the adapter's plain `stream()` — each chunk becomes a `text_delta` and a final `finish` is synthesized. Engines therefore support `stream_events()` over every adapter.
- **`stream()` as a filter:** implementations SHOULD express `stream()` as a filter over `stream_events()` that yields `text_delta` payloads and rethrows `done.response.error` as an exception, preserving the legacy contract.
- **Session save:** identical to `run()`, including the tool-calls rule — nothing is saved while the response carries tool calls (see `tool-calling.md`).

---

## Cancellation (spec 2.4.0)

`run()`, `stream()`, and `stream_events()` accept an optional cancellation signal (`AbortSignal` in TypeScript; an equivalent token elsewhere). Caller cancellation aborts in-flight provider work and surfaces as `REQUEST_ABORTED` — thrown by adapters, then handled per the method's error semantics. Timeouts remain `PROVIDER_TIMEOUT`; implementations MUST distinguish the two causes. The connect timeout MUST NOT terminate a healthy stream mid-read: once response headers arrive, only the caller signal remains armed.

---

## Session handling in `stream()`

Session handling at the start of `stream()` follows the **same decision tree** as `run()` (see `session-lifecycle.md`). The difference is only in the post-call save:

```
had_error = false
parts = []

try:
    for each chunk from adapter.stream():
        parts.append(chunk)
        yield chunk
except PriestError:
    had_error = true
    raise   // re-propagate

finally:
    if not had_error AND parts is non-empty AND session is not null AND store is not null:
        full_text = "".join(parts)
        session.append_turn("user", request.prompt)
        session.append_turn("assistant", full_text)
        store.save(session)
```

If the stream is cancelled or interrupted before completion, **the session is not saved**. This matches the `had_error = true` path.

---

## Default stream implementation

Provider adapters that do not support native streaming **MUST** provide a default implementation that calls `complete()` and yields the full text as a single chunk:

```
async def stream(messages, config, output_spec):
    result = await complete(messages, config, output_spec)
    if result.text:
        yield result.text
```

This ensures that `engine.stream()` works with any adapter, even those without native streaming support.

---

## Adapter streaming

Adapters that support native streaming override the default:

| Provider | Streaming protocol |
|----------|--------------------|
| Ollama | NDJSON over HTTP chunked transfer (`stream: true`). One JSON object per line. |
| OpenAI-compatible | Server-Sent Events (SSE). Lines prefixed with `data: `. `[DONE]` sentinel. |
| Anthropic | Server-Sent Events (SSE). Event type `content_block_delta`, field `delta.text`. |

See `behavior/providers.md` for wire-level details.
