from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load a small .env file without overriding real environment variables."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    app_env: Literal["development", "test", "production"] = "development"
    app_secret: str = "dev-only-secret"
    dataset_cutoff_date: str = "2026-10-30"

    database_url: str = (
        "postgresql+psycopg2://uzex_owner:uzex_owner_dev_password@127.0.0.1:55432/uzex"
    )
    database_readonly_url: str = (
        "postgresql+psycopg2://uzex_reader:uzex_reader_dev_password@127.0.0.1:55432/uzex"
    )

    planner_mode: Literal["local", "llm"] = "local"
    llm_provider: str = "groq"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=12.0, gt=0, le=30)
    llm_max_output_tokens: int = Field(default=900, ge=128, le=4096)
    llm_retry_once: bool = True

    # --- V2.1 provider reliability -------------------------------------
    # The production primary is Groq GPT-OSS 120B.  Fallbacks are built only
    # from explicitly configured and pre-qualified entries; the default chain
    # is empty so a degraded model is never used silently.
    llm_primary_provider: str = "groq"
    llm_primary_model: str = "openai/gpt-oss-120b"
    llm_fallback_chain: str = ""
    # Same-provider model redundancy, tried before any cross-provider chain.
    # This is an explicit operator decision (for example Groq keeps a separate
    # rate-limit bucket for every model), so these entries are not gated by the
    # qualification report that cross-provider fallbacks require.
    llm_model_fallback_chain: str = ""
    llm_fallback_max_hops: int = Field(default=2, ge=0, le=5)
    llm_breaker_failure_threshold: int = Field(default=5, ge=1, le=100)
    llm_breaker_recovery_seconds: float = Field(default=60.0, ge=0, le=3600)
    llm_rate_limit_short_max_seconds: float = Field(default=60.0, ge=1, le=600)
    llm_max_concurrent_requests: int = Field(default=4, ge=1, le=64)
    llm_max_queue_wait_seconds: float = Field(default=5.0, ge=0, le=30)
    llm_inline_cooldown_wait_seconds: float = Field(default=2.0, ge=0, le=10)
    llm_qualification_required: bool = True
    # Risk-based semantic critic.  Disabled by default: it may only add a
    # clarification/unsupported outcome to a risky plan, never a fact, and it
    # must not change behaviour until its own evaluation shows no regressions.
    llm_plan_critic_mode: Literal["off", "risk_based"] = "off"
    # Hybrid Knowledge RAG.  It supplies only approved reference context and
    # can never add a fact.  Disabled by default until the identical
    # RAG_OFF/RAG_ON evaluation proves an improvement without regressions.
    llm_rag_enabled: bool = False
    llm_rag_top_k: int = Field(default=5, ge=1, le=5)
    llm_rag_min_relevance: float = Field(default=0.12, ge=0, le=1)

    # Native Gemini API (primary when LLM_PROVIDER=gemini).  The model name is
    # configuration, not an assumption: the live validation report records the
    # exact model that answered.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_key: str = ""
    # Reserve key.  Automatic rotation is intentionally not implemented: a
    # 429/401 is a technical provider failure and follows the normal failover
    # policy instead of silently spending a second quota.
    gemini_api_key_secondary: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # "json_schema" uses responseJsonSchema (standard JSON Schema).
    # "response_schema" uses the older OpenAPI-subset responseSchema field.
    gemini_schema_mode: Literal["json_schema", "response_schema"] = "json_schema"

    # Groq OpenAI-compatible endpoint.  V2.1 production primary.
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    # Credential redundancy only.  These keys MUST belong to the same
    # authorized Groq organization/project; they are never a quota pool and
    # are never rotated on HTTP 429.
    groq_api_key_secondary: str = ""
    groq_api_key_secondary_same_org: str = ""
    groq_api_key_tertiary_same_org: str = ""
    groq_organization_scope_id: str = "groq-default-org"
    groq_project_scope_id: str = "groq-default-project"
    # Optional second Groq organization.  It is used only for a long daily
    # quota exhaustion in the primary scope, never for short RPM/TPM limits and
    # never mixed with the primary keys.
    groq_failover_api_keys: str = ""
    groq_failover_organization_scope_id: str = ""
    groq_failover_project_scope_id: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    llm_fallback_enabled: bool = True

    sql_timeout_ms: int = Field(default=8000, ge=500, le=30000)
    backend_timeout_seconds: float = Field(default=20.0, gt=0, le=60)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    rate_limit_per_minute: int = Field(default=30, ge=1, le=300)
    max_question_length: int = Field(default=1500, ge=10, le=5000)

    # --- V3 conversational layer ---------------------------------------
    # Memory is keyed strictly by the caller's session_id.  With no
    # session_id the orchestrator behaves exactly as before.
    conversation_memory_enabled: bool = True
    conversation_max_turns: int = Field(default=6, ge=1, le=50)
    conversation_max_sessions: int = Field(default=2000, ge=1, le=100000)
    # Natural-language answer generation may only rephrase a verified fact
    # object.  Disabled by default until its text validator and a qualified
    # provider are both available.
    answer_generation_mode: Literal["off", "llm"] = "off"
    answer_generation_max_chars: int = Field(default=900, ge=80, le=4000)

    # --- General AI Mode ------------------------------------------------
    # The LLM planner decides whether a question is general knowledge or needs
    # the registry.  General answers never carry data/evidence and are never
    # produced by the local planner.
    general_mode_enabled: bool = True
    general_answer_max_chars: int = Field(default=2000, ge=100, le=8000)
    # A verified data answer may be extended with an explicitly labelled
    # hypothesis block.  The hypothesis can never add facts or numbers.
    explanation_mode: Literal["off", "llm"] = "llm"
    explanation_max_chars: int = Field(default=900, ge=100, le=4000)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            app_secret=os.getenv("APP_SECRET", "dev-only-secret"),
            dataset_cutoff_date=os.getenv("DATASET_CUTOFF_DATE", "2026-10-30"),
            database_url=os.getenv(
                "DATABASE_URL",
                cls.model_fields["database_url"].default,
            ),
            database_readonly_url=os.getenv(
                "DATABASE_READONLY_URL",
                cls.model_fields["database_readonly_url"].default,
            ),
            planner_mode=os.getenv("PLANNER_MODE", "local").strip().lower(),
            llm_provider=os.getenv("LLM_PROVIDER", "groq"),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "12")),
            llm_max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "900")),
            llm_retry_once=_env_bool("LLM_RETRY_ONCE", True),
            llm_primary_provider=os.getenv("LLM_PRIMARY_PROVIDER", "groq"),
            llm_primary_model=os.getenv(
                "LLM_PRIMARY_MODEL", "openai/gpt-oss-120b"
            ),
            llm_fallback_chain=os.getenv("LLM_FALLBACK_CHAIN", ""),
            llm_model_fallback_chain=os.getenv(
                "LLM_MODEL_FALLBACK_CHAIN", ""
            ),
            llm_fallback_max_hops=int(os.getenv("LLM_FALLBACK_MAX_HOPS", "2")),
            llm_breaker_failure_threshold=int(
                os.getenv("LLM_BREAKER_FAILURE_THRESHOLD", "5")
            ),
            llm_breaker_recovery_seconds=float(
                os.getenv("LLM_BREAKER_RECOVERY_SECONDS", "60")
            ),
            llm_rate_limit_short_max_seconds=float(
                os.getenv("LLM_RATE_LIMIT_SHORT_MAX_SECONDS", "60")
            ),
            llm_max_concurrent_requests=int(
                os.getenv("LLM_MAX_CONCURRENT_REQUESTS", "4")
            ),
            llm_max_queue_wait_seconds=float(
                os.getenv("LLM_MAX_QUEUE_WAIT_SECONDS", "5")
            ),
            llm_inline_cooldown_wait_seconds=float(
                os.getenv("LLM_INLINE_COOLDOWN_WAIT_SECONDS", "2")
            ),
            llm_qualification_required=_env_bool(
                "LLM_QUALIFICATION_REQUIRED", True
            ),
            llm_plan_critic_mode=os.getenv(
                "LLM_PLAN_CRITIC_MODE", "off"
            ).strip().lower(),
            llm_rag_enabled=_env_bool("LLM_RAG_ENABLED", False),
            llm_rag_top_k=int(os.getenv("LLM_RAG_TOP_K", "5")),
            llm_rag_min_relevance=float(
                os.getenv("LLM_RAG_MIN_RELEVANCE", "0.12")
            ),
            gemini_base_url=os.getenv(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta",
            ),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_api_key_secondary=os.getenv("GEMINI_API_KEY_SECONDARY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            gemini_schema_mode=os.getenv(
                "GEMINI_SCHEMA_MODE", "json_schema"
            ).strip().lower(),
            groq_base_url=os.getenv(
                "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
            ),
            groq_api_key=os.getenv("GROQ_API_KEY", ""),
            groq_api_key_secondary=os.getenv("GROQ_API_KEY_SECONDARY", ""),
            groq_api_key_secondary_same_org=os.getenv(
                "GROQ_API_KEY_SECONDARY_SAME_ORG", ""
            ),
            groq_api_key_tertiary_same_org=os.getenv(
                "GROQ_API_KEY_TERTIARY_SAME_ORG", ""
            ),
            groq_organization_scope_id=os.getenv(
                "GROQ_ORGANIZATION_SCOPE_ID", "groq-default-org"
            ),
            groq_project_scope_id=os.getenv(
                "GROQ_PROJECT_SCOPE_ID", "groq-default-project"
            ),
            groq_failover_api_keys=os.getenv("GROQ_FAILOVER_API_KEYS", ""),
            groq_failover_organization_scope_id=os.getenv(
                "GROQ_FAILOVER_ORGANIZATION_SCOPE_ID", ""
            ),
            groq_failover_project_scope_id=os.getenv(
                "GROQ_FAILOVER_PROJECT_SCOPE_ID", ""
            ),
            groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            llm_fallback_enabled=_env_bool("LLM_FALLBACK_ENABLED", True),
            sql_timeout_ms=int(os.getenv("SQL_TIMEOUT_MS", "8000")),
            backend_timeout_seconds=float(os.getenv("BACKEND_TIMEOUT_SECONDS", "20")),
            cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173"),
            rate_limit_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "30")),
            max_question_length=int(os.getenv("MAX_QUESTION_LENGTH", "1500")),
            conversation_memory_enabled=_env_bool(
                "CONVERSATION_MEMORY_ENABLED", True
            ),
            conversation_max_turns=int(os.getenv("CONVERSATION_MAX_TURNS", "6")),
            conversation_max_sessions=int(
                os.getenv("CONVERSATION_MAX_SESSIONS", "2000")
            ),
            answer_generation_mode=os.getenv(
                "ANSWER_GENERATION_MODE", "off"
            ).strip().lower(),
            answer_generation_max_chars=int(
                os.getenv("ANSWER_GENERATION_MAX_CHARS", "900")
            ),
            general_mode_enabled=_env_bool("GENERAL_MODE_ENABLED", True),
            general_answer_max_chars=int(
                os.getenv("GENERAL_ANSWER_MAX_CHARS", "2000")
            ),
            explanation_mode=os.getenv(
                "EXPLANATION_MODE", "llm"
            ).strip().lower(),
            explanation_max_chars=int(
                os.getenv("EXPLANATION_MAX_CHARS", "900")
            ),
        )

    def groq_credential_entries(self) -> list[tuple[str, str]]:
        """All configured Groq keys, ordered primary -> redundancy.

        The registry deduplicates identical secrets.  Every entry is treated as
        the same explicitly configured organization/project scope; the executor
        never merges credentials from another scope and never rotates on 429.
        """

        return [
            ("groq-primary", self.groq_api_key),
            ("groq-secondary", self.groq_api_key_secondary),
            ("groq-secondary-same-org", self.groq_api_key_secondary_same_org),
            ("groq-tertiary-same-org", self.groq_api_key_tertiary_same_org),
        ]

    def groq_credential_count(self) -> int:
        return len(
            {
                secret
                for _, secret in self.groq_credential_entries()
                if secret
            }
        )

    def groq_failover_credential_entries(self) -> list[tuple[str, str]]:
        """Credentials from an explicitly configured, independent Groq org.

        The list is empty unless ``GROQ_FAILOVER_API_KEYS`` is set.  These keys
        are only used after the primary organization reports a long (daily)
        quota exhaustion; they are never merged into the primary registry.
        """

        entries: list[tuple[str, str]] = []
        for index, raw in enumerate(
            self.groq_failover_api_keys.split(","), start=1
        ):
            secret = raw.strip()
            if secret:
                entries.append((f"groq-failover-{index}", secret))
        return entries

    def effective_primary_provider(self) -> str:
        provider = (self.llm_primary_provider or self.llm_provider).strip().lower()
        return provider

    def effective_primary_model(self) -> str:
        if self.llm_primary_provider.strip().lower() == "groq":
            return self.llm_primary_model or self.groq_model
        return self.llm_primary_model or self.llm_model

    def fallback_chain(self) -> list[tuple[str, str]]:
        """Parse ``LLM_FALLBACK_CHAIN`` as ``provider:model,provider:model``.

        Entries are only built when the chain is explicitly configured.  The
        default is empty: an unqualified model is never used as a silent
        replacement for the production primary.
        """

        entries: list[tuple[str, str]] = []
        for raw in self.llm_fallback_chain.split(","):
            item = raw.strip()
            if not item or ":" not in item:
                continue
            provider, model = item.split(":", 1)
            provider = provider.strip().lower()
            model = model.strip()
            if provider and model:
                entries.append((provider, model))
        return entries[: self.llm_fallback_max_hops]

    def model_fallback_chain(self) -> list[tuple[str, str]]:
        """Parse ``LLM_MODEL_FALLBACK_CHAIN`` as model-only or provider:model.

        These entries are used only after a technical provider failure and
        before the cross-provider ``LLM_FALLBACK_CHAIN``.  Omitting the provider
        means "same provider as the primary" and therefore the same keys.  A
        different model has its own provider-side rate-limit bucket (Groq and
        the other supported providers meter per model per organization).
        """

        entries: list[tuple[str, str]] = []
        default_provider = self.effective_primary_provider()
        for raw in self.llm_model_fallback_chain.split(","):
            item = raw.strip()
            if not item:
                continue
            if ":" in item:
                provider, model = item.split(":", 1)
                provider = provider.strip().lower()
                model = model.strip()
            else:
                provider = default_provider
                model = item
            if provider and model:
                entries.append((provider, model))
        return entries[: self.llm_fallback_max_hops]

    def llm_configured(self) -> bool:
        provider = self.llm_provider.strip().lower()
        if provider == "gemini":
            return bool(self.gemini_api_key and self.gemini_model)
        if provider == "groq":
            return bool(self.groq_credential_count() and self.groq_model)
        return bool(self.llm_api_key and self.llm_model)

    def public_safe_dict(self) -> dict[str, object]:
        return {
            "app_env": self.app_env,
            "planner_mode": self.planner_mode,
            "llm_provider": self.llm_provider if self.planner_mode == "llm" else None,
            "llm_primary_provider": self.effective_primary_provider(),
            "llm_primary_model": self.effective_primary_model() or None,
            "llm_configured": self.llm_configured(),
            # Counts/booleans only: never the keys themselves.
            "groq_credential_count": self.groq_credential_count(),
            "llm_secondary_keys_configured": bool(
                self.gemini_api_key_secondary
                or self.groq_api_key_secondary
                or self.groq_api_key_secondary_same_org
                or self.groq_api_key_tertiary_same_org
            ),
            "llm_fallback_enabled": self.llm_fallback_enabled,
            "llm_fallback_chain": self.llm_fallback_chain,
            "llm_model_fallback_chain": self.llm_model_fallback_chain,
            "llm_plan_critic_mode": self.llm_plan_critic_mode,
            "llm_rag_enabled": self.llm_rag_enabled,
            "llm_rag_top_k": self.llm_rag_top_k,
            "llm_rag_min_relevance": self.llm_rag_min_relevance,
            "general_mode_enabled": self.general_mode_enabled,
            "explanation_mode": self.explanation_mode,
            "dataset_cutoff_date": self.dataset_cutoff_date,
        }


settings = Settings.from_env()
