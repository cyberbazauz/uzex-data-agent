from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass
class CheckResult:
    name: str
    ok: bool
    expected: Any = None
    actual: Any = None
    severity: str = "critical"
    details: str | None = None
    samples: list[dict[str, Any]] = field(default_factory=list)


def _scalar(conn: Connection, sql: str, **params: Any) -> Any:
    return conn.execute(text(sql), params).scalar()


def _rows(conn: Connection, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(row) for row in conn.execute(text(sql), params).all()]


def _check_equal(name: str, actual: Any, expected: Any, severity: str = "critical") -> CheckResult:
    return CheckResult(name=name, ok=actual == expected, expected=expected, actual=actual, severity=severity)


def _check_zero(name: str, actual: Any, expected: Any = 0, **kwargs: Any) -> CheckResult:
    return CheckResult(name=name, ok=actual == expected, expected=expected, actual=actual, **kwargs)


def _compare_counts(name: str, actual_rows: list[tuple[Any, ...]], expected: dict[str, int]) -> CheckResult:
    actual = {str(key): int(value) for key, value in actual_rows}
    expected_clean = {str(key): int(value) for key, value in expected.items()}
    diff = {
        key: {"expected": expected_clean.get(key), "actual": actual.get(key)}
        for key in sorted(set(expected_clean) | set(actual))
        if expected_clean.get(key) != actual.get(key)
    }
    return CheckResult(
        name=name,
        ok=not diff,
        expected=expected_clean,
        actual=actual,
        details=None if not diff else "distribution mismatch",
        samples=[{"key": key, **value} for key, value in diff.items()][:20],
    )


def run_integrity_checks(conn: Connection, manifest: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    row_counts = manifest["row_counts"]

    # 1. Row counts per source file and totals.
    for file_name in [f"deals_{year}.csv" for year in range(2022, 2027)]:
        actual = _scalar(
            conn, "SELECT count(*) FROM stg_deals WHERE source_file = :file", file=file_name
        )
        results.append(_check_equal(f"row_count:{file_name}", actual, row_counts[file_name]))
    results.append(
        _check_equal(
            "row_count:deals_total",
            _scalar(conn, "SELECT count(*) FROM stg_deals"),
            sum(row_counts[f"deals_{year}.csv"] for year in range(2022, 2027)),
        )
    )
    for file_name, table, key in (
        ("contracts.csv", "stg_contracts", "contract_no"),
        ("participants.csv", "stg_participants", "inn"),
        ("brokers.csv", "stg_brokers", "broker_code"),
        ("usd_rates.csv", "stg_usd_rates", "rate_date"),
    ):
        actual = _scalar(conn, f"SELECT count(*) FROM {table}")
        results.append(_check_equal(f"row_count:{file_name}", actual, row_counts[file_name]))

    # 2. Primary key uniqueness.
    for name, table, key in (
        ("unique:deal_no", "stg_deals", "deal_no"),
        ("unique:contract_no", "stg_contracts", "contract_no"),
        ("unique:participant_inn", "stg_participants", "inn"),
        ("unique:broker_code", "stg_brokers", "broker_code"),
        ("unique:rate_date", "stg_usd_rates", "rate_date"),
    ):
        actual = _scalar(conn, f"SELECT count(*) - count(DISTINCT {key}) FROM {table}")
        results.append(_check_zero(name, actual))

    # 3. Foreign keys and cross-table consistency.
    mismatch_queries: dict[str, str] = {
        "unknown_contract": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_contracts c USING (contract_no)
            WHERE c.contract_no IS NULL
        """,
        "unknown_seller": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_participants p ON p.inn = d.seller_inn
            WHERE p.inn IS NULL
        """,
        "unknown_buyer": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_participants p ON p.inn = d.buyer_inn
            WHERE p.inn IS NULL
        """,
        "unknown_seller_broker": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_brokers b ON b.broker_code = d.seller_broker_code
            WHERE b.broker_code IS NULL
        """,
        "unknown_buyer_broker": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_brokers b ON b.broker_code = d.buyer_broker_code
            WHERE b.broker_code IS NULL
        """,
        "seller_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.seller_inn <> c.seller_inn
        """,
        "trade_type_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.trade_type <> c.trade_type
        """,
        "contract_type_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.contract_type <> c.contract_type
        """,
        "contract_form_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.contract_form <> c.contract_form
        """,
        "unit_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.unit_name <> c.unit_name
        """,
        "lot_mismatch": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.lot_multiplier <> c.lot_multiplier
        """,
        "outside_contract_period": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE d.deal_date < c.registration_date OR d.deal_date > c.valid_until
        """,
        "rate_missing": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN stg_usd_rates u ON u.rate_date = d.deal_date
            WHERE u.rate_date IS NULL
        """,
        "uzs_not_internal": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE c.currency = 'Узбекский сум' AND d.contract_type <> 'Внутренний'
        """,
        "usd_not_export": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_contracts c USING (contract_no)
            WHERE c.currency = 'Доллар США' AND d.contract_type <> 'Экспортный'
        """,
        "unknown_currency": """
            SELECT count(*) FROM stg_contracts c
            WHERE c.currency NOT IN ('Узбекский сум', 'Доллар США')
        """,
        "seller_wrong_role": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_participants p ON p.inn = d.seller_inn
            WHERE p.role <> 'Продавец'
        """,
        "buyer_wrong_role": """
            SELECT count(*) FROM stg_deals d
            JOIN stg_participants p ON p.inn = d.buyer_inn
            WHERE p.role <> 'Покупатель'
        """,
        "unknown_warehouse_region": """
            SELECT count(*) FROM stg_deals d
            LEFT JOIN regions r ON r.region_id = d.warehouse_region_id
            WHERE r.region_id IS NULL
        """,
        "unknown_contract_region": """
            SELECT count(*) FROM stg_contracts c
            LEFT JOIN regions r ON r.region_id = c.warehouse_region_id
            WHERE r.region_id IS NULL
        """,
        "unknown_participant_region": """
            SELECT count(*) FROM stg_participants p
            LEFT JOIN regions r ON r.region_id = p.company_region_id
            WHERE r.region_id IS NULL
        """,
        "unknown_broker_region": """
            SELECT count(*) FROM stg_brokers b
            LEFT JOIN regions r ON r.region_id = b.region_id
            WHERE r.region_id IS NULL
        """,
        "nonpositive_quantity": "SELECT count(*) FROM stg_deals WHERE quantity_lots <= 0",
        "nonpositive_lot": "SELECT count(*) FROM stg_deals WHERE lot_multiplier <= 0",
        "negative_amount": "SELECT count(*) FROM stg_deals WHERE deal_amount < 0",
        "negative_starting_price": "SELECT count(*) FROM stg_deals WHERE starting_price < 0",
        "negative_quantity": "SELECT count(*) FROM stg_deals WHERE quantity_lots < 0",
        "negative_delivered": "SELECT count(*) FROM stg_deals WHERE delivered_lots < 0",
        "delivered_gt_qty": """
            SELECT count(*) FROM stg_deals
            WHERE delivered_lots IS NOT NULL AND delivered_lots > quantity_lots
        """,
        "contract_lot_nonpositive": "SELECT count(*) FROM stg_contracts WHERE lot_multiplier <= 0",
        "contract_invalid_validity": """
            SELECT count(*) FROM stg_contracts WHERE valid_until < registration_date
        """,
        "contract_negative_price": "SELECT count(*) FROM stg_contracts WHERE base_price < 0",
        "rate_nonpositive": "SELECT count(*) FROM stg_usd_rates WHERE uzs_per_usd <= 0",
        "new_not_cutoff": """
            SELECT count(*) FROM stg_deals
            WHERE status = 'Новый' AND deal_date <> DATE '2026-10-30'
        """,
        "contracts_with_multiple_deal_product_names": """
            SELECT count(*) FROM (
                SELECT contract_no
                FROM stg_deals
                GROUP BY contract_no
                HAVING count(DISTINCT deal_product_name) > 1
            ) t
        """,
    }
    expected_zero = manifest.get("expected_mismatches_zero", {})
    for name, sql in mismatch_queries.items():
        actual = _scalar(conn, sql)
        expected = expected_zero.get(name, 0)
        results.append(
            _check_zero(name, actual, expected=expected)
            if expected == 0
            else _check_equal(name, actual, expected)
        )

    # 4. Nullable fields must stay NULL, never become zero/empty strings.
    null_counts = manifest["null_counts"]
    for field, key in (
        ("payment_date", "payment_date"),
        ("delivery_date", "delivery_date"),
        ("delivered_lots", "delivered_lots"),
    ):
        actual = _scalar(conn, f"SELECT count(*) FROM stg_deals WHERE {field} IS NULL")
        results.append(_check_equal(f"null_count:{field}", actual, null_counts[key]))

    # 5. Distributions.
    for name, sql, expected_key in (
        (
            "distribution:status",
            "SELECT status, count(*) FROM stg_deals GROUP BY status",
            "status_counts",
        ),
        (
            "distribution:trade_type",
            "SELECT trade_type, count(*) FROM stg_deals GROUP BY trade_type",
            "trade_type_counts",
        ),
        (
            "distribution:contract_type",
            "SELECT contract_type, count(*) FROM stg_deals GROUP BY contract_type",
            "contract_type_counts",
        ),
        (
            "distribution:contract_form",
            "SELECT contract_form, count(*) FROM stg_deals GROUP BY contract_form",
            "contract_form_counts",
        ),
        (
            "distribution:unit",
            "SELECT unit_name, count(*) FROM stg_deals GROUP BY unit_name",
            "unit_counts",
        ),
        (
            "distribution:deal_region",
            "SELECT warehouse_region_raw, count(*) FROM stg_deals GROUP BY warehouse_region_raw",
            "deal_region_counts",
        ),
        (
            "distribution:contract_region",
            "SELECT warehouse_region_raw, count(*) FROM stg_contracts GROUP BY warehouse_region_raw",
            "contract_region_counts",
        ),
        (
            "distribution:participant_region",
            "SELECT company_region_raw, count(*) FROM stg_participants GROUP BY company_region_raw",
            "participant_region_counts",
        ),
        (
            "distribution:broker_region",
            "SELECT region_raw, count(*) FROM stg_brokers GROUP BY region_raw",
            "broker_region_counts",
        ),
        (
            "distribution:participant_role",
            "SELECT role, count(*) FROM stg_participants GROUP BY role",
            "participant_role_counts",
        ),
        (
            "distribution:contract_currency",
            "SELECT currency, count(*) FROM stg_contracts GROUP BY currency",
            "contract_currency_counts",
        ),
        (
            "distribution:contract_ref_type",
            "SELECT contract_type, count(*) FROM stg_contracts GROUP BY contract_type",
            "contract_ref_contract_type_counts",
        ),
        (
            "distribution:contract_ref_form",
            "SELECT contract_form, count(*) FROM stg_contracts GROUP BY contract_form",
            "contract_ref_contract_form_counts",
        ),
        (
            "distribution:contract_ref_trade_type",
            "SELECT trade_type, count(*) FROM stg_contracts GROUP BY trade_type",
            "contract_ref_trade_type_counts",
        ),
        (
            "distribution:contract_unit",
            "SELECT unit_name, count(*) FROM stg_contracts GROUP BY unit_name",
            "contract_unit_counts",
        ),
        (
            "distribution:product_group",
            "SELECT product_group, count(*) FROM stg_contracts GROUP BY product_group",
            "product_group_counts",
        ),
        (
            "distribution:product_category",
            "SELECT product_category, count(*) FROM stg_contracts GROUP BY product_category",
            "product_category_counts",
        ),
        (
            "distribution:industry",
            "SELECT industry, count(*) FROM stg_contracts GROUP BY industry",
            "industry_counts",
        ),
    ):
        results.append(_compare_counts(name, _rows(conn, sql), manifest[expected_key]))

    # 6. Distinct used entities.
    distinct_expected = manifest["distinct_counts"]
    distinct_queries = {
        "deal_numbers": "SELECT count(DISTINCT deal_no) FROM stg_deals",
        "contracts_used": "SELECT count(DISTINCT contract_no) FROM stg_deals",
        "deal_product_names": "SELECT count(DISTINCT deal_product_name) FROM stg_deals",
        "contract_product_names": "SELECT count(DISTINCT product_name) FROM stg_contracts",
        "contract_hs_codes": "SELECT count(DISTINCT hs_code) FROM stg_contracts",
        "seller_inns_used": "SELECT count(DISTINCT seller_inn) FROM stg_deals",
        "buyer_inns_used": "SELECT count(DISTINCT buyer_inn) FROM stg_deals",
        "seller_brokers_used": "SELECT count(DISTINCT seller_broker_code) FROM stg_deals",
        "buyer_brokers_used": "SELECT count(DISTINCT buyer_broker_code) FROM stg_deals",
        "participants_used": """
            SELECT count(*) FROM (
                SELECT seller_inn AS inn FROM stg_deals
                UNION
                SELECT buyer_inn AS inn FROM stg_deals
            ) t
        """,
        "participants_unused": """
            SELECT count(*) FROM stg_participants p
            WHERE NOT EXISTS (SELECT 1 FROM stg_deals d WHERE d.seller_inn = p.inn OR d.buyer_inn = p.inn)
        """,
        "contracts_unused": """
            SELECT count(*) FROM stg_contracts c
            WHERE NOT EXISTS (SELECT 1 FROM stg_deals d WHERE d.contract_no = c.contract_no)
        """,
    }
    for key, sql in distinct_queries.items():
        results.append(_check_equal(f"distinct:{key}", _scalar(conn, sql), distinct_expected[key]))

    # 7. Control aggregates, exact NUMERIC comparison.
    controls = manifest["control_sums"]
    internal_uzs = _scalar(
        conn,
        """
        SELECT SUM(d.deal_amount)
        FROM stg_deals d
        JOIN stg_contracts c USING (contract_no)
        WHERE c.currency = 'Узбекский сум'
        """,
    )
    export_usd = _scalar(
        conn,
        """
        SELECT SUM(d.deal_amount)
        FROM stg_deals d
        JOIN stg_contracts c USING (contract_no)
        WHERE c.currency = 'Доллар США'
        """,
    )
    export_converted = _scalar(
        conn,
        """
        SELECT SUM(d.deal_amount * u.uzs_per_usd)
        FROM stg_deals d
        JOIN stg_contracts c USING (contract_no)
        JOIN stg_usd_rates u ON u.rate_date = d.deal_date
        WHERE c.currency = 'Доллар США'
        """,
    )
    results.append(
        _check_equal("control:internal_uzs", internal_uzs, Decimal(controls["internal_uzs"]))
    )
    results.append(
        _check_equal("control:export_usd", export_usd, Decimal(controls["export_usd"]))
    )
    results.append(
        _check_equal(
            "control:export_converted_uzs",
            export_converted,
            Decimal(controls["export_converted_uzs"]),
        )
    )
    if internal_uzs is not None and export_converted is not None:
        results.append(
            _check_equal(
                "control:total_uzs_readme",
                internal_uzs + export_converted,
                Decimal(controls["total_uzs_readme"]),
            )
        )
    else:
        results.append(
            CheckResult(
                name="control:total_uzs_readme",
                ok=False,
                expected=controls["total_uzs_readme"],
                actual=None,
                details="component aggregate is NULL",
            )
        )

    # 8. Other exact expectations from the manifest.
    simple_checks = {
        "delivered_samples": ("SELECT count(*) FROM stg_deals WHERE delivered_lots IS NOT NULL", "delivered_samples"),
        "new_not_cutoff": ("SELECT count(*) FROM stg_deals WHERE status='Новый' AND deal_date <> DATE '2026-10-30'", "new_not_cutoff"),
        "amount_zero": ("SELECT count(*) FROM stg_deals WHERE deal_amount = 0", "amount_zero"),
        "rate_count": ("SELECT count(*) FROM stg_usd_rates", "usd_rates"),
    }
    for name, (sql, manifest_key) in simple_checks.items():
        if manifest_key == "usd_rates":
            expected = manifest["usd_rates"]["count"]
        elif manifest_key == "delivered_samples":
            expected = manifest["delivered_samples"]
        else:
            expected = manifest[manifest_key]
        results.append(_check_equal(name, _scalar(conn, sql), expected))

    date_range = manifest["date_range"]
    actual_min = _scalar(conn, "SELECT min(deal_date) FROM stg_deals")
    actual_max = _scalar(conn, "SELECT max(deal_date) FROM stg_deals")
    results.append(_check_equal("date:min", str(actual_min), date_range["min_deal_date"]))
    results.append(_check_equal("date:max", str(actual_max), date_range["max_deal_date"]))

    rate_range = manifest["usd_rates"]
    results.append(
        _check_equal(
            "usd_rates:min_date",
            str(_scalar(conn, "SELECT min(rate_date) FROM stg_usd_rates")),
            rate_range["min_date"],
        )
    )
    results.append(
        _check_equal(
            "usd_rates:max_date",
            str(_scalar(conn, "SELECT max(rate_date) FROM stg_usd_rates")),
            rate_range["max_date"],
        )
    )
    results.append(
        _check_equal(
            "usd_rates:min_value",
            _scalar(conn, "SELECT min(uzs_per_usd) FROM stg_usd_rates"),
            Decimal(rate_range["min_value"]),
        )
    )
    results.append(
        _check_equal(
            "usd_rates:max_value",
            _scalar(conn, "SELECT max(uzs_per_usd) FROM stg_usd_rates"),
            Decimal(rate_range["max_value"]),
        )
    )

    return results


def critical_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [item for item in results if item.severity == "critical" and not item.ok]
