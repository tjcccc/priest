# AGENTS

Small repo overlay for `priest`; keep global rules in the global `AGENTS.md`.

## Boundaries

- This is the transport-agnostic core library. Do not add CLI, FastAPI, UI, global config, or hardcoded user paths here.
- App behavior belongs in sibling `../priests`.
- Public contracts include schemas, error codes, context assembly, provider adapters, session behavior, memory helpers, and `spec/`.
- `response.text` stays raw model output; parsing and tool loops belong to callers.

## Change Notes

- Update `spec/` when public API or canonical behavior changes.
- Update `README.md` for public usage/install/provider changes.
- Update `DEVLOG.md` for meaningful library changes.
- Preserve compatibility unless the task is explicitly breaking; record breaking changes in `spec/CHANGELOG.md`.

## Checks

- Unit tests: `uv run pytest tests/ -m "not integration" -v`
- Full tests, if live provider prerequisites are available: `uv run pytest tests/ -v`
- Targeted tests: `uv run pytest tests/<file>.py -v`
- Ollama smoke, if running locally: `uv run python scripts/try_run.py --model qwen3.5:9b --prompt "hello"`
