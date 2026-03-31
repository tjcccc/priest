from enum import StrEnum


class ErrorCode(StrEnum):
    # Profile errors
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    PROFILE_INVALID = "PROFILE_INVALID"

    # Session errors
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_STORE_ERROR = "SESSION_STORE_ERROR"

    # Provider errors
    PROVIDER_NOT_REGISTERED = "PROVIDER_NOT_REGISTERED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"

    # Request errors
    REQUEST_INVALID = "REQUEST_INVALID"

    # Generic
    INTERNAL_ERROR = "INTERNAL_ERROR"


class PriestError(Exception):
    """Base exception for all priest errors."""

    def __init__(self, code: ErrorCode, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ProfileNotFoundError(PriestError):
    def __init__(self, name: str) -> None:
        super().__init__(
            ErrorCode.PROFILE_NOT_FOUND,
            f"Profile '{name}' not found",
            profile=name,
        )


class SessionNotFoundError(PriestError):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            ErrorCode.SESSION_NOT_FOUND,
            f"Session '{session_id}' not found",
            session_id=session_id,
        )


class ProviderNotRegisteredError(PriestError):
    def __init__(self, provider: str) -> None:
        super().__init__(
            ErrorCode.PROVIDER_NOT_REGISTERED,
            f"Provider '{provider}' is not registered",
            provider=provider,
        )


class ProviderTimeoutError(PriestError):
    def __init__(self, provider: str, timeout: float) -> None:
        super().__init__(
            ErrorCode.PROVIDER_TIMEOUT,
            f"Provider '{provider}' timed out after {timeout}s",
            provider=provider,
            timeout=timeout,
        )


class ProviderError(PriestError):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(
            ErrorCode.PROVIDER_ERROR,
            f"Provider '{provider}' error: {message}",
            provider=provider,
        )
