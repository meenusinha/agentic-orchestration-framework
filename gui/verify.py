#!/usr/bin/env python3
"""Build every configured repo's code graph and report what was found (setup GUI, last step).

Prints JSON lines: {"event": "progress", "msg": ...} while working, {"event": "bar", ...}
for the progress bar (repository, stage, done/total, overall fraction, time left in the
stage), then one {"event": "result", ...} with per-repo stats and the cross-repo interface map.
Two passes: the first builds each graph and publishes its IDL interfaces, the
second (fast) links every repo to its peers' interfaces.

    verify.py [--fresh NAME ...]     NAME: start that repository's GraphRAG from scratch
                                     (cached vectors are dropped too)"""

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
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))


def emit(**kw):
    print(json.dumps(kw), flush=True)


# Share of one repository's build taken by each stage — for the overall progress bar only.
STAGES = {"read": (0.0, 0.05), "parse": (0.05, 0.25), "embed": (0.25, 0.95), "write": (0.95, 1.0)}
PASS_SHARE = {1: (0.0, 0.9), 2: (0.9, 1.0)}         # building, then linking (fast: vectors are reused)


class Bar:
    """Turns build progress into throttled bar events with an overall fraction and the time
    left in the current stage (from the rate so far)."""
    def __init__(self, n_repos: int, every: float = 0.4):
        self.n, self.every, self.last, self.stage_t0, self.key = n_repos, every, 0.0, time.time(), None

    def hook(self, p: int, i: int, repo: str):
        def progress(stage: str, done: int, total: int):
            now = time.time()
            if (p, repo, stage) != self.key:
                self.key, self.stage_t0, self.last = (p, repo, stage), now, 0.0
            finished = total and done >= total
            if now - self.last < self.every and not finished:
                return
            self.last = now
            lo, hi = STAGES.get(stage, (0.0, 1.0))
            frac = lo + (hi - lo) * (done / total if total else 1.0)
            plo, phi = PASS_SHARE[p]
            overall = plo + (phi - plo) * (i + frac) / self.n
            elapsed = now - self.stage_t0
            left = elapsed / done * (total - done) if done and total and elapsed > 2 else None
            emit(event="bar", repo=repo, phase="building" if p == 1 else "linking", stage=stage, done=done,
                 total=total, overall=round(min(overall, 1.0), 4), stage_seconds_left=left)
        return progress


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="append", default=[], metavar="NAME")
    args = ap.parse_args()
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
    bar = Bar(len(repos))
    for p in (1, 2):
        for i, r in enumerate(repos):
            root = Path(r["path"]).expanduser()
            rc = yaml.safe_load((root / "mcp" / "config.yaml").read_text()) if (root / "mcp" / "config.yaml").exists() else {}
            emit(event="progress", msg=("Building" if p == 1 else "Linking") + f" {r['name']} …")
            if r["name"] not in rags:
                rags[r["name"]] = RepoRAG(r["name"], str(root), settings.roots(root, rc), embedding_model=str(model),
                                          file_types=rc.get("file_types"), structure=rc.get("structure"),
                                          extra_extensions=rc.get("extra_extensions"), peers=peers,
                                          progress=bar.hook(p, i, r["name"]), **settings.rag_kwargs(rc))
                if r["name"] in args.fresh:
                    emit(event="progress", msg=f"Starting {r['name']} from scratch (stored graph and vectors removed) …")
                    rags[r["name"]].clear_index()
            rags[r["name"]].progress = bar.hook(p, i, r["name"])
            stats[r["name"]] = st = rags[r["name"]].build_or_load_index() or {}
            if st.get("rebuilt"):
                emit(event="progress", msg=f"  {r['name']}: {st['files']} files, {st['nodes']} units — "
                                          f"{st.get('embedded', 0)} embedded, {st.get('reused', 0)} reused")
            else:
                emit(event="progress", msg=f"  {r['name']}: up to date ({st.get('files', 0)} files)")
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
