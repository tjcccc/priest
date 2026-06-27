from priest.engine import PriestEngine, PriestStreamEvent
from priest.errors import (
    ErrorCode,
    PriestError,
    ProfileNotFoundError,
    ProviderError,
    ProviderNotRegisteredError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    SessionNotFoundError,
)
from priest.providers.base import AdapterCallOptions, AdapterResult, AdapterStreamEvent, ProviderAdapter
from priest.schema.request import (
    AssistantToolTurn,
    ImageInput,
    NamedToolChoice,
    OutputSpec,
    PriestConfig,
    PriestRequest,
    SessionRef,
    ToolCall,
    ToolChoice,
    ToolDefinition,
    ToolExchangeTurn,
    ToolResultTurn,
)
from priest.tool_loop import (
    ApprovalDecision,
    ToolExecutionResult,
    ToolLoopResult,
    run_with_tools,
)
from priest.schema.response import ExecutionInfo, PriestResponse, SessionInfo, UsageInfo
from priest.compactor import (
    COMPACTION_TRIGGER_RATIO,
    DEFAULT_COMPACTION_KEEP_TURNS,
    SUMMARY_MAX_OUTPUT_TOKENS,
    CompactionPlan,
    build_summary_messages,
    plan_compaction,
    should_compact,
)
from priest.session.model import COMPACTION_METADATA_KEY, CompactionState

__all__ = [
    # Core engine
    "PriestEngine",
    # Request types
    "PriestConfig",
    "PriestRequest",
    "SessionRef",
    "OutputSpec",
    "ImageInput",
    # Response types
    "PriestResponse",
    "ExecutionInfo",
    "UsageInfo",
    "SessionInfo",
    # Exceptions and error codes
    "PriestError",
    "ErrorCode",
    "ProfileNotFoundError",
    "SessionNotFoundError",
    "ProviderNotRegisteredError",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderRateLimitedError",
    # Tool calling (spec 2.4.0)
    "ToolDefinition",
    "ToolChoice",
    "NamedToolChoice",
    "ToolCall",
    "ToolExchangeTurn",
    "AssistantToolTurn",
    "ToolResultTurn",
    "run_with_tools",
    "ToolExecutionResult",
    "ApprovalDecision",
    "ToolLoopResult",
    # Streaming (spec 2.4.0)
    "PriestStreamEvent",
    # Conversation compaction (spec 2.5.0)
    "CompactionState",
    "CompactionPlan",
    "COMPACTION_METADATA_KEY",
    "COMPACTION_TRIGGER_RATIO",
    "DEFAULT_COMPACTION_KEEP_TURNS",
    "SUMMARY_MAX_OUTPUT_TOKENS",
    "should_compact",
    "plan_compaction",
    "build_summary_messages",
    # Adapter base types (for custom provider implementations)
    "ProviderAdapter",
    "AdapterResult",
    "AdapterCallOptions",
    "AdapterStreamEvent",
]
