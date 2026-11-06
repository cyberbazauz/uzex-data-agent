from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from app.agent.answer_generator import (
    AnswerGenerator,
    GroundedAnswerLayer,
    TemplateAnswerGenerator,
    VerifiedFactBundle,
)
from app.agent.conversation import (
    ConversationMemory,
    ConversationTurn,
    InMemoryConversationMemory,
    Referent,
    resolve_follow_up,
)
from app.agent.general import (
    ExplanationLayer,
    GeneralAnswerer,
    TextResponder,
    compose_explained_answer,
    looks_like_general_question,
    looks_like_registry_question,
)
from app.agent.planner import (
    LocalPlannerAdapter,
    QueryPlanner,
    build_planner,
)
from app.agent.plan_critic import (
    CriticDecision,
    PlanCritic,
    clarification_for,
)
from app.agent.query_plan import ExecutionPlan, QueryPlan
from app.config import Settings, settings as default_settings
from app.database.connection import get_active_dataset, readonly_connection
from app.domain.enums import (
    Decision,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    MatchKind,
    ResponseStatus,
    Subject,
)
from app.domain.errors import (
    AmbiguousEntity,
    EntityNotFound,
    UnsupportedQuery,
    UnverifiedQuery,
)
from app.entities.resolver import EntityResolver
from app.handlers.base import Evidence, HandlerResult
from app.handlers.dispatch import execute
from app.language import LanguageDetection, detect_language
from app.llm.errors import ProviderTechnicalError
from app.responses.formatter import (
    format_clarification,
    format_found,
    format_general_unavailable,
    format_not_found,
    format_unsupported,
    format_unverified,
)
from app.responses.models import (
    AnswerData,
    ChatResponse,
    ClarificationPayload,
    Telemetry,
)
from app.responses.templates import LocaleContext, context_for
from app.validation.answer_grounding import validate_answer_grounding
from app.validation.cross_check import validate_cross_checks
from app.validation.evidence_validator import validate_evidence
from app.validation.plan_validator import PlanValidator
from app.validation.result_validator import validate_result

logger = logging.getLogger("uzex.agent")

_REQUEST_ID = re.compile(r"[^A-Za-z0-9._:-]")
ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Executor = Callable[[Connection, ExecutionPlan, str], HandlerResult]


def normalize_request_id(value: str | None) -> str:
    if value:
        cleaned = _REQUEST_ID.sub("", value.strip())[:128]
        if cleaned:
            return cleaned
    return f"req_{uuid4().hex}"


def _exception_diagnostics(exc: BaseException) -> dict[str, object]:
    """Return sanitized failure fields only; never keys, prompts or bodies."""

    data = getattr(exc, "diagnostics", None)
    if callable(data):
        try:
            data = data()
        except Exception:  # noqa: BLE001 - diagnostics must never mask a query
            data = None
    if isinstance(data, dict):
        return data
    # Plain UnverifiedQuery/UnsupportedQuery messages are fixed server-side
    # strings (they never contain provider bodies, prompts or keys).  Keeping
    # the reason makes a validator rejection distinguishable from a provider
    # failure in the structured log.
    message = str(exc).strip().replace("\n", " ")
    if message:
        return {
            "error_type": type(exc).__name__,
            "reason": message[:200],
        }
    return {"error_type": type(exc).__name__}


class Orchestrator:
    """Run the mandated chain and map every failure to a safe public state."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        planner: QueryPlanner | None = None,
        connection_factory: ConnectionFactory | None = None,
        executor: Executor | None = None,
        plan_validator: PlanValidator | None = None,
        plan_critic: PlanCritic | None = None,
        memory: ConversationMemory | None = None,
        answer_generator: AnswerGenerator | None = None,
        general_responder: TextResponder | None = None,
    ) -> None:
        self._settings = settings or default_settings
        self._planner = planner or build_planner(self._settings)
        self._connection_factory = connection_factory or readonly_connection
        self._executor = executor or execute
        self._plan_validator = plan_validator or PlanValidator()
        if plan_critic is not None:
            self._plan_critic = plan_critic
        elif self._settings.llm_plan_critic_mode == "risk_based":
            self._plan_critic = PlanCritic(enabled=True)
        else:
            # Off by default: no extra model call and no behaviour change.
            self._plan_critic = None

        if self._settings.conversation_memory_enabled:
            self._memory: ConversationMemory | None = (
                memory
                or InMemoryConversationMemory(
                    max_turns=self._settings.conversation_max_turns,
                    max_sessions=self._settings.conversation_max_sessions,
                )
            )
        else:
            self._memory = None

        if self._settings.answer_generation_mode == "llm":
            self._answers: GroundedAnswerLayer | None = GroundedAnswerLayer(
                answer_generator or TemplateAnswerGenerator(),
                max_chars=self._settings.answer_generation_max_chars,
            )
        else:
            self._answers = None

        responder = general_responder
        if responder is None and callable(
            getattr(self._planner, "respond", None)
        ):
            # The qualified planning provider may also answer free text; it
            # still receives no tools and no database access.
            responder = self._planner  # type: ignore[assignment]

        self._general: GeneralAnswerer | None = None
        self._explanation: ExplanationLayer | None = None
        if responder is not None:
            if self._settings.general_mode_enabled:
                self._general = GeneralAnswerer(
                    responder,
                    max_chars=self._settings.general_answer_max_chars,
                )
            if self._settings.explanation_mode == "llm":
                self._explanation = ExplanationLayer(
                    responder,
                    max_chars=self._settings.explanation_max_chars,
                )

    def reset_session(self, session_id: str) -> None:
        if self._memory is not None:
            self._memory.reset(session_id)

    def chat(
        self,
        question: str,
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> ChatResponse:
        started = time.perf_counter()
        request = normalize_request_id(request_id)
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must not be blank")
        if len(question) > self._settings.max_question_length:
            raise ValueError("question exceeds the configured maximum length")

        detection = detect_language(question)
        ctx = context_for(detection.language, detection.script)

        describe: dict[str, object] = {}
        try:
            describe = self._planner.describe()
        except Exception:  # noqa: BLE001 - telemetry must never break a query
            describe = {}
        planner_provider = str(describe.get("name") or self._planner.name)
        planner_model = describe.get("model")
        if planner_model is None and planner_provider == "local":
            planner_model = "deterministic_v1"
        llm_attempted = planner_provider != "local"

        telemetry: dict[str, object] = {
            "planner_provider": planner_provider,
            "planner_model": planner_model,
            "llm_attempted": llm_attempted,
            "llm_duration_ms": None,
            "fallback_used": False,
            "fallback_reason": None,
            "cache_hit": False,
            "query_plan": None,
            "planner_duration_ms": None,
            "sql_duration_ms": None,
            "answer_validation": None,
            "mode": "data",
            "explanation_validation": None,
            "context_used": False,
            "session_id_present": bool(session_id),
        }
        plan: QueryPlan | None = None

        def respond(
            status: ResponseStatus,
            answer: str,
            *,
            data: AnswerData | None = None,
            clarification: ClarificationPayload | None = None,
            evidence: Evidence | None = None,
        ) -> ChatResponse:
            response = self._response(
                status,
                detection,
                request,
                started,
                answer,
                data=data,
                clarification=clarification,
                evidence=evidence,
            )
            response = response.model_copy(update={"telemetry": Telemetry(**telemetry)})
            try:
                self._remember(session_id, question, plan, response)
            except Exception:  # noqa: BLE001 - memory must never break an answer
                logger.warning(
                    "conversation_memory_failure request_id=%s", request
                )
            return response

        try:
            planner_started = time.perf_counter()
            pre_routed_general = (
                self._general is not None
                and looks_like_general_question(question)
            )
            if pre_routed_general:
                # A deterministic, token-free route for unambiguous general
                # questions.  It still creates a validated QueryPlan and never
                # opens a database connection; ambiguous questions use the
                # planner below.
                plan = QueryPlan.general(
                    language=detection.language,
                    script=detection.script,
                )
                telemetry["planner_provider"] = "pre_router"
                telemetry["planner_model"] = "deterministic_general"
                telemetry["llm_attempted"] = False
                telemetry["planner_duration_ms"] = 0
                logger.info(
                    "general_pre_router request_id=%s language=%s",
                    request,
                    detection.language.value,
                )
            else:
                plan = self._planner.plan(question, detection)
                planner_ms = max(
                    0, int(round((time.perf_counter() - planner_started) * 1000))
                )
                telemetry["planner_duration_ms"] = planner_ms
                if llm_attempted:
                    telemetry["llm_duration_ms"] = planner_ms

            previous = self._memory.get_last(session_id) if session_id and self._memory else None
            merged = resolve_follow_up(question, detection, previous)
            if merged is not None and plan.decision is not Decision.GENERAL:
                # An explicit GENERAL classification is never reinterpreted as
                # an elliptical data follow-up.  The planner saw the full text.
                plan = merged
                telemetry["context_used"] = True
            telemetry["query_plan"] = plan.model_dump(mode="json")

            if plan.decision is Decision.GENERAL:
                telemetry["mode"] = "general"
                if looks_like_registry_question(question):
                    # Defence in depth: a ``GENERAL`` decision must never hide
                    # a request for registry facts.  The deterministic planner
                    # can only produce a strict, already-tested data plan.
                    plan = LocalPlannerAdapter().plan(question, detection)
                    telemetry["mode"] = "data"
                    telemetry["fallback_used"] = True
                    telemetry["fallback_reason"] = "general_mode_guard"
                    telemetry["query_plan"] = plan.model_dump(mode="json")
                else:
                    if self._general is None:
                        raise UnverifiedQuery("general mode is not configured")
                    history = (
                        self._memory.recent(session_id)
                        if session_id and self._memory is not None
                        else ()
                    )
                    try:
                        answer = self._general.answer(
                            question, detection, history
                        )
                    except ProviderTechnicalError as exc:
                        logger.warning(
                            "general_provider_failure request_id=%s diagnostics=%s",
                            request,
                            exc.diagnostics,
                        )
                        telemetry["answer_validation"] = "general_provider_error"
                        telemetry["fallback_used"] = True
                        telemetry["fallback_reason"] = "general_provider_error"
                        return respond(
                            ResponseStatus.GENERAL_UNAVAILABLE,
                            format_general_unavailable(ctx),
                        )
                    except UnverifiedQuery as exc:
                        logger.warning(
                            "general_validation_failure request_id=%s "
                            "error_type=%s reason=%s",
                            request,
                            type(exc).__name__,
                            str(exc),
                        )
                        telemetry["answer_validation"] = "general_validation_error"
                        telemetry["fallback_used"] = True
                        telemetry["fallback_reason"] = "general_validation_error"
                        return respond(
                            ResponseStatus.GENERAL_UNAVAILABLE,
                            format_general_unavailable(ctx),
                        )
                    except Exception as exc:  # noqa: BLE001 - isolate general AI
                        logger.warning(
                            "general_answer_failure request_id=%s error_type=%s",
                            request,
                            type(exc).__name__,
                        )
                        telemetry["answer_validation"] = "general_error"
                        telemetry["fallback_used"] = True
                        telemetry["fallback_reason"] = "general_error"
                        return respond(
                            ResponseStatus.GENERAL_UNAVAILABLE,
                            format_general_unavailable(ctx),
                        )
                    telemetry["answer_validation"] = "general_validated"
                    return respond(ResponseStatus.GENERAL, answer)

            if plan.decision is Decision.CLARIFY:
                clarification = plan.clarification
                if clarification is None:
                    raise UnverifiedQuery("CLARIFY plan has no clarification payload")
                clarification_text = format_clarification(
                    ctx, clarification.code, clarification.options
                )
                return respond(
                    ResponseStatus.NEEDS_CLARIFICATION,
                    clarification_text,
                    clarification=ClarificationPayload(
                        code=clarification.code,
                        question=clarification_text,
                        options=clarification.options,
                    ),
                )
            if plan.decision is Decision.UNSUPPORTED:
                return respond(
                    ResponseStatus.UNSUPPORTED,
                    format_unsupported(ctx),
                )
            if plan.decision is Decision.EXECUTE and plan.explanation_requested:
                telemetry["mode"] = "data_explanation"

            if self._plan_critic is not None and plan.decision is Decision.EXECUTE:
                # The critic may only narrow a risky plan to clarification or
                # unsupported.  It cannot add entities, facts or permissions.
                critic = self._plan_critic.review(question, plan)
                if critic.decision is CriticDecision.CLARIFY:
                    code = clarification_for(critic)
                    clarification_text = format_clarification(ctx, code, [])
                    return respond(
                        ResponseStatus.NEEDS_CLARIFICATION,
                        clarification_text,
                        clarification=ClarificationPayload(
                            code=code,
                            question=clarification_text,
                            options=[],
                        ),
                    )
                if critic.decision is CriticDecision.REJECT:
                    return respond(
                        ResponseStatus.UNSUPPORTED,
                        format_unsupported(ctx),
                    )

            with self._connection_factory() as conn:
                dataset = get_active_dataset(conn)
                if dataset is None:
                    raise UnverifiedQuery("no active dataset is available")
                if dataset.get("validation_status") != "PASSED":
                    raise UnverifiedQuery("active dataset did not pass validation")
                version = str(dataset["dataset_version"])
                cutoff = str(dataset["max_deal_date"])
                bounds = _dataset_bounds(dataset)

                self._plan_validator.validate(plan, bounds)
                execution = EntityResolver(conn).resolve(plan)
                sql_started = time.perf_counter()
                result = self._executor(conn, execution, request)
                telemetry["sql_duration_ms"] = max(
                    0, int(round((time.perf_counter() - sql_started) * 1000))
                )
                validate_result(
                    result,
                    execution,
                    dataset_version=version,
                    dataset_cutoff=cutoff,
                )
                validate_cross_checks(result, execution)

                if not result.has_answer:
                    evidence = result.to_evidence(require_answer=False)
                    if evidence is None:
                        raise UnverifiedQuery(
                            "empty result has no dataset evidence metadata"
                        )
                    mode = "zero" if result.matched_rows == 0 else "non_negative"
                    validate_evidence(
                        evidence,
                        dataset_version=version,
                        dataset_cutoff=cutoff,
                        matched_rows_mode=mode,
                    )
                    telemetry["answer_validation"] = "not_applicable"
                    return respond(
                        ResponseStatus.NOT_FOUND,
                        format_not_found(ctx, no_value=result.matched_rows > 0),
                        evidence=evidence,
                    )

                evidence = result.to_evidence()
                if evidence is None:
                    raise UnverifiedQuery("found result has no evidence")
                validate_evidence(
                    evidence,
                    dataset_version=version,
                    dataset_cutoff=cutoff,
                    matched_rows_mode="positive",
                )
                formatted = format_found(execution, result, ctx)
                validate_answer_grounding(formatted.answer, execution, result)
                answer = formatted.answer
                telemetry["answer_validation"] = "grounded"
                if self._answers is not None:
                    bundle = VerifiedFactBundle(
                        status=ResponseStatus.FOUND.value,
                        facts=formatted.data.model_dump(mode="json"),
                        evidence={
                            "source_tables": evidence.source_tables,
                            "dataset_version": evidence.dataset_version,
                            "matched_rows": evidence.matched_rows,
                        },
                        deterministic_answer=formatted.answer,
                    )
                    answer, validation = self._answers.render(
                        bundle, execution, result
                    )
                    telemetry["answer_validation"] = validation
                if plan.explanation_requested:
                    if self._explanation is None:
                        # Explanation is optional: the verified deterministic
                        # answer is still complete and must be returned as is.
                        telemetry["explanation_validation"] = "disabled"
                    else:
                        try:
                            explanation = self._explanation.explain(
                                question=question,
                                detection=detection,
                                deterministic_answer=answer,
                                execution=execution,
                                result=result,
                                data=formatted.data,
                                evidence=evidence.model_dump(mode="json"),
                            )
                            answer = compose_explained_answer(
                                answer,
                                explanation,
                                locale=detection.locale,
                            )
                            telemetry["explanation_validation"] = "validated"
                        except Exception as exc:  # noqa: BLE001 - keep the facts
                            telemetry["explanation_validation"] = "fallback"
                            logger.warning(
                                "explanation_rejected request_id=%s reason=%s: %s",
                                request,
                                type(exc).__name__,
                                exc,
                            )
                return respond(
                    ResponseStatus.FOUND,
                    answer,
                    data=formatted.data,
                    evidence=evidence,
                )

        except AmbiguousEntity as exc:
            clarification_text = format_clarification(
                ctx, exc.clarification_code, exc.options
            )
            return respond(
                ResponseStatus.NEEDS_CLARIFICATION,
                clarification_text,
                clarification=ClarificationPayload(
                    code=exc.clarification_code,
                    question=clarification_text,
                    options=exc.options,
                ),
            )
        except EntityNotFound as exc:
            return respond(
                ResponseStatus.NOT_FOUND,
                format_not_found(ctx, entity=exc),
            )
        except UnsupportedQuery:
            return respond(
                ResponseStatus.UNSUPPORTED,
                format_unsupported(ctx),
            )
        except UnverifiedQuery as exc:
            logger.warning(
                "query_unverified request_id=%s mode=%s diagnostics=%s",
                request,
                telemetry.get("mode"),
                _exception_diagnostics(exc),
            )
            return respond(
                ResponseStatus.UNVERIFIED,
                format_unverified(ctx),
            )
        except (SQLAlchemyError, TimeoutError, OSError) as exc:
            logger.warning(
                "database_or_timeout_failure request_id=%s error_type=%s",
                request,
                type(exc).__name__,
            )
            return respond(
                ResponseStatus.UNVERIFIED,
                format_unverified(ctx),
            )
        except Exception as exc:  # noqa: BLE001 - public API must fail closed
            logger.warning(
                "unexpected_query_failure request_id=%s error_type=%s",
                request,
                type(exc).__name__,
            )
            return respond(
                ResponseStatus.UNVERIFIED,
                format_unverified(ctx),
            )

    def _remember(
        self,
        session_id: str | None,
        question: str,
        plan: QueryPlan | None,
        response: ChatResponse,
    ) -> None:
        if not session_id or self._memory is None or plan is None:
            return

        if plan.decision is Decision.GENERAL:
            # General turns are remembered as dialogue context only.  They are
            # never used as registry facts and carry no referents or data.  A
            # failed general answer is not dialogue and must not shape the next
            # turn.
            if response.status is not ResponseStatus.GENERAL:
                return
            self._memory.append(
                session_id,
                ConversationTurn(
                    question=question,
                    plan=plan,
                    status=response.status.value,
                    answer=response.answer,
                ),
            )
            return

        if (
            plan.decision is not Decision.EXECUTE
            or response.status
            not in (ResponseStatus.FOUND, ResponseStatus.NOT_FOUND)
        ):
            return
        self._memory.append(
            session_id,
            ConversationTurn(
                question=question,
                plan=plan,
                status=response.status.value,
                referents=_turn_referents(plan, response),
                answer=response.answer,
            ),
        )

    def _response(
        self,
        status: ResponseStatus,
        detection: LanguageDetection,
        request_id: str,
        started: float,
        answer: str,
        *,
        data: AnswerData | None = None,
        clarification: ClarificationPayload | None = None,
        evidence: Evidence | None = None,
    ) -> ChatResponse:
        duration_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
        response = ChatResponse(
            status=status,
            language=detection.language.value,
            script=detection.script.value,
            locale=detection.locale,
            answer=answer,
            data=data,
            clarification=clarification,
            evidence=evidence,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        logger.info(
            "chat_completed request_id=%s status=%s duration_ms=%s",
            request_id,
            status.value,
            duration_ms,
        )
        return response


def _turn_referents(
    plan: QueryPlan, response: ChatResponse
) -> tuple[Referent, ...]:
    """Extract only companies that the validated answer actually returned."""

    if (
        plan.subject is not Subject.PARTICIPANTS
        and plan.intent is not Intent.PARTICIPANT_LOOKUP
    ):
        return ()
    if response.data is None:
        return ()

    role: EntityRole | None = None
    if GroupField.SELLER_INN in plan.group_by:
        role = EntityRole.SELLER
    elif GroupField.BUYER_INN in plan.group_by:
        role = EntityRole.BUYER
    else:
        for entity in plan.entity_candidates:
            if entity.kind is MatchKind.PARTICIPANT and entity.role in (
                EntityRole.SELLER,
                EntityRole.BUYER,
            ):
                role = entity.role
                break

    referents: list[Referent] = []
    for row in response.data.rows:
        name = row.label
        if not name:
            name = next(
                (str(value) for value in row.group.values() if value), None
            )
        if not name:
            name = next(
                (str(value) for value in row.fields.values() if value), None
            )
        if name:
            referents.append(Referent(name=name, role=role))
    return tuple(referents[:10])


def _dataset_bounds(dataset: dict[str, Any]) -> tuple[date, date] | None:
    minimum = dataset.get("min_deal_date")
    maximum = dataset.get("max_deal_date")
    if minimum is None or maximum is None:
        return None
    if isinstance(minimum, date) and isinstance(maximum, date):
        return minimum, maximum
    try:
        return date.fromisoformat(str(minimum)), date.fromisoformat(str(maximum))
    except ValueError:
        return None
