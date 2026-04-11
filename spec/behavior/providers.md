# Provider Translations

This document defines how each provider adapter translates the common message format to its wire format.

Reference implementations: `priest/providers/ollama_provider.py`, `priest/providers/openai_compat_provider.py`, `priest/providers/anthropic_provider.py`

---

## Common message format

The engine always passes messages as a list of objects:

```json
[
  { "role": "system",    "content": "..." },
  { "role": "user",      "content": "..." },
  { "role": "assistant", "content": "..." }
]
```

This is the OpenAI messages convention. Each adapter translates it as needed.

---

## Ollama (`/api/chat`)

**Default base URL:** `http://localhost:11434`

**Endpoint:** `POST {base_url}/api/chat`

**Request payload:**

```json
{
  "model": "<config.model>",
  "messages": [ ... ],      // common format — Ollama accepts OpenAI convention
  "stream": false           // true for stream()
}
```

**Field mapping:**

| priest field | Ollama payload field |
|---|---|
| `config.max_output_tokens` | `options.num_predict` |
| `output_spec.provider_format == "json"` | `"format": "json"` added to payload |
| `config.provider_options` | Merged into payload root (may override defaults) |

**Response parsing:**

| Ollama response field | priest field |
|---|---|
| `message.content` | `result.text` |
| `prompt_eval_count` | `result.input_tokens` |
| `eval_count` | `result.output_tokens` |
| `done_reason` | mapped via finish reason table below |

**Finish reason mapping:**

| `done_reason` | `finished_reason` |
|---|---|
| `"stop"` | `"stop"` |
| `"length"` | `"length"` |
| `"load"` | `"stop"` |
| anything else | `"unknown"` |

**Streaming (NDJSON):** Set `"stream": true`. Each response line is a JSON object. Yield `data.message.content` for each line where `content` is non-empty. Stop when `data.done == true`.

**Default timeout:** 60 seconds.

---

## OpenAI-compatible (`/v1/chat/completions`)

Covers: OpenAI, Gemini, Bailian (Alibaba), MiniMax, DeepSeek, Kimi, Groq, OpenRouter, and any custom `/v1/chat/completions` endpoint.

**Endpoint:** `POST {base_url}/v1/chat/completions`

**Request payload:**

```json
{
  "model": "<config.model>",
  "messages": [ ... ]
}
```

**Field mapping:**

| priest field | OpenAI payload field |
|---|---|
| `config.max_output_tokens` | `max_tokens` |
| `output_spec.provider_format == "json"` | `response_format: {"type": "json_object"}` |
| `config.provider_options` | `extra_body` (merged into request body by the SDK) |

**Response parsing:**

| OpenAI response field | priest field |
|---|---|
| `choices[0].message.content` | `result.text` |
| `usage.prompt_tokens` | `result.input_tokens` |
| `usage.completion_tokens` | `result.output_tokens` |
| `choices[0].finish_reason` | mapped via finish reason table below |

**Finish reason mapping:**

| `finish_reason` | `finished_reason` |
|---|---|
| `"stop"` | `"stop"` |
| `"length"` | `"length"` |
| `"content_filter"` | `"unknown"` |
| anything else | `"unknown"` |

**Streaming (SSE):** Set `"stream": true`. Parse Server-Sent Events: filter lines starting with `data: `, strip prefix, parse JSON. Yield `choices[0].delta.content` when non-empty. Stop on `data: [DONE]`.

**Authentication:** `Authorization: Bearer <api_key>` header (handled by the OpenAI SDK or manually).

**Default timeout:** 60 seconds.

---

## Anthropic (`/v1/messages`)

**Default base URL:** `https://api.anthropic.com`

**Endpoint:** `POST {base_url}/v1/messages`

**Required headers:**

```
x-api-key: <api_key>
anthropic-version: 2023-06-01
content-type: application/json
```

**Message transformation:**

Anthropic does not accept a `system` role in the messages array. Before sending:

```
system_parts = [m.content for m in messages if m.role == "system"]
turns        = [m for m in messages if m.role != "system"]

payload.system   = "\n\n".join(system_parts)   // only if system_parts is non-empty
payload.messages = turns
```

**Request payload:**

```json
{
  "model": "<config.model>",
  "messages": [ ... ],
  "max_tokens": <config.max_output_tokens or 8096>,
  "system": "..."         // only if system_parts is non-empty
}
```

**Default `max_tokens`:** `8096` (required field; Anthropic rejects requests without it).

**Field mapping:**

| priest field | Anthropic payload field |
|---|---|
| `config.max_output_tokens` | `max_tokens` (default: 8096) |
| `config.provider_options` | Merged into payload root |
| `output_spec.provider_format` | Not natively supported — `prompt_format` covers this via the system prompt |

**Response parsing:**

| Anthropic response field | priest field |
|---|---|
| `content[0].text` (where `type == "text"`) | `result.text` |
| `usage.input_tokens` | `result.input_tokens` |
| `usage.output_tokens` | `result.output_tokens` |
| `stop_reason` | mapped via finish reason table below |

**Finish reason mapping:**

| `stop_reason` | `finished_reason` |
|---|---|
| `"end_turn"` | `"stop"` |
| `"max_tokens"` | `"length"` |
| `"stop_sequence"` | `"stop"` |
| anything else | `"unknown"` |

**Streaming (SSE):** Set `"stream": true`. Parse SSE: filter lines starting with `data: `, strip prefix, parse JSON. Handle events where `type == "content_block_delta"` and yield `delta.text` when non-empty. The `[DONE]` sentinel is not used by Anthropic — stop on connection close.
