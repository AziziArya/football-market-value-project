"""
reporting/coverage_report.py

Read-only reporting over the unified DB. Every number here is a plain
COUNT/GROUP BY query against tables built in Phases 1-2 — no statistical
inference, no imputation, no changes to matching thresholds or merge
behavior (this module never writes to the DB).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

IDENTITY_FIELDS = ("display_name", "nationality", "club", "date_of_birth", "position")


@dataclass
class CoverageReport:
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_coverage: dict = field(default_factory=dict)
    matching_stats: dict = field(default_factory=dict)
    field_completeness: dict = field(default_factory=dict)
    lineage_coverage: dict = field(default_factory=dict)
    injury_status: dict = field(default_factory=dict)
    market_value_coverage: dict = field(default_factory=dict)
    missing_data_summary: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        return path

    def print_summary(self) -> None:
        print("=" * 60)
        print(f"COVERAGE REPORT — generated {self.generated_at}")
        print("-" * 60)
        print("source coverage:", self.source_coverage)
        print("matching stats: ", self.matching_stats)
        print("field completeness:")
        for source, fields_ in self.field_completeness.items():
            print(f"  {source}:")
            for fname, stats in fields_.items():
                print(f"    {fname:<16} {stats['present']}/{stats['total']} ({stats['pct']}%)")
        print("lineage coverage (rows linked to a canonical player):")
        for table, stats in self.lineage_coverage.items():
            print(f"  {table:<24} {stats['with_player_uid']}/{stats['total']} ({stats['pct']}%)")
        print("injury status:", self.injury_status)
        print("market value coverage (reported separately, never merged):")
        for kind, stats in self.market_value_coverage.items():
            print(f"  {kind:<24} {stats['players_with_data']}/{stats['total_players']} ({stats['pct']}%)")
        if self.missing_data_summary:
            print("missing data summary:")
            for line in self.missing_data_summary:
                print(f"  - {line}")
        print("=" * 60)


def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 2)


def _matching_stats(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute(
        "select match_status, count(*) from identity_matches where is_best = true group by match_status"
    ).fetchall()
    counts = {status: 0 for status in ("MATCHED", "PROBABLE_MATCH", "AMBIGUOUS", "UNMATCHED")}
    for status, n in rows:
        counts[status] = n
    total = sum(counts.values())
    stats = dict(counts)
    stats["total"] = total
    for status in ("MATCHED", "PROBABLE_MATCH", "AMBIGUOUS", "UNMATCHED"):
        stats[f"{status.lower()}_pct"] = _pct(counts[status], total)
    return stats


def _source_coverage(con: duckdb.DuckDBPyConnection) -> dict:
    ea_total = con.execute("select count(*) from ea_fc26_attributes").fetchone()[0]
    tm_total = con.execute(
        "select count(distinct source_record_id) from player_field_values where source = 'transfermarkt_dataset'"
    ).fetchone()[0]
    return {
        "ea_fc26": {"total_records": ea_total},
        "transfermarkt_dataset": {"total_player_records": tm_total},
    }


def _field_completeness(con: duckdb.DuckDBPyConnection) -> dict:
    result = {}
    for source, denominator_query in (
        ("ea_fc26", "select count(*) from ea_fc26_attributes"),
        ("transfermarkt_dataset", "select count(distinct source_record_id) from player_field_values where source='transfermarkt_dataset'"),
    ):
        total = con.execute(denominator_query).fetchone()[0]
        result[source] = {}
        for field_name in IDENTITY_FIELDS:
            present = con.execute(
                "select count(distinct source_record_id) from player_field_values "
                "where source = ? and field_name = ? and field_value is not null and field_value != ''",
                [source, field_name],
            ).fetchone()[0]
            result[source][field_name] = {"present": present, "total": total, "pct": _pct(present, total)}
    return result


def _lineage_coverage(con: duckdb.DuckDBPyConnection) -> dict:
    result = {}
    for table in ("ea_fc26_attributes", "player_field_values", "market_value_history", "transfers", "appearances"):
        total = con.execute(f"select count(*) from {table}").fetchone()[0]
        with_uid = con.execute(f"select count(*) from {table} where player_uid is not null").fetchone()[0]
        result[table] = {"with_player_uid": with_uid, "total": total, "pct": _pct(with_uid, total)}
    return result


def _injury_status(con: duckdb.DuckDBPyConnection) -> dict:
    rows = con.execute("select status, count(*) from injury_data_status group by status").fetchall()
    counts = {"NO_SOURCE_AVAILABLE": 0, "CONFIRMED_NO_INJURIES": 0, "HAS_RECORDS": 0}
    for status, n in rows:
        counts[status] = n
    total_players = con.execute("select count(*) from players").fetchone()[0]
    missing = con.execute(
        "select count(*) from players p where not exists (select 1 from injury_data_status s where s.player_uid = p.player_uid)"
    ).fetchone()[0]
    stats = dict(counts)
    stats["total_players"] = total_players
    stats["players_missing_status_row"] = missing  # should always be 0; a gap here is a real bug, not noise
    return stats


def _market_value_coverage(con: duckdb.DuckDBPyConnection) -> dict:
    total_players = con.execute("select count(*) from players").fetchone()[0]

    tm_players = con.execute(
        "select count(distinct player_uid) from market_value_history where player_uid is not null"
    ).fetchone()[0]
    ea_ingame_players = con.execute(
        "select count(*) from ea_fc26_attributes where player_uid is not null and value_eur_ingame is not null"
    ).fetchone()[0]
    ml_predicted_players = con.execute(
        "select count(*) from ea_fc26_attributes where player_uid is not null and predicted_value_eur is not null"
    ).fetchone()[0]

    return {
        "transfermarkt_market_value": {"players_with_data": tm_players, "total_players": total_players, "pct": _pct(tm_players, total_players)},
        "ea_ingame_value": {"players_with_data": ea_ingame_players, "total_players": total_players, "pct": _pct(ea_ingame_players, total_players)},
        "ml_predicted_value": {"players_with_data": ml_predicted_players, "total_players": total_players, "pct": _pct(ml_predicted_players, total_players)},
    }


def _missing_data_summary(matching_stats, field_completeness, injury_status, market_value_coverage) -> list[str]:
    lines = []

    if matching_stats["total"] > 0:
        lines.append(
            f"identity matching: {matching_stats['UNMATCHED']}/{matching_stats['total']} "
            f"({matching_stats['unmatched_pct']}%) EA players unmatched to transfermarkt_dataset"
        )
    if matching_stats.get("AMBIGUOUS", 0) > 0:
        lines.append(f"identity matching: {matching_stats['AMBIGUOUS']} case(s) AMBIGUOUS, pending human review")

    for source, fields_ in field_completeness.items():
        for fname, stats in fields_.items():
            if stats["total"] > 0 and stats["pct"] < 100.0:
                lines.append(f"{source}.{fname}: {stats['present']}/{stats['total']} ({stats['pct']}%) — some players missing this field")

    if injury_status["total_players"] > 0:
        n = injury_status["NO_SOURCE_AVAILABLE"]
        lines.append(
            f"injury: {n}/{injury_status['total_players']} players NO_SOURCE_AVAILABLE — "
            "no injury source approved yet (see Phase 0/1 audit); this is NOT the same as 'confirmed healthy'"
        )
    if injury_status["players_missing_status_row"] > 0:
        lines.append(
            f"DATA GAP: {injury_status['players_missing_status_row']} player(s) have NO injury_data_status row at all "
            "— mark_no_injury_source_available() should cover every player; investigate."
        )

    mv = market_value_coverage["ml_predicted_value"]
    if mv["total_players"] > 0 and mv["players_with_data"] == 0:
        lines.append(
            f"ml_predicted_value: 0/{mv['total_players']} players ({mv['pct']}%) — "
            "ML pipeline not yet integrated (Phase 3), no predictions exist"
        )

    return lines


def build_coverage_report(con: duckdb.DuckDBPyConnection) -> CoverageReport:
    matching_stats = _matching_stats(con)
    source_coverage = _source_coverage(con)
    field_completeness = _field_completeness(con)
    lineage_coverage = _lineage_coverage(con)
    injury_status = _injury_status(con)
    market_value_coverage = _market_value_coverage(con)
    missing_data_summary = _missing_data_summary(matching_stats, field_completeness, injury_status, market_value_coverage)

    return CoverageReport(
        source_coverage=source_coverage,
        matching_stats=matching_stats,
        field_completeness=field_completeness,
        lineage_coverage=lineage_coverage,
        injury_status=injury_status,
        market_value_coverage=market_value_coverage,
        missing_data_summary=missing_data_summary,
    )
