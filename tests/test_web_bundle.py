"""The phone version bundles a fixed list of Python files (web/build.py PY_FILES): every
local module those files import must be on the list, or the app can't start."""
import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _bundle():
    spec = importlib.util.spec_from_file_location("webbuild", ROOT / "web" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return set(mod.PY_FILES)


def _local(name):
    """The repo file for a module name, if it's one of ours."""
    parts = name.split(".")
    for cand in (ROOT.joinpath(*parts).with_suffix(".py"), ROOT.joinpath(*parts, "__init__.py")):
        if cand.exists():
            return cand.relative_to(ROOT).as_posix()
    return None


def test_bundled_imports_are_bundled():
    bundle = _bundle()
    missing = set()
    for f in bundle:
        tree = ast.parse((ROOT / f).read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                local = _local(n)
                if local and local not in bundle and not local.startswith(("solver/", "tests/")):
                    missing.add((f, local))
    assert not missing, f"imported but not bundled: {sorted(missing)}"
