from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from sqlalchemy import text

from app.database.connection import readonly_connection

QUERIES = {
    "2024-01-01": "select count(*) from deals where deal_date = date '2024-01-01'",
    "2024-01-02": "select count(*) from deals where deal_date = date '2024-01-02'",
    "not_signed_2024": (
        "select count(*) from deals d join contracts c using(contract_no) "
        "where d.status = 'Не подписан' and d.deal_date between date '2024-01-01' and date '2024-12-31'"
    ),
    "not_signed_all": (
        "select count(*) from deals d join contracts c using(contract_no) where d.status = 'Не подписан'"
    ),
    "export_sum": (
        "select count(*), sum(d.deal_amount) from deals d join contracts c using(contract_no) "
        "where c.contract_type = 'Экспортный'"
    ),
    "internal_2024_sum": (
        "select count(*), sum(d.deal_amount) from deals d join contracts c using(contract_no) "
        "where c.contract_type = 'Внутренний' and d.deal_date between date '2024-01-01' and date '2024-12-31'"
    ),
}


with readonly_connection() as conn:
    for name, sql in QUERIES.items():
        print(name, conn.execute(text(sql)).all())
