import subprocess, sys, pathlib
ROOT = pathlib.Path("/tmp/mut6")
SVC, SUM, SCH, RTR = "api/services/player_service.py", "api/services/player_summary.py", "api/schemas/ea_attributes.py", "api/routers/player.py"
M = [
 ("M01 overall_rating swapped with potential",  SVC, "overall_rating=row.overall_rating, potential=row.potential,", "overall_rating=row.potential, potential=row.overall_rating,"),
 ("M02 overall_rating off by one",              SVC, "overall_rating=row.overall_rating,", "overall_rating=row.overall_rating + 1,"),
 ("M03 preferred_foot dropped",                 SVC, "preferred_foot=row.preferred_foot,", "preferred_foot=None,"),
 ("M04 preferred_foot defaulted to Right",      SVC, "preferred_foot=row.preferred_foot,", "preferred_foot=row.preferred_foot or 'Right',"),
 ("M05 EA value taken from market domain",      SUM, "amount_eur=row.value_eur_ingame, provenance=ea_provenance(row))", "amount_eur=(row.value_eur_ingame or 0) * 2, provenance=ea_provenance(row))"),
 ("M06 NULL EA value reported as 0",            SUM, "amount_eur=row.value_eur_ingame, provenance=ea_provenance(row))", "amount_eur=row.value_eur_ingame or 0, provenance=ea_provenance(row))"),
 ("M07 NULL EA value still AVAILABLE",          SUM, "Availability.AVAILABLE if row.value_eur_ingame is not None else Availability.NO_SOURCE_DATA", "Availability.AVAILABLE"),
 ("M08 EA value availability always NO_SOURCE", SUM, "Availability.AVAILABLE if row.value_eur_ingame is not None else Availability.NO_SOURCE_DATA", "Availability.NO_SOURCE_DATA"),
 ("M09 provenance data_origin hard-coded SAMPLE", SUM, 'data_origin=origin_for("ea_fc26"))', 'data_origin=DataOrigin.SAMPLE_FIXTURE)'),
 ("M10 provenance source wrong",                SUM, 'SourceProvenance(source="ea_fc26", dataset_version=row.dataset_version', 'SourceProvenance(source="transfermarkt_dataset", dataset_version=row.dataset_version'),
 ("M11 provenance dataset_version dropped",     SUM, 'SourceProvenance(source="ea_fc26", dataset_version=row.dataset_version', 'SourceProvenance(source="ea_fc26", dataset_version=None'),
 ("M12 provenance fetched_at = now (fabricated)", SUM, "fetched_at=_seconds(row.fetched_at),\n                            data_origin", "fetched_at=__import__('datetime').datetime.now(),\n                            data_origin"),
 ("M13 detailed attributes claimed AVAILABLE",  SVC, "EaDetailedAttributes(availability=Availability.NOT_YET_INTEGRATED, attributes=None)", "EaDetailedAttributes(availability=Availability.AVAILABLE, attributes=None)"),
 ("M14 detailed attributes invented",           SVC, "EaDetailedAttributes(availability=Availability.NOT_YET_INTEGRATED, attributes=None)", "EaDetailedAttributes(availability=Availability.NOT_YET_INTEGRATED, attributes={'pace': row.overall_rating})"),
 ("M15 detailed attributes NO_SOURCE_DATA",     SVC, "EaDetailedAttributes(availability=Availability.NOT_YET_INTEGRATED, attributes=None)", "EaDetailedAttributes(availability=Availability.NO_SOURCE_DATA, attributes=None)"),
 ("M16 contains_sample_data always True",       SVC, "contains_sample_data=provenance.data_origin is DataOrigin.SAMPLE_FIXTURE", "contains_sample_data=True"),
 ("M17 unknown id -> 200",                      SVC, "            raise player_not_found(raw_player_id)\n\n        provenance", "            return None\n\n        provenance"),
 ("M18 malformed id -> 404",                    SVC, "        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        provenance", "        if ea_id is None:\n            raise player_not_found(raw_player_id)\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        provenance"),
 ("M19 no health gate (no 503)",                SVC, "        if not state.ok:\n            raise data_unavailable()\n        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        provenance", "        ea_id = parse_player_id(raw_player_id)\n        if ea_id is None:\n            raise invalid_player_id()\n        row = state.index.get(ea_id)\n        if row is None:\n            raise player_not_found(raw_player_id)\n\n        provenance"),
 ("M20 canonical players hit the database",     SVC, "        provenance = player_summary.ea_provenance(row)\n        return EaAttributesResponse(", "        if row.entity_kind is EntityKind.CANONICAL:\n            with self._db.cursor() as cur:\n                cur.execute('select 1')\n        provenance = player_summary.ea_provenance(row)\n        return EaAttributesResponse("),
 ("M21 EA-only players hit the database",       SVC, "        provenance = player_summary.ea_provenance(row)\n        return EaAttributesResponse(", "        with self._db.cursor() as cur:\n            cur.execute('select 1')\n        provenance = player_summary.ea_provenance(row)\n        return EaAttributesResponse("),
 ("M22 market value mixed in (field added)",    SCH, "    provenance: SourceProvenance\n", "    provenance: SourceProvenance\n    market_value_eur: int | None = None\n"),
 ("M23 model estimate mixed in (field added)",  SCH, "    provenance: SourceProvenance\n", "    provenance: SourceProvenance\n    predicted_value_eur: int | None = None\n"),
 ("M24 identity leaked (field added)",          SCH, "    provenance: SourceProvenance\n", "    provenance: SourceProvenance\n    canonical_player_uid: str | None = None\n"),
 ("M25 wrong player_id echoed",                 SVC, 'player_id=f"ea:{ea_id}", overall_rating', 'player_id="ea:1", overall_rating'),
 ("M26 ea_ingame_value provenance differs from top-level", SVC, "ea_ingame_value=player_summary.ea_ingame_value(row),", "ea_ingame_value=player_summary.ea_ingame_value(row).model_copy(update={'provenance': None}),"),
 ("M27 getPlayer EA value no longer shared",    SUM, "    ea_value = ea_ingame_value(row)\n", "    ea_value = EaIngameValue(availability=Availability.AVAILABLE, amount_eur=(row.value_eur_ingame or 0) + 1, provenance=ea_provenance(row))\n"),
 ("M28 attributes typed as str map (OpenAPI drift)", SCH, "    attributes: dict[str, int] | None\n", "    attributes: dict[str, str] | None\n"),
 ("M29 route removed from router",              RTR, '@router.get("/players/{player_id}/ea-attributes"', '@router.get("/players/{player_id}/ea-attributes-x"'),
]
only = sys.argv[1:]; bad = 0
for name, rel, old, new in M:
    if only and name.split()[0] not in only: continue
    f = ROOT / rel; src = f.read_text()
    if src.count(old) != 1:
        print(f"!! {name}: pattern found {src.count(old)}x (mutation invalid)"); bad += 1; continue
    f.write_text(src.replace(old, new))
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_api/test_ea_attributes_endpoint.py", "tests/test_api/test_conformance.py", "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    finally:
        f.write_text(src)
    caught = r.returncode != 0
    last = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(("CAUGHT     " if caught else "NOT CAUGHT ") + name + "  " + (last[0].split("::")[-1][:70] if last else ""))
    bad += (not caught)
print("not-caught / invalid:", bad)
