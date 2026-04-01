from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PriestConfig(BaseModel):
    provider: str
    model: str
    timeout_seconds: float | None = None
    max_output_tokens: int | None = None
    # Advisory only — enforcement is the host app's responsibility.
    cost_limit: float | None = None
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


class PriestRequest(BaseModel):
    config: PriestConfig
    profile: str = "default"
    prompt: str
    session: SessionRef | None = None
    # Injected at the top of the system prompt — highest priority context.
    # Use for app-layer policy: current date, runtime environment, guardrails, etc.
    system_context: list[str] = Field(default_factory=list)
    # Strings appended to the user turn as additional context.
    extra_context: list[str] = Field(default_factory=list)
    # Arbitrary caller metadata — passed through to PriestResponse unchanged.
    metadata: dict[str, Any] = Field(default_factory=dict)
    output: OutputSpec = Field(default_factory=OutputSpec)
