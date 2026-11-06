from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from importer.normalizers import RowValidationError

DEAL_HEADERS = [
    "Номер сделки",
    "Дата сделки",
    "Время сделки",
    "Тип торгов",
    "Количество",
    "Сумма сделки",
    "Стартовая цена",
    "Номер контракта",
    "Наименование товара",
    "Лот",
    "Единица измерения",
    "Регион",
    "Тип контракта",
    "Форма контракта",
    "Состояние сделки",
    "ИНН продавца",
    "ИНН покупателя",
    "Брокер продавца",
    "Брокер покупателя",
    "Дата оплаты",
    "Дата поставки",
    "Поставлено",
]

CONTRACT_HEADERS = [
    "Номер контракта",
    "Наименование продукта",
    "Марка товара",
    "Размер товара",
    "Сорт товара",
    "Тип товара",
    "Класс товара",
    "Код ТН ВЭД",
    "Группа товара",
    "Категория товара",
    "Отрасль промышленности",
    "Единица измерения",
    "Лот",
    "Валюта",
    "Базисная цена",
    "Сумма залога",
    "Базис поставки",
    "Регион",
    "Адрес склада",
    "Тип торгов",
    "Тип контракта",
    "Форма контракта",
    "Срок оплаты",
    "Срок поставки",
    "Упаковка",
    "ИНН продавца",
    "Дата регистрации",
    "Действует до",
]

PARTICIPANT_HEADERS = ["ИНН", "Наименование", "Регион", "Вид деятельности", "Роль", "Плательщик НДС"]
BROKER_HEADERS = ["Код брокера", "Наименование", "ИНН", "Регион"]
USD_RATE_HEADERS = ["Дата", "Курс"]

EXPECTED_HEADERS: dict[str, list[str]] = {
    "contracts.csv": CONTRACT_HEADERS,
    "participants.csv": PARTICIPANT_HEADERS,
    "brokers.csv": BROKER_HEADERS,
    "usd_rates.csv": USD_RATE_HEADERS,
}
for _year in range(2022, 2027):
    EXPECTED_HEADERS[f"deals_{_year}.csv"] = DEAL_HEADERS

EXPECTED_FILES = tuple(EXPECTED_HEADERS)


@dataclass(frozen=True)
class CsvFile:
    name: str
    path: Path
    headers: list[str]
    row_count: int


def inspect_file(path: Path) -> CsvFile:
    expected = EXPECTED_HEADERS.get(path.name)
    if expected is None:
        raise ValueError(f"unexpected file: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"{path.name}: empty file") from exc
        if header != expected:
            raise ValueError(
                f"{path.name}: header mismatch. Expected {expected!r}, got {header!r}"
            )
        count = 0
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise RowValidationError(
                    path.name,
                    row_number,
                    f"expected {len(header)} columns, got {len(row)}",
                )
            count += 1
    return CsvFile(name=path.name, path=path, headers=header, row_count=count)


def iter_rows(csv_file: CsvFile) -> Iterator[tuple[int, dict[str, str]]]:
    with csv_file.path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = next(reader)
        if header != csv_file.headers:
            raise ValueError(f"{csv_file.name}: header changed while reading")
        for row_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise RowValidationError(
                    csv_file.name,
                    row_number,
                    f"expected {len(header)} columns, got {len(row)}",
                )
            yield row_number, dict(zip(header, row, strict=True))
