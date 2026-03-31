from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from priest.schema.request import OutputSpec, PriestConfig


@dataclass
class AdapterResult:
    """Raw result from a provider adapter before mapping to PriestResponse."""
    text: str | None
    raw: Any | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


class ProviderAdapter(ABC):
    """Base class for all provider adapters.

    Adapters are thin translators: messages in, AdapterResult out.
    They do not inspect profile content, call back into the engine,
    or perform any business logic beyond sending the request and
    normalizing the response.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AdapterResult: ...
