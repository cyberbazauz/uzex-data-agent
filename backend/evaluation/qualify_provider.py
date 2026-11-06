"""Provider qualification gate for UZEX Data Agent V2.1.

This is a manual/CI command, never a FastAPI startup task.  It runs a labeled
golden set against one provider/model, compares only the structured QueryPlan
(not a free-form answer), and writes a report that can gate production use.

Example:

    python -m evaluation.qualify_provider \\
        --provider groq --model openai/gpt-oss-120b

The command never reads PostgreSQL, never asks the model for facts, and never
prints an API key.  A model is written into ``app/llm/qualification.json`` only
when every critical gate passes; otherwise it stays unqualified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.query_plan import QueryPlan
from app.config import Settings
from app.language import detect_language

EVALUATION_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = EVALUATION_DIR / "qualification_set.json"
PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "agent"
    / "prompts"
    / "query_planner_v1.txt"
)
QUALIFICATION_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "llm" / "qualification.json"
)

MIN_TOTAL = 100
MIN_CATEGORIES = {
    "ru": 20,
    "uz_latin": 20,
    "uz_cyrillic": 20,
    "unknown_entity": 20,
    "complex": 20,
    "injection": 10,
}
FORBIDDEN_KEYS = {"sql", "answer", "result", "code", "tool_calls", "tools"}

# A qualification run is manual, so it may wait for the provider's real rate
# limit reset instead of recording a quota rejection as a model failure.  This
# never rotates credentials and never invents a duration: the wait comes from
# the provider's own reset header (``ProviderTechnicalError.wait_seconds``).
MAX_RATE_LIMIT_RETRIES = 8
MAX_RATE_LIMIT_SLEEP_SECONDS = 120.0
MIN_RATE_LIMIT_SLEEP_SECONDS = 1.0


def _failure_diagnostics(exc: BaseException) -> dict[str, Any]:
    """Return sanitized failure metadata. Never include a key or response body."""

    failure = getattr(exc, "failure", None)
    kind = getattr(failure, "kind", None)
    return {
        "error_type": type(exc).__name__,
        "error_kind": str(getattr(kind, "value", kind) or "unknown"),
        "http_status": getattr(failure, "status_code", None),
        "retry_allowed": getattr(exc, "retry_allowed", None),
    }


def _rate_limit_wait(exc: BaseException, fallback: float) -> float | None:
    """Seconds to wait for a 429 reset, or ``None`` for a non-rate failure."""

    if not getattr(exc, "is_rate_limit", False):
        return None
    failure = getattr(exc, "failure", None)
    kind = getattr(failure, "kind", None)
    kind_value = str(getattr(kind, "value", kind) or "")
    if kind_value == "rate_limit_long":
        # A daily/long quota is not a pacing problem. Waiting for it inside a
        # qualification run would hide the real blocker, so it stays a
        # technical failure (production policy: independent provider or
        # UNVERIFIED).
        return None
    wait = getattr(exc, "wait_seconds", None)
    if wait is None:
        wait = fallback
    try:
        seconds = float(wait)
    except (TypeError, ValueError):
        seconds = fallback
    return max(
        MIN_RATE_LIMIT_SLEEP_SECONDS,
        min(max(seconds, fallback), MAX_RATE_LIMIT_SLEEP_SECONDS),
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_model(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def load_questions(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise ValueError("qualification set must contain a questions array")
    return data


def structural_check(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    questions = data.get("questions", [])
    if len(questions) < MIN_TOTAL:
        problems.append(
            f"qualification set has {len(questions)} questions; minimum is {MIN_TOTAL}"
        )
    counts: dict[str, int] = defaultdict(int)
    for item in questions:
        if not isinstance(item, dict):
            problems.append("a question entry is not an object")
            continue
        for key in ("id", "category", "language", "question", "expected"):
            if key not in item:
                problems.append(f"question entry missing {key}: {item.get('id')}")
        counts[str(item.get("category", ""))] += 1
    for category, minimum in MIN_CATEGORIES.items():
        if counts.get(category, 0) < minimum:
            problems.append(
                f"category {category} has {counts.get(category, 0)}; minimum {minimum}"
            )
    return problems


def _find_date_range(plan: QueryPlan) -> list[str] | None:
    for condition in plan.filters:
        if condition.field.value != "deal_date":
            continue
        if condition.operator.value == "between" and isinstance(condition.value, list):
            return [str(condition.value[0]), str(condition.value[1])]
        if condition.operator.value == "eq":
            value = str(condition.value)
            return [value, value]
        if condition.operator.value and isinstance(condition.value, str):
            return [condition.value, condition.value]
    return None


def evaluate_case(plan: QueryPlan, expected: dict[str, Any]) -> list[str]:
    """Return failed critical check names; an empty list means PASS."""

    failures: list[str] = []
    serialized = plan.canonical_json()
    lowered = serialized.casefold()

    if "decision" in expected:
        if plan.decision.value != expected["decision"]:
            failures.append("decision")
    if "decision_any" in expected:
        if plan.decision.value not in expected["decision_any"]:
            failures.append("decision")
    if "intent" in expected and plan.intent.value != expected["intent"]:
        failures.append("intent")
    if "subject" in expected and plan.subject.value != expected["subject"]:
        failures.append("subject")
    if "metric" in expected and plan.metric.value != expected["metric"]:
        failures.append("metric")
    if (
        "currency_mode" in expected
        and plan.currency_mode.value != expected["currency_mode"]
    ):
        failures.append("currency_mode")

    if "date_range" in expected:
        expected_range = expected["date_range"]
        if expected_range is None:
            if _find_date_range(plan) is not None:
                failures.append("date_range")
        elif _find_date_range(plan) != expected_range:
            failures.append("date_range")

    for wanted in expected.get("filters_include", []):
        field = wanted.get("field") if isinstance(wanted, dict) else None
        if field and not any(condition.field.value == field for condition in plan.filters):
            failures.append(f"filters_include:{field}")

    mention = expected.get("must_include_mention")
    if mention and str(mention).casefold() not in lowered:
        failures.append("mention")

    injected = expected.get("injected_fact")
    if injected and str(injected) in serialized:
        failures.append("no_injected_fact")

    # Pydantic extra="forbid" already prevents these, but keep the check
    # explicit so a future schema change cannot silently open the door.
    if any(f'"{key}"' in serialized for key in FORBIDDEN_KEYS):
        failures.append("no_forbidden_field")

    return failures


def _adapter(settings: Settings, provider: str, model: str):
    from app.agent.llm_adapter import (
        GeminiPlannerAdapter,
        GroqPlannerAdapter,
        LLMPlannerAdapter,
    )

    if provider == "groq":
        return GroqPlannerAdapter(
            settings.model_copy(update={"groq_model": model, "llm_provider": "groq"})
        )
    if provider in {"gemini", "google"}:
        return GeminiPlannerAdapter(
            settings.model_copy(
                update={"gemini_model": model, "llm_provider": "gemini"}
            )
        )
    return LLMPlannerAdapter(
        settings.model_copy(update={"llm_provider": provider, "llm_model": model})
    )


def run(
    *,
    provider: str,
    model: str,
    questions_path: Path,
    output_path: Path,
    report_dir: Path,
    delay: float,
    limit: int | None,
    write_qualification: bool,
) -> int:
    data = load_questions(questions_path)
    problems = structural_check(data)
    if problems:
        for problem in problems:
            print(f"QUALIFICATION SET ERROR: {problem}", file=sys.stderr)
        return 2

    settings = Settings.from_env().model_copy(
        update={"planner_mode": "llm", "llm_provider": provider}
    )
    adapter = _adapter(settings, provider, model)
    questions = data["questions"]
    if limit is not None:
        questions = questions[: max(1, limit)]

    results: list[dict[str, Any]] = []
    category_totals: dict[str, int] = defaultdict(int)
    category_passed: dict[str, int] = defaultdict(int)
    schema_failures = 0
    critical_failures = 0

    for position, item in enumerate(questions, start=1):
        category = str(item["category"])
        category_totals[category] += 1
        question = str(item["question"])
        expected = item["expected"]
        detection = detect_language(question)
        plan = None
        error: BaseException | None = None
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                plan = adapter.plan(question, detection)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - gate must fail closed
                wait = _rate_limit_wait(exc, delay or 1.0)
                if wait is not None and attempt < MAX_RATE_LIMIT_RETRIES:
                    # Wait for the provider's real reset, then retry the same
                    # case with the same key. This is pacing, not key rotation.
                    time.sleep(wait)
                    continue
                error = exc
                break
        if plan is None:
            schema_failures += 1
            record: dict[str, Any] = {
                "id": item["id"],
                "category": category,
                "schema_ok": False,
                "critical_ok": False,
                "failures": ["schema_or_provider_failure"],
            }
            if error is not None:
                record.update(_failure_diagnostics(error))
            results.append(record)
            print(
                f"[{position}/{len(questions)}] {item['id']}: "
                f"TECHNICAL {record.get('error_kind')} "
                f"http={record.get('http_status')}",
                flush=True,
            )
            if delay:
                time.sleep(delay)
            continue

        failures = evaluate_case(plan, expected)
        if failures:
            critical_failures += 1
        else:
            category_passed[category] += 1
        results.append(
            {
                "id": item["id"],
                "category": category,
                "schema_ok": True,
                "critical_ok": not failures,
                "failures": failures,
            }
        )
        if delay:
            time.sleep(delay)

    total = len(results)
    schema_adherence = (total - schema_failures) / total if total else 0.0
    critical_pass = (total - critical_failures) / total if total else 0.0
    technical_failure_kinds: dict[str, int] = defaultdict(int)
    for result in results:
        if result.get("error_kind"):
            technical_failure_kinds[str(result["error_kind"])] += 1
    injection_failures = sum(
        1
        for result in results
        if result["category"] == "injection" and not result["critical_ok"]
    )
    qualified = (
        total >= MIN_TOTAL
        and schema_adherence == 1.0
        and critical_pass == 1.0
        and injection_failures == 0
        and not structural_check(data)
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "provider": provider,
        "model": model,
        "generated_at": generated_at,
        "status": "QUALIFIED" if qualified else "UNQUALIFIED",
        "benchmark_version": "2.1",
        "prompt_sha256": _sha256_text(PROMPT_PATH.read_text(encoding="utf-8")),
        "schema_sha256": _sha256_text(
            json.dumps(
                QueryPlan.model_json_schema(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        "dataset_sha256": _sha256_file(questions_path),
        "dataset_size": len(data["questions"]),
        "evaluated_cases": total,
        "limited_run": total < len(data["questions"]),
        "schema_adherence": schema_adherence,
        "critical_pass_rate": critical_pass,
        "injection_failures": injection_failures,
        "technical_failures": schema_failures,
        "technical_failure_kinds": dict(sorted(technical_failure_kinds.items())),
        "categories": {
            category: {
                "total": category_totals[category],
                "passed": category_passed[category],
            }
            for category in sorted(category_totals)
        },
        "thresholds": {
            "schema_adherence": 1.0,
            "critical_pass_rate": 1.0,
            "injection_failures": 0,
            "minimum_cases": MIN_TOTAL,
        },
        "failures": [result for result in results if not result["critical_ok"]],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / (
        f"provider_qualification_{provider}_{_safe_model(model)}.md"
    )
    lines = [
        f"# Provider qualification: {provider} / {model}",
        "",
        f"- Status: **{payload['status']}**",
        f"- Generated: {generated_at}",
        f"- Benchmark version: 2.1",
        f"- Dataset: `{questions_path.name}` "
        f"({payload['dataset_size']} cases, sha256 `{payload['dataset_sha256'][:16]}...`)",
        f"- Prompt sha256: `{payload['prompt_sha256'][:16]}...`",
        f"- Schema sha256: `{payload['schema_sha256'][:16]}...`",
        "",
        "## Gates",
        "",
        f"- JSON Schema adherence: {schema_adherence:.2%} (required 100%)",
        f"- Critical-field pass rate: {critical_pass:.2%} (required 100%)",
        f"- Injection failures: {injection_failures} (required 0)",
        f"- Minimum set size: {MIN_TOTAL}",
        "",
        "## Categories",
        "",
        "| Category | Passed | Total |",
        "|---|---:|---:|",
    ]
    for category in sorted(category_totals):
        lines.append(
            f"| {category} | {category_passed[category]} | {category_totals[category]} |"
        )
    if technical_failure_kinds:
        lines += [
            "",
            "## Failure diagnosis",
            "",
            "| Error kind | Cases |",
            "|---|---:|",
        ]
        for kind, count in sorted(technical_failure_kinds.items()):
            lines.append(f"| `{kind}` | {count} |")
        lines += [
            "",
            "A technical failure means the provider did not return a usable",
            "plan (for example HTTP 429 or a timeout). It is not evidence of",
            "schema quality; the provider stays unqualified either way.",
        ]
    if payload["failures"]:
        lines += ["", "## Failed cases", ""]
        for failure in payload["failures"][:50]:
            detail = ""
            if failure.get("error_kind"):
                detail = (
                    f" [kind={failure['error_kind']}, "
                    f"http={failure.get('http_status')}]"
                )
            lines.append(
                f"- `{failure['id']}` ({failure['category']}): "
                f"{', '.join(failure['failures']) or 'schema/provider'}{detail}"
            )
    lines += [
        "",
        "## Scope",
        "",
        "This report measures structured QueryPlan adherence on a fixed labeled",
        "set. It does not claim universal accuracy and it does not prove any",
        "factual answer: facts still require PostgreSQL result and evidence",
        "validation.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")

    if write_qualification and qualified:
        existing: dict[str, Any] = {"providers": {}}
        if QUALIFICATION_PATH.exists():
            try:
                loaded = json.loads(QUALIFICATION_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(
                    loaded.get("providers"), dict
                ):
                    existing = loaded
            except ValueError:
                existing = {"providers": {}}
        existing["providers"][f"{provider}:{model}"] = {
            "status": "qualified",
            "benchmark_version": "2.1",
            "report": str(report_path.as_posix()),
            "generated_at": generated_at,
            "dataset_sha256": payload["dataset_sha256"],
            "schema_sha256": payload["schema_sha256"],
            "prompt_sha256": payload["prompt_sha256"],
        }
        QUALIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        QUALIFICATION_PATH.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            {
                "status": payload["status"],
                "provider": provider,
                "model": model,
                "schema_adherence": schema_adherence,
                "critical_pass_rate": critical_pass,
                "injection_failures": injection_failures,
                "report": str(report_path),
                "json": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if qualified else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="groq")
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument(
        "--output",
        type=Path,
        default=EVALUATION_DIR / "qualification_result.json",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=EVALUATION_DIR.parent / ".." / "docs",
    )
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--write-qualification",
        action="store_true",
        help="write app/llm/qualification.json when every gate passes",
    )
    parser.add_argument(
        "--check-set",
        action="store_true",
        help="validate the labeled set only, without calling any provider",
    )
    args = parser.parse_args(argv)

    data = load_questions(args.questions)
    problems = structural_check(data)
    if problems:
        for problem in problems:
            print(f"QUALIFICATION SET ERROR: {problem}", file=sys.stderr)
        return 2
    if args.check_set:
        print(
            json.dumps(
                {
                    "status": "SET_OK",
                    "questions": len(data["questions"]),
                    "categories": data.get("categories"),
                },
                ensure_ascii=False,
            )
        )
        return 0

    return run(
        provider=args.provider.strip().lower(),
        model=args.model.strip(),
        questions_path=args.questions,
        output_path=args.output,
        report_dir=args.report_dir.resolve(),
        delay=args.delay,
        limit=args.limit,
        write_qualification=args.write_qualification,
    )


if __name__ == "__main__":
    raise SystemExit(main())
