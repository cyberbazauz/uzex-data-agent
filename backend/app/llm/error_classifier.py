from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Mapping

import httpx


class ProviderFailureKind(StrEnum):
    """A provider failure category, independent of a single HTTP status."""

    AUTH_INVALID = "auth_invalid"
    ACCESS_DENIED = "access_denied"
    RATE_LIMIT_SHORT = "rate_limit_short"
    RATE_LIMIT_LONG = "rate_limit_long"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_FOUND = "model_not_found"
    TRANSPORT = "transport"
    UNKNOWN = "unknown"


# Duration values returned by Groq/OpenAI rate headers look like "2.5s",
# "1m30s", "120ms" or a plain number of seconds.
_DURATION_PART = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>ms|s|m|h)?",
    re.IGNORECASE,
)


def _parse_duration(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass

    total = 0.0
    consumed = ""
    for match in _DURATION_PART.finditer(text):
        if match.start() != len(consumed):
            return None
        amount = float(match.group("value"))
        unit = (match.group("unit") or "s").lower()
        factor = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
        total += amount * factor
        consumed += match.group(0)
    if consumed and len(consumed) == len(text):
        return max(0.0, total)
    return None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup for plain dicts and httpx.Headers."""
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return None


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    raw = _header(headers, name)
    if raw is None:
        return None
    try:
        return int(float(raw.strip()))
    except ValueError:
        return None


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    direct = _parse_duration(value)
    if direct is not None:
        return direct
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


@dataclass(frozen=True)
class RateLimitInfo:
    """Only values actually supplied by the provider are stored.

    A missing reset is represented by ``None``.  The code never invents a
    duration when the provider did not send one.
    """

    retry_after_seconds: float | None = None
    reset_requests_seconds: float | None = None
    reset_tokens_seconds: float | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    limit_requests: int | None = None
    limit_tokens: int | None = None
    request_id: str | None = None

    @property
    def reset_seconds(self) -> float | None:
        values = [
            value
            for value in (
                self.retry_after_seconds,
                self.reset_requests_seconds,
                self.reset_tokens_seconds,
            )
            if value is not None
        ]
        return min(values) if values else None

    @property
    def has_headers(self) -> bool:
        return any(
            value is not None
            for value in (
                self.retry_after_seconds,
                self.reset_requests_seconds,
                self.reset_tokens_seconds,
                self.remaining_requests,
                self.remaining_tokens,
                self.limit_requests,
                self.limit_tokens,
            )
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "retry_after_seconds": self.retry_after_seconds,
            "reset_seconds": self.reset_seconds,
            "remaining_requests": self.remaining_requests,
            "remaining_tokens": self.remaining_tokens,
            "limit_requests": self.limit_requests,
            "limit_tokens": self.limit_tokens,
            "request_id": self.request_id,
        }


@dataclass(frozen=True)
class ProviderFailure:
    """A sanitized, transport-independent failure description."""

    kind: ProviderFailureKind
    provider: str
    model: str | None
    status_code: int | None = None
    message: str = ""
    retry_after_seconds: float | None = None
    rate: RateLimitInfo | None = None
    credential_specific: bool = False
    retryable: bool = False
    fallback_allowed: bool = False
    quota_scope: str | None = None

    @property
    def is_rate_limit(self) -> bool:
        return self.kind in (
            ProviderFailureKind.RATE_LIMIT_SHORT,
            ProviderFailureKind.RATE_LIMIT_LONG,
        )

    @property
    def is_credential_failure(self) -> bool:
        return self.kind in (
            ProviderFailureKind.AUTH_INVALID,
            ProviderFailureKind.ACCESS_DENIED,
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "provider": self.provider,
            "model": self.model,
            "status_code": self.status_code,
            "message": self.message,
            "retry_after_seconds": self.retry_after_seconds,
            "rate": self.rate.public_dict() if self.rate else None,
            "credential_specific": self.credential_specific,
            "retryable": self.retryable,
            "fallback_allowed": self.fallback_allowed,
            "quota_scope": self.quota_scope,
        }


def parse_rate_headers(headers: Mapping[str, str] | None) -> RateLimitInfo:
    if not headers:
        return RateLimitInfo()
    retry_after = _retry_after_seconds(_header(headers, "retry-after"))
    retry_after_ms = _parse_duration(_header(headers, "retry-after-ms"))
    if retry_after_ms is not None:
        retry_after_ms = retry_after_ms / 1000.0
    if retry_after is None and retry_after_ms is not None:
        retry_after = retry_after_ms
    return RateLimitInfo(
        retry_after_seconds=retry_after,
        reset_requests_seconds=_parse_duration(
            _header(headers, "x-ratelimit-reset-requests")
        ),
        reset_tokens_seconds=_parse_duration(
            _header(headers, "x-ratelimit-reset-tokens")
        ),
        remaining_requests=_header_int(headers, "x-ratelimit-remaining-requests"),
        remaining_tokens=_header_int(headers, "x-ratelimit-remaining-tokens"),
        limit_requests=_header_int(headers, "x-ratelimit-limit-requests"),
        limit_tokens=_header_int(headers, "x-ratelimit-limit-tokens"),
        request_id=_header(headers, "x-request-id"),
    )


def _sanitized_message(provider: str, status_code: int | None) -> str:
    # Never echo a response body: it can contain account metadata, prompt
    # fragments or credentials echoed by a misconfigured gateway.
    if status_code is None:
        return f"{provider} transport failure"
    return f"{provider} HTTP {status_code}"


def classify_http_failure(
    *,
    provider: str,
    model: str | None,
    status_code: int,
    headers: Mapping[str, str] | None = None,
    rate_limit_short_max_seconds: float = 60.0,
    error_code: str | None = None,
) -> ProviderFailure:
    """Classify one HTTP failure without making an account-pool decision."""
    rate = parse_rate_headers(headers)
    message = _sanitized_message(provider, status_code)

    if status_code == 401:
        return ProviderFailure(
            kind=ProviderFailureKind.AUTH_INVALID,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            credential_specific=True,
            retryable=False,
            fallback_allowed=True,
        )
    if status_code == 403:
        return ProviderFailure(
            kind=ProviderFailureKind.ACCESS_DENIED,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            credential_specific=True,
            retryable=False,
            fallback_allowed=True,
        )
    if status_code == 429:
        reset = rate.reset_seconds
        short = reset is not None and reset <= rate_limit_short_max_seconds
        return ProviderFailure(
            kind=(
                ProviderFailureKind.RATE_LIMIT_SHORT
                if short
                else ProviderFailureKind.RATE_LIMIT_LONG
            ),
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            retry_after_seconds=reset,
            rate=rate,
            credential_specific=False,
            # A short limit may be retried after its real reset.  A missing or
            # long reset is not retried while the user waits.
            retryable=short,
            fallback_allowed=True,
            quota_scope=f"{provider}:organization/model",
        )
    if status_code == 404:
        return ProviderFailure(
            kind=ProviderFailureKind.MODEL_NOT_FOUND,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            credential_specific=False,
            retryable=False,
            fallback_allowed=True,
        )
    if status_code in (408, 504):
        return ProviderFailure(
            kind=ProviderFailureKind.TIMEOUT,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            retryable=True,
            fallback_allowed=True,
        )
    if 500 <= status_code <= 599:
        return ProviderFailure(
            kind=ProviderFailureKind.SERVER_ERROR,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            retryable=True,
            fallback_allowed=True,
        )
    if status_code in (400, 409, 413, 422):
        # A provider-side JSON-schema rejection is not a configuration error:
        # the model output was truncated or malformed and the same request can
        # succeed on a second attempt.  Every other 4xx in this range remains
        # terminal, so a schema/code/config error is never hidden by retrying
        # until an unsafe plan is accepted.
        retryable = error_code == "json_validate_failed"
        return ProviderFailure(
            kind=ProviderFailureKind.INVALID_REQUEST,
            provider=provider,
            model=model,
            status_code=status_code,
            message=message,
            credential_specific=False,
            retryable=retryable,
            fallback_allowed=False,
        )
    return ProviderFailure(
        kind=ProviderFailureKind.UNKNOWN,
        provider=provider,
        model=model,
        status_code=status_code,
        message=message,
        retryable=False,
        fallback_allowed=False,
    )


def classify_exception(
    exc: BaseException,
    *,
    provider: str,
    model: str | None,
) -> ProviderFailure:
    if isinstance(exc, httpx.TimeoutException) or isinstance(exc, TimeoutError):
        return ProviderFailure(
            kind=ProviderFailureKind.TIMEOUT,
            provider=provider,
            model=model,
            message=f"{provider} timeout",
            retryable=True,
            fallback_allowed=True,
        )
    if isinstance(exc, httpx.TransportError) or isinstance(exc, OSError):
        return ProviderFailure(
            kind=ProviderFailureKind.TRANSPORT,
            provider=provider,
            model=model,
            message=f"{provider} transport failure",
            retryable=True,
            fallback_allowed=True,
        )
    return ProviderFailure(
        kind=ProviderFailureKind.UNKNOWN,
        provider=provider,
        model=model,
        message=f"{provider} provider failure ({type(exc).__name__})",
        retryable=False,
        fallback_allowed=False,
    )
