"""
scripts/generate_coverage_report.py

Usage:
    python3 scripts/generate_coverage_report.py
    python3 scripts/generate_coverage_report.py --db-path /custom/path.duckdb
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import duckdb

from config.settings import PipelineConfig
from reporting.coverage_report import build_coverage_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate a coverage/data-quality report")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    config = PipelineConfig.from_env()
    db_path = args.db_path or config.db_path
    report_dir = args.report_dir or config.report_dir

    con = duckdb.connect(str(db_path))
    try:
        report = build_coverage_report(con)
    finally:
        con.close()

    report.print_summary()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved_path = report.save(report_dir / f"coverage_report_{timestamp}.json")
    print(f"\nreport saved to {saved_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
