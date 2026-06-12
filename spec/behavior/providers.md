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

Since spec 2.4.0 three message extensions exist (all absent on tool-free text runs):

- **Multimodal user content:** when `request.images` is set, the final user message `content` becomes a list of OpenAI-format content blocks — `{type: "image_url", image_url: {url}}` blocks first (path/data sources rendered as `data:<media_type>;base64,<data>` URLs), then one `{type: "text", text}` block last.
- **Assistant tool calls:** `{role: "assistant", content, tool_calls: [ToolCall]}` (from `tool_exchange` replay).
- **Tool results:** `{role: "tool", content, tool_call_id, name}` (from `tool_exchange` replay).

Adapters translate these per the sections below. See `tool-calling.md` for the loop contract.

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

**Images:** Ollama uses a top-level `images` field (list of base64 strings) on the user message rather than inline content blocks. Translate multimodal user content by joining text blocks with a single space into `content` and collecting base64 payloads from `data:` URLs into `images`. HTTP/HTTPS image URLs are not supported — raise `PROVIDER_ERROR` if encountered.

**Tools (spec 2.4.0):**

| spec | Ollama wire |
|---|---|
| `request.tools` | `tools: [{type: "function", function: {name, description, parameters}}]` (OpenAI shape) |
| `tool_choice` | ignored (no Ollama equivalent) |
| assistant `tool_calls` | `tool_calls: [{function: {name, arguments: <object>}}]` — synthesized ids dropped on the wire |
| `tool` role message | `{role: "tool", content, tool_name: <name>}` |
| response `message.tool_calls[].function.{name, arguments}` | `ToolCall` with synthesized id `call_N`; arguments arrive as a parsed object |

Streaming tool calls arrive whole within one NDJSON chunk (`message.tool_calls`) — emit start/end event pairs per call. When any tool call was seen, `finished_reason` is `"tool_calls"` regardless of `done_reason`.

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

**Images:** multimodal user content blocks pass through unchanged — they are already OpenAI wire format.

**Tools (spec 2.4.0):**

| spec | OpenAI wire |
|---|---|
| `request.tools` | `tools: [{type: "function", function: {name, description, parameters}}]` |
| `tool_choice` | `"auto"`/`"none"`/`"required"` pass through; `{name}` → `{type: "function", function: {name}}` |
| assistant `tool_calls` | `tool_calls: [{id, type: "function", function: {name, arguments: <JSON string>}}]`; `content` is `null` when the accompanying text is empty |
| `tool` role message | `{role: "tool", tool_call_id, content}` |
| response `choices[0].message.tool_calls` | `ToolCall`; `function.arguments` is a JSON **string** — parse it, substitute `{}` on failure |
| `finish_reason == "tool_calls"` | `finished_reason: "tool_calls"` |

Streaming tool calls arrive as fragments in `choices[0].delta.tool_calls[]`, keyed by `index`; `id` and `function.name` typically arrive in the first fragment and `function.arguments` accumulates across fragments. Finalize accumulated calls in index order when the stream ends or `finish_reason` arrives.

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

**Images:** OpenAI-format `image_url` blocks become Anthropic image blocks — `data:` URLs → `{type: "image", source: {type: "base64", media_type, data}}`, HTTP/HTTPS URLs → `{type: "image", source: {type: "url", url}}`.

**Tools (spec 2.4.0):**

| spec | Anthropic wire |
|---|---|
| `request.tools` | `tools: [{name, description, input_schema: <parameters>}]` |
| `tool_choice` | `"auto"` → `{type: "auto"}`, `"none"` → `{type: "none"}`, `"required"` → `{type: "any"}`, `{name}` → `{type: "tool", name}` |
| assistant `tool_calls` | content blocks: optional `{type: "text", text}` first, then `{type: "tool_use", id, name, input: <arguments>}` per call |
| `tool` role message | merged into a **user** message of `{type: "tool_result", tool_use_id, content}` blocks; consecutive tool messages merge into one user message (Anthropic requires alternating roles) |
| response `content[].tool_use` blocks | `ToolCall` with the provider's `id`; `input` is a parsed object |
| `stop_reason == "tool_use"` | `finished_reason: "tool_calls"` |

Streaming tool calls: `content_block_start` with `content_block.type == "tool_use"` opens a call (provider block `index` maps to tool-call event index assigned in tool_use-block order); `content_block_delta` with `delta.type == "input_json_delta"` accumulates `partial_json`; `content_block_stop` finalizes the call. `message_delta` carries `stop_reason` and `usage.output_tokens`; `message_start` carries `usage.input_tokens`.
