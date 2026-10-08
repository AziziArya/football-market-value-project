import subprocess, sys, shutil, pathlib
ROOT = pathlib.Path("/tmp/mut")
SVC, DEF, SCH, RTR = "api/services/player_service.py", "api/readmodels/definitions.py", "api/schemas/market_value.py", "api/routers/player.py"
M = [
 ("M01 order by date DESC",            DEF, "order by valuation_date asc, dataset_version asc, imported_at asc, id asc", "order by valuation_date desc, dataset_version asc, imported_at asc, id asc"),
 ("M02 ignore dataset_version in order", DEF, "order by valuation_date asc, dataset_version asc, imported_at asc, id asc", "order by valuation_date asc, imported_at asc, id asc"),
 ("M03 dataset_version DESC",          DEF, "order by valuation_date asc, dataset_version asc, imported_at asc, id asc", "order by valuation_date asc, dataset_version desc, imported_at asc, id asc"),
 ("M04 no duplicate collapsing",       DEF, ") where rn = 1\norder by valuation_date asc", ") where rn >= 1\norder by valuation_date asc"),
 ("M05 collapse keeps OLDEST import",  DEF, "order by imported_at desc, id desc) as rn\n  from market_value_history where player_uid = ?", "order by imported_at asc, id asc) as rn\n  from market_value_history where player_uid = ?"),
 ("M06 cross-player leak (no uid filter)", DEF, "from market_value_history where player_uid = ?\n) where rn = 1\norder by valuation_date asc", "from market_value_history where ? is not null\n) where rn = 1\norder by valuation_date asc"),
 ("M07 EA-only -> NO_SOURCE_DATA",     SVC, "availability = Availability.NOT_MATCHED", "availability = Availability.NO_SOURCE_DATA"),
 ("M08 empty canonical -> NOT_MATCHED", SVC, "Availability.AVAILABLE if rows else Availability.NO_SOURCE_DATA", "Availability.AVAILABLE if rows else Availability.NOT_MATCHED"),
 ("M09 empty canonical -> AVAILABLE",  SVC, "Availability.AVAILABLE if rows else Availability.NO_SOURCE_DATA", "Availability.AVAILABLE"),
 ("M10 unknown id -> 200 not 404",     SVC, "            raise player_not_found(raw_player_id)\n\n        rows = []", "            return MarketValueResponse(player_id=raw_player_id, availability=Availability.NOT_MATCHED, points=[], latest=None, meta=ResponseMeta(contract_version=CONTRACT_VERSION, generated_at=datetime.now(timezone.utc).replace(microsecond=0), contains_sample_data=False))\n\n        rows = []"),
 ("M11 latest = first point",          SVC, "latest=points[-1] if points else None", "latest=points[0] if points else None"),
 ("M12 latest = None always",          SVC, "latest=points[-1] if points else None", "latest=None"),
 ("M13 data_origin hard-coded REAL",   SVC, "data_origin=origin_for(r.source)", "data_origin=DataOrigin.REAL_FULL"),
 ("M14 contains_sample_data False",    SVC, "contains_sample_data=any(p.data_origin is DataOrigin.SAMPLE_FIXTURE for p in points)", "contains_sample_data=False"),
 ("M15 contains_sample_data True",     SVC, "contains_sample_data=any(p.data_origin is DataOrigin.SAMPLE_FIXTURE for p in points)", "contains_sample_data=True"),
 ("M16 invented trend field",          SCH, "    latest: MarketValuePoint | None\n", "    latest: MarketValuePoint | None\n    trend: str | None = None\n"),
 ("M17 EA value mixed in",             SCH, "    latest: MarketValuePoint | None\n", "    latest: MarketValuePoint | None\n    ea_ingame_value: int | None = None\n"),
 ("M18 NULL source fabricated",        SVC, "source=r.source,", "source=r.source or 'transfermarkt_dataset',"),
 ("M19 NULL source kept as null",      SCH, "        if data.get(\"source\") is None:\n            data.pop(\"source\", None)\n", "        pass\n"),
 ("M20 skip health gate (no 503)",     SVC, "        if not state.ok:\n            raise data_unavailable()\n        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        rows = []", "        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        rows = []"),
 ("M21 DatabaseUnavailable not mapped", SVC, "            except DatabaseUnavailable:\n                raise data_unavailable()\n            availability", "            except ZeroDivisionError:\n                raise data_unavailable()\n            availability"),
 ("M22 EA-only hits the database",     SVC, "        if row.entity_kind is EntityKind.CANONICAL:\n            try:\n                with self._db.cursor() as cur:\n                    rows = player_detail_repository.market_value_history(cur, row.canonical_player_uid)", "        if True:\n            try:\n                with self._db.cursor() as cur:\n                    rows = player_detail_repository.market_value_history(cur, row.canonical_player_uid or 'none')"),
 ("M23 malformed id -> 404",           SVC, "        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        rows = []", "        if ea_id is None:\n            raise player_not_found(raw_player_id)\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        rows = []"),
 ("M24 amount as 0 when missing / amount scaled", SVC, "amount_eur=r.value_eur", "amount_eur=r.value_eur // 1000"),
 ("M26 generated schema over-claims source as required", SCH, "    model_config = ConfigDict(json_schema_extra=_source_is_optional)\n", "    pass\n"),
 ("M25 wrong player_id echoed",        SVC, 'player_id=f"ea:{ea_id}", availability=availability', 'player_id="ea:1", availability=availability'),
]
only = sys.argv[1:] 
bad = 0
for name, rel, old, new in M:
    if only and name.split()[0] not in only: continue
    f = ROOT / rel; src = f.read_text()
    if src.count(old) != 1:
        print(f"!! {name}: pattern found {src.count(old)}x (mutation invalid)"); bad += 1; continue
    f.write_text(src.replace(old, new))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_api/test_market_value_endpoint.py", "tests/test_api/test_conformance.py", "-q", "-x", "-p", "no:cacheprovider"],
                           cwd=ROOT, capture_output=True, text=True)
    finally:
        f.write_text(src)
    caught = r.returncode != 0
    last = [l for l in r.stdout.splitlines() if l.startswith("FAILED") or "failed" in l][:1]
    print(("CAUGHT    " if caught else "NOT CAUGHT") + f" {name}   {last[0][:110] if last else ''}")
    bad += (not caught)
print("not-caught:", bad)
