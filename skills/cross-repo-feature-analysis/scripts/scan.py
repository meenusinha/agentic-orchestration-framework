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


def report(raw: str, fw: Path) -> list[tuple[str, str]]:
    from codegraph.extract import extract
    from codegraph.indexer import IndexConfig, collect_files
    from codegraph.structure import resolve

    m = re.match(r"^([A-Za-z0-9_-]+)=(.+)$", raw)
    name, path = (m.group(1), m.group(2)) if m else (None, raw)
    root = Path(path).expanduser().resolve()
    if name is None:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", root.name).strip("-").lower() or "repo"
    print(f"\n━━ {name}  ({root})")
    if not root.is_dir():
        print("   ERROR: path does not exist")
        return []
    nested = sorted(p.parent.name for p in root.glob("*/.git"))
    if not (root / ".git").exists() and nested:
        print(f"   ⚠ Not a repo itself — it contains separate repos: {', '.join(nested)}. Connect those individually.")
        return []

    cfg_now = _current_config(root)
    cfg = IndexConfig(name, root, [root], cfg_now.get("file_types"), cfg_now.get("structure"))
    files, unknown, secrets = collect_files(cfg)
    rels = [rel for rel, _, _ in files]
    st = resolve(root, rels, cfg.structure)

    langs = collections.Counter(lang for _, _, lang in files)
    print("   Languages: " + (", ".join(f"{l} {n}" for l, n in langs.most_common()) or "(none recognised)"))
    if secrets:
        print(f"   Secret-store files (never indexed): {secrets}")

    build_files = sorted({t.defined_in for t in st.targets.values()})
    has_ccdb = (root / "compile_commands.json").exists() or any(root.glob("*/compile_commands.json"))
    print(f"   Build: {', '.join(build_files) or 'no Makefile/CMakeLists targets found'}"
          f"{' + compile_commands.json' if has_ccdb else ''}")
    for t in sorted(st.targets.values(), key=lambda t: (t.kind, t.name)):
        links = f", links {', '.join(sorted(t.links)[:6])}" if t.links else ""
        print(f"     {t.kind:10s} {t.name}: {len(t.sources)} source file(s){links}")

    idl = [(rel, ab) for rel, ab, lang in files if lang == "idl"]
    if idl:
        ifaces = []
        for rel, ab in idl:
            try:
                tree = extract(rel, ab.read_text(errors="replace"), "idl")
            except Exception:
                continue
            ifaces += [f"{n.qualname} ({sum(c.kind == 'idl_operation' for c in n.children)} ops)"
                       for n in tree.walk() if n.kind == "idl_interface"]
        print(f"   IDL: {len(idl)} file(s); interfaces: {', '.join(ifaces[:8]) or 'none'}"
              f"{' …' if len(ifaces) > 8 else ''}")
    if st.generated:
        first = next(iter(st.generated))
        print(f"   Generated code: {len(st.generated)} file(s), e.g. {first} ({st.generated[first]})")

    print("   Proposed structure (role per folder; correct anything wrong with --role):")
    by_top = collections.defaultdict(collections.Counter)
    for rel in rels:
        parts = PurePosixPath(rel).parts
        top = parts[0] if len(parts) > 1 else "(repo root)"
        by_top[top][st.roles.get(rel, "implementation")] += 1
    for top, roles in sorted(by_top.items()):
        role, _ = roles.most_common(1)[0]
        mixed = f"  (also {', '.join(f'{r} {c}' for r, c in roles.most_common()[1:])})" if len(roles) > 1 else ""
        note = "  — signatures only" if role == "external" else ""
        label = top + ("/" if top != "(repo root)" else "")
        print(f"     {label:24s} {role:15s} {sum(roles.values())} file(s){mixed}{note}")

    asks = []
    if unknown:
        print("   UNKNOWN FILE TYPES — ask the developer what these contain:")
        for ext, samples in sorted(unknown.items()):
            what, suggestion = _hint(root / samples[0])
            print(f"     {ext:10s} {len(samples)} file(s), e.g. {samples[0]} — looks like {what}")
            asks.append((ext, suggestion))

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
    ap.add_argument("repos", nargs="+", metavar="[NAME=]PATH")
    a = ap.parse_args()
    fw = Path(a.framework).expanduser().resolve()
    _load_framework(fw)
    for r in a.repos:
        report(r, fw)
    print()
