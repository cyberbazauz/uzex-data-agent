from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from enum import StrEnum

from app.llm.error_classifier import ProviderFailureKind


class CredentialState(StrEnum):
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"
    COOLDOWN = "COOLDOWN"
    DISABLED = "DISABLED"


@dataclass
class Credential:
    """One provider credential with its *explicit* quota scope.

    The raw secret is excluded from ``repr`` and from every public snapshot.
    ``masked_id`` is a one-way fingerprint used only for logs and UI so an
    operator can tell two credentials apart without seeing either key.
    """

    name: str
    provider: str
    api_key: str = field(repr=False)
    organization_scope_id: str
    project_scope_id: str
    purpose: str = "credential_redundancy"
    state: CredentialState = CredentialState.ACTIVE
    last_failure_kind: str | None = None

    @property
    def masked_id(self) -> str:
        digest = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:10]
        return f"{self.provider}-{digest}"

    @property
    def scope_key(self) -> str:
        """Quota is bound to organization + project + model, not to a key.

        Two keys in the same scope never represent two independent quotas.
        The circuit breaker and cooldown registry therefore key on this value,
        never on ``masked_id``.
        """

        return (
            f"{self.provider}:{self.organization_scope_id}:"
            f"{self.project_scope_id}"
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": self.provider,
            "masked_id": self.masked_id,
            "organization_scope_id": self.organization_scope_id,
            "project_scope_id": self.project_scope_id,
            "purpose": self.purpose,
            "state": self.state.value,
            "last_failure_kind": self.last_failure_kind,
        }


class CredentialRegistry:
    """Ordered, thread-safe credential set for exactly one provider scope.

    Policy enforced here (see V2.1 Stage A):

    * a credential is marked ``INVALID`` only after a credential-specific
      authentication/access error (401/403);
    * 429 never changes the active credential, because quota belongs to the
      organization/project scope, not to an individual key;
    * ``next_for_auth_failure`` is the only method that advances to another
      key and it refuses to cross an organization/project scope boundary.

    The registry never decides to rotate on its own; the caller must pass a
    classified failure.  This keeps the policy observable in tests.
    """

    def __init__(self, credentials: list[Credential] | None = None) -> None:
        self._lock = threading.RLock()
        self._credentials: list[Credential] = []
        for credential in credentials or []:
            self.add(credential)

    def __len__(self) -> int:
        return len(self._credentials)

    def add(self, credential: Credential) -> None:
        with self._lock:
            # Deduplicate by the secret itself so the same key cannot be
            # registered twice under two names.
            if any(item.api_key == credential.api_key for item in self._credentials):
                return
            self._credentials.append(credential)

    def all(self) -> list[Credential]:
        with self._lock:
            return list(self._credentials)

    def active(self) -> list[Credential]:
        with self._lock:
            return [
                item
                for item in self._credentials
                if item.state is CredentialState.ACTIVE
            ]

    def get(self, name: str) -> Credential | None:
        with self._lock:
            for item in self._credentials:
                if item.name == name:
                    return item
        return None

    def first_active(self, *, exclude: set[str] | None = None) -> Credential | None:
        excluded = exclude or set()
        with self._lock:
            for item in self._credentials:
                if item.state is CredentialState.ACTIVE and item.name not in excluded:
                    return item
        return None

    def mark_invalid(self, name: str, *, failure_kind: ProviderFailureKind) -> None:
        with self._lock:
            item = self.get(name)
            if item is None:
                return
            item.state = CredentialState.INVALID
            item.last_failure_kind = failure_kind.value

    def mark_cooldown(self, name: str, *, failure_kind: ProviderFailureKind) -> None:
        """Record a quota failure without deactivating the credential.

        A cooldown is scope-wide and lives in ``CooldownRegistry``.  This flag
        is informational only and must never be used to rotate to another key
        in the same scope.
        """

        with self._lock:
            item = self.get(name)
            if item is None:
                return
            if item.state is CredentialState.ACTIVE:
                item.last_failure_kind = failure_kind.value

    def mark_disabled(self, name: str) -> None:
        with self._lock:
            item = self.get(name)
            if item is not None:
                item.state = CredentialState.DISABLED

    def next_for_auth_failure(self, failed_name: str) -> Credential | None:
        """Advance to another authorized key **in the same scope only**.

        This is credential recovery, not quota pooling.  It must only be called
        after a 401/403 for the failed credential.
        """

        with self._lock:
            failed = self.get(failed_name)
            if failed is None:
                return None
            for item in self._credentials:
                if item.name == failed_name:
                    continue
                if item.state is not CredentialState.ACTIVE:
                    continue
                if (
                    item.provider == failed.provider
                    and item.organization_scope_id == failed.organization_scope_id
                    and item.project_scope_id == failed.project_scope_id
                ):
                    return item
        return None

    def scope_key_for(self, name: str) -> str | None:
        item = self.get(name)
        return item.scope_key if item is not None else None

    def scope_keys(self) -> set[str]:
        with self._lock:
            return {item.scope_key for item in self._credentials}

    def public_snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [item.public_dict() for item in self._credentials]
