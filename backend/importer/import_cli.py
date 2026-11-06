from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.database.connection import get_active_dataset, get_engine
from app.database.models import DICTIONARY_VERSION, SCHEMA_VERSION
from importer.integrity_checks import (
    CheckResult,
    critical_failures,
    run_integrity_checks,
)
from importer.manifests import (
    build_manifest,
    dataset_version_from_files,
    load_manifest,
    sha256_file,
    write_manifest,
)
from importer.staging import (
    BROKER_COLUMNS,
    CONTRACT_COLUMNS,
    DEAL_COLUMNS,
    PARTICIPANT_COLUMNS,
    USD_RATE_COLUMNS,
    create_staging,
    load_all,
    seed_reference_data,
)

LOGGER = logging.getLogger("uzex.importer")
REPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "manifests"


class ImportRejected(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _verify_manifest_checksums(
    data_dir: Path, manifest: dict[str, Any], zip_path: Path | None
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, info in manifest["files"].items():
        path = data_dir / name
        if not path.exists():
            results.append(
                CheckResult(f"checksum:{name}", False, info["sha256"], None, details="file missing")
            )
            continue
        actual = sha256_file(path)
        results.append(CheckResult(f"checksum:{name}", actual == info["sha256"], info["sha256"], actual))
    if zip_path and zip_path.exists() and manifest.get("zip_sha256"):
        actual_zip = sha256_file(zip_path)
        results.append(
            CheckResult("checksum:zip", actual_zip == manifest["zip_sha256"], manifest["zip_sha256"], actual_zip)
        )
    return results


def _swap_dataset(conn: Connection, manifest: dict[str, Any], version: str) -> None:
    # The whole replace is one transaction. A failure keeps the previous active
    # dataset untouched; the unique partial index guarantees one active row.
    conn.execute(text("DELETE FROM deals"))
    conn.execute(text("DELETE FROM contracts"))
    conn.execute(text("DELETE FROM participants"))
    conn.execute(text("DELETE FROM brokers"))
    conn.execute(text("DELETE FROM usd_rates"))
    conn.execute(
        text(
            f"INSERT INTO participants ({', '.join(PARTICIPANT_COLUMNS)}) "
            f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM stg_participants"
        )
    )
    conn.execute(
        text(
            f"INSERT INTO brokers ({', '.join(BROKER_COLUMNS)}) "
            f"SELECT {', '.join(BROKER_COLUMNS)} FROM stg_brokers"
        )
    )
    conn.execute(
        text(
            f"INSERT INTO contracts ({', '.join(CONTRACT_COLUMNS)}) "
            f"SELECT {', '.join(CONTRACT_COLUMNS)} FROM stg_contracts"
        )
    )
    conn.execute(
        text(
            f"INSERT INTO usd_rates ({', '.join(USD_RATE_COLUMNS)}) "
            f"SELECT {', '.join(USD_RATE_COLUMNS)} FROM stg_usd_rates"
        )
    )
    conn.execute(
        text(
            f"INSERT INTO deals ({', '.join(DEAL_COLUMNS)}) "
            f"SELECT {', '.join(DEAL_COLUMNS)} FROM stg_deals"
        )
    )
    conn.execute(text("UPDATE dataset_meta SET is_active = false WHERE is_active = true"))
    conn.execute(
        text(
            """
            INSERT INTO dataset_meta (
                dataset_version, imported_at, zip_checksum, file_checksums,
                row_counts, min_deal_date, max_deal_date, validation_status,
                schema_version, dictionary_version, is_active, notes
            ) VALUES (
                :version, :imported_at, :zip_checksum, CAST(:file_checksums AS jsonb),
                CAST(:row_counts AS jsonb), :min_date, :max_date, 'PASSED',
                :schema_version, :dictionary_version, true, CAST(:notes AS jsonb)
            )
            ON CONFLICT (dataset_version) DO UPDATE SET
                imported_at = EXCLUDED.imported_at,
                zip_checksum = EXCLUDED.zip_checksum,
                file_checksums = EXCLUDED.file_checksums,
                row_counts = EXCLUDED.row_counts,
                min_deal_date = EXCLUDED.min_deal_date,
                max_deal_date = EXCLUDED.max_deal_date,
                validation_status = EXCLUDED.validation_status,
                schema_version = EXCLUDED.schema_version,
                dictionary_version = EXCLUDED.dictionary_version,
                is_active = true,
                notes = EXCLUDED.notes
            """
        ),
        {
            "version": version,
            "imported_at": datetime.now(timezone.utc),
            "zip_checksum": manifest.get("zip_sha256"),
            "file_checksums": _json(manifest["files"]),
            "row_counts": _json(manifest["row_counts"]),
            "min_date": manifest["date_range"]["min_deal_date"],
            "max_date": manifest["date_range"]["max_deal_date"],
            "schema_version": SCHEMA_VERSION,
            "dictionary_version": DICTIONARY_VERSION,
            "notes": _json(
                {
                    "distinct_counts": manifest["distinct_counts"],
                    "control_sums": manifest["control_sums"],
                    "cutoff_date": manifest["cutoff_date"],
                }
            ),
        },
    )


def _verify_final(conn: Connection, manifest: dict[str, Any], version: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    expected_total = sum(
        manifest["row_counts"][f"deals_{year}.csv"] for year in range(2022, 2027)
    )
    actual_total = conn.execute(text("SELECT count(*) FROM deals")).scalar()
    checks.append(
        CheckResult("final:deals_total", actual_total == expected_total, expected_total, actual_total)
    )
    for file_name, table, key in (
        ("contracts.csv", "contracts", "contract_no"),
        ("participants.csv", "participants", "inn"),
        ("brokers.csv", "brokers", "broker_code"),
        ("usd_rates.csv", "usd_rates", "rate_date"),
    ):
        actual = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        checks.append(
            CheckResult(
                f"final:{table}",
                actual == manifest["row_counts"][file_name],
                manifest["row_counts"][file_name],
                actual,
            )
        )
    wrong_version = conn.execute(
        text("SELECT count(*) FROM deals WHERE dataset_version <> :v"), {"v": version}
    ).scalar()
    checks.append(CheckResult("final:deal_version", wrong_version == 0, 0, wrong_version))
    internal_uzs = conn.execute(
        text(
            """
            SELECT SUM(d.deal_amount) FROM deals d
            JOIN contracts c USING (contract_no)
            WHERE c.currency = 'Узбекский сум'
            """
        )
    ).scalar()
    expected_internal = manifest["control_sums"]["internal_uzs"]
    checks.append(
        CheckResult(
            "final:internal_uzs",
            str(internal_uzs) == str(expected_internal) or internal_uzs == Decimal(str(expected_internal)),
            expected_internal,
            str(internal_uzs),
        )
    )
    active = conn.execute(
        text("SELECT count(*) FROM dataset_meta WHERE is_active = true AND dataset_version = :v"),
        {"v": version},
    ).scalar()
    checks.append(CheckResult("final:active_version", active == 1, 1, active))
    return checks


def _write_report(report: dict[str, Any], version: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"import_report_{version.replace(':', '_')}.json"
    path.write_text(_json(report) + "\n", encoding="utf-8")
    latest = REPORT_DIR / "last_import_report.json"
    latest.write_text(_json(report) + "\n", encoding="utf-8")
    return path


def _record_failed_attempt(
    conn: Connection, version: str, manifest: dict[str, Any], failures: list[CheckResult]
) -> None:
    failed_version = f"{version}:failed:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}:{uuid.uuid4().hex[:6]}"
    conn.execute(
        text(
            """
            INSERT INTO dataset_meta (
                dataset_version, imported_at, zip_checksum, file_checksums,
                row_counts, min_deal_date, max_deal_date, validation_status,
                schema_version, dictionary_version, is_active, notes
            ) VALUES (
                :version, :imported_at, :zip_checksum, CAST(:file_checksums AS jsonb),
                CAST(:row_counts AS jsonb), NULL, NULL, 'FAILED',
                :schema_version, :dictionary_version, false, CAST(:notes AS jsonb)
            )
            """
        ),
        {
            "version": failed_version,
            "imported_at": datetime.now(timezone.utc),
            "zip_checksum": manifest.get("zip_sha256"),
            "file_checksums": _json(manifest.get("files", {})),
            "row_counts": _json(manifest.get("row_counts", {})),
            "schema_version": SCHEMA_VERSION,
            "dictionary_version": DICTIONARY_VERSION,
            "notes": _json(
                {
                    "reason": "integrity_checks_failed",
                    "failures": [
                        {
                            "name": item.name,
                            "expected": str(item.expected),
                            "actual": str(item.actual),
                        }
                        for item in failures[:100]
                    ],
                }
            ),
        },
    )


def run_import(
    data_dir: Path,
    zip_path: Path | None,
    manifest_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    file_checksums = {
        name: {"sha256": info["sha256"]} for name, info in manifest["files"].items()
    }
    version = dataset_version_from_files(file_checksums)

    with get_engine(readonly=False).connect() as conn:
        active = get_active_dataset(conn)
    if active and active["dataset_version"] == version and not force:
        LOGGER.info("dataset %s is already active; no import needed", version)
        return {"status": "SKIPPED_ALREADY_ACTIVE", "dataset_version": version}

    checksum_checks = _verify_manifest_checksums(data_dir, manifest, zip_path)
    checksum_failures = critical_failures(checksum_checks)
    if checksum_failures:
        report = {
            "status": "REJECTED",
            "dataset_version": version,
            "failures": [item.__dict__ for item in checksum_failures],
        }
        _write_report(report, version)
        raise ImportRejected("source files do not match the manifest checksums")

    checks: list[CheckResult] = []
    load_counts: dict[str, int] = {}
    engine = get_engine(readonly=False)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            seed_reference_data(conn)
            create_staging(conn)
            LOGGER.info("loading CSV into staging")
            load_counts = load_all(conn, data_dir, version)
            LOGGER.info("staging loaded: %s", load_counts)
            checks = run_integrity_checks(conn, manifest)
            failures = critical_failures(checks)
            if failures:
                transaction.rollback()
                with engine.connect() as failure_conn:
                    _record_failed_attempt(failure_conn, version, manifest, failures)
                    failure_conn.commit()
                report = {
                    "status": "REJECTED",
                    "dataset_version": version,
                    "load_counts": load_counts,
                    "checks": [item.__dict__ for item in checks],
                    "failures": [item.__dict__ for item in failures],
                }
                _write_report(report, version)
                return report

            if dry_run:
                transaction.rollback()
                report = {
                    "status": "DRY_RUN_PASSED",
                    "dataset_version": version,
                    "load_counts": load_counts,
                    "checks": [item.__dict__ for item in checks],
                }
                _write_report(report, version)
                return report

            LOGGER.info("validating and swapping normalized tables")
            _swap_dataset(conn, manifest, version)
            final_checks = _verify_final(conn, manifest, version)
            final_failures = critical_failures(final_checks)
            if final_failures:
                transaction.rollback()
                report = {
                    "status": "REJECTED_FINAL_VALIDATION",
                    "dataset_version": version,
                    "final_checks": [item.__dict__ for item in final_checks],
                    "failures": [item.__dict__ for item in final_failures],
                }
                _write_report(report, version)
                return report
            transaction.commit()
            LOGGER.info("dataset %s is active", version)
        except Exception:
            if transaction.is_active:
                transaction.rollback()
            raise

    # ANALYZE is a performance operation, not part of data correctness.
    with engine.connect() as conn:
        conn.execute(text("ANALYZE deals"))
        conn.execute(text("ANALYZE contracts"))
        conn.execute(text("ANALYZE participants"))
        conn.execute(text("ANALYZE brokers"))
        conn.execute(text("ANALYZE usd_rates"))
        conn.commit()

    report = {
        "status": "IMPORTED",
        "dataset_version": version,
        "load_counts": load_counts,
        "checks": [item.__dict__ for item in checks],
        "final_checks": [item.__dict__ for item in final_checks],
    }
    path = _write_report(report, version)
    report["report_path"] = str(path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import UZEX CSV data reproducibly")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "raw" / "uzex_spot_data",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "uzex_spot_data.zip",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "data"
        / "manifests"
        / "uzex_spot_data.expected.json",
    )
    parser.add_argument("--force", action="store_true", help="re-import even if active")
    parser.add_argument("--dry-run", action="store_true", help="validate without swapping")
    parser.add_argument(
        "--build-manifest",
        action="store_true",
        help="build the expected manifest from the current files and exit",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    data_dir = args.data_dir.resolve()
    zip_path = args.zip.resolve() if args.zip else None
    manifest_path = args.manifest.resolve()

    if args.build_manifest:
        manifest = build_manifest(data_dir, zip_path)
        write_manifest(manifest, manifest_path)
        print(
            json.dumps(
                {
                    "manifest": str(manifest_path),
                    "dataset_version": manifest["dataset_version"],
                    "row_counts": manifest["row_counts"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        report = run_import(
            data_dir,
            zip_path,
            manifest_path,
            force=args.force,
            dry_run=args.dry_run,
        )
    except ImportRejected as exc:
        LOGGER.error("import rejected: %s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("import failed: %s", exc)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str)[:12000])
    return 0 if report["status"] in {"IMPORTED", "DRY_RUN_PASSED", "SKIPPED_ALREADY_ACTIVE"} else 1


if __name__ == "__main__":
    sys.exit(main())
