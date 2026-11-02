#!/usr/bin/env python3
"""
Connect repositories to the Agentic Orchestration framework in one step.

Does steps 2-5 of the README for the agent:
  1. Puts the embedding model in <framework>/models/ if it is missing — copied
     from the approved copy you point to (--model-from), hash-verified. It never
     downloads anything; without --model-from it stops and lists any usable copies
     it finds on this machine.
  2. Adds/updates each repo in <framework>/orchestrator/mcp/config.yaml
  3. Copies repo-agent/mcp/ and repo-agent/knowledge/ into each repo and
     fills in repo_name, display_name and what to index (src_path,
     knowledge_path, extra_extensions) in its mcp/config.yaml
  4. Runs orchestrator/setup.py to generate the client config for every repo

Safe to re-run: existing repos keep their settings unless a flag for them is
given, new ones are added. Run scan.py first to see what each repo should index.

Indexing flags are repeatable and per repo (NAME is the repo id):
  --src NAME=./server        source folder or glob; repeat for several; NAME=null for none
  --knowledge NAME=./docs    extra .md doc folder; ./knowledge is always included
  --ext NAME=.vue            extra file type to index as code
If a repo's indexing settings change, its .rag_index/ cache is deleted so the
index is rebuilt with the new settings on the next query.

Embedding model (only needed while <framework>/models/granite-embedding-small-english-r2 is missing):
  --model-from PATH          copy the approved model's files from an existing copy (hash-verified)
  --find-model               just list usable copies found on this machine, then exit

Run it with the framework venv's Python so the generated configs pin it:
  <framework>/.venv/bin/python configure.py --framework <framework> \\
      --repo /abs/path/frontend --repo backend=/abs/path/api --src backend=./app
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

def _require_supported_os(what: str) -> None:
    """Supported platforms: Windows and Linux. AO_ALLOW_UNSUPPORTED_OS=1 overrides this
    deliberately (e.g. to run the tests elsewhere); a warning is printed."""
    import os
    if sys.platform.startswith("linux") or sys.platform == "win32":
        return
    if os.environ.get("AO_ALLOW_UNSUPPORTED_OS", "").strip().lower() in ("1", "true", "yes"):
        print(f"WARNING: {what} is running on an unsupported platform ({sys.platform}) "
              "because AO_ALLOW_UNSUPPORTED_OS is set.", file=sys.stderr)
        return
    sys.exit(f"ERROR: {what} supports Windows and Linux only (this system: {sys.platform}).")


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

_require_supported_os("configure.py")
_require_venv("configure.py")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run this with the framework venv's Python.")
    sys.exit(1)

# Approved embedding model, pinned to an exact revision and to the SHA-256 of every file.
# Must match APPROVED_MODELS / APPROVED_MODEL_FILES in repo-agent/mcp/repo_rag.py.
MODEL_ID       = "ibm-granite/granite-embedding-small-english-r2"
MODEL_REVISION = "2ab6fa8ea2d674564defd37171ae19079b864b33"
MODEL_SHA256   = "dcbfaf2ee50d358763cbc0c08e5b045ecbf6aeb02dc0e86ffb910029bf9ebb5f"
MODEL_DIRNAME  = "granite-embedding-small-english-r2"
# Exactly the files sentence-transformers needs: safetensors weights + JSON config +
# tokenizer. Never pickle weights (pytorch_model.bin) or code (*.py).
MODEL_FILES    = [
    "config.json", "modules.json", "sentence_bert_config.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "model.safetensors", "1_Pooling/config.json",
]
MODEL_FILE_SHA256 = {
    "1_Pooling/config.json":     "7fae3727656cff4a760964a58f1e4b31695c97c75d2e26346df32a87d494bbca",
    "config.json":               "154518e6452854eb11123ce433d165f2773437aa0f36935d2204249b2c327198",
    "model.safetensors":         "dcbfaf2ee50d358763cbc0c08e5b045ecbf6aeb02dc0e86ffb910029bf9ebb5f",
    "modules.json":              "0580fad756940809b1bc064ce1e7c1275c8b0dd80869562fe0abeb6f7059cb97",
    "sentence_bert_config.json": "23aa234ef6021d513cdf56947c07aaa6977000d85dcd1fd423f650b3382a39ca",
    "special_tokens_map.json":   "ea97ecdbcc73713039d8d64dbb05e3689495c96657fbd9a18f5bed381be81049",
    "tokenizer.json":            "6c8aaa9a542084f2457eab775d4eeb51f92a70c0fd9de28d5edb0ddec3c08d30",
    "tokenizer_config.json":     "c16f1b07f93d05be753ec0acf8cb511d895a8bdb09669f6b156df81e3f96c2eb",
}
UNSAFE_MODEL_EXT = {".py", ".pyc", ".bin", ".pt", ".pth", ".pkl", ".pickle", ".ckpt", ".h5",
                    ".ot", ".onnx", ".msgpack", ".joblib", ".npy", ".npz", ".so", ".dylib"}
INDEX_DIR_NAME = ".rag_index"
DEFAULT_LOG_FILE = "~/.agentic-orchestration/logs/mcp-orchestration.log"
AGENT_FILES    = ("mcp_server.py", "repo_rag.py", "demo_logger.py")
SRC_CANDIDATES = ("src", "lib", "app", "pkg", "source")
PLACEHOLDER    = "/absolute/path/to/"
INDEX_KEYS     = ("src_path", "knowledge_path", "extra_extensions", "file_types", "structure")
ROLES          = ("interface", "implementation", "internal", "external", "library", "test", "build", "generated",
                  "docs", "tooling", "config", "data", "example")
INDEX_MODES    = ("full", "signatures", "skip")

ORCH_HEADER = """\
# Orchestrator configuration — lists every repo the agents can consult.
# Written by the cross-repo-feature-analysis skill (scripts/configure.py).
# Edit freely, then re-run:  python orchestrator/setup.py

"""


def _parse_repo(raw: str) -> tuple[str, str, Path]:
    """Return (name, display_name, path) for "name=path" or just "path".

    Only split on "=" when the left side is a valid name, so paths that contain
    "=" (or Windows drive letters) still work.
    """
    m = re.match(r"^([A-Za-z0-9_-]+)=(.+)$", raw)
    if m:
        name, path = m.group(1), m.group(2)
        return name, name, Path(path).expanduser().resolve()
    p = Path(raw).expanduser().resolve()
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", p.name).strip("-").lower() or "repo"
    return name, p.name, p


def _as_list(v) -> list:
    return [] if v is None else v if isinstance(v, list) else [v]


def _one_or_list(items: list):
    """Write a single path as a scalar (like the template), several as a list."""
    return None if not items else items[0] if len(items) == 1 else items


def _guess_src(repo: Path) -> str | None:
    for c in SRC_CANDIDATES:
        if (repo / c).is_dir():
            return f"./{c}"
    return None


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pin_sha256(path: Path) -> str:
    """SHA-256 used for hash-pinning model files. JSON text files are hashed with Windows line
    endings (CRLF) normalised to LF, so a checkout or copy tool that converts line endings is
    not mistaken for tampering; every other change still fails. Weights are hashed byte-exact."""
    import hashlib
    if path.suffix.lower() != ".json":
        return _sha256(path)
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _approved_hashes() -> set[str]:
    extra = {h.strip().lower() for h in os.environ.get("EMBEDDING_MODEL_SHA256", "").split(",") if h.strip()}
    return {MODEL_SHA256} | extra


def _model_problem(d: Path, strict_files: bool = True) -> str | None:
    """Why `d` is not the approved embedding model, or None.

    strict_files: also reject code/pickle files in the folder (the framework's own
    model folder). A source folder for --model-from may hold extras (e.g. a full
    Hugging Face snapshot) because only MODEL_FILES are ever copied out of it.
    """
    if not d.is_dir():
        return "not a folder"
    missing = [f for f in MODEL_FILES if not (d / f).is_file()]
    if missing:
        return f"missing {', '.join(missing)}"
    if strict_files:
        unsafe = sorted(str(f.relative_to(d)) for f in d.rglob("*")
                        if f.is_file() and f.suffix.lower() in UNSAFE_MODEL_EXT)
        if unsafe:
            return f"contains code or pickle files: {', '.join(unsafe[:5])}"
    digest = _sha256(d / "model.safetensors")
    if digest not in _approved_hashes():
        return (f"model.safetensors SHA-256 {digest[:16]}… does not match the approved "
                f"{MODEL_ID} weights ({MODEL_SHA256[:16]}…)")
    if digest == MODEL_SHA256:                    # the approved model: every file must match
        changed = [f for f, h in MODEL_FILE_SHA256.items() if f != "model.safetensors" and _pin_sha256(d / f) != h]
        if changed:
            return (f"{', '.join(changed)} {'does' if len(changed) == 1 else 'do'} not match the approved "
                    f"{MODEL_ID} files (SHA-256 differs)")
    return None


def _find_model_copies() -> list[Path]:
    """Copies of the approved model in the usual Hugging Face caches (hash-verified)."""
    homes = [Path(os.environ[v]).expanduser() for v in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE") if v in os.environ]
    if "HF_HOME" in os.environ:
        homes.append(Path(os.environ["HF_HOME"]).expanduser() / "hub")
    homes.append(Path.home() / ".cache" / "huggingface" / "hub")
    candidates = [s for h in homes
                  for s in sorted((h / f"models--{MODEL_ID.replace('/', '--')}" / "snapshots").glob("*"))]
    seen, found = set(), []
    for c in candidates:
        if c.exists() and c.resolve() not in seen and _model_problem(c, strict_files=False) is None:
            seen.add(c.resolve())
            found.append(c)
    return found


def _check_model_plan(model_dir: Path, model_from: Path | None) -> None:
    """Fail before writing anything if the model is missing and the user hasn't chosen."""
    if _model_problem(model_dir) is None:
        return
    if model_from is not None:
        problem = _model_problem(model_from, strict_files=False)
        if problem:
            raise SystemExit(f"ERROR: --model-from {model_from} is not usable: {problem}")
        return
    found = _find_model_copies()
    lines = [f"ERROR: the embedding model is missing from {model_dir}.",
             f"Get the approved {MODEL_ID} folder from a location you trust, then re-run with:",
             "  --model-from PATH   copy the approved files from that folder (hash-verified)"]
    if found:
        lines.append("Usable copies already on this machine:")
        lines += [f"  {f}" for f in found]
    raise SystemExit("\n".join(lines))


def _ensure_model(model_dir: Path, model_from: Path | None) -> None:
    if _model_problem(model_dir) is None:
        print(f"  ✓ embedding model present  {model_dir}")
        return
    model_dir.mkdir(parents=True, exist_ok=True)
    if model_from is not None:
        # Copy only the approved files (resolving HF-cache symlinks); never whole folders
        for name in MODEL_FILES:
            dst = model_dir / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(model_from / name, dst)
        print(f"  ✓ embedding model copied from {model_from}")
    else:
        raise SystemExit("ERROR: the embedding model is missing — pass --model-from PATH")
    problem = _model_problem(model_dir)
    if problem:
        raise SystemExit(f"ERROR: embedding model in {model_dir} is not usable after setup: {problem}")


def _set_key(text: str, key: str, value) -> str:
    """Replace a top-level `key: ...` entry, keeping the template's comments.

    The entry includes any continuation lines (indented or `- ` items), so a
    hand-written block list such as
        src_path:
          - ./a
    is replaced whole instead of leaving its items behind.
    """
    line = f"{key}: {yaml.safe_dump(value, default_flow_style=True).strip().removesuffix('...').strip()}"
    pattern = re.compile(rf"(?m)^{re.escape(key)}:.*(?:\n(?:[ \t]+\S.*|-.*))*")
    if pattern.search(text):
        text = pattern.sub(lambda _: line, text, count=1)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    if (yaml.safe_load(text) or {}).get(key) != value:
        raise SystemExit(f"ERROR: could not update '{key}' in mcp/config.yaml safely — edit it by hand")
    return text


_PARSE_AS_ALIASES = {"c++": "cpp", "cxx": "cpp", "py": "python", "md": "markdown", "txt": "text",
                     "ts": "typescript", "js": "javascript", "m": "matlab", "pl": "perl", "cs": "csharp",
                     "c#": "csharp", "golang": "go", "rs": "rust", "plain": "text", "config": "text"}
_VALID_PARSE_AS = {"c", "cpp", "python", "idl", "markdown", "text", "matlab", "perl", "javascript",
                   "typescript", "tsx", "java", "go", "rust", "csharp"}


def _parse_file_types(values: list[str]) -> dict:
    out = {}
    for v in values:
        ext, _, rest = v.partition(":")
        if not ext or not rest:
            raise SystemExit(f"ERROR: --file-type expects NAME=.EXT:KIND[:PARSE_AS], got '{v}'")
        kind, _, parse_as = rest.partition(":")
        kind = kind.strip().lower()
        ext = ext if ext.startswith(".") or "." in ext else "." + ext
        if kind == "ignore":
            out[ext.lower()] = {"kind": "ignore"}
            continue
        parse_as = _PARSE_AS_ALIASES.get(parse_as.strip().lower(), parse_as.strip().lower()) or "text"
        if parse_as.startswith("<") or parse_as not in _VALID_PARSE_AS:
            raise SystemExit(f"ERROR: --file-type {v}: parse_as must be one of {sorted(_VALID_PARSE_AS)} "
                             "— ask the developer which language these files are written in")
        out[ext.lower()] = {"kind": kind, "parse_as": parse_as}
    return out


def _parse_roles(values: list[str]) -> list:
    out = []
    for v in values:
        parts = v.rsplit(":", 2) if v.count(":") >= 2 else v.rsplit(":", 1)
        if len(parts) < 2:
            raise SystemExit(f"ERROR: --role expects NAME=GLOB:ROLE[:INDEX], got '{v}'")
        glob_, role = parts[0], parts[1].lower()
        mode = parts[2].lower() if len(parts) == 3 else ""
        if role not in ROLES:
            raise SystemExit(f"ERROR: --role {v}: role must be one of {', '.join(ROLES)}")
        if mode and mode not in INDEX_MODES:
            raise SystemExit(f"ERROR: --role {v}: index mode must be one of {', '.join(INDEX_MODES)}")
        entry = {"path": glob_, "role": role}
        if mode:
            entry["index"] = mode
        out.append(entry)
    return out


def _read_repo_cfg(repo: Path) -> dict:
    cfg = repo / "mcp" / "config.yaml"
    try:
        return yaml.safe_load(cfg.read_text()) or {} if cfg.exists() else {}
    except (OSError, yaml.YAMLError):
        return {}


def _require_classified(framework: Path, targets, file_types_by) -> None:
    """Stop until every unknown file type in every repo has been classified by the developer."""
    mcp_dir = framework / "repo-agent" / "mcp"
    if not (mcp_dir / "codegraph").is_dir():
        return
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    from codegraph.indexer import IndexConfig, collect_files
    problems = []
    for name, _, path in targets:
        current = _read_repo_cfg(path).get("file_types") or {}
        ft = {**current, **file_types_by.get(name, {})}
        _, unknown, _ = collect_files(IndexConfig(name, path, [path], ft))
        for ext, samples in sorted(unknown.items()):
            problems.append(f"  {name}: {ext}  ({len(samples)} file(s), e.g. {samples[0]})")
    if problems:
        raise SystemExit("ERROR: these file types are not classified yet. Ask the developer what each contains, "
                         "then pass --file-type NAME=.EXT:KIND[:PARSE_AS] (KIND 'ignore' to skip):\n" +
                         "\n".join(problems))


def _install_repo_agent(framework: Path, name: str, display: str, repo: Path, indexing: dict) -> str:
    """Install/refresh the repo agent. `indexing` holds only the keys the user set."""
    template = framework / "repo-agent"
    mcp_dir  = repo / "mcp"
    mcp_dir.mkdir(exist_ok=True)
    for f in AGENT_FILES:  # framework code — always refresh to the current version
        shutil.copy2(template / "mcp" / f, mcp_dir / f)
    pkg = template / "mcp" / "codegraph"
    if pkg.is_dir():       # code-graph package: replace wholesale so removed modules don't linger
        shutil.rmtree(mcp_dir / "codegraph", ignore_errors=True)
        shutil.copytree(pkg, mcp_dir / "codegraph", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    cfg_path = mcp_dir / "config.yaml"
    fresh = not cfg_path.exists()
    text = (template / "mcp" / "config.yaml").read_text() if fresh else cfg_path.read_text()
    before = yaml.safe_load(text) or {}

    text = _set_key(text, "repo_name", name)
    if fresh:
        text = _set_key(text, "display_name", display)
        indexing.setdefault("src_path", ".")          # whole repo: the code graph skips noise itself
    for key, value in indexing.items():
        text = _set_key(text, key, value)
    cfg_path.write_text(text)

    after = yaml.safe_load(text)
    note = ""
    if not fresh and any(before.get(k) != after.get(k) for k in INDEX_KEYS):
        # the code graph notices changed settings itself and rebuilds on next start,
        # reusing cached embeddings — deleting the index would only throw those away
        note = "  (indexing changed — graph rebuilds on next start, embeddings reused)"

    knowledge = repo / "knowledge"
    if not knowledge.exists():
        shutil.copytree(template / "knowledge", knowledge)

    docs = sum(1 for d in _as_list(after.get("knowledge_path")) if (repo / d).is_dir()
               for p in (repo / d).rglob("*.md") if p.name.lower() != "readme.md")
    return (f"src_path={after.get('src_path')}  knowledge_path={after.get('knowledge_path')}  "
            f"extra_extensions={after.get('extra_extensions')}  knowledge docs={docs}{note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--framework", required=True, help="path to the framework folder")
    ap.add_argument("--repo", action="append", default=[], metavar="[NAME=]PATH",
                    help="repo to connect (repeatable); name defaults to the folder name")
    ap.add_argument("--src", action="append", default=[], metavar="NAME=PATH",
                    help="source folder/glob to index for a repo (repeatable); NAME=null for none")
    ap.add_argument("--knowledge", action="append", default=[], metavar="NAME=PATH",
                    help="extra .md doc folder for a repo (repeatable); ./knowledge is always kept")
    ap.add_argument("--model-from", metavar="PATH",
                    help="copy the approved embedding model's files from an existing copy")
    ap.add_argument("--model-only", action="store_true",
                    help="only copy the model (--model-from), then exit")
    ap.add_argument("--find-model", action="store_true",
                    help="list usable copies of the embedding model on this machine and exit")
    ap.add_argument("--file-type", action="append", default=[], metavar="NAME=.EXT:KIND[:PARSE_AS]",
                    help="what an unknown file type contains (from the developer): kind = implementation, "
                         "interface, config, docs, data, … or ignore; parse_as = c, cpp, python, idl, perl, "
                         "matlab, markdown, text, …")
    ap.add_argument("--role", action="append", default=[], metavar="NAME=GLOB:ROLE[:INDEX]",
                    help="structure manifest entry: folder glob → role (interface, internal, external, test, …), "
                         "optional index mode full/signatures/skip")
    ap.add_argument("--allow-unclassified", action="store_true",
                    help="proceed even if some file types are still unclassified (they are not indexed)")
    ap.add_argument("--ext", action="append", default=[], metavar="NAME=.EXT",
                    help="extra file extension to index as code for a repo (repeatable)")
    args = ap.parse_args()

    framework = Path(args.framework).expanduser().resolve()
    if not (framework / "orchestrator" / "setup.py").exists():
        raise SystemExit(f"ERROR: {framework} is not the framework folder (orchestrator/setup.py not found)")
    model_dir  = framework / "models" / MODEL_DIRNAME
    model_from = Path(args.model_from).expanduser().resolve() if args.model_from else None

    if args.model_only:                                 # just put the model in place (setup GUI)
        if not model_from and _model_problem(model_dir) is not None:
            raise SystemExit("ERROR: --model-only needs --model-from PATH")
        _check_model_plan(model_dir, model_from)
        _ensure_model(model_dir, model_from)
        return

    if args.find_model:
        state = "present" if _model_problem(model_dir) is None else "missing"
        print(f"Framework model folder: {model_dir} ({state})")
        found = _find_model_copies()
        print("Usable copies on this machine:" if found else "No usable copies found in the usual caches.")
        for f in found:
            print(f"  {f}")
        return

    def _per_repo(values: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in values:
            n, sep, v = item.partition("=")
            if not sep:
                raise SystemExit(f"ERROR: expected NAME=VALUE, got '{item}'")
            out.setdefault(n, [])
            if v.lower() not in ("null", "none", ""):
                out[n].append(v)
        return out

    src_by, knowledge_by, ext_by = _per_repo(args.src), _per_repo(args.knowledge), _per_repo(args.ext)
    ftype_by, role_by = _per_repo(args.file_type), _per_repo(args.role)
    file_types_by = {n: _parse_file_types(v) for n, v in ftype_by.items()}
    structure_by = {n: _parse_roles(v) for n, v in role_by.items()}

    orch_cfg_path = framework / "orchestrator" / "mcp" / "config.yaml"
    orch_cfg = yaml.safe_load(orch_cfg_path.read_text()) or {}
    repos = [r for r in orch_cfg.get("repos") or [] if not str(r.get("path", "")).startswith(PLACEHOLDER)]
    by_name = {r["name"]: r for r in repos}

    # Validate everything before writing anything, so a bad argument never
    # leaves some repos half-configured.
    targets = [_parse_repo(raw) for raw in args.repo]
    for _, _, path in targets:
        if not path.is_dir():
            raise SystemExit(f"ERROR: repo path does not exist: {path}")
        if (path / "mcp").exists() and not (path / "mcp" / "mcp_server.py").exists():
            raise SystemExit(
                f"ERROR: {path / 'mcp'} exists but is not a repo-agent folder. "
                "Rename it or move it aside, then re-run."
            )
    unknown = (set(src_by) | set(knowledge_by) | set(ext_by) | set(ftype_by) | set(role_by)) - \
        {n for n, _, _ in targets}
    if unknown:
        raise SystemExit(f"ERROR: --src/--knowledge/--ext/--file-type/--role name(s) {sorted(unknown)} "
                         "have no matching --repo")
    if not args.allow_unclassified:
        _require_classified(framework, targets, file_types_by)
    if len(set(by_name) | {name for name, _, _ in targets}) < 2:
        raise SystemExit("ERROR: at least two repos are needed for cross-repo analysis")
    _check_model_plan(model_dir, model_from)

    print("\nConfiguring cross-repo orchestration\n")
    _ensure_model(model_dir, model_from)

    for name, display, path in targets:
        entry = by_name.get(name)
        if entry is None:
            entry = {"name": name, "display_name": display, "path": str(path)}
            repos.append(entry)
            by_name[name] = entry
        else:
            entry["path"] = str(path)

        indexing = {}
        if name in src_by:
            indexing["src_path"] = _one_or_list(src_by[name])
        if name in knowledge_by:
            dirs = ["./knowledge"] + [d for d in knowledge_by[name] if d.rstrip("/") not in ("knowledge", "./knowledge")]
            indexing["knowledge_path"] = _one_or_list(dirs)
        if name in ext_by:
            indexing["extra_extensions"] = [e if e.startswith(".") else f".{e}" for e in ext_by[name]] or None
        if name in file_types_by:
            current = (_read_repo_cfg(path).get("file_types") or {})
            indexing["file_types"] = {**current, **file_types_by[name]}
        if name in structure_by:
            indexing["structure"] = structure_by[name]
        summary = _install_repo_agent(framework, name, entry.get("display_name", name), path, indexing)
        print(f"  ✓ {name:20s} {path}\n      {summary}")

    orch_cfg["repos"] = repos
    orch_cfg.setdefault("log_file", DEFAULT_LOG_FILE)
    orch_cfg.setdefault("log_content", False)
    orch_cfg_path.write_text(ORCH_HEADER + yaml.safe_dump(orch_cfg, sort_keys=False))
    print(f"  ✓ orchestrator config      {orch_cfg_path}  ({len(repos)} repos)\n", flush=True)

    result = subprocess.run([sys.executable, str(framework / "orchestrator" / "setup.py")],
                            cwd=framework / "orchestrator")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
