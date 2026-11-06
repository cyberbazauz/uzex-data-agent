from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.errors import UnverifiedQuery

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.llm.error_classifier import ProviderFailure


class ProviderEnvelopeError(ValueError):
    """The provider returned HTTP 2xx but the transport envelope is unusable.

    This is a technical failure (retryable once), unlike an empty candidate
    list which is a semantic fail-closed outcome.
    """


class ProviderTechnicalError(UnverifiedQuery):
    """A provider failed technically (transport, timeout, HTTP or envelope).

    Only this class authorises the fallback planner.  A semantically invalid
    plan is never a technical failure: it must not trigger a second model,
    because the second model would be asked to reinterpret an unsafe answer.

    ``retry_allowed`` and ``wait_seconds`` are filled by the reliability layer
    so the adapter's bounded retry loop never retries a terminal failure (for
    example a daily quota 429) and honours the provider's real reset header
    instead of inventing a duration.
    """

    code = "LLM_PROVIDER_TECHNICAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        failure: "ProviderFailure | None" = None,
        retry_allowed: bool = True,
        wait_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.retry_allowed = retry_allowed
        self.wait_seconds = wait_seconds

    @property
    def provider(self) -> str | None:
        return self.failure.provider if self.failure is not None else None

    @property
    def is_rate_limit(self) -> bool:
        return bool(self.failure and self.failure.is_rate_limit)

    @property
    def fallback_allowed(self) -> bool:
        return bool(self.failure.fallback_allowed) if self.failure else True

    @property
    def diagnostics(self) -> dict[str, object]:
        """Sanitized fields safe for a structured log.

        Never contains an API key, prompt, request body or provider response
        body.  ``status_code``/``retry_after_seconds``/``quota_scope`` make a
        429 distinguishable from a validation or configuration failure.
        """

        if self.failure is None:
            return {
                "error_type": type(self).__name__,
                "retry_allowed": self.retry_allowed,
                "wait_seconds": self.wait_seconds,
            }
        failure = self.failure
        kind = getattr(failure.kind, "value", failure.kind)
        return {
            "error_type": type(self).__name__,
            "failure_kind": kind,
            "provider": failure.provider,
            "model": failure.model,
            "status_code": failure.status_code,
            "retry_after_seconds": failure.retry_after_seconds,
            "rate": failure.rate.public_dict() if failure.rate else None,
            "quota_scope": failure.quota_scope,
            "fallback_allowed": failure.fallback_allowed,
            "retryable": failure.retryable,
            "retry_allowed": self.retry_allowed,
            "wait_seconds": self.wait_seconds,
        }
