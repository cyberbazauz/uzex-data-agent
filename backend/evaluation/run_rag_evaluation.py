"""Hybrid Knowledge RAG evaluation for UZEX Data Agent V2.1.

Two independent modes:

``--retrieval-only`` (default)
    Deterministic, offline evaluation of the retrieval layer.  It never calls
    an LLM and never reads PostgreSQL.  It proves the knowledge index contains
    only approved reference material, respects language/script isolation, and
    never returns a golden aggregate fact as context.

``--live``
    Optional identical-provider comparison of ``RAG_OFF`` versus ``RAG_ON`` on
    a labeled query set.  Only the structured ``QueryPlan`` is compared; no
    facts are produced and no SQL is executed.  The command requires explicit
    credentials and is never part of normal startup or test runs.

Neither mode enables RAG in configuration.  Enabling ``LLM_RAG_ENABLED`` is a
separate, evidence-based operator decision.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agent.llm_adapter import GroqPlannerAdapter, LLMPlannerAdapter  # noqa: E402
from app.config import Settings  # noqa: E402
from app.domain.errors import UnverifiedQuery  # noqa: E402
from app.language import detect_language  # noqa: E402
from app.rag.document_loader import load_knowledge  # noqa: E402
from app.rag.hybrid_retriever import (  # noqa: E402
    CONTEXT_HEADER,
    HybridRetriever,
)
from evaluation.qualify_provider import evaluate_case  # noqa: E402

EVALUATION_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = EVALUATION_DIR / "qualification_set.json"
DEFAULT_RETRIEVAL_REPORT = (
    EVALUATION_DIR / "reports" / "rag_retrieval_report.json"
)
DEFAULT_LIVE_REPORT = EVALUATION_DIR / "reports" / "rag_live_comparison.json"
DEFAULT_DOCS_DIR = BACKEND_ROOT.parent / "docs"

# A thousand-separated integer is the shape of a golden aggregate from the
# dataset documentation.  No retrieved chunk may contain one.
GOLDEN_FACT = re.compile(r"\d{1,3}(?:[ \u00a0]\d{3})+")

# Deterministic retrieval checks.  ``sources`` lists source ids that must be
# retrieved for the question; ``types`` lists required source types.
RETRIEVAL_CASES: list[dict[str, Any]] = [
    {
        "id": "ru_count_2024",
        "question": "Сколько сделок было в 2024 году?",
        "language": "ru",
        "script": "cyrillic",
        "sources": ["uzex_synonyms_ru", "uzex_query_examples"],
        "types": ["LANGUAGE_SYNONYM", "QUERY_PATTERN"],
    },
    {
        "id": "uz_latn_count_2025",
        "question": "2025-yilda nechta bitim bo'ldi?",
        "language": "uz",
        "script": "latin",
        "sources": ["uzex_synonyms_uz_latin", "uzex_query_examples"],
        "types": ["LANGUAGE_SYNONYM", "QUERY_PATTERN"],
    },
    {
        "id": "uz_cyrl_count_2025",
        "question": "2025-йилда нечта битим бўлди?",
        "language": "uz",
        "script": "cyrillic",
        "sources": ["uzex_synonyms_uz_cyrillic"],
        "types": ["LANGUAGE_SYNONYM"],
    },
    {
        "id": "ru_export_usd_sum",
        "question": "Какая общая сумма экспортных сделок в долларах?",
        "language": "ru",
        "script": "cyrillic",
        # Currency conversion and export rules live in the source README.
        "sources": ["uzex_source_readme"],
        "types": ["BUSINESS_RULE"],
    },
    {
        "id": "ru_top_sellers",
        "question": "Топ-5 продавцов по количеству сделок",
        "language": "ru",
        "script": "cyrillic",
        "sources": ["uzex_synonyms_ru"],
        "types": ["LANGUAGE_SYNONYM"],
    },
    {
        "id": "ru_volume_by_unit",
        "question": "Объём поставок по единицам измерения",
        "language": "ru",
        "script": "cyrillic",
        "sources": ["uzex_synonyms_ru"],
        "types": ["LANGUAGE_SYNONYM"],
    },
    {
        "id": "ru_warehouse_region_rule",
        "question": "Что такое регион склада?",
        "language": "ru",
        "script": "cyrillic",
        "sources": ["uzex_data_dictionary"],
        "types": ["FIELD_DEFINITION"],
    },
    {
        "id": "ru_missing_entity",
        "question": "Покажи участника Несуществующая Компания",
        "language": "ru",
        "script": "cyrillic",
        "sources": [],
        "types": [],
    },
]


def _context_has_language(result: Any, language: str, script: str) -> bool:
    for hit in result.hits:
        chunk = hit.chunk
        if chunk.language not in {"*", language}:
            return False
        if chunk.script not in {"*", script}:
            return False
    return True


def run_retrieval_only(
    *,
    output_path: Path,
    report_path: Path,
    top_k: int,
    min_relevance: float,
) -> int:
    loaded = load_knowledge()
    retriever = HybridRetriever(
        loaded,
        top_k=top_k,
        min_relevance=min_relevance,
        today=date(2026, 11, 5),
    )
    results: list[dict[str, Any]] = []
    golden_leaks = 0
    language_failures = 0
    missing_sources = 0
    injection_failures = 0

    for case in RETRIEVAL_CASES:
        result = retriever.retrieve(
            case["question"],
            language=case["language"],
            script=case["script"],
        )
        source_ids = [hit.chunk.source_id for hit in result.hits]
        source_types = [hit.chunk.source_type.value for hit in result.hits]
        context = result.reference_context

        failures: list[str] = []
        if result.hits:
            if CONTEXT_HEADER not in context:
                failures.append("context header missing")
                injection_failures += 1
            if "UNTRUSTED REFERENCE DATA" not in context:
                failures.append("untrusted marker missing")
                injection_failures += 1
        if context and GOLDEN_FACT.search(context):
            failures.append("golden aggregate leaked into context")
            golden_leaks += 1
        if not _context_has_language(result, case["language"], case["script"]):
            failures.append("language/script isolation violated")
            language_failures += 1
        for expected in case["sources"]:
            if expected not in source_ids:
                failures.append(f"expected source {expected!r} not retrieved")
                missing_sources += 1
        for expected_type in case["types"]:
            if expected_type not in source_types:
                failures.append(f"expected type {expected_type!r} not retrieved")
        if len(result.hits) > top_k:
            failures.append("top_k exceeded")

        results.append(
            {
                "id": case["id"],
                "question": case["question"],
                "language": case["language"],
                "script": case["script"],
                "hits": len(result.hits),
                "source_ids": source_ids,
                "source_types": source_types,
                "scores": [round(hit.relevance, 4) for hit in result.hits],
                "context_chars": len(context),
                "passed": not failures,
                "failures": failures,
            }
        )

    chunks = loaded.chunks
    source_type_counts = Counter(chunk.source_type.value for chunk in chunks)
    approved = all(
        chunk.approval_status.value == "APPROVED" for chunk in chunks
    )
    docs_have_golden = any(
        GOLDEN_FACT.search(chunk.chunk_text) for chunk in chunks
    )
    rejected = [item.model_dump() for item in loaded.rejected]

    passed = sum(1 for item in results if item["passed"])
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "retrieval_only",
        "live_calls": False,
        "status": "PASS"
        if (
            passed == len(results)
            and not docs_have_golden
            and approved
            and not rejected
        )
        else "FAIL",
        "top_k": top_k,
        "min_relevance": min_relevance,
        "index": {
            "chunks": len(chunks),
            "rejected": rejected,
            "source_type_counts": dict(sorted(source_type_counts.items())),
            "all_approved": approved,
            "golden_fact_in_chunks": docs_have_golden,
        },
        "cases_total": len(results),
        "cases_passed": passed,
        "golden_fact_leaks": golden_leaks,
        "language_isolation_failures": language_failures,
        "missing_expected_sources": missing_sources,
        "untrusted_marker_failures": injection_failures,
        "results": results,
        "limitations": [
            "Retrieval scores are interpretation hints, not factual evidence.",
            "This mode does not call a model and cannot prove answer accuracy.",
            "RAG remains disabled by default; enable only after a live "
            "RAG_OFF/RAG_ON run shows no regressions.",
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Hybrid RAG retrieval evaluation",
        "",
        f"- Mode: retrieval-only (offline, no LLM calls)",
        f"- Status: **{payload['status']}**",
        f"- Generated: {payload['generated_at']}",
        f"- Indexed chunks: {len(chunks)}",
        f"- Rejected sources/chunks: {len(rejected)}",
        f"- All chunks approved: {approved}",
        f"- Golden aggregate numbers in chunks: {docs_have_golden}",
        "",
        "## Source types",
        "",
        "| Source type | Chunks |",
        "|---|---:|",
    ]
    for name, count in sorted(source_type_counts.items()):
        lines.append(f"| {name} | {count} |")
    lines += [
        "",
        "## Retrieval cases",
        "",
        "| Case | Hits | Passed | Failures |",
        "|---|---:|---|---|",
    ]
    for item in results:
        lines.append(
            f"| `{item['id']}` | {item['hits']} | "
            f"{'yes' if item['passed'] else 'no'} | "
            f"{', '.join(item['failures']) or '—'} |"
        )
    lines += [
        "",
        "## Invariants",
        "",
        f"- Golden-fact leaks: {golden_leaks} (required 0)",
        f"- Language/script isolation failures: {language_failures} (required 0)",
        f"- Missing expected sources: {missing_sources} (required 0)",
        f"- Untrusted-marker failures: {injection_failures} (required 0)",
        "",
        "Retrieval only explains vocabulary and business rules. It is not a",
        "source of facts: every value, company, INN, product, sum and code",
        "must still be returned and validated from PostgreSQL.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "mode": "retrieval_only",
                "status": payload["status"],
                "cases": f"{passed}/{len(results)}",
                "chunks": len(chunks),
                "golden_fact_leaks": golden_leaks,
                "report": str(output_path),
                "markdown": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if payload["status"] == "PASS" else 1


def _adapter(settings: Settings) -> Any:
    provider = settings.llm_provider.strip().lower()
    if provider == "groq":
        return GroqPlannerAdapter(settings=settings)
    return LLMPlannerAdapter(settings=settings)


def run_live(
    *,
    questions_path: Path,
    output_path: Path,
    report_path: Path,
    limit: int | None,
    delay: float,
) -> int:
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = data.get("questions", data)
    if limit is not None:
        questions = questions[: max(1, limit)]

    base = Settings.from_env().model_copy(
        update={"planner_mode": "llm", "llm_provider": "groq"}
    )
    if not (base.groq_api_key and base.groq_model):
        print(
            "BLOCKED: no Groq credentials configured for a live comparison.",
            file=sys.stderr,
        )
        return 2

    settings_off = base.model_copy(update={"llm_rag_enabled": False})
    settings_on = base.model_copy(update={"llm_rag_enabled": True})
    adapter_off = _adapter(settings_off)
    adapter_on = _adapter(settings_on)

    results: list[dict[str, Any]] = []
    regressions = 0
    improvements = 0
    changed = 0
    off_latencies: list[float] = []
    on_latencies: list[float] = []

    try:
        for index, item in enumerate(questions):
            if index and delay > 0:
                time.sleep(delay)
            question = str(item["question"])
            expected = item.get("expected", {})
            detection = detect_language(question)

            row: dict[str, Any] = {
                "id": item.get("id"),
                "category": item.get("category"),
                "question": question,
            }
            for label, adapter, latencies in (
                ("off", adapter_off, off_latencies),
                ("on", adapter_on, on_latencies),
            ):
                started = time.perf_counter()
                plan = None
                error = None
                try:
                    plan = adapter.plan(question, detection)
                except UnverifiedQuery as exc:  # noqa: BLE001 - recorded
                    error = f"UnverifiedQuery: {exc}"
                except Exception as exc:  # noqa: BLE001 - recorded
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = (time.perf_counter() - started) * 1000
                latencies.append(elapsed)
                failures = (
                    ["schema_or_provider_failure"]
                    if plan is None
                    else evaluate_case(plan, expected)
                )
                row[label] = {
                    "plan": plan.model_dump(mode="json") if plan else None,
                    "error": error,
                    "latency_ms": round(elapsed, 1),
                    "passed": not failures,
                    "failures": failures,
                }

            if row["off"]["plan"] != row["on"]["plan"]:
                changed += 1
            if row["off"]["passed"] and not row["on"]["passed"]:
                regressions += 1
            if not row["off"]["passed"] and row["on"]["passed"]:
                improvements += 1
            results.append(row)
    finally:
        adapter_off.close()
        adapter_on.close()

    off_passed = sum(1 for row in results if row["off"]["passed"])
    on_passed = sum(1 for row in results if row["on"]["passed"])
    recommendation = (
        "eligible_for_manual_enable"
        if regressions == 0 and on_passed >= off_passed
        else "keep_rag_disabled"
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live_comparison",
        "live_calls": True,
        "provider": "groq",
        "model": base.groq_model,
        "questions": len(results),
        "limited_run": limit is not None,
        "rag_off": {
            "passed": off_passed,
            "pass_rate": round(off_passed / len(results), 4) if results else 0.0,
            "median_latency_ms": round(median(off_latencies), 1)
            if off_latencies
            else None,
        },
        "rag_on": {
            "passed": on_passed,
            "pass_rate": round(on_passed / len(results), 4) if results else 0.0,
            "median_latency_ms": round(median(on_latencies), 1)
            if on_latencies
            else None,
        },
        "plans_changed": changed,
        "regressions": regressions,
        "improvements": improvements,
        "recommendation": recommendation,
        "results": results,
        "limitations": [
            "QueryPlan comparison only; no SQL and no factual answer is proven.",
            "A limited run cannot authorize production enablement.",
            "RAG stays disabled unless an unreduced run shows no regressions.",
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Hybrid RAG live comparison (RAG_OFF vs RAG_ON)",
        "",
        f"- Provider/model: {payload['provider']} / {payload['model']}",
        f"- Questions: {payload['questions']}"
        f"{' (limited run)' if payload['limited_run'] else ''}",
        f"- Generated: {payload['generated_at']}",
        f"- Recommendation: **{recommendation}**",
        "",
        "| Variant | Passed | Pass rate | Median latency |",
        "|---|---:|---:|---:|",
        f"| RAG_OFF | {off_passed} | {payload['rag_off']['pass_rate']:.2%} | "
        f"{payload['rag_off']['median_latency_ms']} ms |",
        f"| RAG_ON | {on_passed} | {payload['rag_on']['pass_rate']:.2%} | "
        f"{payload['rag_on']['median_latency_ms']} ms |",
        "",
        f"- Plans changed by RAG: {changed}",
        f"- Regressions: {regressions} (required 0 to consider enabling)",
        f"- Improvements: {improvements}",
        "",
        "The comparison uses the identical provider, model, prompt and schema.",
        "It measures structured QueryPlan agreement only. PostgreSQL remains the",
        "only source of facts.",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "mode": "live_comparison",
                "off": f"{off_passed}/{len(results)}",
                "on": f"{on_passed}/{len(results)}",
                "regressions": regressions,
                "improvements": improvements,
                "recommendation": recommendation,
                "report": str(output_path),
                "markdown": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if regressions == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="run the deterministic offline retrieval evaluation (default)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="run a live RAG_OFF/RAG_ON structured-plan comparison",
    )
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--min-relevance", type=float, default=0.12)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args(argv)

    if args.live:
        return run_live(
            questions_path=args.questions,
            output_path=args.output or DEFAULT_LIVE_REPORT,
            report_path=args.report
            or (DEFAULT_LIVE_REPORT.with_suffix(".md")),
            limit=args.limit,
            delay=args.delay,
        )

    return run_retrieval_only(
        output_path=args.output or DEFAULT_RETRIEVAL_REPORT,
        report_path=args.report
        or (DEFAULT_RETRIEVAL_REPORT.with_suffix(".md")),
        top_k=args.top_k,
        min_relevance=args.min_relevance,
    )


if __name__ == "__main__":
    raise SystemExit(main())
