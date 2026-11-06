from __future__ import annotations

from app.domain.enums import ClarificationCode


class AgentError(Exception):
    """Base class for controlled query-execution failures.

    The orchestrator maps these to the public response statuses.  They are
    never used as a substitute for a legitimate empty result set.
    """

    code = "AGENT_ERROR"


class UnsupportedQuery(AgentError):
    """The validated plan asks for something the schema cannot answer safely."""

    code = "UNSUPPORTED_QUERY"


class UnverifiedQuery(AgentError):
    """The database or a data-integrity check prevents a trustworthy answer."""

    code = "UNVERIFIED_QUERY"


class AmbiguousEntity(AgentError):
    """A role or meaning is missing, so a financial query must not be executed."""

    code = "AMBIGUOUS_ENTITY"

    def __init__(
        self,
        clarification_code: ClarificationCode,
        detail: str,
        options: list[str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.clarification_code = clarification_code
        self.detail = detail
        self.options = options or []


class EntityNotFound(AgentError):
    """An explicitly named entity does not exist in the active dataset.

    This is not the same as an empty aggregate result.  The orchestrator maps
    it to NOT_FOUND with an entity-specific message; it never invents a match.
    """

    code = "ENTITY_NOT_FOUND"

    def __init__(self, kind: str, mention: str, detail: str | None = None) -> None:
        message = detail or f"{kind} '{mention}' was not found in the active dataset"
        super().__init__(message)
        self.kind = kind
        self.mention = mention
        self.detail = message
