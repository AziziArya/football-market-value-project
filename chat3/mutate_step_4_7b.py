import subprocess, sys, pathlib, json
ROOT = pathlib.Path("/tmp/mut7")
SCH, SUM, SVC, INIT, OA = "api/schemas/player.py", "api/services/player_summary.py", "api/services/player_service.py", "api/__init__.py", "api_contract/openapi.json"
MV, PID = "api/schemas/market_value.py", "api/domain/player_id.py"
IDENT_OLD = "    entity_kind: EntityKind\n    source_ids: SourceIds"
IDENT_NEW = "    entity_kind: EntityKind\n    canonical_player_uid: str | None = None\n    source_ids: SourceIds"
SUM_OLD = "        entity_kind=row.entity_kind,\n"
SUM_NEW = "        entity_kind=row.entity_kind, canonical_player_uid=uid,\n"
def contract_edit(fn):                       # mutate the contract JSON programmatically
    def apply(root):
        f = root / OA; d = json.loads(f.read_text(encoding="utf-8")); fn(d); f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return apply
def add_prop(d): d["components"]["schemas"]["Identity"]["properties"]["canonical_player_uid"] = {"type": ["string", "null"]}
def add_example_uid(d): d["components"]["examples"]["lineage_vini"]["value"]["identity"]["canonical_player_uid"] = "bdfaa72e-a493-40b4-a15e-0117251dab30"
def old_info_version(d): d["info"]["version"] = "0.4.0-draft"
def one_old_example_version(d):
    for ex in d["components"]["examples"].values():
        if "meta" in ex["value"]: ex["value"]["meta"]["contract_version"] = "0.4.0-draft"; break
def rename_prop(d):
    p = d["components"]["schemas"]["Identity"]["properties"]; p["internal_uid"] = {"type": "string"}
def drop_entity_kind_required(d): d["components"]["schemas"]["Identity"]["required"].remove("links")
M = [
 ("M01 field back in schema only (serialized as null)", [(SCH, IDENT_OLD, IDENT_NEW)]),
 ("M02 full re-exposure: schema + summary passes the uid", [(SCH, IDENT_OLD, IDENT_NEW), (SUM, SUM_OLD, SUM_NEW)]),
 ("M03 uid exposed under another name (uid)", [(SCH, IDENT_OLD, "    entity_kind: EntityKind\n    uid: str | None = None\n    source_ids: SourceIds"), (SUM, SUM_OLD, "        entity_kind=row.entity_kind, uid=uid,\n")]),
 ("M04 uid leaked in market-value response (field ref)", [(MV, "    latest: MarketValuePoint | None\n", "    latest: MarketValuePoint | None\n    ref: str | None = None\n"),
        (SVC, "            player_id=f\"ea:{ea_id}\", availability=availability, points=points,", "            ref=row.canonical_player_uid, player_id=f\"ea:{ea_id}\", availability=availability, points=points,")]),
 ("M05 uid leaked WITHOUT dashes (hex) in market-value", [(MV, "    latest: MarketValuePoint | None\n", "    latest: MarketValuePoint | None\n    ref: str | None = None\n"),
        (SVC, "            player_id=f\"ea:{ea_id}\", availability=availability, points=points,", "            ref=(row.canonical_player_uid or '').replace('-', '') or None, player_id=f\"ea:{ea_id}\", availability=availability, points=points,")]),
 ("M06 contract: property restored in Identity", [(contract_edit(add_prop),)]),
 ("M07 contract: sandbox uid left in an example", [(contract_edit(add_example_uid),)]),
 ("M08 contract: info.version not bumped", [(contract_edit(old_info_version),)]),
 ("M09 contract: one example keeps meta 0.4.0-draft", [(contract_edit(one_old_example_version),)]),
 ("M10 code: CONTRACT_VERSION not bumped", [(INIT, 'CONTRACT_VERSION = "0.5.0-draft"', 'CONTRACT_VERSION = "0.4.0-draft"')]),
 ("M11 contract: internal_uid property added", [(contract_edit(rename_prop),)]),
 ("M12 contract: Identity.required weakened", [(contract_edit(drop_entity_kind_required),)]),
 ("M13 OVER-REMOVAL: internal uid lost in summary (uid=None)", [(SUM, "    uid = row.canonical_player_uid\n", "    uid = None\n")]),
 ("M14 OVER-REMOVAL: entity_kind always EA_ONLY", [(SUM, "        entity_kind=row.entity_kind,\n", "        entity_kind=EntityKind.EA_ONLY,\n")]),
 ("M15 p:<uuid>/p: ids accepted", [(PID, 'return int(raw[3:]) if _RE.fullmatch(raw) else None', 'return int(raw[3:]) if (_RE.fullmatch(raw) or raw.startswith("p:")) and raw[3:].isdigit() else (1 if raw.startswith("p:") else None)')]),
 ("M16 400 echoes the rejected id in `instance` (could echo a uid)", [("api/errors.py", '"must match ea:<number> (no leading zeros, at most 9 digits)"}],\n                    redact_instance=True)', '"must match ea:<number> (no leading zeros, at most 9 digits)"}],\n                    redact_instance=False)')]),
]
only = sys.argv[1:]; bad = 0
TESTS = ["tests/test_api/test_no_public_uid.py", "tests/test_api/test_conformance.py", "tests/test_api/test_domain.py", "tests/test_contract", "tests/test_api/test_player_endpoint.py", "tests/test_api/test_search_endpoint.py"]
for name, edits in M:
    if only and name.split()[0] not in only: continue
    backups = {}
    ok = True
    try:
        for e in edits:
            if len(e) == 1:                                   # programmatic contract edit
                f = ROOT / OA; backups.setdefault(OA, f.read_text(encoding="utf-8")); e[0](ROOT)
            else:
                rel, old, new = e; f = ROOT / rel; src = f.read_text(encoding="utf-8"); backups.setdefault(rel, src)
                if src.count(old) != 1: print(f"!! {name}: pattern {rel} found {src.count(old)}x (mutation invalid)"); ok = False; break
                f.write_text(src.replace(old, new), encoding="utf-8")
        if not ok: bad += 1; continue
        r = subprocess.run([sys.executable, "-m", "pytest", *TESTS, "-q", "-x", "-p", "no:cacheprovider"], cwd=ROOT, capture_output=True, text=True)
    finally:
        for rel, src in backups.items(): (ROOT / rel).write_text(src, encoding="utf-8")
    caught = r.returncode != 0
    last = [l for l in r.stdout.splitlines() if l.startswith("FAILED")][:1]
    print(("CAUGHT     " if caught else "NOT CAUGHT ") + name + "  " + (last[0].split("::")[-1][:62] if last else ""))
    bad += (not caught)
print("not-caught / invalid:", bad)
