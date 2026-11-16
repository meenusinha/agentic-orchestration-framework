#!/usr/bin/env python3
"""Preview skip rules on one repository: which files each rule matches, and an example of what it
removes. Read-only. Run by the setup page and graphrag.py with a time limit, so a slow pattern can
never hang them.

    skip_preview.py --repo NAME --words JSON-LIST --patterns JSON-LIST

Prints JSON lines: progress messages, then one {"event": "result", ...}.
"""

import os
import sys


def _ignore_foreign_pythonpath():
    """Use only this environment's packages. PYTHONPATH/PYTHONHOME set for other tools would be
    searched before the virtual environment, and their libraries (possibly built for another
    Python version) imported instead. Drop them here and for every process started from here;
    only this program's own folder is kept."""
    own = os.path.dirname(os.path.abspath(__file__))
    for var in ("PYTHONPATH", "PYTHONHOME"):
        value = os.environ.pop(var, None)
        if var == "PYTHONPATH" and value:
            foreign = {os.path.abspath(p) for p in value.split(os.pathsep) if p} - {own}
            sys.path[:] = [p for p in sys.path if not p or os.path.abspath(p) not in foreign]


_ignore_foreign_pythonpath()


import argparse
import json
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))
sys.path.insert(0, str(FW / "gui"))
EXAMPLE_LINES, EXAMPLE_CHARS = 8, 600


def emit(**kw):
    print(json.dumps(kw), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--words", default="[]")
    ap.add_argument("--patterns", default="[]")
    a = ap.parse_args()
    import graphrag_ops as ops
    from codegraph.indexer import IndexConfig, collect_files
    from codegraph.skiprules import SkipRules
    words, patterns = json.loads(a.words), json.loads(a.patterns)
    clean, errors = ops.settings.validate({"skip_header_words": words, "skip_patterns": patterns})
    if errors:
        emit(event="result", ok=False, error="; ".join(errors))
        return
    repos = {r["name"]: r for r in ops.configured_repos()}
    if a.repo not in repos:
        emit(event="result", ok=False, error=f"unknown repository {a.repo!r}")
        return
    r = repos[a.repo]
    rc, root = ops.repo_cfg(r), Path(r["path"]).expanduser()
    s = ops.settings.load(rc)
    ic = IndexConfig(r["name"], root, ops.settings.roots(root, rc), rc.get("file_types"), rc.get("structure"),
                     rc.get("extra_extensions"), max_file_bytes=s["max_file_kb"] * 1024, exclude=s["exclude"])
    files = collect_files(ic)[0]
    rules = [("header", SkipRules(clean["skip_header_words"], []))] if clean["skip_header_words"] else []
    rules += [(f"pattern {i + 1}", SkipRules([], [p])) for i, p in enumerate(clean["skip_patterns"])]
    found = {name: {"rule": name, "files": 0, "blocks": 0, "lines": 0, "example": None} for name, _ in rules}
    for i, (rel, ab, _) in enumerate(files):
        if i % 500 == 0:
            emit(event="progress", msg=f"checked {i} of {len(files)} files")
        try:
            raw = ab.read_bytes()
        except OSError:
            continue
        if len(raw) > ic.max_file_bytes:
            continue
        text = raw.decode("utf-8", "replace")
        for name, rule in rules:
            _, hits = rule.apply(text)
            if not hits:
                continue
            f = found[name]
            f["files"] += 1
            f["blocks"] += len(hits)
            f["lines"] += sum(b - a2 + 1 for _, a2, b in hits)
            if f["example"] is None:
                _, first, last = hits[0]
                removed = "\n".join(text.splitlines()[first - 1:min(last, first + EXAMPLE_LINES - 1)])
                f["example"] = {"file": rel, "lines": [first, last], "text": removed[:EXAMPLE_CHARS],
                                "more": last - first + 1 > EXAMPLE_LINES}
    emit(event="result", ok=True, repo=a.repo, files_checked=len(files), rules=list(found.values()))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report, don't crash the GUI
        emit(event="result", ok=False, error=f"{type(e).__name__}: {e}")
