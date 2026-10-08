import subprocess, sys, pathlib
ROOT = pathlib.Path("/tmp/mut5")
SVC, DEF, SCH, REPO, RTR = "api/services/player_service.py", "api/readmodels/definitions.py", "api/schemas/injuries.py", "api/repositories/player_detail_repository.py", "api/routers/player.py"
HASREC = "                        if block.status is InjuryStatus.HAS_RECORDS:\n"
M = [
 ("M01 EA-only -> NO_SOURCE_AVAILABLE",     SVC, "block, records = InjuryBlock(status=InjuryStatus.NOT_EVALUATED), []", "block, records = InjuryBlock(status=InjuryStatus.NO_SOURCE_AVAILABLE), []"),
 ("M02 EA-only -> CONFIRMED_NO_INJURIES",   SVC, "block, records = InjuryBlock(status=InjuryStatus.NOT_EVALUATED), []", "block, records = InjuryBlock(status=InjuryStatus.CONFIRMED_NO_INJURIES), []"),
 ("M03 canonical forced to CONFIRMED (healthy)", SVC, "block = InjuryBlock(status=InjuryStatus(stored.status), checked_at", "block = InjuryBlock(status=InjuryStatus.CONFIRMED_NO_INJURIES, checked_at"),
 ("M04 canonical forced to NOT_EVALUATED",  SVC, "block = InjuryBlock(status=InjuryStatus(stored.status), checked_at", "block = InjuryBlock(status=InjuryStatus.NOT_EVALUATED, checked_at"),
 ("M05 checked_at dropped",                 SVC, "checked_at=player_summary._seconds(stored.checked_at))\n                        if block", "checked_at=None)\n                        if block"),
 ("M06 checked_at = now (fabricated)",      SVC, "checked_at=player_summary._seconds(stored.checked_at))\n                        if block", "checked_at=datetime.now(timezone.utc).replace(microsecond=0))\n                        if block"),
 ("M07 unknown id -> 200",                  SVC, "            raise player_not_found(raw_player_id)\n\n        block, records", "            return InjuriesResponse(player_id=raw_player_id, injury=InjuryBlock(status=InjuryStatus.NOT_EVALUATED), records=[], meta=ResponseMeta(contract_version=CONTRACT_VERSION, generated_at=datetime.now(timezone.utc).replace(microsecond=0), contains_sample_data=False))\n\n        block, records"),
 ("M08 malformed id -> 404",                SVC, "        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        block, records", "        if ea_id is None:\n            raise player_not_found(raw_player_id)\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        block, records"),
 ("M09 no health gate (no 503)",            SVC, "        if not state.ok:\n            raise data_unavailable()\n        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        block, records", "        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        block, records"),
 ("M10 DatabaseUnavailable not mapped",     SVC, "            except DatabaseUnavailable:\n                raise data_unavailable()\n\n        return InjuriesResponse", "            except ZeroDivisionError:\n                raise data_unavailable()\n\n        return InjuriesResponse"),
 ("M11 EA-only hits the database",          SVC, "        if row.entity_kind is EntityKind.CANONICAL:\n            try:\n                with self._db.cursor() as cur:\n                    stored", "        if True:\n            try:\n                with self._db.cursor() as cur:\n                    stored"),
 ("M12 records read for every status",      SVC, HASREC, "                        if True:\n"),
 ("M13 records never read",                 SVC, HASREC, "                        if False:\n"),
 ("M14 records ignore uid filter (leak)",   DEF, "from injury_records where player_uid = ?", "from injury_records where ? is not null"),
 ("M15 order by start_date DESC",           DEF, "order by start_date asc nulls last, expected_return asc nulls last, id asc", "order by start_date desc nulls last, expected_return asc nulls last, id asc"),
 ("M16 NULLs first",                        DEF, "order by start_date asc nulls last, expected_return asc nulls last, id asc", "order by start_date asc nulls first, expected_return asc nulls last, id asc"),
 ("M17a ignore expected_return (start, id asc)", DEF, "order by start_date asc nulls last, expected_return asc nulls last, id asc", "order by start_date asc nulls last, id asc"),
 ("M17b id tie-break reversed",             DEF, "order by start_date asc nulls last, expected_return asc nulls last, id asc", "order by start_date asc nulls last, expected_return asc nulls last, id desc"),
 ("M17c no tie-break at all",               DEF, "order by start_date asc nulls last, expected_return asc nulls last, id asc", "order by start_date asc nulls last"),
 ("M18 days_out derived from dates",        REPO, "None if r[4] is None else int(r[4])", "(((r[3] or r[2]) - r[1]).days if r[1] and (r[3] or r[2]) else (None if r[4] is None else int(r[4])))"),
 ("M19 days_out defaults to 0 when NULL",   REPO, "None if r[4] is None else int(r[4])", "0 if r[4] is None else int(r[4])"),
 ("M20 source fabricated when NULL",        SVC, "actual_return=r.actual_return, days_out=r.days_out, source=r.source)", "actual_return=r.actual_return, days_out=r.days_out, source=r.source or 'transfermarkt_dataset')"),
 ("M21 contains_sample_data always True",   SVC, "contains_sample_data=any(origin_for(r.source) is DataOrigin.SAMPLE_FIXTURE for r in records)", "contains_sample_data=True"),
 ("M22 contains_sample_data always False",  SVC, "contains_sample_data=any(origin_for(r.source) is DataOrigin.SAMPLE_FIXTURE for r in records)", "contains_sample_data=False"),
 ("M23 invented burden field",              SCH, "    records: list[InjuryRecord]\n", "    records: list[InjuryRecord]\n    injury_burden_days: int | None = None\n"),
 ("M24 market-value impact field mixed in", SCH, "    records: list[InjuryRecord]\n", "    records: list[InjuryRecord]\n    market_value_impact_pct: float | None = None\n"),
 ("M25 record gets derived field",          SCH, "    source: str | None = None\n", "    source: str | None = None\n    severity: str | None = None\n"),
 ("M26 wrong player_id echoed",             SVC, 'player_id=f"ea:{ea_id}", injury=block', 'player_id="ea:1", injury=block'),
 ("M27 record field dropped (source)",      SVC, "actual_return=r.actual_return, days_out=r.days_out, source=r.source)", "actual_return=r.actual_return, days_out=r.days_out, source=None)"),
 ("M28 status string not validated -> HAS_RECORDS forced when rows exist", SVC, "block = InjuryBlock(status=InjuryStatus(stored.status), checked_at", "block = InjuryBlock(status=InjuryStatus.HAS_RECORDS, checked_at"),
]
only = sys.argv[1:]; bad = 0
for name, rel, old, new in M:
    if only and name.split()[0] not in only: continue
    f = ROOT / rel; src = f.read_text()
    if src.count(old) != 1:
        print(f"!! {name}: pattern found {src.count(old)}x (mutation invalid)"); bad += 1; continue
    f.write_text(src.replace(old, new))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_api/test_injuries_endpoint.py", "tests/test_api/test_conformance.py", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    finally:
        f.write_text(src)
    caught = r.returncode != 0
    last = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(("CAUGHT     " if caught else "NOT CAUGHT ") + name + "  " + (last[0].split("::")[-1][:75] if last else ""))
    bad += (not caught)
print("not-caught / invalid:", bad)
