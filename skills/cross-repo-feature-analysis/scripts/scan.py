#!/usr/bin/env python3
"""
Inspect repos and propose what the RAG index should include. Read-only.

For each repo, prints:
  - doc folders with indexable .md files (README.md is never indexed)
  - doc files in formats the index cannot read (.rst, .adoc, .txt, ...)
  - core source folders, with tests, examples/demos, git submodules and
    nested repos set aside
  - code-like file types the default index skips (candidates for --ext)
  - the current indexing config, if the repo is already connected
  - suggested configure.py flags

The index walks each configured folder recursively with no exclusions. A folder
is flagged ✗ only when that walk would pick up indexable files from dependency,
build, cache or environment folders (node_modules/, .venv/, build/, ...); a
cleaner */src glob is suggested instead where the layout allows it.

Usage:
  python scan.py /abs/path/repo-a [name=]/abs/path/repo-b ...
"""
import os
import re
import shlex
import sys
from collections import Counter, defaultdict
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
    ".erl", ".hs", ".ml", ".clj", ".r", ".R", ".jl", ".m", ".mm", ".groovy", ".pl",
    ".hpp", ".hh", ".kts", ".fs", ".vb", ".zig", ".nim", ".sol", ".avsc",
}
UNREADABLE_DOC_EXT = {".rst", ".adoc", ".asciidoc", ".txt", ".org", ".tex"}

# Dependencies, build output, environments and caches: never worth indexing
NOISE_DIRS = {
    "node_modules", "bower_components", "vendor", "third_party", "third-party",
    ".venv", "venv", "env", ".env", ".tox", ".nox", "site-packages",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".cache",
    "dist", "build", "out", "target", "bin", "obj", ".next", ".nuxt", ".svelte-kit",
    ".gradle", ".idea", ".vscode", "coverage", "htmlcov", ".chroma_db", "Pods",
    "DerivedData", ".terraform", "generated", ".turbo", ".parcel-cache",
}
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing", "e2e", "integration_tests"}
EXAMPLE_DIRS = {"demo", "demos", "example", "examples", "sample", "samples", "benchmark",
                "benchmarks", "bench", "tutorial", "tutorials", "notebooks", "doc", "docs"}
AGENT_DIRS = {"mcp", ".claude", ".cursor", ".github"}  # framework / tool files at repo root
MONOREPO_PARENTS = {"packages", "modules", "services", "apps", "libs", "components", "crates", "plugins"}


def _kind(name: str, suffix: str) -> str | None:
    if suffix == ".md":  # repo_rag globs "*.md" and skips README.md
        return None if name.lower() == "readme.md" else "md"
    if suffix in INDEXED_EXT:
        return "code"
    if suffix in EXTRA_CANDIDATES:
        return "extra"
    if suffix.lower() in UNREADABLE_DOC_EXT:
        return "otherdoc"
    return None


def _submodules(root: Path) -> set[str]:
    gm = root / ".gitmodules"
    if not gm.exists():
        return set()
    return {m.strip() for m in re.findall(r"(?m)^\s*path\s*=\s*(.+)$", gm.read_text(errors="ignore"))}


def scan(root: Path) -> dict:
    """One unpruned walk (like the indexer's rglob), recording which files are clean
    and which come from noise folders or nested repos."""
    submods = _submodules(root)
    clean = defaultdict(Counter)        # top -> Counter(kind)
    noisy = defaultdict(Counter)        # top -> Counter(noise label) for code/md files
    noisy_kind = defaultdict(Counter)   # top -> Counter(kind) of noisy files
    src_clean, src_noisy = Counter(), Counter()   # top -> files under top/*/src
    code_ext = defaultdict(Counter)     # top -> Counter(ext) of clean code
    extra_ext = defaultdict(Counter)    # top -> Counter(ext) of clean code-like files not indexed by default
    other_docs = Counter()
    nested_repos: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        rel_dir = d.relative_to(root)
        if ".git" in dirnames:
            dirnames.remove(".git")  # VCS internals: never indexable, can be huge
        if rel_dir.parts and (".git" in filenames or (d / ".git").is_dir()):
            nested_repos.add(rel_dir.as_posix())
        for fn in filenames:
            kind = _kind(fn, Path(fn).suffix)
            if kind is None:
                continue
            parts = rel_dir.parts
            top = parts[0] if parts else None
            label = None
            for i, part in enumerate(parts):
                prefix = "/".join(parts[:i + 1])
                if prefix in submods or prefix in nested_repos:
                    label = f"{prefix} (separate repo)"
                    break
                if part in NOISE_DIRS:
                    label = prefix
                    break
            if top in AGENT_DIRS:
                continue
            in_src = len(parts) >= 3 and parts[2] == "src"
            if label:
                if kind in ("code", "md"):
                    noisy[top][label] += 1
                    noisy_kind[top][kind] += 1
                    if in_src:
                        src_noisy[top] += 1
                continue
            clean[top][kind] += 1
            if kind == "code":
                code_ext[top][Path(fn).suffix] += 1
                if in_src:
                    src_clean[top] += 1
            elif kind == "extra":
                extra_ext[top][Path(fn).suffix] += 1
            elif kind == "otherdoc":
                other_docs[Path(fn).suffix.lower()] += 1

    return {"clean": clean, "noisy": noisy, "noisy_kind": noisy_kind, "src_clean": src_clean,
            "src_noisy": src_noisy, "code_ext": code_ext, "extra_ext": extra_ext,
            "other_docs": other_docs, "submodules": submods, "nested": nested_repos,
            "is_git": (root / ".git").exists(), "has_readme": (root / "README.md").exists()}


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


def _noise_note(counter: Counter) -> str:
    return ", ".join(lbl for lbl, _ in counter.most_common(3))


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
    top_repos = sorted(n for n in r["nested"] | r["submodules"] if "/" not in n)
    if not r["is_git"] and top_repos:
        others = sorted(e.name for e in root.iterdir()
                        if e.is_dir() and not e.name.startswith(".") and e.name not in top_repos)
        print(f"   ⚠ Not a repo itself — it contains separate repos: {', '.join(top_repos)}.")
        if others:
            print(f"     Other folders here: {', '.join(others)}.")
        print("     Connect the repos individually instead (run scan.py on each).")
        return

    current = _current_config(root)
    if current:
        print(f"   current config: src_path={current['src_path']}  knowledge_path={current['knowledge_path']}"
              f"  extra_extensions={current['extra_extensions']}")

    def category(top: str) -> str:
        if top in r["submodules"]:
            return "submodule"
        if top in r["nested"]:
            return "nested"
        if top.lower() in TEST_DIRS:
            return "tests"
        if top.lower() in EXAMPLE_DIRS:  # code in doc/ is usually Sphinx config, not product code
            return "examples"
        return "core"

    tops = set(r["clean"]) | set(r["noisy"])
    vendored = sorted(t for t in tops if t and category(t) in ("submodule", "nested"))

    # ── Docs ──────────────────────────────────────────────────────────────────
    print("   Docs (.md, README.md excluded):")
    usable_docs = []
    doc_tops = sorted((t for t in tops if t and r["clean"][t]["md"] and category(t) not in ("submodule", "nested")),
                      key=lambda t: -r["clean"][t]["md"])
    for t in doc_tops:
        n = r["clean"][t]["md"]
        if t == "knowledge":
            print(f"     ✓ {n:4d}  ./knowledge (always indexed)")
        elif r["noisy_kind"][t]["md"]:
            print(f"     ✗ {n:4d}  ./{t}  — would also pull in .md from {_noise_note(r['noisy'][t])}")
        elif category(t) == "tests":
            print(f"     · {n:4d}  ./{t}  (inside tests — usually left out)")
        else:
            print(f"     ✓ {n:4d}  ./{t}")
            usable_docs.append(f"./{t}")
    if r["clean"][None]["md"]:
        print(f"     · {r['clean'][None]['md']:4d}  .md at repo root — not indexable on their own; "
              "move or summarise into knowledge/")
    if not doc_tops and not r["clean"][None]["md"]:
        print("     (none) — seed knowledge/ with a drafted architecture.md")
    if r["has_readme"]:
        print("     · README.md is never indexed — summarise it into knowledge/ when seeding docs")
    if r["other_docs"]:
        fmt = ", ".join(f"{n} {e}" for e, n in r["other_docs"].most_common())
        print(f"     · {fmt} files — not indexable (only .md); summarise key ones into knowledge/")

    # ── Source ────────────────────────────────────────────────────────────────
    print("   Source (indexable extensions):")
    usable_src = []
    code_tops = sorted((t for t in tops if t and (r["clean"][t]["code"] or r["noisy_kind"][t]["code"])),
                       key=lambda t: -r["clean"][t]["code"])
    for t in code_tops:
        n = r["clean"][t]["code"]
        exts = ", ".join(f"{e}×{c}" for e, c in r["code_ext"][t].most_common(3))
        cat = category(t)
        if cat in ("submodule", "nested"):
            continue  # reported together below
        if cat == "tests":
            print(f"     · {n:5d}  ./{t:22s} tests — usually left out; include if they document behaviour")
            continue
        if cat == "examples":
            print(f"     · {n:5d}  ./{t:22s} examples/docs — usually left out")
            continue
        if not n:
            continue
        has_src_kids = r["src_clean"][t] > 0
        glob_ok = has_src_kids and r["src_noisy"][t] == 0
        if r["noisy_kind"][t]["code"]:
            if glob_ok:
                print(f"     ✓ {r['src_clean'][t]:5d}  ./{t + '/*/src':22s} {exts}  "
                      f"(not ./{t}: it would pull in {_noise_note(r['noisy'][t])})")
                usable_src.append(f"./{t}/*/src")
            else:
                print(f"     ✗ {n:5d}  ./{t:22s} {exts}  — would also pull in code from "
                      f"{_noise_note(r['noisy'][t])}; pick its clean subfolders by hand")
        elif t in MONOREPO_PARENTS and glob_ok and r["src_clean"][t] == n:
            print(f"     ✓ {n:5d}  ./{t + '/*/src':22s} {exts}")
            usable_src.append(f"./{t}/*/src")
        else:
            print(f"     ✓ {n:5d}  ./{t:22s} {exts}")
            usable_src.append(f"./{t}")
    for t in vendored:
        n = r["clean"][t]["code"] + r["noisy_kind"][t]["code"]
        print(f"     · {n:5d}  ./{t:22s} separate repo / git submodule — leave out unless you own it")

    root_code = r["clean"][None]["code"]
    root_noise = sum(sum(c.values()) for c in r["noisy"].values())
    whole_repo_ok = root_code and not usable_src and root_noise == 0
    if root_code:
        if whole_repo_ok:
            print(f"     ✓ {root_code:5d}  . (repo root)              flat repo — indexing the whole repo "
                  "is fine here (also picks up the framework's mcp/*.py once installed)")
            usable_src.append(".")
        else:
            print(f"     · {root_code:5d}  (repo root files)        only indexable by indexing the whole repo")
    if not code_tops and not root_code:
        print("     (no source found)")
    # --ext only matters for files inside the folders being indexed
    indexed_tops = {p.split("/")[1] for p in usable_src if p != "."}
    in_scope, out_scope = Counter(), Counter()
    for t, c in r["extra_ext"].items():
        if "." in usable_src or t in indexed_tops:
            in_scope.update(c)
        else:
            out_scope.update(c)
    if in_scope:
        fmt = ", ".join(f"{e}×{n}" for e, n in in_scope.most_common(6))
        print(f"   Not indexed by default: {fmt}  — add with --ext if they hold real logic")
    if out_scope:
        fmt = ", ".join(f"{e}×{n}" for e, n in out_scope.most_common(4))
        print(f"   (also {fmt} outside the suggested source folders — --ext would not reach them)")

    # ── Suggested flags ───────────────────────────────────────────────────────
    # Quoted: globs like ./packages/*/src must reach configure.py unexpanded
    flags = [("--repo", f"{name}={root}")]
    flags += [("--src", f"{name}={p}") for p in usable_src] or [("--src", f"{name}=null")]
    flags += [("--knowledge", f"{name}={p}") for p in usable_docs]
    flags += [("--ext", f"{name}={e}") for e, n in in_scope.most_common(3) if n >= 5]
    print("   Suggested flags (a starting point — review with the developer):")
    for flag, value in flags:
        print(f"     {flag} {shlex.quote(value)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        report(arg)
    print()
