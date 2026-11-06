from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.query_plan import QueryPlan
from app.rag.chunker import contains_instruction_injection
from app.rag.document_loader import KNOWLEDGE_DIR
from app.rag.knowledge_repository import tokenize

DEFAULT_EXAMPLES = KNOWLEDGE_DIR / "query_examples.json"


class ApprovedExample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    example_id: str
    question: str = Field(min_length=3, max_length=500)
    language: str
    script: str
    plan: QueryPlan
    tags: list[str] = Field(default_factory=list)
    test_fixture_id: str | None = None
    prompt_version: str = "2.1"
    schema_version: str = "1.0"


@dataclass
class ExampleLoadResult:
    examples: list[ApprovedExample] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


def load_approved_examples(path: Path | None = None) -> ExampleLoadResult:
    """Load only APPROVED examples whose plan validates against ``QueryPlan``.

    Examples never contain a stored answer or a golden number; they teach the
    question -> plan mapping.  An invalid plan is rejected instead of being
    weakened.
    """

    source = path or DEFAULT_EXAMPLES
    result = ExampleLoadResult()
    if not source.exists():
        return result
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        result.rejected.append("examples_file_invalid")
        return result
    entries = data.get("examples") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        result.rejected.append("examples_not_a_list")
        return result
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        example_id = str(entry.get("example_id", ""))
        if entry.get("approval_status") != "APPROVED":
            result.rejected.append(f"{example_id}:not_approved")
            continue
        if contains_instruction_injection(str(entry.get("question", ""))):
            result.rejected.append(f"{example_id}:instruction_injection")
            continue
        try:
            example = ApprovedExample(
                example_id=example_id,
                question=str(entry["question"]),
                language=str(entry["language"]),
                script=str(entry["script"]),
                plan=QueryPlan.model_validate(entry["plan"]),
                tags=[str(tag) for tag in entry.get("tags", [])],
                test_fixture_id=entry.get("test_fixture_id"),
                prompt_version=str(entry.get("prompt_version", "2.1")),
                schema_version=str(entry.get("schema_version", "1.0")),
            )
        except (KeyError, TypeError, ValidationError) as exc:
            result.rejected.append(f"{example_id}:{type(exc).__name__}")
            continue
        result.examples.append(example)
    return result


def _plan_pattern(plan: QueryPlan) -> dict[str, Any]:
    # Values (dates, amounts, names) are intentionally omitted.
    return {
        "decision": plan.decision.value,
        "intent": plan.intent.value,
        "subject": plan.subject.value,
        "metric": plan.metric.value,
        "currency_mode": plan.currency_mode.value,
        "filters": [
            {"field": condition.field.value, "operator": condition.operator.value}
            for condition in plan.filters
        ],
        "group_by": [field.value for field in plan.group_by],
        "clarification": (
            plan.clarification.code.value if plan.clarification is not None else None
        ),
    }


def select_examples(
    question: str,
    *,
    language: str,
    script: str,
    examples: list[ApprovedExample] | None = None,
    max_examples: int = 1,
    min_score: float = 0.6,
) -> list[ApprovedExample]:
    pool = examples if examples is not None else load_approved_examples().examples
    query_tokens = set(tokenize(question))
    scored: list[tuple[float, ApprovedExample]] = []
    for example in pool:
        if example.language not in {"*", language}:
            continue
        if example.script not in {"*", script}:
            continue
        overlap = len(query_tokens & set(tokenize(example.question)))
        if overlap == 0:
            continue
        score = overlap / max(1, len(query_tokens))
        if score < min_score:
            continue
        scored.append((score, example))
    scored.sort(key=lambda item: (-item[0], item[1].example_id))
    return [example for _, example in scored[:max_examples]]


def render_examples(examples: list[ApprovedExample]) -> str:
    if not examples:
        return ""
    blocks = [
        "APPROVED QUERY PATTERNS (reference data, not answers; never copy "
        "numeric values from them)."
    ]
    for example in examples:
        blocks.append(
            json.dumps(
                {
                    "question": example.question,
                    "plan_pattern": _plan_pattern(example.plan),
                    "tags": example.tags,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(blocks)
