from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def scalar(conn, sql: str, **params: object) -> object:
    return conn.execute(text(sql), params).scalar()


def test_active_dataset_is_validated(active_dataset: dict, manifest: dict) -> None:
    assert active_dataset["validation_status"] == "PASSED"
    assert active_dataset["dataset_version"] == manifest["dataset_version"]
    assert str(active_dataset["max_deal_date"]) == manifest["date_range"]["max_deal_date"]


def test_golden_deal_counts_by_year(reader_conn, manifest: dict) -> None:
    rows = reader_conn.execute(
        text(
            "SELECT extract(year FROM deal_date)::int AS y, count(*) "
            "FROM deals GROUP BY 1 ORDER BY 1"
        )
    ).all()
    actual = {int(year): int(count) for year, count in rows}
    expected = {
        year: manifest["row_counts"][f"deals_{year}.csv"] for year in range(2022, 2027)
    }
    assert actual == expected
    assert sum(actual.values()) == 500_000


def test_reference_table_counts(reader_conn, manifest: dict) -> None:
    assert scalar(reader_conn, "SELECT count(*) FROM contracts") == manifest["row_counts"]["contracts.csv"]
    assert scalar(reader_conn, "SELECT count(*) FROM participants") == manifest["row_counts"]["participants.csv"]
    assert scalar(reader_conn, "SELECT count(*) FROM brokers") == manifest["row_counts"]["brokers.csv"]
    assert scalar(reader_conn, "SELECT count(*) FROM usd_rates") == manifest["row_counts"]["usd_rates.csv"]


def test_control_sums_are_exact(reader_conn, manifest: dict) -> None:
    row = reader_conn.execute(
        text(
            """
            SELECT
              SUM(d.deal_amount) FILTER (WHERE c.currency = 'Узбекский сум') AS internal_uzs,
              SUM(d.deal_amount) FILTER (WHERE c.currency = 'Доллар США') AS export_usd,
              SUM(d.deal_amount * u.uzs_per_usd)
                FILTER (WHERE c.currency = 'Доллар США') AS export_converted_uzs
            FROM deals d
            JOIN contracts c USING (contract_no)
            JOIN usd_rates u ON u.rate_date = d.deal_date
            """
        )
    ).one()
    controls = manifest["control_sums"]
    assert row.internal_uzs == Decimal(controls["internal_uzs"])
    assert row.export_usd == Decimal(controls["export_usd"])
    assert row.export_converted_uzs == Decimal(controls["export_converted_uzs"])
    assert row.internal_uzs + row.export_converted_uzs == Decimal(controls["total_uzs_readme"])


def test_nulls_are_preserved(reader_conn, manifest: dict) -> None:
    row = reader_conn.execute(
        text(
            """
            SELECT
              count(*) FILTER (WHERE payment_date IS NULL) AS payment_null,
              count(*) FILTER (WHERE delivery_date IS NULL) AS delivery_null,
              count(*) FILTER (WHERE delivered_lots IS NULL) AS delivered_null
            FROM deals
            """
        )
    ).one()
    assert row.payment_null == manifest["null_counts"]["payment_date"]
    assert row.delivery_null == manifest["null_counts"]["delivery_date"]
    assert row.delivered_null == manifest["null_counts"]["delivered_lots"]


def test_status_distribution(reader_conn, manifest: dict) -> None:
    rows = reader_conn.execute(
        text("SELECT status, count(*) FROM deals GROUP BY status")
    ).all()
    assert {status: count for status, count in rows} == manifest["status_counts"]


def test_all_foreign_keys_and_business_rules(reader_conn) -> None:
    checks = {
        "unknown_contract": """
            SELECT count(*) FROM deals d LEFT JOIN contracts c USING (contract_no)
            WHERE c.contract_no IS NULL
        """,
        "unknown_seller": """
            SELECT count(*) FROM deals d LEFT JOIN participants p ON p.inn = d.seller_inn
            WHERE p.inn IS NULL
        """,
        "unknown_buyer": """
            SELECT count(*) FROM deals d LEFT JOIN participants p ON p.inn = d.buyer_inn
            WHERE p.inn IS NULL
        """,
        "seller_mismatch": """
            SELECT count(*) FROM deals d JOIN contracts c USING (contract_no)
            WHERE d.seller_inn <> c.seller_inn
        """,
        "outside_contract_period": """
            SELECT count(*) FROM deals d JOIN contracts c USING (contract_no)
            WHERE d.deal_date < c.registration_date OR d.deal_date > c.valid_until
        """,
        "rate_missing": """
            SELECT count(*) FROM deals d LEFT JOIN usd_rates u ON u.rate_date = d.deal_date
            WHERE u.rate_date IS NULL
        """,
        "seller_wrong_role": """
            SELECT count(*) FROM deals d JOIN participants p ON p.inn = d.seller_inn
            WHERE p.role <> 'Продавец'
        """,
        "buyer_wrong_role": """
            SELECT count(*) FROM deals d JOIN participants p ON p.inn = d.buyer_inn
            WHERE p.role <> 'Покупатель'
        """,
        "delivered_gt_qty": """
            SELECT count(*) FROM deals
            WHERE delivered_lots IS NOT NULL AND delivered_lots > quantity_lots
        """,
        "multiple_deal_product_names_per_contract": """
            SELECT count(*) FROM (
              SELECT contract_no FROM deals GROUP BY contract_no
              HAVING count(DISTINCT deal_product_name) > 1
            ) t
        """,
    }
    for name, sql in checks.items():
        assert scalar(reader_conn, sql) == 0, name


def test_currency_matches_contract_type(reader_conn) -> None:
    assert (
        scalar(
            reader_conn,
            """
            SELECT count(*) FROM deals d JOIN contracts c USING (contract_no)
            WHERE (c.currency = 'Узбекский сум' AND d.contract_type <> 'Внутренний')
               OR (c.currency = 'Доллар США' AND d.contract_type <> 'Экспортный')
            """,
        )
        == 0
    )
    assert (
        scalar(reader_conn, "SELECT count(*) FROM deals WHERE contract_type = 'Внутренний'")
        == 493_410
    )
    assert (
        scalar(reader_conn, "SELECT count(*) FROM deals WHERE contract_type = 'Экспортный'")
        == 6_590
    )


def test_regions_are_normalized_without_loss(reader_conn) -> None:
    assert scalar(reader_conn, "SELECT count(*) FROM regions") == 14
    assert scalar(reader_conn, "SELECT count(*) FROM deals WHERE warehouse_region_id IS NULL") == 0
    assert scalar(reader_conn, "SELECT count(*) FROM contracts WHERE warehouse_region_id IS NULL") == 0
    assert scalar(reader_conn, "SELECT count(*) FROM participants WHERE company_region_id IS NULL") == 0
    assert scalar(reader_conn, "SELECT count(*) FROM brokers WHERE region_id IS NULL") == 0
    assert (
        scalar(
            reader_conn,
            """
            SELECT count(DISTINCT r.region_id)
            FROM deals d JOIN regions r ON r.region_id = d.warehouse_region_id
            """,
        )
        == 14
    )


def test_warehouse_and_company_regions_are_distinct_columns(reader_conn) -> None:
    columns = {
        row[0]
        for row in reader_conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('deals','participants') AND column_name LIKE '%region%'"
            )
        ).all()
    }
    assert "warehouse_region_id" in columns
    assert "company_region_id" in columns


def test_dataset_version_on_every_row(reader_conn, manifest: dict) -> None:
    for table in ("deals", "contracts", "participants", "brokers", "usd_rates"):
        wrong = scalar(
            reader_conn,
            f"SELECT count(*) FROM {table} WHERE dataset_version <> :v",
            v=manifest["dataset_version"],
        )
        assert wrong == 0, table


def test_runtime_role_cannot_write(reader_conn) -> None:
    with pytest.raises(DBAPIError):
        reader_conn.execute(text("CREATE TABLE forbidden_write_test (id int)"))
    reader_conn.rollback()


def test_unused_reference_rows_are_preserved(reader_conn, manifest: dict) -> None:
    contracts_unused = scalar(
        reader_conn,
        "SELECT count(*) FROM contracts c "
        "WHERE NOT EXISTS (SELECT 1 FROM deals d WHERE d.contract_no = c.contract_no)",
    )
    participants_unused = scalar(
        reader_conn,
        "SELECT count(*) FROM participants p "
        "WHERE NOT EXISTS (SELECT 1 FROM deals d WHERE d.seller_inn = p.inn OR d.buyer_inn = p.inn)",
    )
    assert contracts_unused == manifest["distinct_counts"]["contracts_unused"]
    assert participants_unused == manifest["distinct_counts"]["participants_unused"]
