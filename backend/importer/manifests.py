from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from importer.csv_reader import EXPECTED_FILES, inspect_file, iter_rows
from importer.normalizers import (
    parse_date,
    parse_decimal,
    optional_text,
)

DICTIONARY_VERSION = "1.0"
CUTOFF_DATE = "2026-10-30"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest_digest(file_checksums: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    for name in sorted(file_checksums):
        digest.update(f"{name}:{file_checksums[name]['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def dataset_version_from_files(file_checksums: dict[str, Any]) -> str:
    return f"sha256:{compute_manifest_digest(file_checksums)}"


def _read_reference(data_dir: Path) -> tuple[dict, dict, dict, dict, dict[str, Any]]:
    contracts = {}
    for _, row in iter_rows(inspect_file(data_dir / "contracts.csv")):
        contracts[row["Номер контракта"]] = row
    participants = {}
    for _, row in iter_rows(inspect_file(data_dir / "participants.csv")):
        participants[row["ИНН"]] = row
    brokers = {}
    for _, row in iter_rows(inspect_file(data_dir / "brokers.csv")):
        brokers[row["Код брокера"]] = row
    rates = {}
    for _, row in iter_rows(inspect_file(data_dir / "usd_rates.csv")):
        rates[parse_date(row["Дата"], "Дата", "usd_rates.csv", 0)] = parse_decimal(
            row["Курс"], "Курс", "usd_rates.csv", 0
        )
    return contracts, participants, brokers, rates, {}


def build_manifest(data_dir: Path, zip_path: Path | None = None) -> dict[str, Any]:
    """Build a complete expected manifest for the provided archive.

    This is an administrative tool. The resulting JSON is used by the importer
    as an independent expected baseline, never by the production formatter.
    """
    present = {p.name for p in data_dir.glob("*.csv")}
    missing = set(EXPECTED_FILES) - present
    extra = present - set(EXPECTED_FILES)
    if missing or extra:
        raise ValueError(f"file set mismatch: missing={sorted(missing)} extra={sorted(extra)}")

    files: dict[str, Any] = {}
    for name in EXPECTED_FILES:
        inspected = inspect_file(data_dir / name)
        files[name] = {
            "sha256": sha256_file(inspected.path),
            "rows": inspected.row_count,
            "columns": inspected.headers,
        }

    contracts, participants, brokers, rates, _ = _read_reference(data_dir)

    deal_counts: dict[str, int] = {}
    statuses: Counter[str] = Counter()
    trade_types: Counter[str] = Counter()
    contract_types: Counter[str] = Counter()
    contract_forms: Counter[str] = Counter()
    units: Counter[str] = Counter()
    deal_regions: Counter[str] = Counter()
    deal_numbers: set[str] = set()
    deal_products: set[str] = set()
    deal_products_by_contract: dict[str, set[str]] = defaultdict(set)
    seller_inns: set[str] = set()
    buyer_inns: set[str] = set()
    seller_brokers: set[str] = set()
    buyer_brokers: set[str] = set()
    used_contracts: set[str] = set()
    null_payment = null_delivery = null_delivered = 0
    delivered_samples = 0
    delivered_gt_qty = 0
    amount_zero = 0
    new_not_cutoff = 0
    rate_missing = 0
    sum_uzs = Decimal(0)
    sum_usd = Decimal(0)
    sum_export_uzs = Decimal(0)
    mismatches: Counter[str] = Counter()
    min_date: date | None = None
    max_date: date | None = None

    for year in range(2022, 2027):
        name = f"deals_{year}.csv"
        count = 0
        for row_number, row in iter_rows(inspect_file(data_dir / name)):
            count += 1
            deal_no = row["Номер сделки"]
            if deal_no in deal_numbers:
                mismatches["duplicate_deal_no"] += 1
            deal_numbers.add(deal_no)
            deal_date = parse_date(row["Дата сделки"], "Дата сделки", name, row_number)
            min_date = deal_date if min_date is None else min(min_date, deal_date)
            max_date = deal_date if max_date is None else max(max_date, deal_date)
            status = row["Состояние сделки"]
            statuses[status] += 1
            if status == "Новый" and deal_date != date(2026, 10, 30):
                new_not_cutoff += 1
            trade_types[row["Тип торгов"]] += 1
            contract_types[row["Тип контракта"]] += 1
            contract_forms[row["Форма контракта"]] += 1
            units[row["Единица измерения"]] += 1
            deal_regions[row["Регион"]] += 1
            deal_products.add(row["Наименование товара"])
            deal_products_by_contract[row["Номер контракта"]].add(row["Наименование товара"])
            used_contracts.add(row["Номер контракта"])
            quantity = int(row["Количество"])
            lot = int(row["Лот"])
            amount = parse_decimal(row["Сумма сделки"], "Сумма сделки", name, row_number)
            if amount == 0:
                amount_zero += 1
            if not row["Дата оплаты"]:
                null_payment += 1
            if not row["Дата поставки"]:
                null_delivery += 1
            if not row["Поставлено"]:
                null_delivered += 1
            else:
                delivered_samples += 1
                if int(row["Поставлено"]) > quantity:
                    delivered_gt_qty += 1
            seller_inns.add(row["ИНН продавца"])
            buyer_inns.add(row["ИНН покупателя"])
            seller_brokers.add(row["Брокер продавца"])
            buyer_brokers.add(row["Брокер покупателя"])

            contract = contracts.get(row["Номер контракта"])
            if contract is None:
                mismatches["unknown_contract"] += 1
                continue
            if row["ИНН продавца"] not in participants:
                mismatches["unknown_seller"] += 1
            if row["ИНН покупателя"] not in participants:
                mismatches["unknown_buyer"] += 1
            if row["Брокер продавца"] not in brokers:
                mismatches["unknown_seller_broker"] += 1
            if row["Брокер покупателя"] not in brokers:
                mismatches["unknown_buyer_broker"] += 1
            if row["ИНН продавца"] != contract["ИНН продавца"]:
                mismatches["seller_mismatch"] += 1
            if row["Тип торгов"] != contract["Тип торгов"]:
                mismatches["trade_type_mismatch"] += 1
            if row["Тип контракта"] != contract["Тип контракта"]:
                mismatches["contract_type_mismatch"] += 1
            if row["Форма контракта"] != contract["Форма контракта"]:
                mismatches["contract_form_mismatch"] += 1
            if row["Единица измерения"] != contract["Единица измерения"]:
                mismatches["unit_mismatch"] += 1
            if int(row["Лот"]) != int(contract["Лот"]):
                mismatches["lot_mismatch"] += 1
            if not (
                parse_date(contract["Дата регистрации"], "Дата регистрации", "contracts.csv", 0)
                <= deal_date
                <= parse_date(contract["Действует до"], "Действует до", "contracts.csv", 0)
            ):
                mismatches["outside_contract_period"] += 1
            if deal_date not in rates:
                rate_missing += 1
            if contract["Валюта"] == "Узбекский сум":
                if row["Тип контракта"] != "Внутренний":
                    mismatches["uzs_not_internal"] += 1
                sum_uzs += amount
            elif contract["Валюта"] == "Доллар США":
                if row["Тип контракта"] != "Экспортный":
                    mismatches["usd_not_export"] += 1
                sum_usd += amount
                if deal_date in rates:
                    sum_export_uzs += amount * rates[deal_date]
            else:
                mismatches["unknown_currency"] += 1
        deal_counts[name] = count

    participant_roles = Counter(row["Роль"] for row in participants.values())
    contract_product_names = {row["Наименование продукта"] for row in contracts.values()}
    contract_units = Counter(row["Единица измерения"] for row in contracts.values())
    contract_regions = Counter(row["Регион"] for row in contracts.values())
    participant_regions = Counter(row["Регион"] for row in participants.values())
    broker_regions = Counter(row["Регион"] for row in brokers.values())
    contract_currencies = Counter(row["Валюта"] for row in contracts.values())
    contract_types_ref = Counter(row["Тип контракта"] for row in contracts.values())
    contract_forms_ref = Counter(row["Форма контракта"] for row in contracts.values())
    contract_trade_types_ref = Counter(row["Тип торгов"] for row in contracts.values())
    product_groups = Counter(row["Группа товара"] for row in contracts.values())
    product_categories = Counter(row["Категория товара"] for row in contracts.values())
    industries = Counter(row["Отрасль промышленности"] for row in contracts.values())
    used_participants = seller_inns | buyer_inns

    manifest: dict[str, Any] = {
        "dataset_name": "uzex_spot_data",
        "dictionary_version": DICTIONARY_VERSION,
        "cutoff_date": CUTOFF_DATE,
        "files": files,
        "row_counts": {
            **deal_counts,
            "contracts.csv": len(contracts),
            "participants.csv": len(participants),
            "brokers.csv": len(brokers),
            "usd_rates.csv": len(rates),
        },
        "distinct_counts": {
            "deal_numbers": len(deal_numbers),
            "contracts_used": len(used_contracts),
            "deal_product_names": len(deal_products),
            "contract_product_names": len(contract_product_names),
            "contract_hs_codes": len({row["Код ТН ВЭД"] for row in contracts.values()}),
            "seller_inns_used": len(seller_inns),
            "buyer_inns_used": len(buyer_inns),
            "seller_brokers_used": len(seller_brokers),
            "buyer_brokers_used": len(buyer_brokers),
            "participants_used": len(used_participants),
            "participants_unused": len(participants) - len(used_participants),
            "contracts_unused": len(contracts) - len(used_contracts),
        },
        "null_counts": {
            "payment_date": null_payment,
            "delivery_date": null_delivery,
            "delivered_lots": null_delivered,
        },
        "status_counts": dict(statuses),
        "trade_type_counts": dict(trade_types),
        "contract_type_counts": dict(contract_types),
        "contract_form_counts": dict(contract_forms),
        "unit_counts": dict(units),
        "deal_region_counts": dict(deal_regions),
        "contract_region_counts": dict(contract_regions),
        "participant_region_counts": dict(participant_regions),
        "broker_region_counts": dict(broker_regions),
        "participant_role_counts": dict(participant_roles),
        "contract_currency_counts": dict(contract_currencies),
        "contract_ref_contract_type_counts": dict(contract_types_ref),
        "contract_ref_contract_form_counts": dict(contract_forms_ref),
        "contract_ref_trade_type_counts": dict(contract_trade_types_ref),
        "contract_unit_counts": dict(contract_units),
        "product_group_counts": dict(product_groups),
        "product_category_counts": dict(product_categories),
        "industry_counts": dict(industries),
        "delivered_samples": delivered_samples,
        "delivered_gt_qty": delivered_gt_qty,
        "amount_zero": amount_zero,
        "new_not_cutoff": new_not_cutoff,
        "rate_missing": rate_missing,
        "contracts_with_multiple_deal_product_names": sum(
            1 for names in deal_products_by_contract.values() if len(names) > 1
        ),
        "control_sums": {
            "internal_uzs": str(sum_uzs),
            "export_usd": str(sum_usd),
            "export_converted_uzs": str(sum_export_uzs),
            "total_uzs_readme": str(sum_uzs + sum_export_uzs),
        },
        "date_range": {
            "min_deal_date": min_date.isoformat() if min_date else None,
            "max_deal_date": max_date.isoformat() if max_date else None,
        },
        "usd_rates": {
            "count": len(rates),
            "min_date": min(rates).isoformat(),
            "max_date": max(rates).isoformat(),
            "min_value": str(min(rates.values())),
            "max_value": str(max(rates.values())),
        },
        "expected_mismatches_zero": {
            key: 0
            for key in (
                "duplicate_deal_no",
                "unknown_contract",
                "unknown_seller",
                "unknown_buyer",
                "unknown_seller_broker",
                "unknown_buyer_broker",
                "seller_mismatch",
                "trade_type_mismatch",
                "contract_type_mismatch",
                "contract_form_mismatch",
                "unit_mismatch",
                "lot_mismatch",
                "outside_contract_period",
                "uzs_not_internal",
                "usd_not_export",
                "unknown_currency",
            )
        },
        "observed_mismatches": dict(mismatches),
        "zip_sha256": sha256_file(zip_path) if zip_path and zip_path.exists() else None,
    }
    manifest["dataset_version"] = dataset_version_from_files(files)
    return manifest


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def text_or_none(value: str | None) -> str | None:
    return optional_text(value or "")
