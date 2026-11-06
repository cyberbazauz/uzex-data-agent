from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.orchestrator import Orchestrator  # noqa: E402
from app.domain.enums import ResponseStatus  # noqa: E402

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "questions.json"
DEFAULT_REPORT = Path(__file__).resolve().parent / "reports" / "evaluation_report.json"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _data_values(response: Any) -> list[Any]:
    if response.data is None:
        return []
    values: list[Any] = []
    if response.data.value is not None:
        values.append(response.data.value)
    for row in response.data.rows:
        if row.value is not None:
            values.append(row.value)
    return values


def _value_matches(expected: Any, actual_values: list[Any]) -> bool:
    expected_decimal = _decimal(expected)
    if expected_decimal is None:
        return False
    for actual in actual_values:
        actual_decimal = _decimal(actual)
        if actual_decimal is not None and actual_decimal == expected_decimal:
            return True
    return False


def evaluate(questions: list[dict[str, Any]], orchestrator: Orchestrator) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in questions:
        response = orchestrator.chat(item["question"])
        actual_status = response.status.value
        failures: list[str] = []

        if actual_status != item["expected_status"]:
            failures.append(
                f"status expected {item['expected_status']}, got {actual_status}"
            )
        if item.get("locale") and response.locale != item["locale"]:
            failures.append(f"locale expected {item['locale']}, got {response.locale}")
        if item.get("expected_value") is not None and not _value_matches(
            item["expected_value"], _data_values(response)
        ):
            failures.append(
                f"value expected {item['expected_value']}, "
                f"got {[str(value) for value in _data_values(response)]}"
            )

        # Structural invariants checked for every response, independent of the
        # expected answer.
        if response.status is ResponseStatus.FOUND:
            if response.evidence is None:
                failures.append("FOUND without evidence")
            if response.data is None:
                failures.append("FOUND without data")
        if response.status in {
            ResponseStatus.UNSUPPORTED,
            ResponseStatus.NEEDS_CLARIFICATION,
            ResponseStatus.UNVERIFIED,
            # General AI Mode is conversational and never a verified fact.
            ResponseStatus.GENERAL,
        }:
            if response.data is not None:
                failures.append(f"{actual_status} must not carry data")
            if response.evidence is not None:
                failures.append(f"{actual_status} must not carry evidence")
        if response.status is ResponseStatus.NEEDS_CLARIFICATION:
            # Options are optional in the contract: side/meaning clarifications
            # are a single question, while entity ambiguity may list candidates.
            if response.clarification is None:
                failures.append("clarification without payload")
        if response.status is ResponseStatus.NOT_FOUND and response.evidence is not None:
            if response.evidence.matched_rows != 0:
                failures.append("NOT_FOUND evidence with non-zero matched_rows")

        results.append(
            {
                "id": item["id"],
                "group": item["group"],
                "language": item["language"],
                "question": item["question"],
                "expected_status": item["expected_status"],
                "actual_status": actual_status,
                "locale": response.locale,
                "request_id": response.request_id,
                "duration_ms": response.duration_ms,
                "answer": response.answer,
                "data_value": (
                    str(response.data.value)
                    if response.data is not None and response.data.value is not None
                    else None
                ),
                "data_rows": (
                    len(response.data.rows) if response.data is not None else 0
                ),
                "evidence_rows": (
                    response.evidence.matched_rows
                    if response.evidence is not None
                    else None
                ),
                "passed": not failures,
                "failures": failures,
            }
        )

    passed = sum(1 for item in results if item["passed"])
    by_group: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    status_counts: Counter[str] = Counter()
    for item in results:
        by_group[item["group"]]["total"] += 1
        by_group[item["group"]]["passed"] += int(item["passed"])
        status_counts[item["actual_status"]] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4) if results else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "by_group": dict(sorted(by_group.items())),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the UZEX 100-question evaluation")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="run only the given question id (repeatable)",
    )
    parser.add_argument("--json", action="store_true", help="print the full JSON report")
    args = parser.parse_args(argv)

    questions = json.loads(args.questions.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only)
        questions = [item for item in questions if item["id"] in wanted]
    if not questions:
        print("no questions selected", file=sys.stderr)
        return 2

    report = evaluate(questions, Orchestrator())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"evaluation: {report['passed']}/{report['total']} passed "
            f"({report['pass_rate'] * 100:.1f}%)"
        )
        print("statuses:", json.dumps(report["status_counts"], ensure_ascii=False))
        for group, stats in report["by_group"].items():
            print(f"  {group:15s} {stats['passed']:>2}/{stats['total']}")
        failures = [item for item in report["results"] if not item["passed"]]
        if failures:
            print("\nfailures:")
            for item in failures:
                print(f"  {item['id']} [{item['actual_status']}] {item['question']}")
                for failure in item["failures"]:
                    print(f"      - {failure}")
        print(f"\nreport: {args.output}")

    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
