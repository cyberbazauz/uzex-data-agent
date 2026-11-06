from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database.connection import get_active_dataset, get_engine
from importer.manifests import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / "data" / "manifests" / "uzex_spot_data.expected.json"


@pytest.fixture(scope="session")
def manifest() -> dict:
    return load_manifest(MANIFEST_PATH)


@pytest.fixture()
def reader_conn() -> Iterator[Connection]:
    engine = get_engine(readonly=True)
    with engine.connect() as conn:
        yield conn


@pytest.fixture()
def owner_conn() -> Iterator[Connection]:
    engine = get_engine(readonly=False)
    with engine.connect() as conn:
        yield conn


@pytest.fixture()
def active_dataset(reader_conn: Connection) -> dict:
    dataset = get_active_dataset(reader_conn)
    assert dataset is not None, "no active dataset; run importer first"
    return dataset


def scalar(conn: Connection, sql: str, **params: object) -> object:
    return conn.execute(text(sql), params).scalar()
