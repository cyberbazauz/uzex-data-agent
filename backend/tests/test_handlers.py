from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.agent.query_plan import (
    ComparisonPeriod,
    ComparisonSpec,
    ExecutionPlan,
    FilterCondition,
    QueryPlan,
    ResolvedEntity,
)
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    Language,
    MatchKind,
    Metric,
    Operator,
    RegionMeaning,
    Script,
    Subject,
)
from app.domain.errors import AmbiguousEntity, UnsupportedQuery
from app.handlers.dispatch import execute

DATASET_VERSION = "sha256:96424654325b6c41074b67aa3919435747f9cbc029a7fe9bf5e2dd02810e8fab"


def make_execution(
    *,
    intent: Intent,
    metric: Metric = Metric.DEAL_COUNT,
    subject: Subject = Subject.DEALS,
    filters: list[FilterCondition] | None = None,
    entities: list[ResolvedEntity] | None = None,
    product_contract_count: int | None = None,
    **plan_kwargs: object,
) -> ExecutionPlan:
    filter_list = list(filters or [])
    plan = QueryPlan(
        decision=Decision.EXECUTE,
        language=Language.RU,
        script=Script.CYRILLIC,
        intent=intent,
        subject=subject,
        metric=metric,
        filters=filter_list,
        **plan_kwargs,
    )
    return ExecutionPlan(
        plan=plan,
        filters=filter_list,
        entities=list(entities or []),
        product_contract_count=product_contract_count,
    )


def date_between(start: str, end: str) -> FilterCondition:
    return FilterCondition(
        field=FilterField.DEAL_DATE,
        operator=Operator.BETWEEN,
        value=[start, end],
    )


def raw_scalar(conn: Connection, sql: str, **params: object) -> object:
    return conn.execute(text(sql), params).scalar()


def run(conn: Connection, execution: ExecutionPlan, name: str = "test"):
    return execute(conn, execution, f"exec-{name}")


# ---------------------------------------------------------------------------
# Golden counts
# ---------------------------------------------------------------------------


def test_total_deal_count(reader_conn: Connection) -> None:
    result = run(reader_conn, make_execution(intent=Intent.COUNT))
    assert result.has_answer
    assert result.matched_rows == 500_000
    assert result.rows[0].value == 500_000


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2022, 84_299),
        (2023, 93_985),
        (2024, 106_647),
        (2025, 115_763),
        (2026, 99_306),
    ],
)
def test_golden_count_by_year(
    reader_conn: Connection, year: int, expected: int
) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            filters=[date_between(f"{year}-01-01", f"{year}-12-31")],
        ),
        name=f"year-{year}",
    )
    assert result.matched_rows == expected
    assert result.rows[0].value == expected


@pytest.mark.parametrize(
    ("contract_type", "expected"),
    [("Экспортный", 6_590), ("Внутренний", 493_410)],
)
def test_golden_count_by_contract_type(
    reader_conn: Connection, contract_type: str, expected: int
) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            filters=[
                FilterCondition(
                    field=FilterField.CONTRACT_TYPE,
                    operator=Operator.EQ,
                    value=contract_type,
                )
            ],
        ),
        name=f"type-{contract_type}",
    )
    assert result.matched_rows == expected
    assert result.rows[0].value == expected


# ---------------------------------------------------------------------------
# Money and currency rules
# ---------------------------------------------------------------------------


def test_native_amount_is_separate_per_currency(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.SUM_AMOUNT,
            metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
            currency_mode=CurrencyMode.NATIVE_BY_CURRENCY,
        ),
        name="sum-native",
    )
    assert result.has_answer
    assert len(result.rows) == 2
    values = {row.currency: row.value for row in result.rows}
    assert values["Узбекский сум"] == Decimal("206835219986242.97")
    assert values["Доллар США"] == Decimal("1137509729.39")
    assert result.currency_method is not None
    assert "not combined" in result.currency_method


def test_uzs_converted_total(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.SUM_AMOUNT,
            metric=Metric.AMOUNT_SUM_UZS_CONVERTED,
            currency_mode=CurrencyMode.UZS_CONVERTED,
        ),
        name="sum-converted",
    )
    assert result.has_answer
    assert len(result.rows) == 1
    assert result.rows[0].value == Decimal("220477157554431.7867")
    assert result.rows[0].currency == "Узбекский сум"
    assert result.currency_method is not None
    assert "rate on deal date" in result.currency_method


def test_money_without_currency_mode_is_unsupported(reader_conn: Connection) -> None:
    with pytest.raises(UnsupportedQuery):
        run(
            reader_conn,
            make_execution(
                intent=Intent.SUM_AMOUNT,
                metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
                currency_mode=CurrencyMode.NOT_APPLICABLE,
            ),
            name="sum-no-mode",
        )


def test_average_converted_amount_matches_database(reader_conn: Connection) -> None:
    expected = raw_scalar(
        reader_conn,
        """
        SELECT AVG(CASE WHEN c.currency = 'Доллар США'
                        THEN d.deal_amount * r.uzs_per_usd
                        ELSE d.deal_amount END)
        FROM deals d
        JOIN contracts c ON c.contract_no = d.contract_no
        JOIN usd_rates r ON r.rate_date = d.deal_date
        WHERE d.deal_date BETWEEN :start AND :end
        """,
        start=date(2024, 1, 1),
        end=date(2024, 12, 31),
    )
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.AVERAGE,
            metric=Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
            currency_mode=CurrencyMode.UZS_CONVERTED,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="avg-amount",
    )
    assert result.has_answer
    assert result.rows[0].value == expected


def test_weighted_unit_price_matches_database(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.AVERAGE,
            metric=Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="weighted-price",
    )
    assert result.has_answer
    assert len(result.rows) >= 2
    for row in result.rows:
        expected = raw_scalar(
            reader_conn,
            """
            SELECT SUM(d.deal_amount) / SUM(d.quantity_lots * d.lot_multiplier)
            FROM deals d
            JOIN contracts c ON c.contract_no = d.contract_no
            WHERE d.deal_date BETWEEN :start AND :end
              AND c.currency = :currency
              AND d.unit_name = :unit
            """,
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            currency=row.currency,
            unit=row.unit,
        )
        assert row.value == expected
        assert row.unit is not None
        assert row.currency is not None


def test_min_max_are_separate_per_currency(reader_conn: Connection) -> None:
    for metric in (Metric.AMOUNT_MIN, Metric.AMOUNT_MAX):
        result = run(
            reader_conn,
            make_execution(
                intent=Intent.MIN_MAX,
                metric=metric,
                filters=[date_between("2024-01-01", "2024-12-31")],
            ),
            name=f"minmax-{metric.value}",
        )
        assert result.has_answer
        assert {row.currency for row in result.rows} == {
            "Узбекский сум",
            "Доллар США",
        }
        for row in result.rows:
            aggregate = "MIN" if metric is Metric.AMOUNT_MIN else "MAX"
            expected = raw_scalar(
                reader_conn,
                f"""
                SELECT {aggregate}(d.deal_amount)
                FROM deals d
                JOIN contracts c ON c.contract_no = d.contract_no
                WHERE d.deal_date BETWEEN :start AND :end
                  AND c.currency = :currency
                """,
                start=date(2024, 1, 1),
                end=date(2024, 12, 31),
                currency=row.currency,
            )
            assert row.value == expected
            assert row.extra["deal_no"]
            assert row.extra["deal_date"]
            assert row.unit


# ---------------------------------------------------------------------------
# Volume rules
# ---------------------------------------------------------------------------


def test_volume_is_kept_separate_per_unit(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(intent=Intent.SUM_VOLUME, metric=Metric.VOLUME_SUM_BY_UNIT),
        name="volume",
    )
    assert result.has_answer
    assert len(result.rows) == 7
    assert {row.unit for row in result.rows} == {
        "килограмм",
        "тонна",
        "метр",
        "штука",
        "куб.метр",
        "кв.метр",
        "литр",
    }
    for row in result.rows:
        expected = raw_scalar(
            reader_conn,
            """
            SELECT SUM(quantity_lots * lot_multiplier)
            FROM deals WHERE unit_name = :unit
            """,
            unit=row.unit,
        )
        assert row.value == expected


def test_delivered_volume_excludes_null_rows(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.SUM_VOLUME, metric=Metric.DELIVERED_VOLUME_BY_UNIT
        ),
        name="delivered-volume",
    )
    assert result.has_answer
    non_null = int(
        raw_scalar(
            reader_conn,
            "SELECT count(*) FROM deals WHERE delivered_lots IS NOT NULL",
        )
    )
    assert result.matched_rows == non_null == 453_709


def test_missing_delivered_data_is_not_found(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.SUM_VOLUME,
            metric=Metric.DELIVERED_VOLUME_BY_UNIT,
            filters=[
                FilterCondition(
                    field=FilterField.DEAL_NO, operator=Operator.EQ, value="100017"
                )
            ],
        ),
        name="delivered-null",
    )
    assert result.matched_rows == 0
    assert not result.has_answer
    assert result.to_evidence() is None


# ---------------------------------------------------------------------------
# Grouping, ranking, comparison, trend, listing
# ---------------------------------------------------------------------------


def test_top_contracts_match_database(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.TOP_N,
            metric=Metric.DEAL_COUNT,
            group_by=[GroupField.CONTRACT_NO],
            limit=3,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="top-contracts",
    )
    assert result.has_answer
    assert len(result.rows) == 3
    expected = reader_conn.execute(
        text(
            """
            SELECT contract_no, count(*) AS cnt
            FROM deals
            WHERE deal_date BETWEEN :start AND :end
            GROUP BY contract_no
            ORDER BY cnt DESC, contract_no ASC
            LIMIT 3
            """
        ),
        {"start": date(2024, 1, 1), "end": date(2024, 12, 31)},
    ).all()
    assert [(row.group["contract_no"], row.value) for row in result.rows] == [
        (contract_no, count) for contract_no, count in expected
    ]


def test_top_n_native_amount_ranks_per_currency(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.TOP_N,
            metric=Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY,
            currency_mode=CurrencyMode.NATIVE_BY_CURRENCY,
            group_by=[GroupField.CONTRACT_NO],
            limit=2,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="top-native",
    )
    assert result.has_answer
    currencies = {row.currency for row in result.rows}
    assert currencies == {"Узбекский сум", "Доллар США"}
    for currency in currencies:
        values = [
            row.value for row in result.rows if row.currency == currency
        ]
        assert len(values) == 2
        assert values == sorted(values, reverse=True)


def test_trend_by_year_matches_golden(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.TREND,
            metric=Metric.DEAL_COUNT,
            group_by=[GroupField.YEAR],
        ),
        name="trend-years",
    )
    assert result.has_answer
    assert [(row.group["year"], row.value) for row in result.rows] == [
        (2022, 84_299),
        (2023, 93_985),
        (2024, 106_647),
        (2025, 115_763),
        (2026, 99_306),
    ]


def test_compare_periods(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COMPARE,
            metric=Metric.DEAL_COUNT,
            comparison=ComparisonSpec(
                left=ComparisonPeriod(
                    label="2024", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)
                ),
                right=ComparisonPeriod(
                    label="2025", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
                ),
            ),
        ),
        name="compare",
    )
    assert result.has_answer
    assert [(row.group["period"], row.value) for row in result.rows] == [
        ("2024", 106_647),
        ("2025", 115_763),
    ]


def test_filter_list_hard_limit_and_total(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.FILTER_LIST,
            metric=Metric.RECORD_LOOKUP,
            limit=2,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="filter-list",
    )
    assert result.has_answer
    assert result.matched_rows == 106_647
    assert len(result.rows) == 2
    assert all(row.group["deal_no"] for row in result.rows)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def test_deal_lookup_returns_real_row(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.DEAL_LOOKUP,
            metric=Metric.RECORD_LOOKUP,
            filters=[
                FilterCondition(
                    field=FilterField.DEAL_NO, operator=Operator.EQ, value="100001"
                )
            ],
        ),
        name="deal-lookup",
    )
    assert result.has_answer
    assert result.matched_rows == 1
    row = result.rows[0]
    assert row.group["deal_no"] == "100001"
    assert row.extra["contract_no"]
    assert row.extra["seller_inn"]
    assert row.extra["buyer_inn"]
    assert row.extra["currency"] in ("Узбекский сум", "Доллар США")


def test_deal_lookup_not_found(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.DEAL_LOOKUP,
            metric=Metric.RECORD_LOOKUP,
            filters=[
                FilterCondition(
                    field=FilterField.DEAL_NO,
                    operator=Operator.EQ,
                    value="NO-SUCH-DEAL",
                )
            ],
        ),
        name="deal-missing",
    )
    assert result.matched_rows == 0
    assert not result.has_answer
    assert result.to_evidence() is None


def test_contract_lookup_returns_card(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.CONTRACT_LOOKUP,
            metric=Metric.RECORD_LOOKUP,
            filters=[
                FilterCondition(
                    field=FilterField.CONTRACT_NO,
                    operator=Operator.EQ,
                    value="100001",
                )
            ],
        ),
        name="contract-lookup",
    )
    assert result.has_answer
    assert result.matched_rows == 1
    assert result.rows[0].extra["contract_no"] == "100001"
    assert result.rows[0].extra["product_name"]


def test_participant_lookup_by_inn(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.PARTICIPANT_LOOKUP,
            subject=Subject.PARTICIPANTS,
            metric=Metric.RECORD_LOOKUP,
            filters=[
                FilterCondition(
                    field=FilterField.SELLER_INN,
                    operator=Operator.EQ,
                    value="900099809",
                )
            ],
        ),
        name="participant-lookup",
    )
    assert result.has_answer
    assert result.rows[0].group["inn"] == "900099809"
    assert result.rows[0].extra["name"]


def test_participant_lookup_by_ambiguous_name_fails_closed(
    reader_conn: Connection,
) -> None:
    with pytest.raises(AmbiguousEntity) as error:
        run(
            reader_conn,
            make_execution(
                intent=Intent.PARTICIPANT_LOOKUP,
                subject=Subject.PARTICIPANTS,
                metric=Metric.RECORD_LOOKUP,
                filters=[
                    FilterCondition(
                        field=FilterField.SELLER_INN,
                        operator=Operator.EQ,
                        value="sujo plus mchj",
                    )
                ],
            ),
            name="participant-ambiguous",
        )
    assert error.value.clarification_code is ClarificationCode.PARTICIPANT_AMBIGUOUS
    assert len(error.value.options) >= 2


def test_broker_lookup_by_code(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.BROKER_LOOKUP,
            subject=Subject.BROKERS,
            metric=Metric.RECORD_LOOKUP,
            filters=[
                FilterCondition(
                    field=FilterField.SELLER_BROKER_CODE,
                    operator=Operator.EQ,
                    value="BK001",
                )
            ],
        ),
        name="broker-lookup",
    )
    assert result.has_answer
    assert result.rows[0].group["broker_code"] == "BK001"
    assert result.rows[0].extra["name"]


# ---------------------------------------------------------------------------
# Entity filters and evidence
# ---------------------------------------------------------------------------


def test_region_entity_uses_warehouse_region(reader_conn: Connection) -> None:
    expected = int(
        raw_scalar(
            reader_conn,
            "SELECT count(*) FROM deals WHERE warehouse_region_id = :region_id",
            region_id=12,
        )
    )
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            entities=[
                ResolvedEntity(
                    kind=MatchKind.REGION,
                    id="12",
                    label="г. Ташкент",
                    meaning=RegionMeaning.WAREHOUSE,
                    matched_by="exact_alias",
                )
            ],
        ),
        name="region-warehouse",
    )
    assert result.matched_rows == expected
    assert result.rows[0].value == expected


def test_company_region_without_side_is_ambiguous(reader_conn: Connection) -> None:
    with pytest.raises(AmbiguousEntity) as error:
        run(
            reader_conn,
            make_execution(
                intent=Intent.COUNT,
                entities=[
                    ResolvedEntity(
                        kind=MatchKind.REGION,
                        id="12",
                        label="г. Ташкент",
                        meaning=RegionMeaning.COMPANY,
                        matched_by="exact_alias",
                    )
                ],
            ),
            name="region-company-ambiguous",
        )
    assert error.value.clarification_code is ClarificationCode.REGION_MEANING_AMBIGUOUS


def test_product_base_name_uses_contract_link(reader_conn: Connection) -> None:
    contract_count = int(
        raw_scalar(
            reader_conn,
            "SELECT count(*) FROM contracts WHERE product_name = :name",
            name="Карбамид",
        )
    )
    expected = int(
        raw_scalar(
            reader_conn,
            """
            SELECT count(*)
            FROM deals d
            JOIN contracts c ON c.contract_no = d.contract_no
            WHERE c.product_name = :name
            """,
            name="Карбамид",
        )
    )
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            entities=[
                ResolvedEntity(
                    kind=MatchKind.PRODUCT,
                    id="Карбамид",
                    label="Карбамид",
                    matched_by="exact_product_name",
                )
            ],
            product_contract_count=contract_count,
        ),
        name="product-base",
    )
    assert result.matched_rows == expected
    assert any(
        f"covers {contract_count} contract" in note for note in result.notes
    )


def test_evidence_is_complete_for_found_answer(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            filters=[date_between("2024-01-01", "2024-12-31")],
        ),
        name="evidence",
    )
    evidence = result.to_evidence()
    assert evidence is not None
    assert evidence.source_tables == ["deals"]
    assert evidence.dataset_version == DATASET_VERSION
    assert evidence.dataset_cutoff == "2026-10-30"
    assert evidence.matched_rows == 106_647
    assert evidence.metric == "COUNT(DISTINCT deals.deal_no)"
    assert evidence.execution_id == "exec-evidence"
    assert evidence.filters[0]["field"] == "deal_date"


def test_zero_matching_period_is_not_found(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            filters=[
                FilterCondition(
                    field=FilterField.DEAL_DATE,
                    operator=Operator.GTE,
                    value="2026-10-31",
                )
            ],
        ),
        name="after-cutoff",
    )
    assert result.matched_rows == 0
    assert not result.has_answer
    assert result.rows == []


# ---------------------------------------------------------------------------
# Injection safety at the handler level
# ---------------------------------------------------------------------------


def test_like_wildcards_and_quotes_are_parameterized(reader_conn: Connection) -> None:
    result = run(
        reader_conn,
        make_execution(
            intent=Intent.COUNT,
            filters=[
                FilterCondition(
                    field=FilterField.DEAL_PRODUCT_NAME,
                    operator=Operator.CONTAINS,
                    value="%' OR 1=1 --",
                )
            ],
        ),
        name="injection",
    )
    assert result.matched_rows == 0
    assert not result.has_answer
