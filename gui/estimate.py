#!/usr/bin/env python3
"""Estimate how long creating the GraphRAG takes, per repository and per component (setup GUI, step 6).

Prints JSON lines: {"event": "progress", "msg": ...} while working, then one
{"event": "result", ...}. Nothing is written: the graphs are only read.

How the estimate is made:
  1. Every file that would be indexed is listed and grouped into components (top-level
     folders; very large ones split into their sub-folders).
  2. Files already in the GraphRAG with unchanged content are marked: their vectors are
     reused, so they only cost parsing. Folder roles are resolved as in a build: tests are
     never embedded, external code only by its signatures.
  3. A sample of the real files (up to about 240 KB, spread over every component) is parsed
     and embedded with the real model on this computer. Embedding cost grows faster than the
     text length (codegraph/embedcost.py), so the sample gives the work per source byte, and
     the seconds per unit of work come from this computer's last real build when one was
     recorded (gui/buildspeed.py), else from the sample itself.
The page combines these numbers for whatever the user selects.
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


import hashlib
import json
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))

SAMPLE_BYTES = 240_000           # parsed and embedded to measure this computer's speed
SAMPLE_FILES = 80
SAMPLE_MAX_FILE = 48_000         # very large files are left out of the sample
DEFAULT_RATES = {"parse": 2e-6, "embed": 3e-4}     # seconds per byte, if there is nothing to sample
SIGNATURES_SHARE = 0.1                             # external code: only signatures are embedded


def embed_share(role: str, mode: str) -> float:
    """Share of a file's text that a build embeds, from its role and index mode."""
    if role == "test" or mode == "skip":
        return 0.0
    return SIGNATURES_SHARE if mode == "signatures" else 1.0


def emit(**kw):
    print(json.dumps(kw), flush=True)


class Ticker:
    """Progress messages at most every `every` seconds."""
    def __init__(self, every=1.5):
        self.every, self.last = every, 0.0

    def __call__(self, msg, force=False):
        now = time.time()
        if force or now - self.last >= self.every:
            self.last = now
            emit(event="progress", msg=msg)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def survey(repo: dict, tick) -> dict:
    """Files to index, grouped into components, with what is already in the GraphRAG."""
    import yaml
    from codegraph import components, settings
    from codegraph.indexer import IndexConfig, collect_files
    from codegraph.store import Store
    from codegraph.structure import resolve
    root = Path(repo["path"]).expanduser()
    cfg_file = root / "mcp" / "config.yaml"
    rc = (yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}) if cfg_file.exists() else {}
    s = settings.load(rc)
    ic = IndexConfig(repo["name"], root, settings.roots(root, rc), rc.get("file_types"), rc.get("structure"),
                     rc.get("extra_extensions"), max_file_bytes=s["max_file_kb"] * 1024,
                     exclude=s["exclude"])                     # deferred components are listed too
    tick(f"{repo['name']}: listing files …", force=True)
    files, _, _ = collect_files(ic)
    sized = []
    for rel, ab, lang in files:
        try:
            size = ab.stat().st_size
        except OSError:
            continue
        if size <= ic.max_file_bytes:
            sized.append((rel, ab, lang, size))
    tick(f"{repo['name']}: reading folder roles and build files …", force=True)
    st = resolve(root, [rel for rel, _, _, _ in sized], ic.structure)
    share = {rel: embed_share(st.roles.get(rel, "implementation"), st.index_mode.get(rel, "full"))
             for rel, _, _, _ in sized}
    store = Store.open_readonly(root / ".rag_index")
    stored = store.file_hashes() if store is not None else {}
    if store is not None:
        store.close()
    cached = set()
    candidates = [(rel, ab) for rel, ab, _, _ in sized if rel in stored]
    for i, (rel, ab) in enumerate(candidates):
        tick(f"{repo['name']}: checking what is already in the GraphRAG — {i}/{len(candidates)} files")
        try:
            if _sha(ab) == stored[rel]:
                cached.add(rel)
        except OSError:
            continue
    deferred = {components.from_setting(d) for d in s["deferred"]}
    comps = []
    for c in components.group([(rel, size) for rel, _, _, size in sized]):
        rels = [rel for rel, _ in c.files]
        n_cached = sum(1 for rel in rels if rel in cached)
        comps.append({"path": c.path, "label": c.label, "files": len(rels), "bytes": c.size,
                      "cached_files": n_cached,
                      "cached_bytes": sum(size for rel, size in c.files if rel in cached),
                      # bytes a build embeds: all of them when starting from scratch, else the new ones
                      "embed_bytes_all": round(sum(size * share[rel] for rel, size in c.files)),
                      "embed_bytes": round(sum(size * share[rel] for rel, size in c.files if rel not in cached)),
                      "state": "indexed" if rels and n_cached == len(rels) else "partly" if n_cached else "new",
                      "deferred": c.path in deferred, "always": c.path == components.ALWAYS})
    return {"name": repo["name"], "path": str(root), "graph_exists": bool(stored), "components": comps,
            "excluded": list(s["exclude"]),                # never indexed (step 4 / Settings)
            "files": len(sized), "bytes": sum(x[3] for x in sized),
            "_sample_pool": [(rel, ab, lang, size) for rel, ab, lang, size in sized       # what a build embeds
                             if rel not in cached and size <= SAMPLE_MAX_FILE and share[rel] == 1.0]
                            or [(rel, ab, lang, size) for rel, ab, lang, size in sized
                                if size <= SAMPLE_MAX_FILE and share[rel] == 1.0]}


def pick_sample(pools: list[list]) -> list:
    """A deterministic spread of files: one file from each folder in turn (two levels deep,
    across every repository), so every part of the code is represented."""
    buckets: dict[str, list] = {}
    for n, pool in enumerate(pools):
        for rel, ab, lang, size in pool:
            key = f"{n}|" + "/".join(rel.split("/")[:2])
            buckets.setdefault(key, []).append((hashlib.sha1(str(ab).encode()).hexdigest(), rel, ab, lang, size))
    queues = [sorted(v) for _, v in sorted(buckets.items(), key=lambda kv: hashlib.sha1(kv[0].encode()).hexdigest())]
    out, total = [], 0
    while queues and len(out) < SAMPLE_FILES and total < SAMPLE_BYTES:
        for q in list(queues):
            if not q:
                queues.remove(q)
                continue
            _, rel, ab, lang, size = q.pop(0)
            out.append((rel, ab, lang, size))
            total += size
            if len(out) >= SAMPLE_FILES or total >= SAMPLE_BYTES:
                break
    return out


def measure(sample: list, embed, tick, recorded: dict | None = None) -> dict:
    """Parse and embed the sample exactly as a build does → seconds per source byte for parsing
    and for embedding. With a recorded real build, its speed is used and the sample only
    contributes how much embedding work each byte of this code means."""
    from codegraph.embedcost import work as embed_work
    from codegraph.extract import extract
    from codegraph.indexer import EMBED_KINDS, _embed_text
    from codegraph.model import FILE
    from codegraph.security import redact
    if not sample:
        return dict(DEFAULT_RATES, sample_files=0, sample_bytes=0, source="default")
    tick(f"Measuring this computer's speed on {len(sample)} sample files …", force=True)
    texts, nbytes = [], 0
    t0 = time.perf_counter()
    for rel, ab, lang, size in sample:
        try:
            text, _ = redact(ab.read_bytes().decode("utf-8", "replace"))
            tree = extract(rel, text, lang)
        except Exception:
            continue
        nbytes += size
        texts += [_embed_text(n) for n in tree.walk() if n.kind in EMBED_KINDS and n.kind != FILE]
    parse_s = time.perf_counter() - t0
    embed_s = 0.0
    if not recorded:                                  # a recorded build's speed makes this unnecessary
        embed(["warm-up", "def warm_up():\n    return 1"])     # the first call pays one-off set-up costs
        t1 = time.perf_counter()
        if texts:
            embed(sorted(texts, key=len))
        embed_s = time.perf_counter() - t1
    nbytes = max(nbytes, 1)
    work = sum(embed_work(len(t)) for t in texts) or 1.0
    out = {"sample_files": len(sample), "sample_bytes": nbytes, "sample_units": len(texts),
           "sample_parse": parse_s / nbytes, "sample_embed": embed_s / nbytes}
    if recorded:
        out.update(parse=recorded["parse_seconds_per_byte"],
                   embed=recorded["embed_seconds_per_work"] * work / nbytes, source="build",
                   recorded=recorded["recorded"])
    else:
        out.update(parse=parse_s / nbytes, embed=embed_s / nbytes, source="sample")
    return out


def main():
    import yaml
    from codegraph import settings
    cfg = yaml.safe_load((FW / "orchestrator" / "mcp" / "config.yaml").read_text(encoding="utf-8")) or {}
    repos = [r for r in cfg.get("repos") or [] if not str(r.get("path", "")).startswith("/absolute/path/to/")]
    if not repos:
        emit(event="result", ok=False, error="no repos configured")
        return
    os.environ.setdefault("DEMO_LOG_FILE", str(Path.home() / ".agentic-orchestration" / "logs" /
                                              "mcp-orchestration.log"))
    tick = Ticker()
    surveys = [survey(r, tick) for r in repos]
    tick("Loading the embedding model …", force=True)
    from repo_rag import RepoRAG
    t0 = time.perf_counter()
    first = surveys[0]
    rag = RepoRAG(first["name"], first["path"], [], embedding_model=str(settings.model_dir(FW, cfg)),
                  embed_batch_size=settings.load({})["embed_batch_size"])
    load_s = time.perf_counter() - t0
    import buildspeed
    rates = measure(pick_sample([s.pop("_sample_pool") for s in surveys]), rag._embed, tick,
                    buildspeed.load(rag._model_sha))
    rates["model_load"] = load_s
    emit(event="result", ok=True, repos=surveys, rates=rates)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # report, don't crash the GUI
        emit(event="result", ok=False, error=f"{type(e).__name__}: {e}")
