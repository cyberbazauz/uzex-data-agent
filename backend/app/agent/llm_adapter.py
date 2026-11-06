from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.agent.query_plan import QueryPlan
from app.config import Settings, settings as default_settings
from app.domain.errors import UnverifiedQuery
from app.language import LanguageDetection
from app.llm.circuit_breaker import CircuitBreakerRegistry
from app.llm.credential_registry import CredentialRegistry
from app.llm.errors import ProviderEnvelopeError, ProviderTechnicalError
from app.llm.provider_manager import ProviderManager, build_credential_registry
from app.llm.rate_limit_state import CooldownRegistry, RequestGate
from app.rag.hybrid_retriever import build_retriever

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "query_planner_v1.txt"

# A single, generic correction is sent after an invalid first answer.  The
# invalid model output is never echoed back into the prompt.
CORRECTION_MESSAGE = (
    "Предыдущий ответ не прошёл серверную проверку. Верни только один "
    "корректный JSON-объект QueryPlan строго по переданной JSON Schema. "
    "Не добавляй Markdown, пояснения и поля вне схемы."
)


@lru_cache(maxsize=2)
def _system_prompt(include_schema: bool = True) -> str:
    base = PROMPT_PATH.read_text(encoding="utf-8")
    if not include_schema:
        # Gemini and Groq receive the schema structurally (responseJsonSchema
        # / strict json_schema), so repeating it in the prompt would only waste
        # tokens and rate-limit budget.
        return (
            f"{base}\n\nВерни JSON-объект QueryPlan строго по JSON Schema, "
            "переданной в structured output. Без Markdown и пояснений."
        )
    schema = json.dumps(
        QueryPlan.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{base}\n\nJSON Schema:\n{schema}"


def _messages(
    question: str,
    detection: LanguageDetection,
    *,
    include_schema: bool = True,
    reference_context: str = "",
) -> list[dict[str, str]]:
    user_payload: dict[str, str] = {
        "question": question,
        "detected_language": detection.language.value,
        "detected_script": detection.script.value,
        "detected_locale": detection.locale,
    }
    if reference_context:
        # Untrusted reference data, never system instructions.  The retriever
        # already wraps chunks with an explicit warning; keeping it inside the
        # user payload prevents a document from impersonating the system role.
        user_payload["reference_context"] = reference_context
    return [
        {"role": "system", "content": _system_prompt(include_schema)},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        },
    ]


_ISO_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\D+(\d{4}-\d{2}-\d{2})")


def _normalize_between_values(decoded: dict[str, Any]) -> None:
    """Accept a textual date range for ``between`` filters.

    Strict structured output represents ``FilterValue`` as a string or an
    array of strings (see :func:`openai_strict_schema`).  Some otherwise valid
    models therefore emit ``"2024-01-01/2024-12-31"`` instead of the two-item
    list that the server-side contract requires.  This is a purely syntactic
    coercion of two ISO dates; it never invents or reorders a bound.
    """

    filters = decoded.get("filters")
    if not isinstance(filters, list):
        return
    for item in filters:
        if not isinstance(item, dict):
            continue
        if str(item.get("operator", "")).strip().lower() != "between":
            continue
        value = item.get("value")
        if not isinstance(value, str):
            continue
        match = _ISO_RANGE_RE.search(value)
        if match:
            item["value"] = [match.group(1), match.group(2)]


def _normalize_filter_list_metric(decoded: dict[str, Any]) -> None:
    """Give a record-list plan its only non-count metric.

    ``filter_list`` selects rows, not an aggregate.  Its allowed metrics are
    ``record_lookup`` and ``deal_count``; the count form is rejected later by
    the plan validator because it would hide the hard row limit.  Some models
    emit the general sentinel ``not_applicable`` instead.  Replacing it with
    ``record_lookup`` adds no fact and no filter: the handler still applies the
    dataset bounds, the hard limit and the deterministic formatter.
    """

    if str(decoded.get("decision", "")).strip().upper() != "EXECUTE":
        return
    if str(decoded.get("intent", "")).strip().lower() != "filter_list":
        return
    if str(decoded.get("subject", "")).strip().lower() != "deals":
        return
    if str(decoded.get("metric", "")).strip().lower() != "not_applicable":
        return
    decoded["metric"] = "record_lookup"


def _parse_plan(content: str, detection: LanguageDetection) -> QueryPlan:
    decoded = json.loads(content)
    if not isinstance(decoded, dict):
        raise ValueError("QueryPlan must be a JSON object")
    _normalize_between_values(decoded)
    _normalize_filter_list_metric(decoded)
    plan = QueryPlan.model_validate(decoded)
    # Language/script are detected deterministically on the server before the
    # LLM is called; they are authoritative and cannot be rewritten by the
    # model.
    return plan.model_copy(
        update={
            "language": detection.language,
            "script": detection.script,
        }
    )


# ---------------------------------------------------------------------------
# JSON Schema helpers
# ---------------------------------------------------------------------------


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref``/``$defs`` so providers without ref support can use it."""
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = str(node["$ref"]).rsplit("/", 1)[-1]
        target = _inline_refs(copy.deepcopy(defs.get(ref, {})), defs)
        extra = {key: value for key, value in node.items() if key != "$ref"}
        if extra and isinstance(target, dict):
            target.update(_inline_refs(extra, defs))
        return target
    return {
        key: _inline_refs(value, defs)
        for key, value in node.items()
        if key != "$defs"
    }


def _without_annotations(node: Any) -> Any:
    if isinstance(node, list):
        return [_without_annotations(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned = {
        key: _without_annotations(value)
        for key, value in node.items()
        if key not in {"title", "default", "examples", "$schema", "$id"}
    }
    for key in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "additionalProperties",
    ):
        cleaned.pop(key, None)
    if "const" in cleaned:
        cleaned["enum"] = [cleaned.pop("const")]
    if "type" in cleaned and isinstance(cleaned["type"], str):
        cleaned["type"] = cleaned["type"].upper()
    return cleaned


def gemini_response_json_schema() -> dict[str, Any]:
    """Standard JSON Schema for Gemini ``responseJsonSchema``."""
    schema = QueryPlan.model_json_schema()
    defs = schema.get("$defs", {})
    return _inline_refs(copy.deepcopy(schema), defs)


def gemini_response_schema() -> dict[str, Any]:
    """OpenAPI-subset schema for the older Gemini ``responseSchema`` field."""
    return _without_annotations(gemini_response_json_schema())


def openai_strict_schema() -> dict[str, Any]:
    """JSON Schema accepted by OpenAI-compatible strict structured output."""
    schema = _inline_refs(
        copy.deepcopy(QueryPlan.model_json_schema()),
        QueryPlan.model_json_schema().get("$defs", {}),
    )

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict) and properties:
                # Strict structured output requires every property to be
                # listed.  Optional values are represented with null unions.
                node["required"] = list(properties.keys())
        for value in list(node.values()):
            walk(value)

    walk(schema)

    # Groq's strict JSON Schema implementation requires every ``anyOf`` branch
    # to be distinguishable.  ``FilterValue`` is a four-way union
    # (str | int | list[str] | list[int]); its two list branches look identical
    # on the wire.  The server-side Pydantic model keeps the full union, while
    # the model is asked for a string or an array of strings, which the smart
    # union coerces.  This changes only the provider wire format, not the
    # validated contract.
    try:
        value_schema = schema["properties"]["filters"]["items"]["properties"]
        value_schema["value"] = {
            "anyOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        }
    except (KeyError, TypeError):  # pragma: no cover - defensive
        pass

    return schema


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter (Groq uses the same transport)
# ---------------------------------------------------------------------------


def _make_manager(
    *,
    settings: Settings,
    provider: str,
    model: str,
    entries: list[tuple[str, str]],
    organization_scope_id: str,
    project_scope_id: str,
) -> ProviderManager:
    registry = build_credential_registry(
        provider=provider,
        entries=entries,
        organization_scope_id=organization_scope_id,
        project_scope_id=project_scope_id,
    )
    return ProviderManager(
        provider=provider,
        model=model,
        registry=registry,
        breakers=CircuitBreakerRegistry(
            failure_threshold=settings.llm_breaker_failure_threshold,
            recovery_seconds=settings.llm_breaker_recovery_seconds,
        ),
        cooldowns=CooldownRegistry(),
        gate=RequestGate(
            settings.llm_max_concurrent_requests,
            max_wait_seconds=settings.llm_max_queue_wait_seconds,
        ),
        rate_limit_short_max_seconds=settings.llm_rate_limit_short_max_seconds,
        max_inline_wait_seconds=settings.llm_inline_cooldown_wait_seconds,
    )


class LLMPlannerAdapter:
    """OpenAI-compatible adapter that may only return a strict QueryPlan.

    The adapter is deliberately small:

    * it never exposes tools/function calling;
    * it parses JSON and validates the whole object with Pydantic
      (``extra="forbid"`` rejects ``sql``, ``answer``, ``result`` and any other
      invented field);
    * it retries at most once for a transport error or an invalid structure;
    * after that it raises ``ProviderTechnicalError`` for a provider failure or
      ``UnverifiedQuery`` for an invalid plan, so the orchestrator reports
      UNVERIFIED instead of guessing or reporting NOT_FOUND.

    Credential selection, 401-only rotation, 429 scope cooldown and the circuit
    breaker live in :class:`app.llm.provider_manager.ProviderManager`.  The
    adapter never sees a second key decision and never logs a secret.
    """

    name = "llm"
    provider_name = "openai_compatible"
    # Subclasses (Groq) opt into strict json_schema structured output.
    use_strict_schema = False
    # When the provider receives the schema structurally, do not duplicate it
    # in the prompt.  This cuts token usage and rate-limit pressure without
    # changing the validated contract.
    schema_in_prompt = True

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self._settings.llm_timeout_seconds,
            transport=transport,
        )
        organization, project = self._scope_ids()
        self._manager = _make_manager(
            settings=self._settings,
            provider=self.provider_name,
            model=self._model_name(),
            entries=self._credential_entries(),
            organization_scope_id=organization,
            project_scope_id=project,
        )
        # Optional approved reference context.  RAG failure is non-fatal and
        # never changes the planner contract or the PostgreSQL fact path.
        self._retriever = None
        if self._settings.llm_rag_enabled:
            try:
                self._retriever = build_retriever(
                    top_k=self._settings.llm_rag_top_k,
                    min_relevance=self._settings.llm_rag_min_relevance,
                )
            except Exception:  # noqa: BLE001 - optional context only
                self._retriever = None

    # -- configuration hooks --------------------------------------------
    def _credential_entries(self) -> list[tuple[str, str]]:
        return [("generic-primary", self._settings.llm_api_key)]

    def _scope_ids(self) -> tuple[str, str]:
        return ("generic-default-org", "generic-default-project")

    def _model_name(self) -> str:
        return self._settings.llm_model

    def _configured(self) -> bool:
        return bool(self._settings.llm_api_key and self._settings.llm_model)

    def _request_options(self) -> dict[str, Any]:
        """Provider/model specific request fields (never a secret)."""

        return {}

    # -- public contract -------------------------------------------------
    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        if not self._configured():
            raise UnverifiedQuery("LLM planner is not configured")

        attempts = 2 if self._settings.llm_retry_once else 1
        messages = _messages(
            question,
            detection,
            include_schema=self.schema_in_prompt,
            reference_context=self._reference_context(question, detection),
        )
        last_error = "unknown"
        technical_failure = False
        last_technical: ProviderTechnicalError | None = None

        for attempt in range(attempts):
            try:
                content = self._request(messages)
                return _parse_plan(content, detection)
            except ProviderTechnicalError as exc:
                last_error = type(exc).__name__
                technical_failure = True
                last_technical = exc
                # Transport/provider errors are retried as-is; the request is
                # unchanged and no model output is echoed back.  A terminal
                # failure (daily quota, schema/config error, open breaker) is
                # never retried.
                if not exc.retry_allowed or attempt + 1 >= attempts:
                    break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = type(exc).__name__
                technical_failure = False
                if attempt + 1 < attempts:
                    messages = [
                        *messages,
                        {"role": "user", "content": CORRECTION_MESSAGE},
                    ]

        if technical_failure and last_technical is not None:
            # Preserve the classified failure so the failover planner can make
            # a policy decision (rate limit vs. credential vs. config error).
            raise last_technical
        raise UnverifiedQuery(
            f"LLM planner did not return a valid QueryPlan ({last_error})"
        )

    def respond(self, system: str, user: str) -> str:
        """Return a free-text answer through the same qualified provider.

        This path is used only after the planner has decided that the question
        is general knowledge or needs a labelled hypothesis block.  The
        provider still receives no tools and no database access.
        """

        if not self._configured():
            raise UnverifiedQuery("LLM planner is not configured")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = 2 if self._settings.llm_retry_once else 1
        last_error = "unknown"
        technical_failure = False
        last_technical: ProviderTechnicalError | None = None

        for attempt in range(attempts):
            try:
                return self._request(messages, structured=False)
            except ProviderTechnicalError as exc:
                last_error = type(exc).__name__
                technical_failure = True
                last_technical = exc
                if not exc.retry_allowed or attempt + 1 >= attempts:
                    break
            except ValueError as exc:
                last_error = type(exc).__name__
                technical_failure = False

        if technical_failure and last_technical is not None:
            raise last_technical
        raise UnverifiedQuery(
            f"LLM provider did not return a text answer ({last_error})"
        )

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": self._settings.llm_provider,
            "model": self._settings.llm_model or None,
            "structured_output": "json_schema" if self.use_strict_schema else "json_object",
            "reliability": self._manager.describe(),
            "rag": self._rag_status(),
        }

    def _rag_status(self) -> dict[str, object]:
        chunks = 0
        if self._retriever is not None:
            chunks = len(self._retriever.repository)
        return {
            "enabled": self._settings.llm_rag_enabled,
            "top_k": self._settings.llm_rag_top_k,
            "min_relevance": self._settings.llm_rag_min_relevance,
            "approved_chunks": chunks,
            "fact_source": "postgresql_only",
        }

    def _reference_context(
        self, question: str, detection: LanguageDetection
    ) -> str:
        """Return approved, untrusted reference context or an empty string.

        A retrieval failure must never block planning and must never be
        reported as NOT_FOUND.  The planner simply continues without context.
        """

        if self._retriever is None:
            return ""
        try:
            result = self._retriever.retrieve(
                question,
                language=detection.language.value,
                script=detection.script.value,
            )
        except Exception:  # noqa: BLE001 - optional context only
            return ""
        return result.reference_context

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LLMPlannerAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals -------------------------------------------------------
    def _response_format(self) -> dict[str, Any]:
        if self.use_strict_schema:
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "query_plan",
                    "strict": True,
                    "schema": openai_strict_schema(),
                },
            }
        return {"type": "json_object"}

    def _request(
        self, messages: list[dict[str, str]], *, structured: bool = True
    ) -> str:
        url = f"{self._settings.llm_base_url.rstrip('/')}/chat/completions"
        body: dict[str, Any] = {
            "model": self._settings.llm_model,
            "temperature": 0,
            "max_tokens": self._settings.llm_max_output_tokens,
            "messages": messages,
            # No tools, no function calling, no database access.
        }
        body.update(self._request_options())
        if structured:
            body["response_format"] = self._response_format()

        def send(api_key: str) -> httpx.Response:
            return self._client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

        return self._manager.execute(send, self._extract)

    @classmethod
    def _extract(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as exc:  # provider returned non-JSON envelope
            raise ProviderEnvelopeError(
                "LLM response envelope is not JSON"
            ) from exc
        return cls._content(payload)

    @staticmethod
    def _content(payload: object) -> str:
        if not isinstance(payload, dict):
            raise ValueError("LLM response envelope is not an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("LLM response has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ValueError("LLM response has no message")
        content = message.get("content")
        if isinstance(content, list):
            # Some OpenAI-compatible providers return content parts.
            parts = [
                part.get("text")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            content = "".join(parts)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM response content is empty")
        return content


class GroqPlannerAdapter(LLMPlannerAdapter):
    """Groq GPT-OSS adapter using strict JSON Schema output.

    It reuses the exact same Pydantic ``QueryPlan`` and the same prompt as the
    primary model; only the transport configuration differs.
    """

    name = "groq"
    provider_name = "groq"
    use_strict_schema = True
    schema_in_prompt = False

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        credential_entries: list[tuple[str, str]] | None = None,
        scope_ids: tuple[str, str] | None = None,
    ) -> None:
        # The base constructor calls these hooks, so the overrides must be set
        # before super().__init__ runs.
        self._credential_override = credential_entries
        self._scope_override = scope_ids
        active = settings or default_settings
        groq_settings = active.model_copy(
            update={
                "llm_provider": "groq",
                "llm_base_url": active.groq_base_url,
                "llm_api_key": active.groq_api_key,
                "llm_model": active.groq_model,
            }
        )
        super().__init__(groq_settings, transport=transport, client=client)

    # -- configuration hooks --------------------------------------------
    def _credential_entries(self) -> list[tuple[str, str]]:
        if self._credential_override is not None:
            return self._credential_override
        return self._settings.groq_credential_entries()

    def _scope_ids(self) -> tuple[str, str]:
        if self._scope_override is not None:
            return self._scope_override
        return (
            self._settings.groq_organization_scope_id,
            self._settings.groq_project_scope_id,
        )

    def _configured(self) -> bool:
        return bool(
            self._manager.registry.active() and self._settings.groq_model
        )

    def _request_options(self) -> dict[str, Any]:
        """Use Qwen's documented instruct mode for structured planning.

        Qwen3.8 is a dual-mode model.  With the default thinking mode it
        answers the planner prompt with terse, sometimes contradictory plans;
        ``reasoning_effort="none"`` selects instruct mode and yields the single
        JSON object the strict schema contract expects.  Only Qwen receives
        these fields, so the primary GPT-OSS request is unchanged.
        """

        model = (self._settings.groq_model or "").strip().lower()
        if model.startswith("qwen/"):
            return {"reasoning_effort": "none", "temperature": 0.7}
        return {}

    def describe(self) -> dict[str, object]:
        organization_scope_id, project_scope_id = self._scope_ids()
        return {
            "name": self.name,
            "provider": "groq",
            "model": self._settings.groq_model or None,
            "structured_output": "json_schema",
            "credential_scope": {
                "organization_scope_id": organization_scope_id,
                "project_scope_id": project_scope_id,
                "credential_count": len(self._manager.registry),
                "rotation_policy": "auth_401_same_scope_only",
                "rotate_on_429": False,
            },
            "reliability": self._manager.describe(),
            "rag": self._rag_status(),
        }


# ---------------------------------------------------------------------------
# Native Gemini adapter
# ---------------------------------------------------------------------------


class GeminiPlannerAdapter:
    """Native Gemini ``generateContent`` adapter with structured output.

    No tools, no code execution and no database access are offered.  The model
    only returns the strict ``QueryPlan`` JSON object; Pydantic validation and
    every later policy check happen on the server.
    """

    name = "gemini"
    provider_name = "gemini"
    schema_in_prompt = False

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self._settings.llm_timeout_seconds,
            transport=transport,
        )
        self._manager = _make_manager(
            settings=self._settings,
            provider="gemini",
            model=self._settings.gemini_model,
            entries=[
                ("gemini-primary", self._settings.gemini_api_key),
                ("gemini-secondary", self._settings.gemini_api_key_secondary),
            ],
            organization_scope_id="gemini-default-org",
            project_scope_id="gemini-default-project",
        )

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        if not self._settings.gemini_api_key or not self._settings.gemini_model:
            raise UnverifiedQuery("Gemini planner is not configured")

        attempts = 2 if self._settings.llm_retry_once else 1
        messages = _messages(
            question, detection, include_schema=self.schema_in_prompt
        )
        last_error = "unknown"
        technical_failure = False
        last_technical: ProviderTechnicalError | None = None

        for attempt in range(attempts):
            try:
                content = self._request(messages)
                return _parse_plan(content, detection)
            except ProviderTechnicalError as exc:
                last_error = type(exc).__name__
                technical_failure = True
                last_technical = exc
                if not exc.retry_allowed or attempt + 1 >= attempts:
                    break
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = type(exc).__name__
                technical_failure = False
                if attempt + 1 < attempts:
                    messages = [
                        *messages,
                        {"role": "user", "content": CORRECTION_MESSAGE},
                    ]

        if technical_failure and last_technical is not None:
            raise last_technical
        raise UnverifiedQuery(
            f"Gemini planner did not return a valid QueryPlan ({last_error})"
        )

    def respond(self, system: str, user: str) -> str:
        if not self._settings.gemini_api_key or not self._settings.gemini_model:
            raise UnverifiedQuery("Gemini planner is not configured")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        attempts = 2 if self._settings.llm_retry_once else 1
        last_error = "unknown"
        technical_failure = False
        last_technical: ProviderTechnicalError | None = None

        for attempt in range(attempts):
            try:
                return self._request(messages, structured=False)
            except ProviderTechnicalError as exc:
                last_error = type(exc).__name__
                technical_failure = True
                last_technical = exc
                if not exc.retry_allowed or attempt + 1 >= attempts:
                    break
            except ValueError as exc:
                last_error = type(exc).__name__
                technical_failure = False

        if technical_failure and last_technical is not None:
            raise last_technical
        raise UnverifiedQuery(
            f"Gemini did not return a text answer ({last_error})"
        )

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "provider": "gemini",
            "model": self._settings.gemini_model or None,
            "structured_output": self._settings.gemini_schema_mode,
            "reliability": self._manager.describe(),
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "GeminiPlannerAdapter":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals -------------------------------------------------------
    def _request(
        self, messages: list[dict[str, str]], *, structured: bool = True
    ) -> str:
        system_text = messages[0]["content"]
        user_text = messages[-1]["content"]
        model = self._settings.gemini_model.removeprefix("models/")
        url = (
            f"{self._settings.gemini_base_url.rstrip('/')}"
            f"/models/{model}:generateContent"
        )

        generation_config: dict[str, Any] = {
            "temperature": 0,
            "maxOutputTokens": self._settings.llm_max_output_tokens,
        }
        if structured:
            generation_config["responseMimeType"] = "application/json"
            if self._settings.gemini_schema_mode == "response_schema":
                generation_config["responseSchema"] = gemini_response_schema()
            else:
                generation_config["responseJsonSchema"] = (
                    gemini_response_json_schema()
                )
        else:
            generation_config["responseMimeType"] = "text/plain"

        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": generation_config,
            # No tools, no function declarations, no code execution.
        }

        def send(api_key: str) -> httpx.Response:
            return self._client.post(
                url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )

        return self._manager.execute(send, self._extract)

    @classmethod
    def _extract(cls, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderEnvelopeError(
                "Gemini response envelope is not JSON"
            ) from exc
        return cls._content(payload)

    @staticmethod
    def _content(payload: object) -> str:
        if not isinstance(payload, dict):
            raise ValueError("Gemini response envelope is not an object")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            # A blocked/empty answer is not a technical failure; it must fail
            # closed rather than be reinterpreted by another model.
            raise ValueError("Gemini response has no candidates")
        candidate = candidates[0]
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise ValueError("Gemini response has no content parts")
        text = "".join(
            part.get("text")
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise ValueError("Gemini response content is empty")
        return text


# ---------------------------------------------------------------------------
# Technical-failure-only failover
# ---------------------------------------------------------------------------


class FailoverPlanner:
    """Use the fallback model only when the primary provider fails technically.

    A valid ``QueryPlan`` with ``decision=EXECUTE`` that later produces an empty
    SQL result is NOT a failure and never reaches this class again: planning
    happens exactly once per request, before SQL execution.

    Policy details:

    * a terminal failure (invalid schema/config, exhausted credential set,
      open breaker) is never hidden by another model;
    * a rate limit may use only a *different, pre-qualified* provider or an
      independent organization/project quota scope, never another key or model
      in the same Groq quota scope;
    * an unqualified fallback is never used.

    Short TPM/RPM windows are included: an independent organization has its
    own budget, so using it is not key pooling and avoids a user-visible wait.
    The same scope still fails closed, preserving the quota boundary.
    """

    name = "failover"

    def __init__(
        self,
        primary: Any,
        fallback: Any,
        *,
        fallback_qualified: bool = True,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_qualified = fallback_qualified
        self.last_used: str | None = None

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        self.last_used = None
        try:
            plan = self._primary.plan(question, detection)
            self.last_used = "primary"
            return plan
        except ProviderTechnicalError as exc:
            # Only transport/HTTP/timeout/envelope failures reach this point.
            if not self._can_failover(exc):
                raise
            plan = self._fallback.plan(question, detection)
            self.last_used = "fallback"
            return plan

    def respond(self, system: str, user: str) -> str:
        self.last_used = None
        try:
            text = self._primary.respond(system, user)
            self.last_used = "primary"
            return text
        except ProviderTechnicalError as exc:
            if not self._can_failover(exc):
                raise
            text = self._fallback.respond(system, user)
            self.last_used = "fallback"
            return text

    def _can_failover(self, exc: ProviderTechnicalError) -> bool:
        if not self._fallback_qualified:
            return False
        if not exc.fallback_allowed:
            return False
        if not exc.is_rate_limit:
            return True
        failure = exc.failure
        if failure is None:
            return False
        # A rate limit belongs to a quota scope.  Only an independent scope
        # (or a genuinely different provider) may be used.  This applies to
        # both short TPM windows and long daily exhaustion: two Groq adapters
        # can share the provider name but belong to different organizations
        # with separate budgets.  A different model is also an independent
        # bucket: Groq applies RPM/RPD/TPM/TPD per model per organization, so
        # the same keys may legitimately continue on another model instead of
        # failing closed.
        primary_scope = self._quota_scope_of(self._primary)
        fallback_scope = self._quota_scope_of(self._fallback)
        if primary_scope and fallback_scope:
            return primary_scope != fallback_scope
        return self._provider_of(self._fallback) != self._provider_of(self._primary)

    @staticmethod
    def _provider_of(planner: Any) -> str:
        describe = getattr(planner, "describe", None)
        if not callable(describe):
            return ""
        try:
            return str(describe().get("provider", "")).strip().lower()
        except Exception:  # pragma: no cover - defensive
            return ""

    @staticmethod
    def _quota_scope_of(planner: Any) -> str:
        """Return ``provider:model:org:project`` for an adapter.

        The model is part of the key because Groq (and the other supported
        providers) meter rate limits per model inside the organization.  Two
        adapters that differ only by model therefore have independent buckets
        even when they share the same API keys.
        """

        describe = getattr(planner, "describe", None)
        if not callable(describe):
            return ""
        try:
            data = describe()
        except Exception:  # pragma: no cover - defensive
            return ""
        if not isinstance(data, dict):
            return ""
        scope = data.get("credential_scope")
        if not isinstance(scope, dict):
            return ""
        organization = scope.get("organization_scope_id")
        project = scope.get("project_scope_id")
        if not isinstance(organization, str) or not organization.strip():
            return ""
        provider = str(data.get("provider", "")).strip().lower()
        model = str(data.get("model", "")).strip().lower()
        return (
            f"{provider}:{model}:{organization.strip()}:"
            f"{str(project or '').strip()}"
        )

    def describe(self) -> dict[str, object]:
        primary_scope = self._quota_scope_of(self._primary)
        fallback_scope = self._quota_scope_of(self._fallback)
        same_provider = self._provider_of(self._fallback) == self._provider_of(
            self._primary
        )
        return {
            "name": self.name,
            "primary": self._primary.describe(),
            "fallback": self._fallback.describe(),
            "fallback_policy": "technical_provider_error_only",
            "fallback_qualified": self._fallback_qualified,
            "same_provider_rate_limit_failover": bool(
                same_provider and primary_scope and fallback_scope
                and primary_scope != fallback_scope
            ),
        }

    def close(self) -> None:
        for planner in (self._primary, self._fallback):
            close = getattr(planner, "close", None)
            if callable(close):
                close()
