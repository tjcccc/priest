# Streaming

This document defines the `stream()` contract and its differences from `run()`.

Reference implementation: `priest/engine.py` (`stream()` method), `priest/providers/base.py`

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

**`stream()` yields only raw text chunks.** There is no final structured response. If callers need usage stats, latency, or session metadata, they must use `run()`.

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
