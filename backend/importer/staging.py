from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.enums import (
    CONTRACT_FORMS,
    CONTRACT_TYPES,
    CURRENCIES,
    STATUSES,
    TRADE_TYPES,
)
from app.domain.normalization import normalize_text
from app.domain.regions import REGIONS, REGION_ALIAS_MAP, resolve_region_raw
from importer.csv_reader import EXPECTED_HEADERS, CsvFile, iter_rows
from importer.normalizers import (
    RowValidationError,
    optional_text,
    parse_bool_da_net,
    parse_date,
    parse_decimal,
    parse_int,
    parse_optional_date,
    parse_optional_int,
    parse_percent,
    parse_time,
    require_text,
)

STAGING_TABLES = (
    "participants",
    "brokers",
    "contracts",
    "deals",
    "usd_rates",
)

DEAL_COLUMNS = [
    "deal_no",
    "deal_date",
    "deal_time",
    "trade_type",
    "quantity_lots",
    "deal_amount",
    "starting_price",
    "contract_no",
    "deal_product_name",
    "deal_product_name_normalized",
    "lot_multiplier",
    "unit_name",
    "warehouse_region_raw",
    "warehouse_region_id",
    "contract_type",
    "contract_form",
    "status",
    "seller_inn",
    "buyer_inn",
    "seller_broker_code",
    "buyer_broker_code",
    "payment_date",
    "delivery_date",
    "delivered_lots",
    "source_file",
    "source_row",
    "dataset_version",
]

CONTRACT_COLUMNS = [
    "contract_no",
    "product_name",
    "product_name_normalized",
    "brand",
    "product_size",
    "grade",
    "product_type",
    "product_class",
    "hs_code",
    "product_group",
    "product_category",
    "industry",
    "unit_name",
    "lot_multiplier",
    "currency",
    "base_price",
    "pledge_percent",
    "delivery_basis",
    "warehouse_region_raw",
    "warehouse_region_id",
    "warehouse_address",
    "trade_type",
    "contract_type",
    "contract_form",
    "payment_days",
    "delivery_days",
    "packaging",
    "seller_inn",
    "registration_date",
    "valid_until",
    "dataset_version",
]

PARTICIPANT_COLUMNS = [
    "inn",
    "name",
    "name_normalized",
    "company_region_raw",
    "company_region_id",
    "activity_type",
    "role",
    "vat_payer",
    "vat_payer_raw",
    "dataset_version",
]

BROKER_COLUMNS = [
    "broker_code",
    "name",
    "name_normalized",
    "inn",
    "region_raw",
    "region_id",
    "dataset_version",
]

USD_RATE_COLUMNS = ["rate_date", "uzs_per_usd", "dataset_version"]

_INN_RE = re.compile(r"^[0-9]{9}$")


def seed_reference_data(conn: Connection) -> None:
    """Seed canonical regions and exact aliases. No invented transliterations."""
    for normalized, entries in REGION_ALIAS_MAP.items():
        region_ids = {entry[0] for entry in entries}
        if len(region_ids) > 1:
            raise ValueError(
                f"region alias collision: {normalized!r} maps to {sorted(region_ids)}"
            )
    for region in REGIONS:
        conn.execute(
            text(
                """
                INSERT INTO regions (region_id, canonical_name_ru, canonical_name_uz)
                VALUES (:id, :ru, :uz)
                ON CONFLICT (region_id) DO UPDATE
                SET canonical_name_ru = EXCLUDED.canonical_name_ru,
                    canonical_name_uz = EXCLUDED.canonical_name_uz
                """
            ),
            {"id": region.region_id, "ru": region.name_ru, "uz": region.name_uz},
        )
        entries = [(region.name_uz, "uz_latn"), (region.name_ru, "ru"), *region.aliases]
        for raw, language in entries:
            conn.execute(
                text(
                    """
                    INSERT INTO region_aliases
                        (region_id, alias_raw, alias_normalized, language)
                    VALUES (:region_id, :raw, :normalized, :language)
                    ON CONFLICT (alias_normalized, region_id) DO UPDATE
                    SET alias_raw = EXCLUDED.alias_raw,
                        language = EXCLUDED.language
                    """
                ),
                {
                    "region_id": region.region_id,
                    "raw": raw,
                    "normalized": normalize_text(raw),
                    "language": language,
                },
            )


def create_staging(conn: Connection) -> None:
    for table in STAGING_TABLES:
        conn.execute(
            text(f"CREATE TEMP TABLE IF NOT EXISTS stg_{table} (LIKE {table} INCLUDING DEFAULTS)")
        )


def truncate_staging(conn: Connection) -> None:
    for table in STAGING_TABLES:
        conn.execute(text(f"TRUNCATE TABLE stg_{table}"))


def _copy_rows(
    conn: Connection,
    table: str,
    columns: list[str],
    rows: Iterable[tuple[Any, ...]],
    batch_size: int = 20_000,
) -> int:
    raw = conn.connection.driver_connection
    cursor = raw.cursor()
    statement = (
        f"COPY stg_{table} ({', '.join(columns)}) "
        "FROM STDIN WITH (FORMAT csv, NULL '')"
    )
    total = 0
    batch: list[tuple[Any, ...]] = []
    try:
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                buffer = io.StringIO()
                writer = csv.writer(buffer, lineterminator="\n")
                writer.writerows(batch)
                buffer.seek(0)
                cursor.copy_expert(statement, buffer)
                total += len(batch)
                batch = []
        if batch:
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows(batch)
            buffer.seek(0)
            cursor.copy_expert(statement, buffer)
            total += len(batch)
    finally:
        cursor.close()
    return total


def _file(name: str, data_dir: Path) -> CsvFile:
    return CsvFile(
        name=name,
        path=data_dir / name,
        headers=EXPECTED_HEADERS[name],
        row_count=0,
    )


def _validate_inn(raw: str, field: str, file_name: str, row_number: int) -> str:
    value = require_text(raw, field, file_name, row_number).strip()
    if not _INN_RE.fullmatch(value):
        raise RowValidationError(file_name, row_number, f"{field} must be 9 digits")
    return value


def _validate_region(raw: str, file_name: str, row_number: int) -> int:
    region_id = resolve_region_raw(raw)
    if region_id is None:
        raise RowValidationError(
            file_name, row_number, f"unknown region value {raw!r}"
        )
    return region_id


def load_participants(conn: Connection, data_dir: Path, dataset_version: str) -> int:
    def rows() -> Iterator[tuple[Any, ...]]:
        file_name = "participants.csv"
        for row_number, row in iter_rows(_file(file_name, data_dir)):
            inn = _validate_inn(row["ИНН"], "ИНН", file_name, row_number)
            name = require_text(row["Наименование"], "Наименование", file_name, row_number)
            region_raw = require_text(row["Регион"], "Регион", file_name, row_number)
            role = require_text(row["Роль"], "Роль", file_name, row_number)
            if role not in ("Продавец", "Покупатель"):
                raise RowValidationError(file_name, row_number, f"unknown role {role!r}")
            vat_raw = require_text(
                row["Плательщик НДС"], "Плательщик НДС", file_name, row_number
            )
            yield (
                inn,
                name,
                normalize_text(name),
                region_raw,
                _validate_region(region_raw, file_name, row_number),
                require_text(row["Вид деятельности"], "Вид деятельности", file_name, row_number),
                role,
                parse_bool_da_net(vat_raw, "Плательщик НДС", file_name, row_number),
                vat_raw,
                dataset_version,
            )

    return _copy_rows(conn, "participants", PARTICIPANT_COLUMNS, rows())


def load_brokers(conn: Connection, data_dir: Path, dataset_version: str) -> int:
    def rows() -> Iterator[tuple[Any, ...]]:
        file_name = "brokers.csv"
        for row_number, row in iter_rows(_file(file_name, data_dir)):
            broker_code = require_text(
                row["Код брокера"], "Код брокера", file_name, row_number
            ).strip()
            name = require_text(row["Наименование"], "Наименование", file_name, row_number)
            region_raw = require_text(row["Регион"], "Регион", file_name, row_number)
            yield (
                broker_code,
                name,
                normalize_text(name),
                _validate_inn(row["ИНН"], "ИНН", file_name, row_number),
                region_raw,
                _validate_region(region_raw, file_name, row_number),
                dataset_version,
            )

    return _copy_rows(conn, "brokers", BROKER_COLUMNS, rows())


def load_contracts(conn: Connection, data_dir: Path, dataset_version: str) -> int:
    def rows() -> Iterator[tuple[Any, ...]]:
        file_name = "contracts.csv"
        for row_number, row in iter_rows(_file(file_name, data_dir)):
            contract_no = require_text(
                row["Номер контракта"], "Номер контракта", file_name, row_number
            ).strip()
            product_name = require_text(
                row["Наименование продукта"], "Наименование продукта", file_name, row_number
            )
            currency = require_text(row["Валюта"], "Валюта", file_name, row_number)
            if currency not in CURRENCIES:
                raise RowValidationError(file_name, row_number, f"unknown currency {currency!r}")
            contract_type = require_text(
                row["Тип контракта"], "Тип контракта", file_name, row_number
            )
            contract_form = require_text(
                row["Форма контракта"], "Форма контракта", file_name, row_number
            )
            trade_type = require_text(
                row["Тип торгов"], "Тип торгов", file_name, row_number
            )
            if contract_type not in CONTRACT_TYPES:
                raise RowValidationError(
                    file_name, row_number, f"unknown contract type {contract_type!r}"
                )
            if contract_form not in CONTRACT_FORMS:
                raise RowValidationError(
                    file_name, row_number, f"unknown contract form {contract_form!r}"
                )
            if trade_type not in TRADE_TYPES:
                raise RowValidationError(
                    file_name, row_number, f"unknown trade type {trade_type!r}"
                )
            region_raw = require_text(row["Регион"], "Регион", file_name, row_number)
            yield (
                contract_no,
                product_name,
                normalize_text(product_name),
                optional_text(row["Марка товара"]),
                optional_text(row["Размер товара"]),
                optional_text(row["Сорт товара"]),
                optional_text(row["Тип товара"]),
                optional_text(row["Класс товара"]),
                require_text(row["Код ТН ВЭД"], "Код ТН ВЭД", file_name, row_number).strip(),
                require_text(row["Группа товара"], "Группа товара", file_name, row_number),
                require_text(row["Категория товара"], "Категория товара", file_name, row_number),
                require_text(
                    row["Отрасль промышленности"],
                    "Отрасль промышленности",
                    file_name,
                    row_number,
                ),
                require_text(row["Единица измерения"], "Единица измерения", file_name, row_number),
                parse_int(row["Лот"], "Лот", file_name, row_number),
                currency,
                parse_decimal(row["Базисная цена"], "Базисная цена", file_name, row_number),
                parse_percent(row["Сумма залога"], "Сумма залога", file_name, row_number),
                require_text(row["Базис поставки"], "Базис поставки", file_name, row_number),
                region_raw,
                _validate_region(region_raw, file_name, row_number),
                require_text(row["Адрес склада"], "Адрес склада", file_name, row_number),
                trade_type,
                contract_type,
                contract_form,
                parse_int(row["Срок оплаты"], "Срок оплаты", file_name, row_number),
                parse_int(row["Срок поставки"], "Срок поставки", file_name, row_number),
                require_text(row["Упаковка"], "Упаковка", file_name, row_number),
                _validate_inn(row["ИНН продавца"], "ИНН продавца", file_name, row_number),
                parse_date(row["Дата регистрации"], "Дата регистрации", file_name, row_number),
                parse_date(row["Действует до"], "Действует до", file_name, row_number),
                dataset_version,
            )

    return _copy_rows(conn, "contracts", CONTRACT_COLUMNS, rows())


def load_usd_rates(conn: Connection, data_dir: Path, dataset_version: str) -> int:
    def rows() -> Iterator[tuple[Any, ...]]:
        file_name = "usd_rates.csv"
        for row_number, row in iter_rows(_file(file_name, data_dir)):
            yield (
                parse_date(row["Дата"], "Дата", file_name, row_number),
                parse_decimal(row["Курс"], "Курс", file_name, row_number),
                dataset_version,
            )

    return _copy_rows(conn, "usd_rates", USD_RATE_COLUMNS, rows())


def load_deals(conn: Connection, data_dir: Path, dataset_version: str) -> int:
    total = 0
    for year in range(2022, 2027):
        file_name = f"deals_{year}.csv"

        def rows(file_name: str = file_name) -> Iterator[tuple[Any, ...]]:
            for row_number, row in iter_rows(_file(file_name, data_dir)):
                status = require_text(
                    row["Состояние сделки"], "Состояние сделки", file_name, row_number
                )
                if status not in STATUSES:
                    raise RowValidationError(
                        file_name, row_number, f"unknown status {status!r}"
                    )
                trade_type = require_text(
                    row["Тип торгов"], "Тип торгов", file_name, row_number
                )
                if trade_type not in TRADE_TYPES:
                    raise RowValidationError(
                        file_name, row_number, f"unknown trade type {trade_type!r}"
                    )
                contract_type = require_text(
                    row["Тип контракта"], "Тип контракта", file_name, row_number
                )
                contract_form = require_text(
                    row["Форма контракта"], "Форма контракта", file_name, row_number
                )
                if contract_type not in CONTRACT_TYPES:
                    raise RowValidationError(
                        file_name, row_number, f"unknown contract type {contract_type!r}"
                    )
                if contract_form not in CONTRACT_FORMS:
                    raise RowValidationError(
                        file_name, row_number, f"unknown contract form {contract_form!r}"
                    )
                region_raw = require_text(row["Регион"], "Регион", file_name, row_number)
                product_name = require_text(
                    row["Наименование товара"], "Наименование товара", file_name, row_number
                )
                yield (
                    require_text(
                        row["Номер сделки"], "Номер сделки", file_name, row_number
                    ).strip(),
                    parse_date(row["Дата сделки"], "Дата сделки", file_name, row_number),
                    parse_time(row["Время сделки"], "Время сделки", file_name, row_number),
                    trade_type,
                    parse_int(row["Количество"], "Количество", file_name, row_number),
                    parse_decimal(row["Сумма сделки"], "Сумма сделки", file_name, row_number),
                    parse_decimal(
                        row["Стартовая цена"], "Стартовая цена", file_name, row_number
                    ),
                    require_text(
                        row["Номер контракта"], "Номер контракта", file_name, row_number
                    ).strip(),
                    product_name,
                    normalize_text(product_name),
                    parse_int(row["Лот"], "Лот", file_name, row_number),
                    require_text(
                        row["Единица измерения"], "Единица измерения", file_name, row_number
                    ),
                    region_raw,
                    _validate_region(region_raw, file_name, row_number),
                    contract_type,
                    contract_form,
                    status,
                    _validate_inn(row["ИНН продавца"], "ИНН продавца", file_name, row_number),
                    _validate_inn(row["ИНН покупателя"], "ИНН покупателя", file_name, row_number),
                    require_text(
                        row["Брокер продавца"], "Брокер продавца", file_name, row_number
                    ).strip(),
                    require_text(
                        row["Брокер покупателя"], "Брокер покупателя", file_name, row_number
                    ).strip(),
                    parse_optional_date(
                        row["Дата оплаты"], "Дата оплаты", file_name, row_number
                    ),
                    parse_optional_date(
                        row["Дата поставки"], "Дата поставки", file_name, row_number
                    ),
                    parse_optional_int(
                        row["Поставлено"], "Поставлено", file_name, row_number
                    ),
                    file_name,
                    row_number,
                    dataset_version,
                )

        total += _copy_rows(conn, "deals", DEAL_COLUMNS, rows())
    return total


def load_all(conn: Connection, data_dir: Path, dataset_version: str) -> dict[str, int]:
    return {
        "participants.csv": load_participants(conn, data_dir, dataset_version),
        "brokers.csv": load_brokers(conn, data_dir, dataset_version),
        "contracts.csv": load_contracts(conn, data_dir, dataset_version),
        "usd_rates.csv": load_usd_rates(conn, data_dir, dataset_version),
        "deals_*.csv": load_deals(conn, data_dir, dataset_version),
    }
