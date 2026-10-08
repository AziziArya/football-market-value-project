import subprocess, sys, pathlib, json
ROOT = pathlib.Path("/tmp/mut9")
APP, SCHC, SRCH, PLR, HLT, FRS, ENUM, CFG, DBF, SSVC, PSVC, PSUM, ERR = ("api/app.py", "api/schemas/common.py", "api/routers/search.py", "api/routers/player.py", "api/routers/health.py",
    "api/routers/freshness.py", "api/domain/enums.py", "api/config.py", "api/repositories/database.py", "api/services/search_service.py", "api/services/player_service.py",
    "api/services/player_summary.py", "api/errors.py")
OA = "api_contract/openapi.json"
def cedit(fn):
    def apply(root):
        f = root / OA; d = json.loads(f.read_text(encoding="utf-8")); fn(d); f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return apply
def stale_description(d): d["info"]["description"] = "CONTRACT - Implemented so far: getHealth, getDataFreshness (Step 4.1) searchPlayers (Step 4.2) and getPlayer (Step 4.3); every other operation is still contract-only. " + d["info"]["description"].split("Step 3.1", 1)[1]
def stale_server(d): d["servers"][0]["description"] = "planned base path"
def shrink_codes(d): del d["paths"]["/players/{player_id}/lineage"]["get"]["responses"]["404"]
def add_422_to_contract(d): d["paths"]["/health"]["get"]["responses"]["422"] = {"description": "x"}
def add_param(d): d["paths"]["/players/search"]["get"]["parameters"].append({"name": "extra", "in": "query", "schema": {"type": "string"}})
def drop_schema_prop(d): del d["components"]["schemas"]["FieldValue"]["properties"]["is_current"]
M = [
 ("M01 422 left in the generated spec", [(APP, '                    operation.get("responses", {}).pop("422", None)\n', '')]),
 ("M02 validation schemas left in the generated spec", [(APP, '            for name in ("HTTPValidationError", "ValidationError"):\n                schema.get("components", {}).get("schemas", {}).pop(name, None)\n', '')]),
 ("M03 error media type left as application/json", [(APP, '                            response["content"] = {PROBLEM_MEDIA_TYPE: response["content"]["application/json"]}', '                            pass')]),
 ("M04 Problem.code is a free string again", [(SCHC, "    code: ProblemCode ", "    code: str ")]),
 ("M05 contract: stale 'implemented so far' text restored", [(cedit(stale_description),)]),
 ("M06 contract: 'planned base path' restored", [(cedit(stale_server),)]),
 ("M07 contract: lineage loses its 404", [(cedit(shrink_codes),)]),
 ("M08 contract: a 422 is added to health", [(cedit(add_422_to_contract),)]),
 ("M09 contract: an extra search parameter", [(cedit(add_param),)]),
 ("M10 contract: FieldValue.is_current removed", [(cedit(drop_schema_prop),)]),
 ("M11 search limit max 50 -> 100", [(SRCH, "limit: int = Query(20, ge=1, le=50)", "limit: int = Query(20, ge=1, le=100)")]),
 ("M12 search q min_length 2 -> 1", [(SRCH, "q: str = Query(..., min_length=2, max_length=80)", "q: str = Query(..., min_length=1, max_length=80)")]),
 ("M13 search sort gets a new value", [(ENUM, "class SearchSort(str, Enum):\n", "class SearchSort(str, Enum):\n    bogus = \"bogus\"\n")]),
 ("M14 search min_overall becomes required", [(SRCH, "min_overall: int | None = Query(None, ge=0, le=99)", "min_overall: int = Query(..., ge=0, le=99)")]),
 ("M15 health route loses its 503 declaration", [(HLT, 'responses={503: {"model": Problem}, 500: {"model": Problem}})', 'responses={500: {"model": Problem}})')]),
 ("M16 lineage route loses its 404 declaration", [(PLR, 'summary="Per-field, per-source values and link states",\n            responses={400: {"model": Problem}, 404: {"model": Problem}, 503: {"model": Problem}, 500: {"model": Problem}})', 'summary="Per-field, per-source values and link states",\n            responses={400: {"model": Problem}, 503: {"model": Problem}, 500: {"model": Problem}})')]),
 ("M17 400 echoes the rejected id (instance not redacted)", [(ERR, '                    redact_instance=True)\n\n\ndef', '                    redact_instance=False)\n\n\ndef')] ),
 ("M18 500 handler leaks the exception text", [(APP, 'return _problem(request, ProblemCode.INTERNAL_ERROR, 500, "Internal error", "An unexpected error occurred.")', 'return _problem(request, ProblemCode.INTERNAL_ERROR, 500, "Internal error", str(exc))')]),
 ("M19 a debug header on every response", [(APP, '        return await call_next(request)\n', '        response = await call_next(request)\n        response.headers["X-Powered-By"] = "uvicorn"\n        return response\n')]),
 ("M20 docs enabled by default", [(CFG, "    enable_docs: bool = False ", "    enable_docs: bool = True ")]),
 ("M21 validation errors echo the rejected value", [(APP, '"reason": _reason(e)}', '"reason": str(e.get("input"))}')]),
 ("M22 a write method is accepted on health", [(HLT, '@router.get(', '@router.post("/health")\ndef _health_post():\n    return {}\n\n\n@router.get(')]),
 ("M23 search runs one extra query per hit (N+1)", [(SSVC, "        for hit, row in zip(result.hits, rows):\n", "        for hit, row in zip(result.hits, rows):\n            with self._db.cursor() as _c:\n                _c.execute('select 1')\n")]),
 ("M24 getPlayer runs a second round of queries", [(PSVC, "                    summary = player_summary.build_summary(row, links, details)\n", "                    cur.execute('select 1'); cur.execute('select 2'); cur.execute('select 3'); cur.execute('select 4'); cur.execute('select 5')\n                    summary = player_summary.build_summary(row, links, details)\n")]),
 ("M25 database opened read-write", [(DBF, "duckdb.connect(str(self._path), read_only=True)", "duckdb.connect(str(self._path), read_only=False)")]),
 ("M26 profile club taken from the nationality (cross-endpoint drift)", [(PSUM, "club=row.club", "club=row.nationality")]),
 ("M27 search echoes the raw q instead of the normalised one", [(SSVC, "query_normalized=q_norm", "query_normalized=q")]),
]
only = sys.argv[1:]; bad = 0
TESTS = ["tests/test_api/test_integration_consistency.py"]
for name, edits in M:
    if only and name.split()[0] not in only: continue
    backups = {}; ok = True
    try:
        for e in edits:
            if len(e) == 1:
                backups.setdefault(OA, (ROOT / OA).read_text(encoding="utf-8")); e[0](ROOT)
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
    print(("CAUGHT     " if caught else "NOT CAUGHT ") + name + "  " + (last[0].split("::")[-1][:60] if last else ""))
    bad += (not caught)
print("not-caught / invalid:", bad)
