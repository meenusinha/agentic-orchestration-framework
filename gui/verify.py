#!/usr/bin/env python3
"""Build every configured repo's code graph and report what was found (setup GUI, last step).

Prints JSON lines: {"event": "progress", "msg": ...} while working, {"event": "bar", ...}
for the progress bar (repository, stage, done/total, overall fraction, time left in the
stage), then one {"event": "result", ...} with per-repo stats and the cross-repo interface map.
Two passes: the first builds each graph and publishes its IDL interfaces, the
second (fast) links every repo to its peers' interfaces.

    verify.py [--fresh NAME ...] [--plan JSON]
      --fresh NAME   start that repository's GraphRAG from scratch (cached vectors are dropped too)
      --plan JSON    {repo: {"parse": seconds, "embed": seconds}} from the page's estimate: weights
                     the overall progress by the work each repository and stage really needs"""

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


STAGE_ORDER = ("read", "parse", "embed", "write")
DEFAULT_WORK = {"parse": 1.0, "embed": 3.0}          # relative work per repository without an estimate


def stage_work(work: dict, linking: bool) -> dict:
    """Expected seconds of each stage of one repository's build. Reading and parsing are
    measured together by the estimate; linking parses again and reuses every vector."""
    parse, embed = max(work.get("parse", 0.0), 0.0), max(work.get("embed", 0.0), 0.0)
    embed = 0.0 if linking else embed
    return {"read": 0.15 * parse + 0.01, "parse": 0.85 * parse + 0.01, "embed": embed,
            "write": 0.03 * (parse + embed) + 0.01}


class Bar:
    """Turns build progress into throttled bar events: the overall fraction is weighted by the
    work each repository and stage needs (from the page's estimate, else equal shares), and the
    time left in the current stage comes from the rate so far."""
    def __init__(self, repos: list, plan: dict | None = None, every: float = 0.4):
        self.every, self.last, self.stage_t0, self.key = every, 0.0, time.time(), None
        self.slots, self.total = {}, 0.0                 # (pass, repo, stage) → (start, length)
        for p in (1, 2):
            for name in repos:
                w = stage_work((plan or {}).get(name) or DEFAULT_WORK, linking=p == 2)
                for stage in STAGE_ORDER:
                    self.slots[(p, name, stage)] = (self.total, w[stage])
                    self.total += w[stage]

    def overall(self, p: int, repo: str, stage: str, frac: float) -> float:
        start, length = self.slots.get((p, repo, stage), (0.0, 0.0))
        return min((start + length * min(max(frac, 0.0), 1.0)) / self.total, 1.0) if self.total else 0.0

    def hook(self, p: int, repo: str):
        def progress(stage: str, done: int, total: int, frac: float | None = None):
            now = time.time()
            if (p, repo, stage) != self.key:
                self.key, self.stage_t0, self.last = (p, repo, stage), now, 0.0
            finished = bool(total) and done >= total
            if now - self.last < self.every and not finished:
                return
            self.last = now
            if frac is None:                             # embedding reports its share of the text itself
                frac = done / total if total else 1.0
            elapsed = now - self.stage_t0
            left = elapsed / frac * (1 - frac) if 0 < frac < 1 and elapsed > 2 else None
            emit(event="bar", repo=repo, phase="building" if p == 1 else "linking", stage=stage, done=done,
                 total=total, overall=round(self.overall(p, repo, stage, frac), 4), stage_seconds_left=left)
        return progress


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="append", default=[], metavar="NAME")
    ap.add_argument("--plan", default="{}")
    args = ap.parse_args()
    try:
        plan = {str(k): {"parse": float(v.get("parse", 0)), "embed": float(v.get("embed", 0))}
                for k, v in (json.loads(args.plan) or {}).items()}
    except (ValueError, TypeError, AttributeError):
        plan = {}
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
    rags, stats, speeds = {}, {}, []
    bar = Bar([r["name"] for r in repos], plan)
    for p in (1, 2):
        for i, r in enumerate(repos):
            root = Path(r["path"]).expanduser()
            rc = yaml.safe_load((root / "mcp" / "config.yaml").read_text()) if (root / "mcp" / "config.yaml").exists() else {}
            emit(event="progress", msg=("Building" if p == 1 else "Linking") + f" {r['name']} …")
            if r["name"] not in rags:
                rags[r["name"]] = RepoRAG(r["name"], str(root), settings.roots(root, rc), embedding_model=str(model),
                                          file_types=rc.get("file_types"), structure=rc.get("structure"),
                                          extra_extensions=rc.get("extra_extensions"), peers=peers,
                                          progress=bar.hook(p, r["name"]), parse_workers=settings.parse_workers(rc),
                                          **settings.rag_kwargs(rc))
                if r["name"] in args.fresh:
                    emit(event="progress", msg=f"Starting {r['name']} from scratch (stored graph and vectors removed) …")
                    rags[r["name"]].clear_index()
            rags[r["name"]].progress = bar.hook(p, r["name"])
            stats[r["name"]] = st = rags[r["name"]].build_or_load_index() or {}
            if p == 1 and st.get("speed"):
                speeds.append(st["speed"])
            if st.get("parallel_fallback"):
                emit(event="progress", msg=f"  {r['name']}: parallel parsing unavailable — parsed one file at a time")
            if st.get("rebuilt"):
                emit(event="progress", msg=f"  {r['name']}: {st['files']} files, {st['nodes']} units — "
                                          f"{st.get('embedded', 0)} embedded, {st.get('reused', 0)} reused")
            else:
                emit(event="progress", msg=f"  {r['name']}: up to date ({st.get('files', 0)} files)")
    import buildspeed
    if speeds and buildspeed.record(speeds, next(iter(rags.values()))._model_sha):
        emit(event="progress", msg="This computer's build speed recorded for later time estimates")
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
