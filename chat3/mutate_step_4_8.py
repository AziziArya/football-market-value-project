import subprocess, sys, pathlib
ROOT = pathlib.Path("/tmp/mut8")
DEF, REPO, SVC, SCH, RTR, SUM = "api/readmodels/definitions.py", "api/repositories/player_detail_repository.py", "api/services/player_service.py", "api/schemas/lineage.py", "api/routers/player.py", "api/services/player_summary.py"
R = "def lineage("         # service edits are restricted to the lineage method (it is the last one of the class)
SQL_ORDER = "order by field_name asc, source asc, source_record_id asc"
SQL_WHERE = "where player_uid = ? or (source = 'ea_fc26' and source_record_id = ?)"
FILTER = '            if v.source != "ea_fc26" and not (v.source in shown_link_of and links[shown_link_of[v.source]].status in LINKED):\n'
M = [
 ("M01 unlinked branch not restricted to ea_fc26 (candidate rows leak)", DEF, SQL_WHERE, "where player_uid = ? or source_record_id = ?", None),
 ("M02 canonical branch missing (only the EA branch)", DEF, SQL_WHERE, "where (? is null or true) and (source = 'ea_fc26' and source_record_id = ?)", None),
 ("M03 no uid branch for linked rows of other sources and wrong param use", DEF, SQL_WHERE, "where source = 'ea_fc26' and source_record_id = ? and ? is not null", None),
 ("M04 no latest-per-key collapse (history listed)", DEF, ") where rn = 1\n" + SQL_ORDER, ") where rn >= 1\n" + SQL_ORDER, None),
 ("M05 collapse ignores the source (sources merged)", DEF, "partition by source, source_record_id, field_name order by fetched_at desc, id desc) as rn\n  from player_field_values\n  where player_uid", "partition by source_record_id, field_name order by fetched_at desc, id desc) as rn\n  from player_field_values\n  where player_uid", None),
 ("M06 oldest row wins (fetched_at asc)", DEF, "order by fetched_at desc, id desc) as rn\n  from player_field_values\n  where player_uid", "order by fetched_at asc, id desc) as rn\n  from player_field_values\n  where player_uid", None),
 ("M07 no id tie-break", DEF, "order by fetched_at desc, id desc) as rn\n  from player_field_values\n  where player_uid", "order by fetched_at desc, id asc) as rn\n  from player_field_values\n  where player_uid", None),
 ("M08 is_current used as the selection signal", DEF, "where player_uid = ? or (source = 'ea_fc26' and source_record_id = ?)\n)", "where is_current and (player_uid = ? or (source = 'ea_fc26' and source_record_id = ?))\n)", None),
 ("M09 sources in reverse order", DEF, SQL_ORDER, "order by field_name asc, source desc, source_record_id asc", None),
 ("M10 fields in reverse order", DEF, SQL_ORDER, "order by field_name desc, source asc, source_record_id asc", None),
 ("M11 link filter removed (non-linked sources shown)", SVC, FILTER, "            if False:\n", R),
 ("M12 unknown sources allowed through the filter", SVC, FILTER, '            if v.source in shown_link_of and links[shown_link_of[v.source]].status not in LINKED:\n', R),
 ("M13 filter keeps only linked sources, drops ea_fc26 for AMBIGUOUS players", SVC, FILTER, '            if v.source in shown_link_of and links[shown_link_of[v.source]].status not in LINKED or v.source == "ea_fc26" and links["transfermarkt"].status not in LINKED:\n', R),
 ("M14 data_origin hard-coded REAL_FULL", SVC, "data_origin=origin_for(v.source)", "data_origin=DataOrigin.REAL_FULL", R),
 ("M15 data_origin parsed from dataset_version", SVC, "data_origin=origin_for(v.source)", "data_origin=(DataOrigin.SAMPLE_FIXTURE if (v.dataset_version or '').startswith('sample') else DataOrigin.REAL_FULL)", R),
 ("M16 contains_sample_data always False", SVC, "contains_sample_data=sample", "contains_sample_data=False", R),
 ("M17 contains_sample_data always True", SVC, "contains_sample_data=sample", "contains_sample_data=True", R),
 ("M18 contains_sample_data ignores the identity (shown ids)", SVC, " or player_summary.identity_shows_sample_data(identity)", "", R),
 ("M19 transfermarkt id always shown (AMBIGUOUS leak via identity)", SVC, "        identity = player_summary.build_identity(row, links)\n", "        identity = player_summary.build_identity(row, links)\n        identity.source_ids.transfermarkt_id = row.transfermarkt_id\n", R),
 ("M20 sources merged: only the first value per field", SVC, "        fields = [LineageField(field_name=name, values=values) for name, values in by_field.items()]", "        fields = [LineageField(field_name=name, values=values[:1]) for name, values in by_field.items()]", R),
 ("M21 fetched_at replaced by the current time", SVC, "fetched_at=player_summary._seconds(v.fetched_at)", "fetched_at=datetime.now(timezone.utc).replace(microsecond=0)", R),
 ("M22 fetched_at dropped", SVC, "fetched_at=player_summary._seconds(v.fetched_at)", "fetched_at=None", R),
 ("M23 is_current forced true", SVC, "is_current=v.is_current", "is_current=True", R),
 ("M24 dataset_version dropped", SVC, "dataset_version=v.dataset_version", "dataset_version=None", R),
 ("M25 field_value stripped/normalised", SVC, "field_value=v.field_value,", "field_value=v.field_value.strip(),", R),
 ("M26 unknown id -> 200", SVC, "        if row is None:\n            raise player_not_found(raw_player_id)\n\n        links", "        if row is None:\n            return None\n\n        links", R),
 ("M27 malformed id -> 404", SVC, "        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        links", "        if ea_id is None:\n            raise player_not_found(raw_player_id)\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        links", R),
 ("M28 no health gate (no 503)", SVC, "        if not state.ok:\n            raise data_unavailable()\n        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        links", "        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        links", R),
 ("M29 DatabaseUnavailable not mapped (500 instead of 503)", SVC, "        except DatabaseUnavailable:\n            raise data_unavailable()\n\n        by_field", "        except ZeroDivisionError:\n            raise data_unavailable()\n\n        by_field", R),
 ("M30 EA-only players served without the database", SVC, "                stored = player_detail_repository.lineage_field_values(cur, row.canonical_player_uid, ea_id)", "                stored = player_detail_repository.lineage_field_values(cur, row.canonical_player_uid, ea_id) if row.canonical_player_uid else []", R),
 ("M31 a second query is made", SVC, "                stored = player_detail_repository.lineage_field_values(cur, row.canonical_player_uid, ea_id)", "                cur.execute('select 1')\n                stored = player_detail_repository.lineage_field_values(cur, row.canonical_player_uid, ea_id)", R),
 ("M32 wrong EA id parameter (off by one)", REPO, "[player_uid, str(ea_fc26_id)]", "[player_uid, str(ea_fc26_id + 1)]", None),
 ("M33 derived field added to a value (confidence)", SCH, "    data_origin: DataOrigin\n", "    data_origin: DataOrigin\n    confidence: float | None = None\n", None),
 ("M34 is_current removed from the schema", SCH, "    is_current: bool | None\n", "", None),
 ("M35 internal uid leaked in meta via identity (field added to response)", SCH, "    meta: ResponseMeta\n", "    meta: ResponseMeta\n    ref: str | None = None\n", None),
 ("M36 route removed", RTR, '@router.get("/players/{player_id}/lineage"', '@router.get("/players/{player_id}/lineage-x"', None),
 ("M37 fields sorted by number of values", SVC, "        fields = [LineageField(field_name=name, values=values) for name, values in by_field.items()]", "        fields = sorted([LineageField(field_name=name, values=values) for name, values in by_field.items()], key=lambda f: -len(f.values))", R),
 ("M38 lineage identity differs from getPlayer (links swapped)", SVC, "        identity = player_summary.build_identity(row, links)\n", "        identity = player_summary.build_identity(row, {'transfermarkt': links['wikidata'], 'wikidata': links['transfermarkt']})\n", R),
]
only = sys.argv[1:]; bad = 0
TESTS = ["tests/test_api/test_lineage_endpoint.py", "tests/test_api/test_conformance.py"]
for name, rel, old, new, region in M:
    if only and name.split()[0] not in only: continue
    f = ROOT / rel; src = f.read_text(encoding="utf-8")
    if region:
        cut = src.index(region); head, tail = src[:cut], src[cut:]
    else:
        head, tail = "", src
    if tail.count(old) != 1:
        print(f"!! {name}: pattern found {tail.count(old)}x (mutation invalid)"); bad += 1; continue
    f.write_text(head + tail.replace(old, new), encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    finally:
        f.write_text(src, encoding="utf-8")
    caught = r.returncode != 0
    last = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(("CAUGHT     " if caught else "NOT CAUGHT ") + name + "  " + (last[0].split("::")[-1][:58] if last else ""))
    bad += (not caught)
print("not-caught / invalid:", bad)
