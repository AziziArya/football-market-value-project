"""Architecture-fitness tests for the real api/ package (complements the generic scan in test_contract/)."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
API = ROOT / "api"
FORBIDDEN_TOP = {"ingestion", "matching", "scripts", "reporting", "db", "config", "pandas", "numpy",
                 "tensorflow", "keras", "catboost", "sklearn", "joblib", "requests", "urllib3"}
SQL_WRITE = re.compile(r"""["'\s](insert\s+into|update\s+\w+\s+set|delete\s+from|create\s+(table|view|index)|drop\s+|alter\s+table|attach\s)""", re.I)


def _files():
    return [f for f in API.rglob("*.py")]


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_package_exists_with_the_planned_layers():
    layers = {p.name for p in API.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert layers == {"domain", "readmodels", "repositories", "services", "schemas", "routers"}


def test_api_never_imports_pipeline_or_ml_or_pandas():
    for f in _files():
        for imp in _imports(f):
            assert imp.split(".")[0] not in FORBIDDEN_TOP, f"{f.relative_to(ROOT)} imports {imp}"


def test_duckdb_is_imported_only_by_repositories():
    users = {str(f.relative_to(API)) for f in _files() if any(i.split(".")[0] == "duckdb" for i in _imports(f))}
    assert users == {"repositories/database.py", "repositories/exceptions.py"}


def test_every_duckdb_connect_is_explicitly_read_only():
    calls = []
    for f in _files():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect":
                calls.append((f, node))
    assert calls, "expected exactly one connection point"
    for f, node in calls:
        kw = {k.arg: k.value for k in node.keywords}
        assert isinstance(kw.get("read_only"), ast.Constant) and kw["read_only"].value is True, f"{f}: connect() must pass read_only=True"
    assert len(calls) == 1


def test_no_write_sql_anywhere_in_the_api():
    for f in _files():
        text = f.read_text(encoding="utf-8")
        assert not SQL_WRITE.search(text), f"write-style SQL in {f.relative_to(ROOT)}"


def test_sql_text_lives_only_in_readmodels_definitions():
    sqlish = re.compile(r"\bselect\b[\s\S]*\bfrom\b", re.I)
    for f in _files():
        if str(f.relative_to(API)) == "readmodels/definitions.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                assert not sqlish.search(node.value), f"SQL-looking literal outside readmodels/definitions.py: {f.relative_to(ROOT)}: {node.value[:60]!r}"


def test_routers_are_thin():
    for f in (API / "routers").glob("*.py"):
        imports = set(_imports(f))
        assert not any(i.startswith(("api.repositories", "api.readmodels", "api.domain", "duckdb")) for i in imports), f.name


def test_no_module_level_connections():
    for f in _files():
        for node in ast.parse(f.read_text(encoding="utf-8")).body:            # module level only
            if isinstance(node, (ast.Assign, ast.Expr)) and isinstance(getattr(node, "value", None), ast.Call):
                fn = node.value.func
                assert not (isinstance(fn, ast.Attribute) and fn.attr == "connect"), f"module-level connect in {f}"
