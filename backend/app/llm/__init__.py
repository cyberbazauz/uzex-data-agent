"""Provider reliability primitives for the UZEX Query Planner.

The package deliberately contains no database or SQL access.  It only decides
whether a provider request may be attempted and how a provider failure must be
classified.  Factual grounding remains in PostgreSQL handlers and validators.
"""

from app.llm.circuit_breaker import (
    BreakerSnapshot,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
)
from app.llm.credential_registry import (
    Credential,
    CredentialRegistry,
    CredentialState,
)
from app.llm.error_classifier import (
    ProviderFailure,
    ProviderFailureKind,
    RateLimitInfo,
    classify_exception,
    classify_http_failure,
    parse_rate_headers,
)
from app.llm.errors import ProviderTechnicalError
from app.llm.provider_manager import (
    ProviderExecutor,
    ProviderManager,
    build_credential_registry,
)
from app.llm.rate_limit_state import (
    CooldownRegistry,
    CooldownSnapshot,
    RequestGate,
)

__all__ = [
    "BreakerSnapshot",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CooldownRegistry",
    "CooldownSnapshot",
    "Credential",
    "CredentialRegistry",
    "CredentialState",
    "ProviderExecutor",
    "ProviderFailure",
    "ProviderFailureKind",
    "ProviderManager",
    "ProviderTechnicalError",
    "RateLimitInfo",
    "RequestGate",
    "build_credential_registry",
    "classify_exception",
    "classify_http_failure",
    "parse_rate_headers",
]
