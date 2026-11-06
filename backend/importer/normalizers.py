from __future__ import annotations

from datetime import date, time
from decimal import Decimal, InvalidOperation


class RowValidationError(ValueError):
    def __init__(self, file_name: str, row_number: int, message: str) -> None:
        super().__init__(f"{file_name}:{row_number}: {message}")
        self.file_name = file_name
        self.row_number = row_number
        self.message = message


def require_text(raw: str, field: str, file_name: str, row_number: int) -> str:
    if raw is None:
        raise RowValidationError(file_name, row_number, f"missing required field {field!r}")
    if raw.strip() == "":
        raise RowValidationError(file_name, row_number, f"empty required field {field!r}")
    return raw


def optional_text(raw: str) -> str | None:
    if raw is None:
        return None
    if raw.strip() == "":
        return None
    return raw


def parse_int(raw: str, field: str, file_name: str, row_number: int) -> int:
    value = require_text(raw, field, file_name, row_number).strip().replace("\u00a0", " ")
    if " " in value:
        value = value.replace(" ", "")
    try:
        return int(value)
    except ValueError as exc:
        raise RowValidationError(
            file_name, row_number, f"invalid integer {field!r}: {raw!r}"
        ) from exc


def parse_optional_int(raw: str, field: str, file_name: str, row_number: int) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    return parse_int(raw, field, file_name, row_number)


def parse_decimal(raw: str, field: str, file_name: str, row_number: int) -> Decimal:
    value = require_text(raw, field, file_name, row_number)
    cleaned = value.replace("\u00a0", " ").replace(" ", "").strip()
    if "," in cleaned:
        raise RowValidationError(
            file_name, row_number, f"comma decimal separator is not allowed in {field!r}"
        )
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise RowValidationError(
            file_name, row_number, f"invalid decimal {field!r}: {raw!r}"
        ) from exc


def parse_percent(raw: str, field: str, file_name: str, row_number: int) -> Decimal:
    value = require_text(raw, field, file_name, row_number).strip()
    if value.endswith("%"):
        value = value[:-1].strip()
    return parse_decimal(value, field, file_name, row_number)


def parse_date(raw: str, field: str, file_name: str, row_number: int) -> date:
    from datetime import datetime

    value = require_text(raw, field, file_name, row_number).strip()
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError as exc:
        raise RowValidationError(
            file_name, row_number, f"invalid date {field!r}: {raw!r}"
        ) from exc


def parse_optional_date(raw: str, field: str, file_name: str, row_number: int) -> date | None:
    if raw is None or raw.strip() == "":
        return None
    return parse_date(raw, field, file_name, row_number)


def parse_time(raw: str, field: str, file_name: str, row_number: int) -> time:
    from datetime import datetime

    value = require_text(raw, field, file_name, row_number).strip()
    try:
        return datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise RowValidationError(
            file_name, row_number, f"invalid time {field!r}: {raw!r}"
        ) from exc


def parse_bool_da_net(raw: str, field: str, file_name: str, row_number: int) -> bool:
    value = require_text(raw, field, file_name, row_number).strip()
    if value == "Да":
        return True
    if value == "Нет":
        return False
    raise RowValidationError(
        file_name, row_number, f"unexpected boolean value {field!r}: {raw!r}"
    )
