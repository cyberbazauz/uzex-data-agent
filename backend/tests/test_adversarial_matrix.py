from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest
from sqlalchemy import text

from app.agent.orchestrator import Orchestrator
from app.agent.query_plan import FilterCondition, QueryPlan
from app.domain.enums import (
    Decision,
    FilterField,
    Intent,
    Language,
    Metric,
    Operator,
    Script,
    Subject,
)
from app.domain.enums import ResponseStatus
from app.language import LanguageDetection

QUESTIONS = Path(__file__).resolve().parents[1] / "evaluation" / "questions.json"


class FixedPlanner:
    name = "fixed-adversarial"

    def __init__(self, plan: QueryPlan) -> None:
        self._plan = plan

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        return self._plan.model_copy(
            update={"language": detection.language, "script": detection.script}
        )

    def describe(self) -> dict[str, object]:
        return {"name": self.name}


def make_plan(**overrides: object) -> QueryPlan:
    values: dict[str, object] = {
        "decision": Decision.EXECUTE,
        "language": Language.RU,
        "script": Script.CYRILLIC,
        "intent": Intent.COUNT,
        "subject": Subject.DEALS,
        "metric": Metric.DEAL_COUNT,
    }
    values.update(overrides)
    return QueryPlan(**values)


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _evaluation_cases() -> list[dict[str, object]]:
    data = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return [
        {
            "id": item["id"],
            "question": item["question"],
            "expected_status": item["expected_status"],
            "expected_value": item.get("expected_value"),
        }
        for item in data
    ]


def _not_found_lookup_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for index in range(10):
        value = f"9999990{index:02d}"
        cases.append(
            {
                "id": f"NF-DEAL-{index:02d}",
                "question": f"lookup absent deal {value}",
                "plan": make_plan(
                    intent=Intent.DEAL_LOOKUP,
                    metric=Metric.RECORD_LOOKUP,
                    filters=[
                        FilterCondition(
                            field=FilterField.DEAL_NO,
                            operator=Operator.EQ,
                            value=value,
                        )
                    ],
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    for index in range(10):
        value = f"9999990{index:02d}"
        cases.append(
            {
                "id": f"NF-CONTRACT-{index:02d}",
                "question": f"lookup absent contract {value}",
                "plan": make_plan(
                    intent=Intent.CONTRACT_LOOKUP,
                    subject=Subject.CONTRACTS,
                    metric=Metric.RECORD_LOOKUP,
                    filters=[
                        FilterCondition(
                            field=FilterField.CONTRACT_NO,
                            operator=Operator.EQ,
                            value=value,
                        )
                    ],
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    for index in range(10):
        value = f"ZZZ No Such Company {index:04d}"
        cases.append(
            {
                "id": f"NF-PARTICIPANT-{index:02d}",
                "question": f"lookup absent participant {value}",
                "plan": make_plan(
                    intent=Intent.PARTICIPANT_LOOKUP,
                    subject=Subject.PARTICIPANTS,
                    metric=Metric.RECORD_LOOKUP,
                    entity_candidates=[
                        {"kind": "participant", "mention": value, "role": "any"}
                    ],
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    for index in range(10):
        value = f"0000000{index:02d}"
        cases.append(
            {
                "id": f"NF-INN-{index:02d}",
                "question": f"count absent seller INN {value}",
                "plan": make_plan(
                    filters=[
                        FilterCondition(
                            field=FilterField.SELLER_INN,
                            operator=Operator.EQ,
                            value=value,
                        )
                    ]
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    return cases


def _not_found_filter_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for year in range(2027, 2037):
        cases.append(
            {
                "id": f"NF-YEAR-{year}",
                "question": f"count deals in absent year {year}",
                "plan": make_plan(
                    filters=[
                        FilterCondition(
                            field=FilterField.DEAL_DATE,
                            operator=Operator.BETWEEN,
                            value=[f"{year}-01-01", f"{year}-12-31"],
                        )
                    ]
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    for index in range(10):
        value = f"9999999999{index:02d}"
        cases.append(
            {
                "id": f"NF-HS-{index:02d}",
                "question": f"count deals with absent HS code {value}",
                "plan": make_plan(
                    filters=[
                        FilterCondition(
                            field=FilterField.HS_CODE,
                            operator=Operator.EQ,
                            value=value,
                        )
                    ]
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    for index in range(10):
        value = f"Несуществующий Товар {index:03d}"
        cases.append(
            {
                "id": f"NF-PRODUCT-{index:02d}",
                "question": f"count deals for absent product {value}",
                "plan": make_plan(
                    filters=[
                        FilterCondition(
                            field=FilterField.PRODUCT_NAME,
                            operator=Operator.CONTAINS,
                            value=value,
                        )
                    ]
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    return cases


def _unsupported_cases() -> list[dict[str, object]]:
    intents = [item for item in Intent if item is not Intent.COMPARE]
    subjects = list(Subject)
    metrics = list(Metric)
    cases: list[dict[str, object]] = []
    for index in range(40):
        intent = intents[index % len(intents)]
        subject = subjects[index % len(subjects)]
        metric = metrics[index % len(metrics)]
        cases.append(
            {
                "id": f"UNSUPPORTED-{index:02d}",
                "question": f"unsupported request {index}",
                "plan": make_plan(
                    decision=Decision.UNSUPPORTED,
                    intent=intent,
                    subject=subject,
                    metric=metric,
                ),
                "expected_status": ResponseStatus.UNSUPPORTED,
            }
        )
    return cases


_INJECTION_PAYLOADS = [
    "100001' OR '1'='1",
    "100001' OR 1=1 --",
    "100001; DROP TABLE deals;--",
    "100001 UNION SELECT * FROM participants",
    "100001/**/OR/**/1=1",
    "100001' AND SLEEP(5)--",
    "100001' AND pg_sleep(5)--",
    "100001\\' OR \\'1\\'=\\'1",
    "100001\"; DROP TABLE deals;--",
    "100001\nOR 1=1",
    "100001%27%20OR%201=1",
    "100001' OR EXISTS(SELECT 1)--",
    "100001' OR 'a'='a",
    "100001'=0--",
    "100001` OR 1=1 --",
    "0x313030303031",
    "100001 OR TRUE",
    "100001) OR (1=1",
    "100001' OR current_user IS NOT NULL--",
    "100001' OR pg_catalog.version() IS NOT NULL--",
    "100001' AND 1=CAST((SELECT count(*) FROM participants) AS int)--",
    "100001' OR (SELECT 1 FROM deals LIMIT 1)=1--",
    "100001' OR 1 BETWEEN 1 AND 1--",
    "100001' OR 1 IN (1)--",
    "100001' OR 'x' LIKE '%'--",
    "100001' OR 1::text='1'--",
    "100001' OR NULL IS NULL--",
    "100001' OR NOT FALSE--",
    "100001' OR 2>1--",
    "100001' OR 1=1 LIMIT 1--",
]


def _injection_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for index, payload in enumerate(_INJECTION_PAYLOADS):
        cases.append(
            {
                "id": f"INJECTION-{index:02d}",
                "question": f"parameterized lookup payload {index}",
                "plan": make_plan(
                    intent=Intent.DEAL_LOOKUP,
                    metric=Metric.RECORD_LOOKUP,
                    filters=[
                        FilterCondition(
                            field=FilterField.DEAL_NO,
                            operator=Operator.EQ,
                            value=payload,
                        )
                    ],
                ),
                "expected_status": ResponseStatus.NOT_FOUND,
            }
        )
    return cases


def build_matrix() -> list[dict[str, object]]:
    matrix = _evaluation_cases()
    matrix.extend(_not_found_lookup_cases())
    matrix.extend(_not_found_filter_cases())
    matrix.extend(_unsupported_cases())
    matrix.extend(_injection_cases())
    return matrix


ADVERSARIAL_MATRIX = build_matrix()


def test_matrix_has_at_least_230_cases() -> None:
    assert len(ADVERSARIAL_MATRIX) >= 230
    identifiers = [item["id"] for item in ADVERSARIAL_MATRIX]
    assert len(identifiers) == len(set(identifiers))


@pytest.mark.parametrize(
    "case",
    ADVERSARIAL_MATRIX,
    ids=[str(item["id"]) for item in ADVERSARIAL_MATRIX],
)
def test_adversarial_matrix_case(case: dict[str, object]) -> None:
    plan = case.get("plan")
    planner = FixedPlanner(plan) if plan is not None else None
    response = Orchestrator(planner=planner).chat(str(case["question"]))

    expected = case["expected_status"]
    if isinstance(expected, str):
        assert response.status.value == expected, (
            f"{case['id']} expected {expected}, got {response.status.value}"
        )
    else:
        assert response.status is expected, (
            f"{case['id']} expected {expected}, got {response.status}"
        )

    if response.status is ResponseStatus.FOUND:
        assert response.data is not None
        assert response.evidence is not None
        expected_value = _decimal(case.get("expected_value"))
        if expected_value is not None:
            values = []
            if response.data.value is not None:
                values.append(response.data.value)
            values.extend(row.value for row in response.data.rows if row.value is not None)
            assert any(_decimal(value) == expected_value for value in values)
        assert "UNVERIFIED" not in response.answer
    else:
        assert response.data is None
        if response.status is ResponseStatus.NOT_FOUND:
            assert response.evidence is None or response.evidence.matched_rows == 0


def test_injection_matrix_did_not_modify_the_database(reader_conn) -> None:
    # The orchestrator uses its own read-only connection; this test proves the
    # parameterized payloads in the matrix were not interpreted as SQL.
    remaining = reader_conn.execute(text("SELECT count(*) FROM deals")).scalar_one()
    assert remaining == 500_000
