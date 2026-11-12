"""Build the website (docs/, which GitHub Pages serves): the landing page, and the phone /
browser version of the trainer under docs/play/.

    python web/build.py --pyodide path/to/node_modules/pyodide

The play page runs the trainer's own Python in the browser (Pyodide): the Python files it
needs and your ranges (my_ranges/, plus the built-in tables) go into docs/play/app.zip.
"""

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web" / "py"))

PY_FILES = [
    "bots/__init__.py", "bots/contender.py", "bots/contender8.py", "bots/lines.py",
    "bots/lines8.py", "bots/opponent.py", "bots/opponent8.py", "bots/postflop.py",
    "bots/postflop8.py", "solve_spot.py", "icm_cfr.py", "bots/preflop8.py", "bots/preflop_data.py", "bots/pushfold_table.py",
    "bots/rangeplay.py", "bots/ranges.py", "bots/shortstack.py", "bots/starter.py", "cards.py", "engine.py",
    "engine8.py", "final_table.py", "flop_sims.py", "icm_study.py", "match.py", "match8.py",
    "solver/__init__.py", "solver/equity.py", "solver/import_ranges.py",
    "solver/import_ranges8.py", "tournament.py", "trainer8.py", "web/py/tkstub.py",
    "web/py/webgame.py"]
DATA_FILES = ["solver/preflop_equity.json"]
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip",
                 "pyodide-lock.json"]


def ranges_json():
    """The trainer's study table (your sims in my_ranges/ + the built-in tables) as JSON."""
    import tkstub
    tkstub.install()
    import trainer8 as T
    T.STUDY_FOLDERS = ("my_ranges",)    # your sims only: study_ranges/ never goes public
    T.load_study_table()
    out = {}
    for spot, deps in T.STUDY_TABLE.items():
        out[spot] = {f"{d:g}": {a: {h: round(f, 3) for h, f in rng.items() if f >= 0.0005}
                                for a, rng in acts.items()}
                     for d, acts in deps.items()}
    return json.dumps(out, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pyodide", required=True, help="the pyodide npm package's folder")
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args()
    out = Path(args.out)
    play = out / "play"
    (play / "pyodide").mkdir(parents=True, exist_ok=True)
    for f in PYODIDE_FILES:
        shutil.copy2(Path(args.pyodide) / f, play / "pyodide" / f)
    with zipfile.ZipFile(play / "app.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in PY_FILES + DATA_FILES:
            z.write(ROOT / f, f)
        z.writestr("ranges.json", ranges_json())
        # the flop solves (postflop/), packed small: the bots play them, read when used
        import flop_sims
        for f in sorted((ROOT / "postflop").glob("*/*.json")):
            d = json.loads(f.read_text())
            z.writestr(f"postflop/{f.parent.name}/{f.name}",
                       json.dumps(flop_sims.pack(d), separators=(",", ":")))
    av = play / "avatars"
    av.mkdir(exist_ok=True)
    keep = {p.name for p in (ROOT / "avatars").glob("*.png")}
    for p in av.glob("*.png"):          # (pictures of players no longer in the list)
        if p.name not in keep:
            p.unlink()
    for p in (ROOT / "avatars").glob("*.png"):
        shutil.copy2(p, av / p.name)
    for p in (ROOT / "web" / "site").rglob("*"):
        if p.is_file():
            dest = out / p.relative_to(ROOT / "web" / "site")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    # the phone keeps the site in its cache: a new cache name for every new build makes it
    # fetch the new version (the service worker file itself is always checked for changes)
    digest = hashlib.sha1()
    for p in sorted(play.rglob("*")):
        if p.is_file() and p.name != "sw.js":
            digest.update(p.relative_to(play).as_posix().encode())
            digest.update(p.read_bytes())
    sw = play / "sw.js"
    sw.write_text(sw.read_text().replace('"ftt-v1"', f'"ftt-{digest.hexdigest()[:10]}"'))
    (out / ".nojekyll").write_text("")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"built {out} ({size / 1e6:.1f} MB; app.zip {(play / 'app.zip').stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
