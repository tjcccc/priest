from typing import AsyncGenerator

from priest.providers.base import AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig


class MockAdapter(ProviderAdapter):
    """Fake provider adapter for unit tests — no network calls."""

    provider_name = "mock"

    def __init__(self, text: str = "hello", finish_reason: str = "stop") -> None:
        self._text = text
        self._finish_reason = finish_reason

    async def complete(self, messages, config, output_spec) -> AdapterResult:
        return AdapterResult(
            text=self._text,
            raw={"mock": True},
            finish_reason=self._finish_reason,
            input_tokens=10,
            output_tokens=5,
        )

    async def stream(
        self,
        messages: list[dict],
        config: PriestConfig,
        output_spec: OutputSpec,
    ) -> AsyncGenerator[str, None]:
        """Yield text one word at a time."""
        for word in self._text.split():
            yield word
