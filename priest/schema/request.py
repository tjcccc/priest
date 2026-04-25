from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class ImageInput(BaseModel):
    """A single image attached to the user turn.

    Provide exactly one of: path (local file), url (http/https), or data (base64-encoded bytes).
    media_type is used when path or data is provided; defaults to image/jpeg.

    Note: not all providers support all source types. Ollama requires base64 (path or data);
    it does not accept http/https URLs. OpenAI-compatible and Anthropic accept all three.

    Image context is not persisted in sessions — only the text prompt is stored.
    Multi-turn image conversations are not supported in v1.
    """
    path: str | None = None
    url: str | None = None
    data: str | None = None
    media_type: str = "image/jpeg"

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> "ImageInput":
        sources = [x for x in (self.path, self.url, self.data) if x is not None]
        if len(sources) != 1:
            raise ValueError("ImageInput requires exactly one of: path, url, or data")
        return self


class PriestConfig(BaseModel):
    provider: str
    model: str
    timeout_seconds: float | None = None
    max_output_tokens: int | None = None
    # Advisory only — enforcement is the host app's responsibility.
    cost_limit: float | None = None
    # Optional ceiling on the assembled system prompt size (characters).
    # When set, the library trims dynamic `memory` entries (tail first), then
    # `profile.memories` entries (tail first), until the system prompt fits.
    # `context`, rules, identity, custom, and format instructions are never trimmed.
    # None = no trimming (default). Callers opt in when they need safety.
    max_system_chars: int | None = None
    # Provider-specific options merged directly into the request payload.
    # Examples: {"think": False} for Ollama/Qwen3, {"temperature": 0.7} etc.
    provider_options: dict[str, Any] = Field(default_factory=dict)


class SessionRef(BaseModel):
    id: str
    continue_existing: bool = True
    # If continue_existing=True but no session with this ID exists,
    # create it using the provided ID rather than raising SESSION_NOT_FOUND.
    create_if_missing: bool = True


class OutputSpec(BaseModel):
    # Activates provider-native structured output when supported
    # (e.g. Ollama's format field, OpenAI's response_format).
    # Currently only "json" has broad provider-native support.
    provider_format: Literal["json"] | None = None

    # Injects a natural-language format instruction into the system prompt.
    # Works with any provider regardless of native support.
    # The raw text is always returned as-is in PriestResponse.text —
    # parsing is the app layer's responsibility.
    prompt_format: Literal["json", "xml", "code"] | None = None

    # JSON Schema for structured output.
    # OpenAI-compat: maps to response_format={"type": "json_schema", ...}.
    # Ollama (v0.5+): maps to format=<schema_dict>.
    # Anthropic: schema description is injected into the system message (no native support).
    # When set, takes precedence over provider_format for the schema-capable path.
    # If prompt_format is also set, both instructions will appear — prefer using one or the other.
    # json_schema_strict=True requires every property listed in required and
    # additionalProperties=False; most user schemas won't satisfy this out of the box.
    json_schema: dict[str, Any] | None = None
    json_schema_name: str = "response"
    json_schema_strict: bool = False


class PriestRequest(BaseModel):
    config: PriestConfig
    profile: str = "default"
    prompt: str
    session: SessionRef | None = None
    # Raw system-level context injected at the top of the system prompt.
    # Passed through untouched — the library never trims or dedupes it.
    # Use for app-layer policy (current date, environment, guardrails) or for
    # callers that want full control over their system prompt.
    context: list[str] = Field(default_factory=list)
    # Dynamic memory entries (raw strings). The library deduplicates by stripped
    # content (against itself and against profile.memories) and, when
    # config.max_system_chars is set, trims from the tail to fit the budget.
    memory: list[str] = Field(default_factory=list)
    # Strings appended to the user turn after the prompt, joined with "\n\n".
    # Use for per-turn ephemeral content (RAG chunks, tool outputs, search hits)
    # that belongs with the user's question rather than the persistent system prompt.
    user_context: list[str] = Field(default_factory=list)
    # Images attached to the user turn. See ImageInput for source options.
    # Not persisted in sessions — image context does not carry across turns.
    images: list[ImageInput] = Field(default_factory=list)
    # Arbitrary caller metadata — passed through to PriestResponse unchanged.
    metadata: dict[str, Any] = Field(default_factory=dict)
    output: OutputSpec = Field(default_factory=OutputSpec)
