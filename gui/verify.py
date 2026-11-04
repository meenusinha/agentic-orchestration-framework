#!/usr/bin/env python3
"""Build every configured repo's code graph and report what was found (setup GUI, last step).

Prints JSON lines: {"event": "progress", "msg": ...} while working, then one
{"event": "result", ...} with per-repo stats and the cross-repo interface map.
Two passes: the first builds each graph and publishes its IDL interfaces, the
second (fast) links every repo to its peers' interfaces."""

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


import json
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))


def emit(**kw):
    print(json.dumps(kw), flush=True)


def main():
    import yaml
    cfg = yaml.safe_load((FW / "orchestrator" / "mcp" / "config.yaml").read_text()) or {}
    repos = [r for r in cfg.get("repos") or [] if not str(r.get("path", "")).startswith("/absolute/path/to/")]
    if not repos:
        emit(event="result", ok=False, error="no repos configured")
        return
    os.environ.setdefault("DEMO_LOG_FILE", str(Path.home() / ".agentic-orchestration" / "logs" /
                                              "mcp-orchestration.log"))
    from repo_rag import RepoRAG
    from codegraph import settings
    model = settings.model_dir(FW, cfg)
    peers = [{"name": r["name"], "path": str(Path(r["path"]).expanduser())} for r in repos]
    rags, stats = {}, {}
    for p in (1, 2):
        for r in repos:
            root = Path(r["path"]).expanduser()
            rc = yaml.safe_load((root / "mcp" / "config.yaml").read_text()) if (root / "mcp" / "config.yaml").exists() else {}
            emit(event="progress", msg=("Building" if p == 1 else "Linking") + f" {r['name']} …")
            if r["name"] not in rags:
                rags[r["name"]] = RepoRAG(r["name"], str(root), settings.roots(root, rc), embedding_model=str(model),
                                          file_types=rc.get("file_types"), structure=rc.get("structure"),
                                          extra_extensions=rc.get("extra_extensions"), peers=peers,
                                          **settings.rag_kwargs(rc))
            stats[r["name"]] = rags[r["name"]].build_or_load_index()
    out, imap = [], {}
    for r in repos:
        rag = rags[r["name"]]
        meta = rag.store.meta("stats", {})
        reg = json.loads((rag.index_dir / "interfaces.json").read_text())
        for d in reg.get("defines", []):
            imap.setdefault(d["id"], {"interface": d["id"], "defined_in": r["name"],
                                      "operations": [o["name"] for o in d["operations"]],
                                      "providers": [], "consumers": []})
        out.append({"name": r["name"], "path": r["path"], "files": meta.get("files", 0),
                    "units": meta.get("nodes", 0), "links": meta.get("edges", 0),
                    "interfaces_defined": [d["id"] for d in reg.get("defines", [])],
                    "unclassified": sorted((stats[r["name"]] or {}).get("unclassified", {}) or {})})
    for r in repos:
        reg = json.loads((rags[r["name"]].index_dir / "interfaces.json").read_text())
        for l in reg.get("links", {}).get("implements", []):
            if l["interface"] in imap:
                imap[l["interface"]]["providers"].append(
                    {"repo": r["name"], "process": l.get("target") or "-", "by": l["qualname"],
                     "file": l.get("file"), "confidence": l["confidence"]})
        seen = set()
        for l in reg.get("links", {}).get("consumes", []):
            k = (l["interface"], l["qualname"])
            if l["interface"] in imap and k not in seen:
                seen.add(k)
                imap[l["interface"]]["consumers"].append(
                    {"repo": r["name"], "process": l.get("target") or "-", "by": l["qualname"],
                     "file": l.get("file"), "operation": l.get("op"), "confidence": l["confidence"]})
    emit(event="result", ok=True, repos=out, interfaces=list(imap.values()))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report, don't crash the GUI
        emit(event="result", ok=False, error=f"{type(e).__name__}: {e}")
