from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.analytics.bank_v47 import estimate_roe_uncertainty

ENV = dict(os.environ)
ENV.setdefault("PGHOST", "localhost")
ENV.setdefault("PGUSER", "postgres")
ENV.setdefault("PGPASSWORD", "postgres")
ENV.setdefault("PGDATABASE", "postgres")
PSQL_TIMEOUT_SECONDS = 120


def psql(*args: str) -> str:
    try:
        result = subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-F", "|", *args],
            cwd=ROOT,
            env=ENV,
            capture_output=True,
            text=True,
            timeout=PSQL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"psql {PSQL_TIMEOUT_SECONDS} saniyede tamamlanmadi"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"psql baslatilamadi: {type(exc).__name__}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"PostgreSQL komutu basarisiz:\n{result.stderr.strip()}")
    return result.stdout


def parse_server_version_num(raw: str) -> int:
    if not isinstance(raw, str):
        raise TypeError("server_version_num metin olmali")
    matches = re.findall(r"(?m)^\s*(\d{5,6})\s*$", raw)
    if len(matches) != 1:
        raise RuntimeError(f"server_version_num ayrıştırılamadı: {raw!r}")
    return int(matches[0])


def assert_postgres_16() -> int:
    version = parse_server_version_num(psql("-c", "SHOW server_version_num;"))
    if not 160000 <= version < 170000:
        raise RuntimeError(f"PostgreSQL 16 gerekli; server_version_num={version}")
    return version


def apply_fixture() -> None:
    for path in (
        ROOT / "sql" / "000_create_schemas.sql",
        ROOT / "sql" / "011_bank_valuation_integration.sql",
        ROOT / "test_fixtures" / "production_integration" / "setup_bank_production_fixture.sql",
    ):
        psql("-f", str(path))


def cleanup_fixture() -> None:
    psql("-c", "DELETE FROM core.bank_metrics_quarterly WHERE ticker = 'FIXBNK';")


def query_slots(analysis_at: str, anchor: str = "2025-12-31") -> list[dict]:
    sql = f"""
    SELECT period_end, roe_ttm, selected_version_tag,
           selected_published_at, selected_version_sequence, record_id
    FROM analytics.bank_point_in_time_slots(
      'FIXBNK', '{analysis_at}'::timestamptz, '{anchor}'::date
    );
    """
    out = psql("-c", sql)
    rows = []
    for line in out.strip().splitlines():
        values = line.split("|")
        if len(values) != 6:
            continue
        period, roe, version, published, sequence, record_id = values
        rows.append({
            "period_end": period,
            "roe": None if roe == "" else float(roe),
            "version": None if version == "" else version,
            "published": None if published == "" else published,
            "sequence": None if sequence == "" else int(sequence),
            "record_id": None if record_id == "" else int(record_id),
        })
    if len(rows) != 8:
        raise AssertionError(f"sekiz slot bekleniyordu, {len(rows)} geldi: {rows!r}")
    return rows


def roe_sus(series):
    valid = sorted(x for x in series if x is not None)
    return median(valid[1:-1])


def assert_daily_reference() -> dict:
    references = {
        "2026-03-01T12:00:00+03:00": {
            "series": [0.1560, 0.1898, None, 0.2346, 0.2100, 0.2400, 0.2950, 0.3080],
            "versions": ["ORIGINAL", "ORIGINAL", None, "ORIGINAL", "RESTATED", "RESTATED", "ORIGINAL", "ORIGINAL"],
            "slope": 0.021040,
            "sd": 0.01192010,
            "missing": 1,
            "n_valid": 7,
        },
        "2025-10-01T12:00:00+03:00": {
            "series": [0.1560, 0.1898, None, 0.2346, 0.2689, 0.2400, None, None],
            "versions": ["ORIGINAL", "ORIGINAL", None, "ORIGINAL", "ORIGINAL", "RESTATED", None, None],
            "slope": 0.024300,
            "sd": 0.00845082,
            "missing": 3,
            "n_valid": 5,
        },
    }
    report = {}
    for analysis_at, expected in references.items():
        rows = query_slots(analysis_at)
        series = [row["roe"] for row in rows]
        versions = [row["version"] for row in rows]
        if series != expected["series"]:
            raise AssertionError(f"seri farkli {analysis_at}: {series}")
        if versions != expected["versions"]:
            raise AssertionError(f"surumler farkli {analysis_at}: {versions}")
        uncertainty = estimate_roe_uncertainty(series)
        if abs(uncertainty["trend_slope"] - expected["slope"]) > 1e-9:
            raise AssertionError((analysis_at, uncertainty["trend_slope"], expected["slope"]))
        if abs(uncertainty["sd_roe_effective"] - expected["sd"]) > 1e-8:
            raise AssertionError((analysis_at, uncertainty["sd_roe_effective"], expected["sd"]))
        if uncertainty["roe_missing_count"] != expected["missing"]:
            raise AssertionError((analysis_at, uncertainty["roe_missing_count"], expected["missing"]))
        if uncertainty["n_valid"] != expected["n_valid"]:
            raise AssertionError((analysis_at, uncertainty["n_valid"], expected["n_valid"]))
        report[analysis_at] = {
            "trend_slope": uncertainty["trend_slope"],
            "sd_roe_effective": uncertainty["sd_roe_effective"],
            "n_valid": uncertainty["n_valid"],
            "roe_missing_count": uncertainty["roe_missing_count"],
            "roe_sus": roe_sus(series),
        }
    return report


def assert_intraday_reference() -> dict:
    expected = {
        "2025-08-08T09:00:00+03:00": (None, None),
        "2025-08-08T12:00:00+03:00": ("ORIGINAL", 0.2809),
        "2025-08-08T18:00:00+03:00": ("RESTATED", 0.2400),
    }
    report = {}
    for analysis_at, target in expected.items():
        rows = query_slots(analysis_at, anchor="2025-06-30")
        latest = rows[-1]
        observed = (latest["version"], latest["roe"])
        if observed != target:
            raise AssertionError((analysis_at, observed, target))
        report[analysis_at] = {"version": observed[0], "roe": observed[1]}
    return report


def run_acceptance(*, keep_fixture: bool = False) -> dict:
    server_version_num = assert_postgres_16()
    fixture_started = False
    try:
        fixture_started = True
        apply_fixture()
        return {
            "postgres_server_version_num": server_version_num,
            "daily": assert_daily_reference(),
            "intraday": assert_intraday_reference(),
            "fixture_kept": keep_fixture,
            "status": "PASS",
        }
    finally:
        if fixture_started and not keep_fixture:
            active_error = sys.exc_info()[0] is not None
            try:
                cleanup_fixture()
            except Exception:
                if not active_error:
                    raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-fixture", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_acceptance(keep_fixture=args.keep_fixture), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
