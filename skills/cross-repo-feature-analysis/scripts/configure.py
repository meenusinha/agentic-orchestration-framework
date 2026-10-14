#!/usr/bin/env python3
"""
Connect repos to the agentic-orchestration-framework in one step.

Does steps 2-5 of the README for the agent:
  1. Puts the embedding model in <framework>/models/ if it is missing — copied
     from an existing copy (--model-from) or downloaded (--download-model).
     It never downloads without --download-model; with neither flag it stops
     and lists any copies it finds on this machine, so the user can choose.
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
If a repo's indexing settings change, its .chroma_db/ cache is deleted so the
index is rebuilt with the new settings on the next query.

Embedding model (only needed while <framework>/models/all-MiniLM-L6-v2 is missing):
  --model-from PATH          copy an existing all-MiniLM-L6-v2 folder (verified first)
  --download-model           download it from Hugging Face (~90 MB)
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

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run this with the framework venv's Python.")
    sys.exit(1)

MODEL_ID       = "sentence-transformers/all-MiniLM-L6-v2"
# Only what sentence-transformers loads. The Hugging Face repo also carries ONNX,
# OpenVINO, TF, Rust and .bin weights (~950 MB in all) that are never used.
MODEL_FILES    = [
    "config.json", "config_sentence_transformers.json", "modules.json",
    "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json",
    "tokenizer_config.json", "vocab.txt", "model.safetensors", "1_Pooling/config.json",
]
AGENT_FILES    = ("mcp_server.py", "repo_rag.py", "demo_logger.py")
SRC_CANDIDATES = ("src", "lib", "app", "pkg", "source")
PLACEHOLDER    = "/absolute/path/to/"
INDEX_KEYS     = ("src_path", "knowledge_path", "extra_extensions")

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


def _model_problem(d: Path) -> str | None:
    """Why `d` is not a usable all-MiniLM-L6-v2 sentence-transformers folder, or None."""
    import json
    if not d.is_dir():
        return "not a folder"
    for f in ("config.json", "modules.json", "1_Pooling/config.json"):
        if not (d / f).is_file():
            return f"missing {f}"
    if not any((d / f).is_file() for f in ("tokenizer.json", "vocab.txt")):
        return "missing tokenizer files"
    weights = [d / f for f in ("model.safetensors", "pytorch_model.bin") if (d / f).is_file()]
    if not weights:
        return "missing model weights (model.safetensors or pytorch_model.bin)"
    if max(w.stat().st_size for w in weights) < 50_000_000:  # real weights are ~90 MB
        return "model weights look incomplete (under 50 MB)"
    try:
        cfg = json.loads((d / "config.json").read_text())
    except (OSError, ValueError):
        return "unreadable config.json"
    if cfg.get("hidden_size") != 384 or cfg.get("num_hidden_layers") != 6:
        return "a different model, not all-MiniLM-L6-v2 (expected 384 dims, 6 layers)"
    return None


def _find_model_copies() -> list[Path]:
    """Usable copies in the usual sentence-transformers / Hugging Face caches."""
    homes = [Path(os.environ[v]).expanduser() for v in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE") if v in os.environ]
    if "HF_HOME" in os.environ:
        homes.append(Path(os.environ["HF_HOME"]).expanduser() / "hub")
    homes.append(Path.home() / ".cache" / "huggingface" / "hub")
    candidates = [s for h in homes
                  for s in sorted((h / f"models--{MODEL_ID.replace('/', '--')}" / "snapshots").glob("*"))]
    st_home = Path(os.environ.get("SENTENCE_TRANSFORMERS_HOME", Path.home() / ".cache" / "torch" / "sentence_transformers"))
    candidates.append(st_home / MODEL_ID.replace("/", "_"))
    seen, found = set(), []
    for c in candidates:
        if c.exists() and c.resolve() not in seen and _model_problem(c) is None:
            seen.add(c.resolve())
            found.append(c)
    return found


def _check_model_plan(model_dir: Path, model_from: Path | None, download: bool) -> None:
    """Fail before writing anything if the model is missing and the user hasn't chosen."""
    if _model_problem(model_dir) is None:
        return
    if model_from is not None:
        problem = _model_problem(model_from)
        if problem:
            raise SystemExit(f"ERROR: --model-from {model_from} is not usable: {problem}")
        return
    if download:
        return
    found = _find_model_copies()
    lines = [f"ERROR: the embedding model is missing from {model_dir}.",
             "Ask the user whether they already have all-MiniLM-L6-v2, then re-run with one of:",
             "  --model-from PATH   copy their existing copy (verified first)",
             "  --download-model    download it from Hugging Face (~90 MB) — only with their permission"]
    if found:
        lines.append("Usable copies already on this machine:")
        lines += [f"  {f}" for f in found]
    raise SystemExit("\n".join(lines))


def _ensure_model(model_dir: Path, model_from: Path | None, download: bool) -> None:
    if _model_problem(model_dir) is None:
        print(f"  ✓ embedding model present  {model_dir}")
        return
    # Copy/download into the folder in place: it holds the framework's tracked .gitkeep
    if model_from is not None:
        # Hugging Face cache snapshots are symlinks into blobs/; copy the real files
        shutil.copytree(model_from, model_dir, symlinks=False, dirs_exist_ok=True)
        print(f"  ✓ embedding model copied from {model_from}")
    else:
        print(f"  … downloading {MODEL_ID} (~90 MB)", flush=True)
        from huggingface_hub import snapshot_download  # installed with sentence-transformers
        snapshot_download(MODEL_ID, local_dir=str(model_dir), allow_patterns=MODEL_FILES)
        print(f"  ✓ embedding model downloaded  {model_dir}")
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


def _install_repo_agent(framework: Path, name: str, display: str, repo: Path, indexing: dict) -> str:
    """Install/refresh the repo agent. `indexing` holds only the keys the user set."""
    template = framework / "repo-agent"
    mcp_dir  = repo / "mcp"
    mcp_dir.mkdir(exist_ok=True)
    for f in AGENT_FILES:  # framework code — always refresh to the current version
        shutil.copy2(template / "mcp" / f, mcp_dir / f)

    cfg_path = mcp_dir / "config.yaml"
    fresh = not cfg_path.exists()
    text = (template / "mcp" / "config.yaml").read_text() if fresh else cfg_path.read_text()
    before = yaml.safe_load(text) or {}

    text = _set_key(text, "repo_name", name)
    if fresh:
        text = _set_key(text, "display_name", display)
        indexing.setdefault("src_path", _guess_src(repo))
    for key, value in indexing.items():
        text = _set_key(text, key, value)
    cfg_path.write_text(text)

    after = yaml.safe_load(text)
    note = ""
    if not fresh and any(before.get(k) != after.get(k) for k in INDEX_KEYS):
        cache = repo / ".chroma_db"
        if cache.exists():
            shutil.rmtree(cache)
            note = "  (indexing changed — cache cleared, rebuilds on next query)"

    knowledge = repo / "knowledge"
    if not knowledge.exists():
        shutil.copytree(template / "knowledge", knowledge)

    docs = sum(1 for d in _as_list(after.get("knowledge_path")) if (repo / d).is_dir()
               for p in (repo / d).rglob("*.md") if p.name.lower() != "readme.md")
    return (f"src_path={after.get('src_path')}  knowledge_path={after.get('knowledge_path')}  "
            f"extra_extensions={after.get('extra_extensions')}  knowledge docs={docs}{note}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--framework", required=True, help="path to the agentic-orchestration-framework clone")
    ap.add_argument("--repo", action="append", default=[], metavar="[NAME=]PATH",
                    help="repo to connect (repeatable); name defaults to the folder name")
    ap.add_argument("--src", action="append", default=[], metavar="NAME=PATH",
                    help="source folder/glob to index for a repo (repeatable); NAME=null for none")
    ap.add_argument("--knowledge", action="append", default=[], metavar="NAME=PATH",
                    help="extra .md doc folder for a repo (repeatable); ./knowledge is always kept")
    ap.add_argument("--model-from", metavar="PATH",
                    help="copy an existing all-MiniLM-L6-v2 folder into <framework>/models/")
    ap.add_argument("--download-model", action="store_true",
                    help="download the embedding model (~90 MB) if it is missing")
    ap.add_argument("--find-model", action="store_true",
                    help="list usable copies of the embedding model on this machine and exit")
    ap.add_argument("--ext", action="append", default=[], metavar="NAME=.EXT",
                    help="extra file extension to index as code for a repo (repeatable)")
    args = ap.parse_args()

    framework = Path(args.framework).expanduser().resolve()
    if not (framework / "orchestrator" / "setup.py").exists():
        raise SystemExit(f"ERROR: {framework} is not an agentic-orchestration-framework clone")
    model_dir  = framework / "models" / "all-MiniLM-L6-v2"
    model_from = Path(args.model_from).expanduser().resolve() if args.model_from else None
    if args.model_from and args.download_model:
        raise SystemExit("ERROR: pass either --model-from or --download-model, not both")

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
    unknown = (set(src_by) | set(knowledge_by) | set(ext_by)) - {n for n, _, _ in targets}
    if unknown:
        raise SystemExit(f"ERROR: --src/--knowledge/--ext name(s) {sorted(unknown)} have no matching --repo")
    if len(set(by_name) | {name for name, _, _ in targets}) < 2:
        raise SystemExit("ERROR: at least two repos are needed for cross-repo analysis")
    _check_model_plan(model_dir, model_from, args.download_model)

    print("\nConfiguring cross-repo orchestration\n")
    _ensure_model(model_dir, model_from, args.download_model)

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
        summary = _install_repo_agent(framework, name, entry.get("display_name", name), path, indexing)
        print(f"  ✓ {name:20s} {path}\n      {summary}")

    orch_cfg["repos"] = repos
    orch_cfg.setdefault("log_file", "/tmp/mcp-orchestration.log")
    orch_cfg_path.write_text(ORCH_HEADER + yaml.safe_dump(orch_cfg, sort_keys=False))
    print(f"  ✓ orchestrator config      {orch_cfg_path}  ({len(repos)} repos)\n", flush=True)

    result = subprocess.run([sys.executable, str(framework / "orchestrator" / "setup.py")],
                            cwd=framework / "orchestrator")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
