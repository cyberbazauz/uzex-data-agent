from __future__ import annotations

import time
from typing import Callable

import httpx

from app.llm.circuit_breaker import CircuitBreakerRegistry
from app.llm.credential_registry import Credential, CredentialRegistry, CredentialState
from app.llm.error_classifier import (
    ProviderFailure,
    ProviderFailureKind,
    classify_exception,
    classify_http_failure,
)
from app.llm.errors import ProviderEnvelopeError, ProviderTechnicalError
from app.llm.rate_limit_state import CooldownRegistry, RequestGate


def build_credential_registry(
    *,
    provider: str,
    entries: list[tuple[str, str]],
    organization_scope_id: str,
    project_scope_id: str,
) -> CredentialRegistry:
    """Build a registry from non-empty ``(name, secret)`` pairs.

    Every entry must belong to the same explicitly configured organization and
    project.  The caller (configuration layer) is responsible for only placing
    same-scope keys in one registry; the executor never merges scopes.
    """

    registry = CredentialRegistry()
    for name, secret in entries:
        if not secret:
            continue
        registry.add(
            Credential(
                name=name,
                provider=provider,
                api_key=secret,
                organization_scope_id=organization_scope_id,
                project_scope_id=project_scope_id,
            )
        )
    return registry


class ProviderManager:
    """Executes one provider call with bounded, policy-compliant reliability.

    Responsibilities:

    * select an ``ACTIVE`` credential from the registry;
    * rotate to another key **only** after HTTP 401 (same scope);
    * never rotate keys on HTTP 429 - quota belongs to the scope;
    * honour provider reset headers for a short cooldown, otherwise fail
      closed (the caller may use an independent, pre-qualified provider or
      report ``UNVERIFIED``);
    * record technical failures on a per-scope circuit breaker;
    * leave retries to the caller's bounded loop, exposing ``retry_allowed``;
    * never log or embed the raw secret in a failure object.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        registry: CredentialRegistry,
        breakers: CircuitBreakerRegistry | None = None,
        cooldowns: CooldownRegistry | None = None,
        gate: RequestGate | None = None,
        rate_limit_short_max_seconds: float = 60.0,
        max_inline_wait_seconds: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.model = model
        self.registry = registry
        self.breakers = breakers or CircuitBreakerRegistry()
        self.cooldowns = cooldowns or CooldownRegistry()
        self.gate = gate or RequestGate()
        self.rate_limit_short_max_seconds = rate_limit_short_max_seconds
        self.max_inline_wait_seconds = max(0.0, max_inline_wait_seconds)
        self._sleep = sleep

    # -- public contract -------------------------------------------------
    def execute(
        self,
        send: Callable[[str], httpx.Response],
        extract: Callable[[httpx.Response], str],
    ) -> str:
        excluded: set[str] = set()
        waited_for_cooldown = False

        while True:
            credential = self.registry.first_active(exclude=excluded)
            if credential is None:
                raise ProviderTechnicalError(
                    f"{self.provider} has no usable credential",
                    failure=self._failure(
                        ProviderFailureKind.AUTH_INVALID,
                        credential_specific=True,
                        fallback_allowed=True,
                        message=f"{self.provider} has no usable credential",
                    ),
                    retry_allowed=False,
                )

            scope = self._scope(credential)
            if self.cooldowns.is_cooling(scope):
                remaining = self.cooldowns.remaining(scope)
                if (
                    not waited_for_cooldown
                    and remaining <= self.max_inline_wait_seconds
                ):
                    waited_for_cooldown = True
                    self._sleep(remaining)
                else:
                    raise ProviderTechnicalError(
                        f"{self.provider} quota cooldown is active",
                        failure=self._failure(
                            ProviderFailureKind.RATE_LIMIT_LONG,
                            message=f"{self.provider} quota cooldown is active",
                            fallback_allowed=True,
                            quota_scope=scope,
                        ),
                        retry_allowed=False,
                        wait_seconds=remaining,
                    )

            breaker = self.breakers.for_scope(scope)
            if not breaker.allow():
                raise ProviderTechnicalError(
                    f"{self.provider} circuit breaker is open",
                    failure=self._failure(
                        ProviderFailureKind.SERVER_ERROR,
                        message=f"{self.provider} circuit breaker is open",
                        retryable=True,
                        fallback_allowed=True,
                        quota_scope=scope,
                    ),
                    retry_allowed=False,
                )

            try:
                with self.gate.slot():
                    response = send(credential.api_key)
            except ProviderTechnicalError:
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                failure = classify_exception(
                    exc, provider=self.provider, model=self.model
                )
                breaker.record_failure()
                raise ProviderTechnicalError(
                    failure.message,
                    failure=failure,
                    retry_allowed=failure.retryable,
                ) from exc

            status = response.status_code
            if 200 <= status <= 299:
                try:
                    content = extract(response)
                except ProviderEnvelopeError as exc:
                    # The transport and HTTP status are fine, but the envelope
                    # is unusable.  This is a technical envelope failure and
                    # may be retried once; it is never a semantic answer.
                    failure = self._failure(
                        ProviderFailureKind.SERVER_ERROR,
                        status_code=status,
                        message=f"{self.provider} response envelope is invalid",
                        retryable=True,
                        fallback_allowed=True,
                        quota_scope=scope,
                    )
                    breaker.record_failure()
                    raise ProviderTechnicalError(
                        failure.message,
                        failure=failure,
                        retry_allowed=True,
                    ) from exc
                breaker.record_success()
                self.cooldowns.clear(scope)
                return content

            failure = classify_http_failure(
                provider=self.provider,
                model=self.model,
                status_code=status,
                headers=response.headers,
                rate_limit_short_max_seconds=self.rate_limit_short_max_seconds,
                error_code=_provider_error_code(response),
            )

            if failure.kind is ProviderFailureKind.AUTH_INVALID:
                # Credential-specific 401: another authorized key in the SAME
                # scope is the only permitted recovery.
                self.registry.mark_invalid(
                    credential.name, failure_kind=failure.kind
                )
                breaker.release()
                excluded.add(credential.name)
                continue

            if failure.is_rate_limit:
                # 429 is scope-level.  Mark the cooldown, but keep every key
                # ACTIVE: switching keys here would be quota pooling.
                self.registry.mark_cooldown(
                    credential.name, failure_kind=failure.kind
                )
                self.cooldowns.set(
                    scope,
                    failure.retry_after_seconds,
                    reason=failure.kind.value,
                    rate=failure.rate,
                )
                short = failure.kind is ProviderFailureKind.RATE_LIMIT_SHORT
                if (
                    short
                    and not waited_for_cooldown
                    and failure.retry_after_seconds is not None
                    and failure.retry_after_seconds <= self.max_inline_wait_seconds
                ):
                    waited_for_cooldown = True
                    breaker.release()
                    self._sleep(failure.retry_after_seconds)
                    self.cooldowns.clear(scope)
                    continue
                breaker.release()
                raise ProviderTechnicalError(
                    failure.message,
                    failure=failure,
                    retry_allowed=False,
                    wait_seconds=failure.retry_after_seconds,
                )

            if failure.kind in (
                ProviderFailureKind.TIMEOUT,
                ProviderFailureKind.SERVER_ERROR,
                ProviderFailureKind.TRANSPORT,
            ):
                breaker.record_failure()
                raise ProviderTechnicalError(
                    failure.message,
                    failure=failure,
                    retry_allowed=True,
                )

            if failure.kind is ProviderFailureKind.MODEL_NOT_FOUND:
                breaker.record_failure()
                raise ProviderTechnicalError(
                    failure.message,
                    failure=failure,
                    retry_allowed=False,
                )

            # 400/403 and anything unknown: configuration or access problem.
            # Do not try another key and do not let another model hide it.
            # Provider-side JSON validation failures are the exception: they
            # are classified retryable above and use the caller's bounded loop.
            breaker.release()
            raise ProviderTechnicalError(
                failure.message,
                failure=failure,
                retry_allowed=failure.retryable,
            )

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "credentials": self.registry.public_snapshot(),
            "breakers": self.breakers.snapshot(),
        }

    # -- internals -------------------------------------------------------
    def _scope(self, credential: Credential) -> str:
        return (
            f"{credential.provider}:{self.model}:"
            f"{credential.organization_scope_id}:{credential.project_scope_id}"
        )

    def _failure(
        self,
        kind: ProviderFailureKind,
        *,
        status_code: int | None = None,
        message: str,
        credential_specific: bool = False,
        retryable: bool = False,
        fallback_allowed: bool = False,
        quota_scope: str | None = None,
    ) -> ProviderFailure:
        return ProviderFailure(
            kind=kind,
            provider=self.provider,
            model=self.model,
            status_code=status_code,
            message=message,
            credential_specific=credential_specific,
            retryable=retryable,
            fallback_allowed=fallback_allowed,
            quota_scope=quota_scope,
        )


# Backwards-friendly alias used in documentation/diagrams.
ProviderExecutor = ProviderManager


def _provider_error_code(response: httpx.Response) -> str | None:
    """Return a provider error code without echoing the response body.

    Only the machine-readable code is extracted; message text may contain
    account metadata or prompt fragments and is never propagated.
    """

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
    else:
        code = payload.get("code")
    return code if isinstance(code, str) else None


def registry_has_active(registry: CredentialRegistry) -> bool:
    return bool(registry.active())


def credential_state(value: str) -> CredentialState:
    return CredentialState(value)
