from __future__ import annotations

"""Run the V3 conversational evaluation set against the real Orchestrator.

The runner never mocks the database: it calls the same ``Orchestrator.chat``
path used by the Telegram bot and the FastAPI app.  Every numeric assertion is
checked against the control values already verified with PostgreSQL.
"""

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

DEFAULT_SET = Path(__file__).resolve().parent / "conversation_set.json"
DEFAULT_REPORT = (
    Path(__file__).resolve().parent / "reports" / "conversation_report.json"
)


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


def _structural_failures(response: Any) -> list[str]:
    failures: list[str] = []
    status = response.status
    if status is ResponseStatus.FOUND:
        if response.evidence is None:
            failures.append("FOUND without evidence")
        if response.data is None:
            failures.append("FOUND without data")
        if response.telemetry is None:
            failures.append("FOUND without telemetry")
        elif response.telemetry.query_plan is None:
            failures.append("FOUND without recorded query plan")
        elif response.telemetry.answer_validation not in {
            "grounded",
            "template",
            "generated_validated",
        }:
            failures.append(
                f"FOUND with answer_validation={response.telemetry.answer_validation}"
            )
    if status in {
        ResponseStatus.UNSUPPORTED,
        ResponseStatus.NEEDS_CLARIFICATION,
        ResponseStatus.UNVERIFIED,
    }:
        if response.data is not None:
            failures.append(f"{status.value} must not carry data")
        if response.evidence is not None:
            failures.append(f"{status.value} must not carry evidence")
    if status is ResponseStatus.NEEDS_CLARIFICATION and response.clarification is None:
        failures.append("clarification without payload")
    if status is ResponseStatus.NOT_FOUND:
        # An analytics query that matched zero rows must prove it with dataset
        # evidence.  A lookup that cannot resolve the named entity fails
        # before SQL runs, so it has no dataset evidence by design; that is
        # still an honest NOT_FOUND, not a fabricated one.
        plan = response.telemetry.query_plan if response.telemetry is not None else None
        lookup_intents = {
            "deal_lookup",
            "contract_lookup",
            "participant_lookup",
            "broker_lookup",
            "product_lookup",
        }
        entity_not_found = bool(
            plan
            and (
                plan.get("entity_candidates")
                or plan.get("intent") in lookup_intents
            )
        )
        if response.evidence is None and not entity_not_found:
            failures.append("NOT_FOUND without dataset evidence")
        elif response.evidence is not None and response.evidence.matched_rows != 0:
            failures.append("NOT_FOUND evidence with non-zero matched_rows")
        if response.data is not None:
            failures.append("NOT_FOUND must not carry data")
    return failures


def _run_turn(
    orchestrator: Orchestrator,
    case_id: str,
    session_id: str,
    spec: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    response = orchestrator.chat(
        spec["question"],
        request_id=f"eval-{case_id}",
        session_id=session_id,
    )
    failures: list[str] = []
    actual_status = response.status.value
    if actual_status != spec["expected_status"]:
        failures.append(
            f"status expected {spec['expected_status']}, got {actual_status}"
        )
    if spec.get("expected_value") is not None and not _value_matches(
        spec["expected_value"], _data_values(response)
    ):
        failures.append(
            f"value expected {spec['expected_value']}, "
            f"got {[str(value) for value in _data_values(response)]}"
        )
    if spec.get("locale") and response.locale != spec["locale"]:
        failures.append(f"locale expected {spec['locale']}, got {response.locale}")
    if "expect_context" in spec and response.telemetry is not None:
        actual_context = response.telemetry.context_used
        if actual_context != spec["expect_context"]:
            failures.append(
                f"context expected {spec['expect_context']}, got {actual_context}"
            )
    failures.extend(_structural_failures(response))

    record = {
        "question": spec["question"],
        "expected_status": spec["expected_status"],
        "expected_value": spec.get("expected_value"),
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
        "data_rows": len(response.data.rows) if response.data is not None else 0,
        "evidence_rows": (
            response.evidence.matched_rows if response.evidence is not None else None
        ),
        "context_used": (
            response.telemetry.context_used if response.telemetry is not None else None
        ),
        "planner_provider": (
            response.telemetry.planner_provider
            if response.telemetry is not None
            else None
        ),
        "query_plan": (
            response.telemetry.query_plan if response.telemetry is not None else None
        ),
        "passed": not failures,
        "failures": failures,
    }
    return record, response


def _run_case(orchestrator: Orchestrator, case: dict[str, Any]) -> dict[str, Any]:
    session_id = f"eval:{case['id']}"
    orchestrator.reset_session(session_id)
    turns: list[dict[str, Any]] = []
    try:
        for spec in case["turns"]:
            if case.get("locale") and "locale" not in spec:
                spec = {**spec, "locale": case["locale"]}
            record, _ = _run_turn(orchestrator, case["id"], session_id, spec)
            turns.append(record)
    except Exception as exc:  # noqa: BLE001 - the report must stay complete
        turns.append(
            {
                "question": "(runner exception)",
                "passed": False,
                "failures": [f"{type(exc).__name__}: {exc}"],
            }
        )
    finally:
        orchestrator.reset_session(session_id)

    passed = all(turn["passed"] for turn in turns)
    return {
        "id": case["id"],
        "category": case["category"],
        "language": case["language"],
        "locale": case.get("locale"),
        "turns": turns,
        "passed": passed,
    }


def _control_results(orchestrator: Orchestrator, control: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, spec in enumerate(control, start=1):
        record, _ = _run_turn(
            orchestrator,
            f"control-{index}",
            f"eval:control:{index}",
            spec,
        )
        results.append(record)
    return results


def _session_isolation(orchestrator: Orchestrator) -> dict[str, Any]:
    """A fresh session must never inherit another session's filters."""

    checks: list[dict[str, Any]] = []
    orchestrator.reset_session("eval:iso-a")
    orchestrator.reset_session("eval:iso-b")

    first = orchestrator.chat(
        "Сколько сделок было в 2024 году?", session_id="eval:iso-a"
    )
    follow_a = orchestrator.chat("А за 2023?", session_id="eval:iso-a")
    follow_b = orchestrator.chat("А за 2023?", session_id="eval:iso-b")

    checks.append(
        {
            "check": "same-session follow-up resolves",
            "passed": follow_a.status is ResponseStatus.FOUND
            and _value_matches(93985, _data_values(follow_a)),
            "detail": f"{follow_a.status.value} / {_data_values(follow_a)}",
        }
    )
    checks.append(
        {
            "check": "fresh session does not inherit filters",
            "passed": follow_b.status
            in {
                ResponseStatus.NEEDS_CLARIFICATION,
                ResponseStatus.UNSUPPORTED,
                ResponseStatus.NOT_FOUND,
            }
            and not _value_matches(93985, _data_values(follow_b)),
            "detail": f"{follow_b.status.value} / {_data_values(follow_b)}",
        }
    )
    checks.append(
        {
            "check": "first turn has no context",
            "passed": first.telemetry is not None
            and first.telemetry.context_used is False,
            "detail": (
                str(first.telemetry.context_used)
                if first.telemetry is not None
                else "no telemetry"
            ),
        }
    )

    orchestrator.reset_session("eval:iso-a")
    after_reset = orchestrator.chat("А за 2023?", session_id="eval:iso-a")
    checks.append(
        {
            "check": "reset clears session context",
            "passed": after_reset.status
            in {
                ResponseStatus.NEEDS_CLARIFICATION,
                ResponseStatus.UNSUPPORTED,
                ResponseStatus.NOT_FOUND,
            }
            and not _value_matches(93985, _data_values(after_reset)),
            "detail": f"{after_reset.status.value} / {_data_values(after_reset)}",
        }
    )
    orchestrator.reset_session("eval:iso-a")
    orchestrator.reset_session("eval:iso-b")
    return {"passed": all(check["passed"] for check in checks), "checks": checks}


def evaluate(
    payload: dict[str, Any],
    orchestrator: Orchestrator,
    *,
    only: set[str] | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    cases = payload["cases"]
    if only:
        cases = [case for case in cases if case["id"] in only]
    if category:
        cases = [case for case in cases if case["category"] == category]
    if not cases:
        raise ValueError("no cases selected")

    results = [_run_case(orchestrator, case) for case in cases]
    passed = sum(1 for item in results if item["passed"])

    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "turns": 0, "passed_turns": 0}
    )
    by_language: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "passed": 0}
    )
    status_counts: Counter[str] = Counter()
    turn_total = 0
    turn_passed = 0
    for item in results:
        by_category[item["category"]]["total"] += 1
        by_category[item["category"]]["passed"] += int(item["passed"])
        by_language[item["language"]]["total"] += 1
        by_language[item["language"]]["passed"] += int(item["passed"])
        for turn in item["turns"]:
            turn_total += 1
            turn_passed += int(turn["passed"])
            if "actual_status" in turn:
                status_counts[turn["actual_status"]] += 1
        by_category[item["category"]]["turns"] += len(item["turns"])
        by_category[item["category"]]["passed_turns"] += sum(
            int(turn["passed"]) for turn in item["turns"]
        )

    control = _control_results(orchestrator, payload.get("control", []))
    isolation = _session_isolation(orchestrator)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results), 4),
        "turns_total": turn_total,
        "turns_passed": turn_passed,
        "turn_pass_rate": round(turn_passed / turn_total, 4) if turn_total else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_language": dict(sorted(by_language.items())),
        "control": {
            "passed": all(item["passed"] for item in control),
            "total": len(control),
            "results": control,
        },
        "session_isolation": isolation,
        "results": results,
    }


def _print_summary(report: dict[str, Any], output: Path) -> None:
    print(
        f"conversation evaluation: {report['passed']}/{report['total']} cases "
        f"({report['pass_rate'] * 100:.1f}%), "
        f"{report['turns_passed']}/{report['turns_total']} turns "
        f"({report['turn_pass_rate'] * 100:.1f}%)"
    )
    print("statuses:", json.dumps(report["status_counts"], ensure_ascii=False))
    for category, stats in report["by_category"].items():
        print(
            f"  {category:12s} {stats['passed']:>2}/{stats['total']:<2} cases "
            f"{stats['passed_turns']:>2}/{stats['turns']:<2} turns"
        )
    for language, stats in report["by_language"].items():
        print(f"  lang {language:10s} {stats['passed']:>2}/{stats['total']}")
    control = report["control"]
    print(f"control questions: {sum(1 for r in control['results'] if r['passed'])}"
          f"/{control['total']} passed")
    for item in control["results"]:
        print(
            f"  {'PASS' if item['passed'] else 'FAIL'} | {item['question']} | "
            f"{item['actual_status']} | {item['data_value']}"
        )
    isolation = report["session_isolation"]
    print(f"session isolation: {'PASS' if isolation['passed'] else 'FAIL'}")
    for check in isolation["checks"]:
        print(
            f"  {'PASS' if check['passed'] else 'FAIL'} | {check['check']} | "
            f"{check['detail']}"
        )

    failures = [
        (case, turn)
        for case in report["results"]
        if not case["passed"]
        for turn in case["turns"]
        if not turn["passed"]
    ]
    if failures:
        print("\nfailures:")
        for case, turn in failures:
            print(f"  {case['id']} [{turn.get('actual_status')}] {turn.get('question')}")
            for failure in turn.get("failures", []):
                print(f"      - {failure}")
    print(f"\nreport: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the V3 conversation evaluation")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--only", action="append", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = json.loads(args.set.read_text(encoding="utf-8"))
    report = evaluate(
        payload,
        Orchestrator(),
        only=set(args.only) if args.only else None,
        category=args.category,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report, args.output)

    return (
        0
        if report["failed"] == 0
        and report["control"]["passed"]
        and report["session_isolation"]["passed"]
        else 1
    )


if __name__ == "__main__":
    sys.exit(main())
