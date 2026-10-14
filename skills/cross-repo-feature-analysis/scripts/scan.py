#!/usr/bin/env python3
"""
Inspect repos and propose what the RAG index should include. Read-only.

For each repo, prints:
  - doc folders with indexable .md files (README.md is never indexed)
  - doc files in formats the index cannot read (.rst, .adoc, .txt, ...)
  - source folders with indexable code, with test and generated folders flagged
  - code-like file types the default index skips (candidates for --ext)
  - the current indexing config, if the repo is already connected
  - suggested configure.py flags

The index walks folders recursively with no exclusions, so it never suggests a
repo root or any folder that contains node_modules/, build output, venvs, etc.

Usage:
  python scan.py /abs/path/repo-a [name=]/abs/path/repo-b ...
"""
import re
import shlex
import sys
from collections import Counter
from pathlib import Path

# Must match _DEFAULT_EXTENSIONS in repo-agent/mcp/repo_rag.py
INDEXED_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h", ".cc", ".cxx",
    ".cs", ".rb", ".swift", ".kt", ".php", ".scala",
    ".thrift", ".proto", ".graphql",
}
# Code-like types the index skips by default; worth offering as extra_extensions
EXTRA_CANDIDATES = {
    ".vue", ".svelte", ".dart", ".sql", ".sh", ".tf", ".lua", ".ex", ".exs",
    ".erl", ".hs", ".ml", ".clj", ".r", ".jl", ".m", ".mm", ".groovy", ".pl",
    ".hpp", ".hh", ".kts", ".fs", ".vb", ".zig", ".nim", ".sol", ".avsc",
}
UNREADABLE_DOC_EXT = {".rst", ".adoc", ".asciidoc", ".txt", ".org", ".tex"}

# Never index these: dependencies, build output, environments, VCS, caches
NOISE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor", "third_party",
    "third-party", "external", "deps", ".venv", "venv", "env", ".env", ".tox", ".nox",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "dist", "build", "out", "target", "bin", "obj", ".next", ".nuxt", ".svelte-kit",
    ".gradle", ".idea", ".vscode", "coverage", "htmlcov", ".chroma_db", "Pods",
    "DerivedData", ".terraform", "generated", "gen", ".cache", ".turbo", ".parcel-cache",
}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "integration_tests"}
AGENT_DIRS = {"mcp", ".claude", ".cursor", ".github"}  # framework / tool files, not project code
MONOREPO_PARENTS = {"packages", "modules", "services", "apps", "libs", "components", "crates", "plugins"}


def _walk(root: Path):
    """Yield files under root, pruning noise dirs (what a *sane* index would see)."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name not in NOISE_DIRS and not (d == root and e.name in AGENT_DIRS):
                    stack.append(e)
            else:
                yield e


def _contains_noise(d: Path, depth: int = 4) -> list[str]:
    """Noise dirs inside d — the real index would descend into these."""
    found, stack = [], [(d, 0)]
    while stack and len(found) < 3:
        cur, lvl = stack.pop()
        if lvl >= depth:
            continue
        try:
            for e in cur.iterdir():
                if e.is_dir() and not e.is_symlink():
                    if e.name in NOISE_DIRS:
                        found.append(str(e.relative_to(d)))
                    else:
                        stack.append((e, lvl + 1))
        except OSError:
            pass
    return found


def _rel(p: Path, root: Path) -> str:
    return "./" + p.relative_to(root).as_posix()


def scan(root: Path) -> dict:
    md_by_dir: Counter = Counter()
    other_docs: Counter = Counter()
    code_by_top: dict[str, Counter] = {}
    skipped_ext: Counter = Counter()
    root_code = Counter()

    for f in _walk(root):
        rel = f.relative_to(root)
        top = rel.parts[0] if len(rel.parts) > 1 else None
        ext = f.suffix.lower()
        if ext == ".md":
            if f.name.lower() != "readme.md":
                # group by top-level folder: the index walks recursively, so ./docs
                # already covers ./docs/guides — listing both would index it twice
                md_by_dir[top or "."] += 1
        elif ext in UNREADABLE_DOC_EXT and top and re.search(r"doc|adr|wiki|spec|design", top, re.I):
            other_docs[ext] += 1
        elif f.suffix in INDEXED_EXT:
            if top is None:
                root_code[f.suffix] += 1
            else:
                code_by_top.setdefault(top, Counter())[f.suffix] += 1
        elif ext in EXTRA_CANDIDATES:
            skipped_ext[ext] += 1

    # Source folders: top-level dirs with code. Expand monorepo parents into a glob
    # when their children share a src/ layout.
    src_dirs, test_dirs = [], []
    for top, counts in sorted(code_by_top.items(), key=lambda kv: -sum(kv[1].values())):
        d = root / top
        n = sum(counts.values())
        if top.lower() in TEST_DIRS:
            test_dirs.append((f"./{top}", n))
            continue
        path, noise = f"./{top}", _contains_noise(d)
        kids_with_src = [k for k in d.iterdir() if k.is_dir() and (k / "src").is_dir()]
        # Use ./<top>/*/src for monorepo layouts, or to step around noise such as
        # apps/site/node_modules when each child keeps its code in src/
        if kids_with_src and ((top in MONOREPO_PARENTS and len(kids_with_src) >= 2) or noise):
            src_noise = [x for k in kids_with_src for x in _contains_noise(k / "src")]
            if not src_noise:
                path, noise = f"./{top}/*/src", []
        src_dirs.append({"path": path, "files": n, "ext": counts, "noise": noise})

    doc_dirs = [{"path": "./" + k if k != "." else ".", "files": v, "noise": _contains_noise(root / k) if k != "." else []}
                for k, v in md_by_dir.most_common()]

    return {"doc_dirs": doc_dirs, "other_docs": other_docs, "src_dirs": src_dirs,
            "test_dirs": test_dirs, "root_code": root_code, "skipped_ext": skipped_ext,
            "has_readme": (root / "README.md").exists()}


def _current_config(root: Path) -> dict | None:
    cfg = root / "mcp" / "config.yaml"
    if not cfg.exists():
        return None
    try:
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
    except Exception:
        return None
    return {k: data.get(k) for k in ("repo_name", "src_path", "knowledge_path", "extra_extensions")}


def report(raw: str) -> None:
    m = re.match(r"^([A-Za-z0-9_-]+)=(.+)$", raw)
    name, path = (m.group(1), m.group(2)) if m else (None, raw)
    root = Path(path).expanduser().resolve()
    if name is None:
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", root.name).strip("-").lower() or "repo"
    print(f"\n━━ {name}  ({root})")
    if not root.is_dir():
        print("   ERROR: path does not exist")
        return

    r = scan(root)
    current = _current_config(root)
    if current:
        print(f"   current config: src_path={current['src_path']}  knowledge_path={current['knowledge_path']}"
              f"  extra_extensions={current['extra_extensions']}")

    print("   Docs (.md, README.md excluded):")
    usable_docs = []
    for d in r["doc_dirs"]:
        if d["path"] == ".":
            print(f"     · {d['files']:4d} .md at repo root — move into knowledge/ or docs/ to index them")
        elif d["path"] == "./knowledge":
            print(f"     ✓ {d['files']:4d}  ./knowledge (always indexed)")
        elif d["noise"]:
            print(f"     ✗ {d['files']:4d}  {d['path']}  — contains {', '.join(d['noise'])}; unsafe to index whole folder")
        else:
            print(f"     ✓ {d['files']:4d}  {d['path']}")
            usable_docs.append(d["path"])
    if not r["doc_dirs"]:
        print("     (none) — seed knowledge/ with a drafted architecture.md")
    if r["has_readme"]:
        print("     · README.md is never indexed — summarise it into knowledge/ when seeding docs")
    if r["other_docs"]:
        fmt = ", ".join(f"{n} {e}" for e, n in r["other_docs"].most_common())
        print(f"     · {fmt} in doc folders — not indexable (only .md); convert key ones to .md")

    print("   Source (indexable extensions):")
    usable_src = []
    for s in r["src_dirs"]:
        top_ext = ", ".join(f"{e}×{n}" for e, n in s["ext"].most_common(3))
        if s["noise"]:
            print(f"     ✗ {s['files']:5d}  {s['path']:22s} {top_ext}  — contains {', '.join(s['noise'])};"
                  " point at its subfolders instead")
        else:
            print(f"     ✓ {s['files']:5d}  {s['path']:22s} {top_ext}")
            usable_src.append(s["path"])
    for t, n in r["test_dirs"]:
        print(f"     · {n:5d}  {t:22s} tests — usually left out; include if tests document behaviour")
    if r["root_code"]:
        n = sum(r["root_code"].values())
        print(f"     · {n:5d}  (repo root files)        not indexable without indexing the whole repo")
    if not r["src_dirs"] and not r["test_dirs"]:
        print("     (no source found)")
    if r["skipped_ext"]:
        fmt = ", ".join(f"{e}×{n}" for e, n in r["skipped_ext"].most_common(6))
        print(f"   Not indexed by default: {fmt}  — add with --ext if they hold real logic")

    # Quoted: globs like ./packages/*/src must reach configure.py unexpanded
    flags = [("--repo", f"{name}={root}")]
    # Biggest source folders first; keep the suggestion focused
    flags += [("--src", f"{name}={p}") for p in usable_src[:4]] or [("--src", f"{name}=null")]
    flags += [("--knowledge", f"{name}={p}") for p in usable_docs[:4]]
    flags += [("--ext", f"{name}={e}") for e, n in r["skipped_ext"].most_common(3) if n >= 5]
    print("   Suggested flags:")
    for flag, value in flags:
        print(f"     {flag} {shlex.quote(value)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        report(arg)
    print()
