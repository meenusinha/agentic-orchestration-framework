#!/usr/bin/env python3
"""GraphRAG from the command line — used by the /graphrag-setup agent skill in Copilot Chat,
and usable by hand. It does what step 4 (folders) and step 6 (estimate, batches, progress)
of the setup page do, with the same code (gui/graphrag_ops.py and the gui/ workers).

Run it with the framework's virtual-environment Python:
    <framework>/.venv/bin/python <framework>/.github/skills/graphrag-setup/scripts/graphrag.py <command>
    (Windows: <framework>\\.venv\\Scripts\\python.exe …\\graphrag.py <command>)

Commands:
  status                         connected repositories, their GraphRAG, excluded folders,
                                 components left for later
  folders REPO [--exclude PATH …] [--include PATH …] [--exclude-all] [--include-all]
                                 folders to index (an excluded folder is never indexed)
  chunking REPO [--mode graph|sliding|hybrid] [--lines N] [--overlap N]
                                 how code is cut into searchable pieces (shown without options)
  skip REPO [--header-word W …] [--pattern REGEX …] [--clear] [--preview-only]
                                 content never indexed: a preview of what the rules remove,
                                 then saved (unless --preview-only)
  estimate                       time to create the GraphRAG, per component, measured on this
                                 computer; suggests a first batch when everything is long
  build [--all | --only R:C … | --add R:C … | --later R:C … | --suggested] [--fresh REPO …]
                                 create or update the GraphRAG, with a progress bar
                                 (R:C = repository:component, e.g. server:src or big:src/net)

Nothing is downloaded and no code leaves the computer.
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
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parents[4]
LAST_ESTIMATE = Path.home() / ".agentic-orchestration" / "last-estimate.json"


def _require_supported_os(what: str) -> None:
    """Supported platforms: Windows and Linux. AO_ALLOW_UNSUPPORTED_OS=1 overrides this
    deliberately (e.g. to run the tests elsewhere); a warning is printed."""
    if sys.platform.startswith("linux") or sys.platform == "win32":
        return
    if os.environ.get("AO_ALLOW_UNSUPPORTED_OS", "").strip().lower() in ("1", "true", "yes"):
        print(f"WARNING: {what} is running on an unsupported platform ({sys.platform}) "
              "because AO_ALLOW_UNSUPPORTED_OS is set.", file=sys.stderr)
        return
    sys.exit(f"ERROR: {what} supports Windows and Linux only (this system: {sys.platform}).")


def _require_venv(what: str) -> None:
    """The framework runs inside its virtual environment. AO_ALLOW_NO_VENV=1 overrides this."""
    if sys.prefix == sys.base_prefix:
        if os.environ.get("AO_ALLOW_NO_VENV", "").strip().lower() in ("1", "true", "yes"):
            print(f"WARNING: {what} is running outside a virtual environment ({sys.executable}) "
                  "because AO_ALLOW_NO_VENV is set.", file=sys.stderr)
            return
        sys.exit(f"ERROR: {what} must run with the framework's virtual-environment Python:\n"
                 f"       {FW / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')}\n"
                 "       (install first: see the /graphrag-setup skill, step 1)")


def _ops():
    sys.path.insert(0, str(FW / "gui"))
    os.environ["PYTHONUTF8"] = "1"                   # the workers inherit UTF-8 mode
    import graphrag_ops
    return graphrag_ops


# ── helpers ───────────────────────────────────────────────────────────────────
def repo_by_name(ops, name: str) -> dict:
    repos = {r["name"]: r for r in ops.configured_repos()}
    if name not in repos:
        sys.exit(f"ERROR: no connected repository {name!r}. Connected: {', '.join(repos) or 'none'} "
                 "(connect repositories with configure.py first)")
    return repos[name]


def indexable_files(ops, r: dict, with_exclusions: bool) -> list:
    """[(rel, size)] of the files a build would index (exclusions applied or not; components
    left for later are always listed)."""
    from codegraph.indexer import IndexConfig, collect_files
    rc, root = ops.repo_cfg(r), Path(r["path"]).expanduser()
    s = ops.settings.load(rc)
    ic = IndexConfig(r["name"], root, ops.settings.roots(root, rc), rc.get("file_types"), rc.get("structure"),
                     rc.get("extra_extensions"), max_file_bytes=s["max_file_kb"] * 1024,
                     exclude=s["exclude"] if with_exclusions else [])
    out = []
    for rel, ab, _ in collect_files(ic)[0]:
        try:
            size = ab.stat().st_size
        except OSError:
            continue
        if size <= ic.max_file_bytes:
            out.append((rel, size))
    return out


def top_folders(ops, r: dict) -> dict:
    """{top-level folder: number of files that would be indexed without exclusions}."""
    out: dict = {}
    for rel, _ in indexable_files(ops, r, with_exclusions=False):
        if "/" in rel:
            top = rel.split("/")[0]
            out[top] = out.get(top, 0) + 1
    return dict(sorted(out.items()))


def folder_entry(entry: str) -> str | None:
    """The top-level folder an exclude entry names exactly (folder, folder/ or folder/**), else None."""
    from codegraph.components import from_setting
    e = entry.strip().replace("\\", "/")
    for suffix in ("/**", "/"):
        if e.endswith(suffix):
            e = e[: -len(suffix)]
    return from_setting(e) if "/" not in e and not any(c in from_setting(e) for c in "*?") else None


def parse_rc(values: list) -> list:
    out = []
    for v in values:
        repo, sep, comp = v.partition(":")
        if not sep or not repo or not comp:
            sys.exit(f"ERROR: {v!r}: expected REPOSITORY:COMPONENT, e.g. server:src")
        out.append((repo, comp.strip().strip("/").replace("\\", "/")))
    return out


def load_estimate():
    try:
        return json.loads(LAST_ESTIMATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_estimate(est: dict) -> None:
    from codegraph.store import write_private_text
    write_private_text(LAST_ESTIMATE, json.dumps(est))


# ── commands ──────────────────────────────────────────────────────────────────
def cmd_status(ops, _args) -> int:
    from codegraph.store import Store
    print(f"Framework: {FW}")
    repos = ops.configured_repos()
    if not repos:
        print("No repositories connected yet — connect them with configure.py (see the skill).")
        return 0
    print(f"Connected repositories: {len(repos)}")
    for r in repos:
        root = Path(r["path"]).expanduser()
        s = ops.settings.load(ops.repo_cfg(r))
        store = Store.open_readonly(root / ".rag_index")
        if store is not None:
            stats, built = store.meta("stats", {}), store.meta("built_at")
            store.close()
            graph = (f"GraphRAG built {time.strftime('%Y-%m-%d %H:%M', time.localtime(built))}: "
                     f"{stats.get('files', 0)} files, {stats.get('nodes', 0)} units, {stats.get('edges', 0)} links"
                     if built else "GraphRAG not created yet")
        else:
            graph = "GraphRAG not created yet"
        from codegraph.components import from_setting
        print(f"\n  {r['name']}  {root}\n    {graph}")
        print(f"    Excluded (never indexed): {', '.join(s['exclude']) or 'none'}")
        print(f"    Left for a later batch:   {', '.join(from_setting(d) for d in s['deferred']) or 'none'}")
        print(f"    Chunking:                 {s['chunking']}" + ("" if s["chunking"] == "graph" else
              f" ({s['chunk_lines']} lines, {s['chunk_overlap']} overlap)"))
        print(f"    Skip rules:               header words: {', '.join(s['skip_header_words']) or 'none'}; "
              f"patterns: {len(s['skip_patterns'])}")
    return 0


def cmd_folders(ops, args) -> int:
    r = repo_by_name(ops, args.repo)
    s = ops.settings.load(ops.repo_cfg(r))
    tops = top_folders(ops, r)
    exclude = list(s["exclude"])
    changed = False
    if args.include_all:
        exclude, changed = [], True
    if args.exclude_all:
        from codegraph.components import to_setting
        exclude = [e for e in exclude if folder_entry(e) is None] + [to_setting(f) for f in tops]
        changed = True
    for path in args.include or []:
        p = path.strip().replace("\\", "/").strip("/")
        keep = [e for e in exclude if e.strip("/") != p and e != p + "/**" and folder_entry(e) != p]
        if len(keep) == len(exclude):
            print(f"note: {p!r} was not excluded by its own entry"
                  + (f" — current exclusions: {', '.join(exclude)}" if exclude else ""))
        exclude, changed = keep, True
    for path in args.exclude or []:
        p = path.strip().replace("\\", "/").strip("/")
        if p and p not in exclude:
            exclude.append(p)
            changed = True
    if changed:
        try:
            plan = ops.plan_setting({r["name"]: exclude}, "exclude")
        except ValueError as e:
            sys.exit(f"ERROR: {e}")
        ops.write_settings(plan)
        print("Saved. It applies at the next build (`build`); VS Code servers that are running follow at their next refresh.\n")
        s = ops.settings.load(ops.repo_cfg(r))
    excluded_folders = {folder_entry(e) for e in s["exclude"]} - {None}
    others = [e for e in s["exclude"] if folder_entry(e) is None]
    print(f"Folders of {r['name']} ({Path(r['path']).expanduser()}):")
    print(f"  {'index':6s} {'folder':32s} files")
    for f, n in tops.items():
        mark = "  no " if f in excluded_folders else "  yes"
        print(f"  {mark:6s} {f + '/':32s} {n}")
    print("  Top-level files are always indexed.")
    if others:
        print(f"  Also excluded: {', '.join(others)}")
    return 0


def save_values(ops, repo: str, values: dict) -> bool:
    try:
        changes = ops.plan_values(repo, values)
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    ops.write_settings(changes)
    if changes:
        print("Saved. The GraphRAG is rebuilt with it at the next `build` (vectors of unchanged text are reused); "
              "VS Code servers that are running follow at their next refresh.")
    return bool(changes)


def cmd_chunking(ops, args) -> int:
    r = repo_by_name(ops, args.repo)
    values = {k: v for k, v in (("chunking", args.mode), ("chunk_lines", args.lines),
                                ("chunk_overlap", args.overlap)) if v is not None}
    if values:
        save_values(ops, r["name"], values)
    s = ops.settings.load(ops.repo_cfg(r))
    print(f"Chunking of {r['name']}: {s['chunking']}" + ("" if s["chunking"] == "graph" else
          f" — {s['chunk_lines']} lines per chunk, {s['chunk_overlap']} lines overlap"))
    print({"graph": "  whole functions, classes, IDL operations and documentation sections, never cut",
           "sliding": "  every file in overlapping chunks; results are chunks with their line range",
           "hybrid": "  whole units as results; long units are also searched in overlapping windows"}[s["chunking"]])
    return 0


def cmd_skip(ops, args) -> int:
    r = repo_by_name(ops, args.repo)
    s = ops.settings.load(ops.repo_cfg(r))
    words = [] if args.clear else list(s["skip_header_words"])
    patterns = [] if args.clear else list(s["skip_patterns"])
    words += [w for w in args.header_word or [] if w not in words]
    patterns += [p for p in args.pattern or [] if p not in patterns]
    if words or patterns:
        print(f"Checking every file of {r['name']} …", flush=True)
        try:
            res = ops.preview_skip_rules(r["name"], words, patterns)
        except RuntimeError as e:
            sys.exit(f"ERROR: {e}")
        print(f"  {'rule':40s} {'files':>6s} {'blocks':>7s} {'lines':>7s}")
        for x in res["rules"]:
            label = ("header words: " + ", ".join(words)) if x["rule"] == "header" else \
                f"{x['rule']}: {patterns[int(x['rule'].split()[1]) - 1]}"
            print(f"  {label[:40]:40s} {x['files']:6d} {x['blocks']:7d} {x['lines']:7d}")
            if x["example"]:
                e = x["example"]
                print(f"    e.g. {e['file']}:{e['lines'][0]}-{e['lines'][1]}")
                for line in e["text"].splitlines():
                    print("      | " + line[:120])
        print(f"  ({res['files_checked']} files checked)")
    else:
        print(f"No skip rules for {r['name']}.")
    if args.preview_only:
        print("Preview only — nothing saved.")
        return 0
    save_values(ops, r["name"], {"skip_header_words": words, "skip_patterns": patterns})
    return 0


def print_estimate(ops, est: dict) -> None:
    r = est["rates"]
    every = ops.total_seconds(est, lambda n, c: True)
    now = ops.total_seconds(est, lambda n, c: not c["deferred"])
    files = sum(rp["files"] for rp in est["repos"])
    nbytes = sum(rp["bytes"] for rp in est["repos"])
    source = {"build": "this computer's last GraphRAG build", "sample": "a sample of this code, embedded with the real model",
              "default": "a default value"}.get(r.get("source"), "a sample")
    print(f"Creating the whole GraphRAG takes {ops.fmt_duration(every)} on this computer "
          f"({files:,} files, {ops.fmt_bytes(nbytes)}). Speed from {source}.")
    for rp in est["repos"]:
        print(f"\n  {rp['name']}  ({rp['files']:,} files · {ops.fmt_bytes(rp['bytes'])} · "
              f"{'GraphRAG exists' if rp['graph_exists'] else 'no GraphRAG yet'})")
        print(f"    {'now':4s} {'component':34s} {'files':>7s} {'size':>9s}  {'status':16s} time")
        for c in rp["components"]:
            mark = "all" if c["always"] else ("no" if c["deferred"] else "yes")
            state = "left for later" if c["deferred"] else {"indexed": "in GraphRAG", "partly": "partly (changed)"}.get(c["state"], "new")
            name = "(top-level files)" if c["always"] else c["path"]
            print(f"    {mark:4s} {name:34s} {c['files']:7,d} {ops.fmt_bytes(c['bytes']):>9s}  {state:16s} "
                  f"{ops.fmt_duration(ops.component_seconds(c, r))}")
        if rp.get("excluded"):
            print(f"    excluded (never indexed): {', '.join(rp['excluded'])}")
    print(f"\nWith the current choice (components marked 'yes'): {ops.fmt_duration(now)}.")
    if every > ops.LARGE_SECONDS:
        batch = ops.suggest_batch(est)
        secs = ops.total_seconds(est, lambda n, c: (n, c["path"]) in batch)
        print(f"That's long — create it in batches. Suggested first batch ({ops.fmt_duration(secs)}): "
              + ", ".join(f"{n}:{p}" for n, p in sorted(batch)) + "\n"
              "  → build --suggested     (or choose with --only / --add / --later; build --all for everything)")


def cmd_estimate(ops, args) -> int:
    print("Estimating (lists the files, checks what is already in the GraphRAG, measures this computer's speed) …",
          flush=True)
    try:
        est = ops.run_worker("estimate.py", on_message=lambda m: print("  " + m, flush=True))
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    save_estimate(est)
    if args.json:
        print(json.dumps(est))
    else:
        print()
        print_estimate(ops, est)
    return 0


def components_now(ops, est) -> dict:
    """{repo: [component paths]} — from the last estimate, or listed now (no model needed)."""
    if est:
        return {rp["name"]: [c["path"] for c in rp["components"] if not c["always"]] for rp in est["repos"]}
    from codegraph import components
    return {r["name"]: [c.path for c in components.group(indexable_files(ops, r, with_exclusions=True)) if c.path]
            for r in ops.configured_repos()}


class Progress:
    """Text progress bar for a terminal or Copilot's terminal tool: one line every few seconds."""
    def __init__(self, expected: float | None, every: float = 5.0):
        self.t0, self.last, self.every, self.expected, self.key = time.time(), 0.0, every, expected, None

    def __call__(self, b: dict) -> None:
        now = time.time()
        key = (b.get("phase"), b.get("repo"), b.get("stage"))
        if now - self.last < self.every and key == self.key and b.get("overall", 0) < 1:
            return
        self.last, self.key = now, key
        o = max(min(b.get("overall", 0.0), 1.0), 0.0)
        elapsed = now - self.t0
        left = elapsed / o * (1 - o) if o > 0.03 else (max(self.expected - elapsed, 0) if self.expected else None)
        stage = {"read": "reading files", "parse": "parsing", "embed": "embedding", "write": "saving"}.get(b.get("stage"), b.get("stage"))
        unit = "units" if b.get("stage") == "embed" else "files"
        count = f" {b['done']:,} / {b['total']:,} {unit}" if b.get("total", 0) > 1 else ""
        bar = "#" * round(o * 30) + "-" * (30 - round(o * 30))
        from graphrag_ops import fmt_duration
        print(f"[{bar}] {o:4.0%}" + (f" · {fmt_duration(left)} left" if left is not None and o < 1 else "")
              + f" · {'Linking' if b.get('phase') == 'linking' else 'Building'} {b.get('repo')}: {stage}{count}", flush=True)


def cmd_build(ops, args) -> int:
    from codegraph.components import from_setting
    est = load_estimate()
    comps = components_now(ops, est)
    names = set(comps)
    for n in args.fresh or []:
        if n not in names:
            sys.exit(f"ERROR: --fresh {n}: no connected repository {n!r}")
    current = {r["name"]: {from_setting(d) for d in ops.settings.load(ops.repo_cfg(r))["deferred"]}
               for r in ops.configured_repos()}
    chosen = [bool(args.all), bool(args.only), bool(args.add or args.later), bool(args.suggested)]
    if sum(chosen) > 1:
        sys.exit("ERROR: use one of --all, --only, --add/--later, --suggested")

    def check(pairs):
        for repo, comp in pairs:
            if repo not in comps:
                sys.exit(f"ERROR: no connected repository {repo!r} (connected: {', '.join(sorted(comps))})")
            if comp not in comps[repo]:
                sys.exit(f"ERROR: {repo} has no component {comp!r}. Components: {', '.join(comps[repo]) or 'none'}"
                         " (run `estimate` to list them)")
        return pairs

    if args.all:
        deferred = {n: [] for n in names}
    elif args.only:
        keep = set(check(parse_rc(args.only)))
        deferred = {n: [c for c in comps[n] if (n, c) not in keep] for n in names}
    elif args.add or args.later:
        add, later = set(check(parse_rc(args.add or []))), set(check(parse_rc(args.later or [])))
        deferred = {n: sorted(({c for c in current.get(n, set()) if c in comps[n]} - {c for r, c in add if r == n})
                              | {c for r, c in later if r == n}) for n in names}
    elif args.suggested:
        if not est:
            sys.exit("ERROR: run `estimate` first — the suggestion comes from it")
        batch = ops.suggest_batch(est)
        deferred = {n: [c for c in comps[n] if (n, c) not in batch] for n in names}
    else:
        deferred = None                                   # build with the current choice
    try:
        changes = ops.plan_deferred(deferred) if deferred is not None else []
    except ValueError as e:
        sys.exit(f"ERROR: {e}")
    final = {n: set(deferred[n]) if deferred is not None else current.get(n, set()) for n in names}
    included = lambda n, c: c["path"] not in final.get(n, set())
    plan = ops.clean_plan(ops.work_plan(est, included, args.fresh or ())) if est else {}
    expected = ops.total_seconds(est, included, args.fresh or ()) if est else None
    left_out = sorted((n, c) for n in names for c in final.get(n, set()))
    print(f"Creating the GraphRAG — {sum(len(v) - len(final.get(n, ())) for n, v in comps.items())} of "
          f"{sum(len(v) for v in comps.values())} components"
          + (f", estimated {ops.fmt_duration(expected)}" if expected else "")
          + (f"; starting from scratch: {', '.join(args.fresh)}" if args.fresh else "") + ".", flush=True)
    ops.write_settings(changes)
    try:
        res = ops.run_worker("verify.py", ops.verify_args(args.fresh or (), plan),
                             on_message=lambda m: print("  " + m, flush=True), on_bar=Progress(expected))
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    print("\n✓ GraphRAG created and linked")
    for r in res["repos"]:
        print(f"  {r['name']}: {r['files']} files, {r['units']} units, {r['links']} links"
              + (f"; IDL interfaces defined: {', '.join(r['interfaces_defined'])}" if r["interfaces_defined"] else ""))
    for i in res["interfaces"]:
        where = lambda x: x["repo"] + (f"/{x['process']}" if x.get("process") not in (None, "", "-") else "")
        prov = "; ".join(sorted({where(p) for p in i["providers"]}))
        cons = "; ".join(sorted({where(c) for c in i["consumers"]}))
        print(f"  {i['interface']}: " + (f"served by {prov}" if prov else "no provider found") + "; "
              + (f"called by {cons}" if cons else "no caller found"))
    if left_out:
        print(f"\nStill left for later ({len(left_out)}): " + ", ".join(f"{n}:{c}" for n, c in left_out[:12])
              + (" …" if len(left_out) > 12 else "")
              + "\n  → build --all   (everything remaining), or build --add REPO:COMPONENT …")
    else:
        print("\nEverything is in the GraphRAG. Reload VS Code so Copilot uses it.")
    if est:
        LAST_ESTIMATE.unlink(missing_ok=True)             # states changed: estimate again for the next choice
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="connected repositories and their GraphRAG")
    f = sub.add_parser("folders", help="folders to index; exclude or include folders")
    f.add_argument("repo")
    f.add_argument("--exclude", action="append", metavar="PATH", help="folder or file pattern never to index")
    f.add_argument("--include", action="append", metavar="PATH", help="index an excluded folder again")
    f.add_argument("--exclude-all", action="store_true", help="exclude every top-level folder")
    f.add_argument("--include-all", action="store_true", help="remove every exclusion")
    c = sub.add_parser("chunking", help="how code is cut into searchable pieces")
    c.add_argument("repo")
    c.add_argument("--mode", choices=("graph", "sliding", "hybrid"))
    c.add_argument("--lines", type=int, help="chunk (window) size in lines")
    c.add_argument("--overlap", type=int, help="lines shared by neighbouring chunks")
    k = sub.add_parser("skip", help="content never indexed (header words, regular expressions)")
    k.add_argument("repo")
    k.add_argument("--header-word", action="append", metavar="W", help="skip the top-of-file comment block containing W")
    k.add_argument("--pattern", action="append", metavar="REGEX", help="skip every match (multi-line mode)")
    k.add_argument("--clear", action="store_true", help="remove the existing rules first")
    k.add_argument("--preview-only", action="store_true", help="show what would be skipped; save nothing")
    e = sub.add_parser("estimate", help="time to create the GraphRAG, per component")
    e.add_argument("--json", action="store_true", help="print the raw estimate as JSON")
    b = sub.add_parser("build", help="create or update the GraphRAG, with a progress bar")
    b.add_argument("--all", action="store_true", help="every component (including all left for later)")
    b.add_argument("--only", action="append", metavar="R:C", help="exactly these components; the rest is left for later")
    b.add_argument("--add", action="append", metavar="R:C", help="add these components to the current choice")
    b.add_argument("--later", action="append", metavar="R:C", help="leave these components for a later batch")
    b.add_argument("--suggested", action="store_true", help="the first batch suggested by the last estimate")
    b.add_argument("--fresh", action="append", metavar="REPO", help="start this repository from scratch")
    args = ap.parse_args()
    _require_supported_os("graphrag.py")
    _require_venv("graphrag.py")
    ops = _ops()
    return {"status": cmd_status, "folders": cmd_folders, "chunking": cmd_chunking, "skip": cmd_skip,
            "estimate": cmd_estimate, "build": cmd_build}[args.cmd](ops, args)


if __name__ == "__main__":
    sys.exit(main())
