# Tool Calling

This document defines the native tool-calling contract introduced in spec 2.4.0.

Reference implementation: TypeScript `@priest-ai/core` v2.4.0 (`src/schema/ToolTypes.ts`, `src/engine/ToolLoop.ts`, provider adapters). Python sync pending.

---

## Design contract: the caller executes

The library **transports** tool definitions and tool calls; it **never executes tools**. Approval policy, sandboxing, and tool implementations belong to the host application. The protocol is:

1. The caller sends `request.tools` (and optionally `tool_choice`).
2. When the model requests tool execution, the response carries `tool_calls` and `execution.finished_reason = "tool_calls"`. `response.text` may carry accompanying text.
3. The caller executes the calls, then re-runs the **same request** with the assistant turn and the tool results appended to `request.tool_exchange`.
4. Repeat until the model answers without tool calls.

`tool_exchange` is the full loop history for the current user turn, replayed in order on every iteration. It is the caller's responsibility to append both the `assistant` turn (echoing the returned `tool_calls`) and one `tool_result` turn per call.

---

## Context assembly

Tool exchange turns are appended to the messages array **after the final user message**, in order:

- `{kind: "assistant", text, tool_calls}` → assistant message carrying the tool calls (text may be empty).
- `{kind: "tool_result", tool_call_id, name, content, is_error}` → tool-role message.

They are not part of the system prompt and are never deduplicated or trimmed.

---

## Session persistence rule

**Tool exchange turns are never persisted in sessions.** The `turns` table stores only `user` and `assistant` roles (see `session-lifecycle.md`), preserving cross-SDK session compatibility with implementations that predate tool calling.

Persistence happens only on the loop's **final** iteration — the run whose response carries no tool calls:

- When `finished_reason = "tool_calls"`: save nothing.
- Otherwise: save the original `request.prompt` as the user turn and the final assistant text as the assistant turn, exactly as a tool-free run would.

Hosts that need durable tool history must store it themselves (e.g. task logs).

---

## Call ids

- Providers that assign call ids (OpenAI-compatible, Anthropic) keep them.
- Providers that do not (Ollama) get synthesized ids `call_0`, `call_1`, ... in response order. Adapters drop the synthesized id on the wire when sending results and use the provider's own correlation mechanism (`tool_name` for Ollama).

## Argument parsing

`ToolCall.arguments` is always a parsed JSON object. When a provider returns unparseable argument JSON (e.g. a truncated stream), implementations MUST substitute `{}` rather than raise — the host's tool layer is the right place to reject bad arguments.

## tool_choice mapping

| Spec value | OpenAI-compatible | Anthropic | Ollama |
|---|---|---|---|
| `"auto"` | `"auto"` | `{type: "auto"}` | ignored |
| `"none"` | `"none"` | `{type: "none"}` | ignored |
| `"required"` | `"required"` | `{type: "any"}` | ignored |
| `{name}` | `{type: "function", function: {name}}` | `{type: "tool", name}` | ignored |

See `providers.md` for full wire-format mappings.

---

## Optional convenience loop: `run_with_tools`

Implementations SHOULD provide a convenience loop with this shape:

```
run_with_tools(engine, request, executor, hooks?) -> {response, exchange, iteration_limit_reached}
```

- `executor(tool_call) -> {content, is_error?}` — caller-supplied; errors are returned as content with `is_error`, not raised.
- `hooks.on_tool_call(tool_call) -> {approved, reason?}` — optional approval gate. A denial injects a `tool_result` with `is_error = true` and content `"Tool call denied by the caller[: reason]"` without executing, so the model can react.
- `hooks.max_iterations` — maximum engine runs (model turns), default 10, clamped to ≥ 1.
- `hooks.signal` / cancellation token — threaded into every engine run.

The loop runs the request, executes approved calls in order, appends to the exchange, and repeats until the response carries no tool calls, is not ok, or the iteration cap is reached (`iteration_limit_reached = true`, last response returned as-is).
