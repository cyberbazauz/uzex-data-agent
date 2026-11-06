from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.agent.query_plan import QueryPlan
from app.config import Settings, settings as default_settings
from app.language import LanguageDetection

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


class QueryPlanner(Protocol):
    """The only planner contract used by the orchestrator.

    Both the deterministic local planner and the LLM adapter implement this
    protocol and return the same strict ``QueryPlan`` model.  Downstream code
    never needs to know which backend produced the plan.
    """

    name: str

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        ...

    def describe(self) -> dict[str, object]:
        ...


class LocalPlannerAdapter:
    """Deterministic rule-based planner used offline, in tests and in demos."""

    name = "local"

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        # Imported lazily so the LLM adapter does not pull in the whole
        # rule set and vice versa.
        from app.agent.local_planner import plan_question

        return plan_question(question, detection)

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "model": None}


def _build_provider(
    provider: str,
    model: str,
    active: Settings,
) -> QueryPlanner:
    """Build one adapter for an explicit provider/model pair."""

    from app.agent.llm_adapter import (
        GeminiPlannerAdapter,
        GroqPlannerAdapter,
        LLMPlannerAdapter,
    )

    if provider == "groq":
        return GroqPlannerAdapter(
            active.model_copy(
                update={"llm_provider": "groq", "groq_model": model}
            )
        )
    if provider in {"gemini", "google"}:
        return GeminiPlannerAdapter(
            active.model_copy(
                update={"llm_provider": "gemini", "gemini_model": model}
            )
        )
    return LLMPlannerAdapter(
        active.model_copy(update={"llm_provider": provider, "llm_model": model})
    )


def _apply_model_fallbacks(
    primary: QueryPlanner, active: Settings
) -> QueryPlanner:
    """Wrap ``primary`` with explicitly configured same-provider models.

    The entries come from ``LLM_MODEL_FALLBACK_CHAIN``.  They are operator
    approved (rather than qualification-gated) because they reuse the same
    provider and credentials; the only thing that changes is the model and
    therefore its independent provider-side rate-limit bucket.
    """

    from app.agent.llm_adapter import FailoverPlanner

    entries = active.model_fallback_chain()
    if not entries:
        return primary
    primary_provider = active.effective_primary_provider()
    primary_model = active.effective_primary_model()
    current = primary
    for provider_name, model in entries:
        if (
            provider_name == primary_provider
            and model == primary_model
        ):
            continue
        current = FailoverPlanner(
            current,
            _build_provider(provider_name, model, active),
            fallback_qualified=True,
        )
    return current


def build_planner(settings: Settings | None = None) -> QueryPlanner:
    """Build the configured planner without ever reading secrets in the client.

    ``LLM_PROVIDER`` selects the primary transport:

    * ``groq`` - OpenAI-compatible strict structured output (V2.1 default);
    * ``gemini`` - native ``generateContent`` with a JSON Schema response;
    * anything else - the generic OpenAI-compatible adapter.

    When Gemini is explicitly selected and a Groq key is configured, the legacy
    failover wrapper is preserved.  New Groq-primary deployments use
    ``LLM_FALLBACK_CHAIN`` with pre-qualified independent providers only; the
    default chain is empty, so GPT-OSS 120B is never silently replaced by an
    unqualified model.
    """
    active = settings or default_settings
    if active.planner_mode != "llm":
        return LocalPlannerAdapter()

    from app.agent.llm_adapter import (
        FailoverPlanner,
        GeminiPlannerAdapter,
        GroqPlannerAdapter,
        LLMPlannerAdapter,
    )

    provider = active.llm_provider.strip().lower()
    if provider == "gemini":
        primary: QueryPlanner = GeminiPlannerAdapter(active)
        if active.llm_fallback_enabled:
            primary = _apply_model_fallbacks(primary, active)
        if (
            active.llm_fallback_enabled
            and active.groq_credential_count()
            and active.groq_model
        ):
            return FailoverPlanner(primary, GroqPlannerAdapter(active))
        return primary
    if provider == "groq":
        primary = GroqPlannerAdapter(active)
        if not active.llm_fallback_enabled:
            return primary
        # Model redundancy on the same keys comes first: Groq meters every
        # model in a separate bucket, so another model is a genuinely
        # independent quota even inside the same organization.
        primary = _apply_model_fallbacks(primary, active)
        # An explicitly configured second organization is a quota-independent
        # fallback for a long daily exhaustion.  It is never used for a short
        # RPM/TPM wait and its keys are never merged into the primary registry.
        failover_entries = active.groq_failover_credential_entries()
        failover_scope = (
            active.groq_failover_organization_scope_id.strip(),
            active.groq_failover_project_scope_id.strip(),
        )
        primary_scope = (
            active.groq_organization_scope_id.strip(),
            active.groq_project_scope_id.strip(),
        )
        if (
            failover_entries
            and all(failover_scope)
            and failover_scope != primary_scope
        ):
            primary = FailoverPlanner(
                primary,
                GroqPlannerAdapter(
                    active,
                    credential_entries=failover_entries,
                    scope_ids=failover_scope,
                ),
                fallback_qualified=True,
            )
        chain = active.fallback_chain()
        if not chain:
            return primary
        if active.llm_qualification_required:
            # A fallback without a recorded qualification report is not used.
            # Stage B writes the report; an operator can explicitly relax the
            # requirement for a laboratory run.
            from app.llm.qualification import is_qualified

            chain = [
                (provider_name, model)
                for provider_name, model in chain
                if is_qualified(provider_name, model)
            ]
        if not chain:
            return primary
        current: QueryPlanner = primary
        for fallback_provider, fallback_model in chain:
            current = FailoverPlanner(
                current,
                _build_provider(fallback_provider, fallback_model, active),
                fallback_qualified=True,
            )
        return current
    return LLMPlannerAdapter(active)

