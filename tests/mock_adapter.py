from typing import AsyncGenerator

from priest.providers.base import AdapterCallOptions, AdapterResult, ProviderAdapter
from priest.schema.request import OutputSpec, PriestConfig, ToolCall


class MockAdapter(ProviderAdapter):
    """Fake provider adapter for unit tests — no network calls."""

    provider_name = "mock"

    def __init__(self, text: str = "hello", finish_reason: str = "stop") -> None:
        self._text = text
        self._finish_reason = finish_reason

    async def complete(
        self,
        messages,
        config,
        output_spec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult:
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
        options: AdapterCallOptions | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text one word at a time."""
        for word in self._text.split():
            yield word


class ScriptedAdapter(ProviderAdapter):
    """Adapter scripted with a sequence of AdapterResults, one per complete().

    Records every messages list and call options it receives.
    """

    provider_name = "mock"

    def __init__(self, results: list[AdapterResult]) -> None:
        self._results = results
        self._cursor = 0
        self.calls: list[dict] = []

    async def complete(
        self,
        messages,
        config,
        output_spec,
        options: AdapterCallOptions | None = None,
    ) -> AdapterResult:
        self.calls.append({"messages": messages, "options": options})
        result = self._results[min(self._cursor, len(self._results) - 1)]
        self._cursor += 1
        return result


def scripted_result(
    text: str = "",
    finish_reason: str = "stop",
    tool_calls: list[ToolCall] | None = None,
) -> AdapterResult:
    return AdapterResult(
        text=text,
        raw=None,
        finish_reason=finish_reason,
        input_tokens=None,
        output_tokens=None,
        tool_calls=tool_calls,
    )
