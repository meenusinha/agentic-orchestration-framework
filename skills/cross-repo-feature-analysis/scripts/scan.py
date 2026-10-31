#!/usr/bin/env python3
"""
Inspect repos before connecting them. Read-only.

Uses the framework's own indexer, so what it reports is exactly what will be
indexed. For each repo it prints:
  - languages found (files per language)
  - build systems and the processes / libraries they build (Makefiles,
    compile_commands.json, CMakeLists.txt — read, never executed)
  - IDL files and the interfaces they define
  - generated code and where it comes from
  - the proposed structure: the role of each top-level folder
    (interface, implementation, internal, external, test, generated, docs, …)
  - UNKNOWN FILE TYPES the indexer cannot classify, with a hint about what
    they look like — ask the developer what each one contains
  - suggested configure.py flags

Usage:
  <framework>/.venv/bin/python scan.py --framework <framework> /abs/repo-a [name=]/abs/repo-b …
"""
import argparse
import collections
import re
import shlex
import sys
from pathlib import Path, PurePosixPath

def _require_venv(what: str) -> None:
    """The framework installs, configures and runs inside a Python virtual environment by
    default. Set AO_ALLOW_NO_VENV=1 to override deliberately (a warning is printed)."""
    if sys.prefix == sys.base_prefix:
        import os
        if os.environ.get("AO_ALLOW_NO_VENV", "").strip().lower() in ("1", "true", "yes"):
            print(f"WARNING: {what} is running outside a virtual environment ({sys.executable}) "
                  "because AO_ALLOW_NO_VENV is set.", file=sys.stderr)
            return
        sys.exit(f"ERROR: {what} must run inside a Python virtual environment (venv).\n"
                 f"       Create it once:  python3 -m venv <framework>/.venv\n"
                 f"       Then use:        <framework>/.venv/bin/python …   "
                 f"(Windows: <framework>\\.venv\\Scripts\\python.exe)\n"
                 f"       To run without a venv on purpose, set AO_ALLOW_NO_VENV=1.")

_require_venv("scan.py")


def _load_framework(fw: Path):
    mcp_dir = fw / "repo-agent" / "mcp"
    if not (mcp_dir / "codegraph").is_dir():
        raise SystemExit(f"ERROR: {fw} does not contain repo-agent/mcp/codegraph")
    sys.path.insert(0, str(mcp_dir))


def _current_config(root: Path) -> dict:
    cfg = root / "mcp" / "config.yaml"
    if not cfg.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(cfg.read_text()) or {}
    except Exception:
        return {}


def _hint(path: Path) -> tuple[str, str]:
    """(what it looks like, suggested --file-type value) for an unknown file type."""
    from codegraph.idl import looks_like_idl
    try:
        text = path.read_text(errors="replace")[:20000]
    except OSError:
        return "unreadable", "ignore"
    if looks_like_idl(text, path.name):
        return "interface definitions (IDL-like: interface blocks declaring operations)", "interface:idl"
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith(("#", "//", ";", "--"))]
    kv = sum(bool(re.match(r"^\s*[\w.\-]+\s*(=|:|\s)\s*\S", l)) for l in lines)
    code = len(re.findall(r"[{};]\s*$|^\s*(def|function|sub|class|struct|void|int|return|if|for|while)\b",
                          text, re.M))
    if lines and kv / len(lines) > 0.7 and code < 3:
        return "configuration (key / value lines)", "config:text"
    if code >= 3:
        return "source code — ask which language (c, cpp, python, perl, matlab, …)", "implementation:<language>"
    if re.search(r"^#{1,6}\s|\n={3,}\n", text, re.M):
        return "documentation (markdown-like)", "docs:markdown"
    return "plain text", "docs:text"


def repo_name_for(raw: str) -> tuple[str, Path]:
    m = re.match(r"^([A-Za-z0-9_-]+)=(.+)$", raw)
    name, path = (m.group(1), m.group(2)) if m else (None, raw)
    root = Path(path).expanduser().resolve()
    if name is None:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", root.name).strip("-").lower() or "repo"
    return name, root


def analyze(name: str, root: Path) -> dict:
    """Structured scan of one repo (used by the setup GUI and by report())."""
    from codegraph.extract import extract
    from codegraph.indexer import IndexConfig, collect_files
    from codegraph.structure import resolve

    out = {"name": name, "path": str(root), "ok": True}
    if not root.is_dir():
        return dict(out, ok=False, error="path does not exist")
    nested = sorted(p.parent.name for p in root.glob("*/.git"))
    if not (root / ".git").exists() and nested:
        return dict(out, ok=False, error="not a repo itself — it contains separate repos: " + ", ".join(nested),
                    nested_repos=nested)
    cfg_now = _current_config(root)
    cfg = IndexConfig(name, root, [root], cfg_now.get("file_types"), cfg_now.get("structure"))
    files, unknown, secrets = collect_files(cfg)
    rels = [rel for rel, _, _ in files]
    st = resolve(root, rels, cfg.structure)
    out["languages"] = dict(collections.Counter(lang for _, _, lang in files).most_common())
    out["secret_files"] = secrets
    out["build_files"] = sorted({t.defined_in for t in st.targets.values()})
    out["compile_db"] = (root / "compile_commands.json").exists() or any(root.glob("*/compile_commands.json"))
    out["targets"] = [{"name": t.name, "kind": t.kind, "sources": len(t.sources), "links": sorted(t.links)}
                      for t in sorted(st.targets.values(), key=lambda t: (t.kind, t.name))]
    ifaces = []
    for rel, ab, lang in files:
        if lang != "idl":
            continue
        try:
            tree = extract(rel, ab.read_text(errors="replace"), "idl")
        except Exception:
            continue
        ifaces += [{"name": n.qualname, "file": rel,
                    "operations": [c.name for c in n.children if c.kind == "idl_operation"]}
                   for n in tree.walk() if n.kind == "idl_interface"]
    out["idl_files"] = sum(1 for _, _, l in files if l == "idl")
    out["interfaces"] = ifaces
    out["generated"] = [{"file": f, "reason": r} for f, r in sorted(st.generated.items())]
    by_top = collections.defaultdict(collections.Counter)
    for rel in rels:
        parts = PurePosixPath(rel).parts
        by_top[parts[0] if len(parts) > 1 else ""][st.roles.get(rel, "implementation")] += 1
    out["folders"] = [{"folder": top, "role": roles.most_common(1)[0][0], "files": sum(roles.values()),
                       "mixed": dict(roles) if len(roles) > 1 else {}}
                      for top, roles in sorted(by_top.items())]
    out["unknown_types"] = []
    for ext, samples in sorted(unknown.items()):
        what, suggestion = _hint(root / samples[0])
        kind, _, parse_as = suggestion.partition(":")
        out["unknown_types"].append({"ext": ext, "count": len(samples), "sample": samples[0], "looks_like": what,
                                     "kind": kind, "parse_as": "" if parse_as.startswith("<") else parse_as,
                                     "needs_language": parse_as.startswith("<")})
    out["configured"] = bool(cfg_now)
    return out


def report(raw: str, fw: Path) -> list[tuple[str, str]]:
    name, root = repo_name_for(raw)
    a = analyze(name, root)
    print(f"\n━━ {name}  ({root})")
    if not a["ok"]:
        print(f"   ⚠ {a['error']}")
        return []
    print("   Languages: " + (", ".join(f"{l} {n}" for l, n in a["languages"].items()) or "(none recognised)"))
    if a["secret_files"]:
        print(f"   Secret-store files (never indexed): {a['secret_files']}")
    print(f"   Build: {', '.join(a['build_files']) or 'no Makefile/CMakeLists targets found'}"
          f"{' + compile_commands.json' if a['compile_db'] else ''}")
    for t in a["targets"]:
        links = f", links {', '.join(t['links'][:6])}" if t["links"] else ""
        print(f"     {t['kind']:10s} {t['name']}: {t['sources']} source file(s){links}")
    if a["idl_files"]:
        names = [f"{i['name']} ({len(i['operations'])} ops)" for i in a["interfaces"]]
        print(f"   IDL: {a['idl_files']} file(s); interfaces: {', '.join(names[:8]) or 'none'}"
              f"{' …' if len(names) > 8 else ''}")
    if a["generated"]:
        g = a["generated"][0]
        print(f"   Generated code: {len(a['generated'])} file(s), e.g. {g['file']} ({g['reason']})")
    print("   Proposed structure (role per folder; correct anything wrong with --role):")
    for f in a["folders"]:
        label = (f["folder"] + "/") if f["folder"] else "(repo root)"
        mixed = {k: v for k, v in f["mixed"].items() if k != f["role"]}
        extra = f"  (also {', '.join(f'{r} {c}' for r, c in mixed.items())})" if mixed else ""
        note = "  — signatures only" if f["role"] == "external" else ""
        print(f"     {label:24s} {f['role']:15s} {f['files']} file(s){extra}{note}")
    asks = []
    if a["unknown_types"]:
        print("   UNKNOWN FILE TYPES — ask the developer what these contain:")
        for u in a["unknown_types"]:
            print(f"     {u['ext']:10s} {u['count']} file(s), e.g. {u['sample']} — looks like {u['looks_like']}")
            asks.append((u["ext"], f"{u['kind']}:{u['parse_as'] or '<language>'}"))
    flags = [("--repo", f"{name}={root}"), ("--src", f"{name}=.")]
    for ext, suggestion in asks:
        flags.append(("--file-type", f"{name}={ext}:{suggestion}"))
    print("   Suggested flags (unknown types show a GUESS — confirm each with the developer):")
    for flag, value in flags:
        print(f"     {flag} {shlex.quote(value)}")
    return asks


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--framework", required=True, help="path to the agentic-orchestration-framework clone")
    ap.add_argument("--json", action="store_true", help="print one JSON document (for the setup GUI)")
    ap.add_argument("repos", nargs="+", metavar="[NAME=]PATH")
    a = ap.parse_args()
    fw = Path(a.framework).expanduser().resolve()
    _load_framework(fw)
    if a.json:
        import json
        print(json.dumps([analyze(*repo_name_for(r)) for r in a.repos]))
        sys.exit(0)
    for r in a.repos:
        report(r, fw)
    print()
