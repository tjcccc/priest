# Error Codes

All priest errors carry a `code` string from the table below.

Reference implementation: `priest/errors.py`

---

## Error table

| Code | Class | Trigger | Details fields |
|------|-------|---------|----------------|
| `PROFILE_NOT_FOUND` | `ProfileNotFoundError` | Named profile not found in filesystem or built-in | `profile` (string) |
| `PROFILE_INVALID` | `ProfileInvalidError` | Profile directory found but structurally malformed | `profile` (string) |
| `SESSION_NOT_FOUND` | `SessionNotFoundError` | `continue_existing=true`, `create_if_missing=false`, session absent | `session_id` (string) |
| `SESSION_STORE_ERROR` | `SessionStoreError` | Storage backend failure (I/O error, corrupt DB, etc.) | varies |
| `PROVIDER_NOT_REGISTERED` | `ProviderNotRegisteredError` | No adapter registered for `config.provider` | `provider` (string) |
| `PROVIDER_TIMEOUT` | `ProviderTimeoutError` | Request exceeded `timeout_seconds` | `provider` (string), `timeout` (string of float) |
| `PROVIDER_ERROR` | `ProviderError` | HTTP error or network failure | `provider` (string) |
| `PROVIDER_RATE_LIMITED` | `ProviderRateLimitedError` | HTTP 429 from provider | `provider` (string), `retry_after` (string of float, optional) |
| `REQUEST_INVALID` | — | Malformed request fields | varies |
| `REQUEST_ABORTED` | — | Caller cancelled an in-flight request via the cancellation signal (spec 2.4.0) | `provider` (string) |
| `IMAGE_LOAD_ERROR` | `ImageLoadError` | `ImageInput.path` could not be read | `path` (string) |
| `INTERNAL_ERROR` | — | Unexpected failure | varies |

---

## Exception vs. response error

Two errors are always **thrown as exceptions** and never placed into `PriestResponse.error`:

- **`PROVIDER_NOT_REGISTERED`** — no adapter means no response can be constructed at all.
- **`SESSION_NOT_FOUND`** — the caller explicitly opted out of session creation; this is a programming error, not a recoverable provider failure.

All other errors from the provider (timeout, HTTP error, rate limit) are **caught and placed** into `PriestResponse.error`. The response is returned with `ok = false`.

---

## Details field encoding

All `details` values are strings. Non-string values (numbers, booleans) are stringified at the point the error is created. This makes `details` safe to serialize as `{"key": "value"}` without type ambiguity.

---

## `finished_reason` on error

When a provider error is captured into `PriestResponse.error`, `execution.finished_reason` is set to `"error"`.
