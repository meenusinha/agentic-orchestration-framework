#!/usr/bin/env python3
"""
End-to-end MCP simulation — runs without VS Code.

Queries all configured repos via MCP and produces a Feature Analysis
Document formatted as markdown, ready to paste into Copilot chat.

Usage (from repo root, with venv active):
  python test_mcp.py "your feature request here"

Output is printed to stdout AND saved to a new file in results/ (never overwritten).
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


import json
import shutil
import subprocess
import threading
import time
from datetime import date
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

_require_supported_os("test_mcp.py")
_require_venv("test_mcp.py")

FRAMEWORK_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(FRAMEWORK_DIR / "orchestrator" / "mcp"))

# Remove system PYTHONPATH so subprocesses use only the venv's packages.
os.environ.pop("PYTHONPATH", None)

# Set env vars before any subprocess calls — inherited by all spawned MCP servers
os.environ["EMBEDDING_MODEL_PATH"] = str(FRAMEWORK_DIR / "models" / "granite-embedding-small-english-r2")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

from router import _mcp_call, OrchestratorRouter
from demo_logger import DEFAULT_LOG_FILE, open_private
import term
import html_log

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_PATH = FRAMEWORK_DIR / "orchestrator" / "mcp" / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"ERROR: Config not found at {CONFIG_PATH}")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

repos = config.get("repos", [])
if not repos:
    print("ERROR: No repos listed in orchestrator/mcp/config.yaml")
    sys.exit(1)

LOG_FILE  = str(Path(config.get("log_file") or DEFAULT_LOG_FILE).expanduser())
HTML_FILE = html_log.path_from_log(LOG_FILE)
os.environ["DEMO_LOG_FILE"] = LOG_FILE
os.environ["DEMO_LOG_CONTENT"] = "1" if config.get("log_content") is True else "0"

# Clear both log files at the start of every run (private 0600 files)
with open_private(LOG_FILE, truncate=True):
    pass
html_log.init(HTML_FILE)

# ── Live log streaming ────────────────────────────────────────────────────────
_stop_streaming = threading.Event()

def _stream_logs():
    """Tail the plain-text log and print colour-coded lines to stdout."""
    path = Path(LOG_FILE)
    for _ in range(20):
        if path.exists():
            break
        time.sleep(0.5)
    if not path.exists():
        return
    with open(path, "r") as f:
        f.seek(0, 2)
        while not _stop_streaming.is_set():
            line = f.readline()
            if line:
                print(f"  {term.GRY}LOG{term._R} | {term.colorize_log(line.rstrip())}", flush=True)
            else:
                time.sleep(0.1)

threading.Thread(target=_stream_logs, daemon=True).start()

# ── Banner ────────────────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print("Usage: python test_mcp.py \"your feature request here\"")
    sys.exit(1)

feature_request = " ".join(sys.argv[1:])

print(term.c(f"\n{'─'*60}", term.GRY))
print(term.c("  Cross-repository analysis", term.CYN))
print(term.c(f"{'─'*60}", term.GRY))
print(f"  Feature request : {term.c(feature_request, term.YEL)}")
print(f"  HTML log        : {term.c('file://' + HTML_FILE, term.BLU)}  (open in browser)")
print(term.c(f"{'─'*60}\n", term.GRY))

# ── Helpers ───────────────────────────────────────────────────────────────────
def _repo_path(repo: dict) -> Path:
    raw = repo["path"]
    p = Path(raw)
    return p if p.is_absolute() else (CONFIG_PATH.parent / p).resolve()

def _repo_script(repo: dict) -> Path | None:
    script = _repo_path(repo) / "mcp" / "mcp_server.py"
    return script if script.exists() else None

def _preflight(script: Path) -> None:
    """Start the server with just an initialize message and surface any stderr."""
    init_msg = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "1.0"}}})
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=init_msg, capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    if proc.stderr.strip():
        print(f"  {term.YEL}SERVER STDERR:{term._R}\n{proc.stderr.strip()}")
    if proc.stdout.strip():
        print(f"  SERVER STDOUT:\n{proc.stdout.strip()[:300]}")

# ── Index cache prompts ───────────────────────────────────────────────────────
print(term.c("Index cache", term.CYN) + " — keeping cache skips re-indexing:")
for repo in repos:
    name   = repo["name"]
    index  = _repo_path(repo) / ".rag_index"
    tag    = term.c(f"[{name}]", term.BLU)
    if index.exists():
        ans = input(f"  {tag} .rag_index exists — delete and rebuild? {term.YEL}[y/N]{term._R} ").strip().lower()
        if ans == "y":
            shutil.rmtree(index)
            print(f"  {tag} {term.c('deleted', term.RED)} — will rebuild index")
        else:
            print(f"  {tag} {term.c('kept', term.GRN)} — will use cached index")
    else:
        print(f"  {tag} no cache — will build fresh")
print()

# ── Pre-flight checks ─────────────────────────────────────────────────────────
print(term.c("Pre-flight", term.CYN) + " — checking each MCP server starts cleanly...")
any_failed = False
for repo in repos:
    name   = repo["name"]
    script = _repo_script(repo)
    tag    = term.c(f"[{name}]", term.BLU)
    if not script:
        print(f"  {tag} {term.c('SKIP', term.YEL)} — mcp_server.py not found")
        continue
    print(f"  {tag} starting server...", end=" ", flush=True)
    try:
        _preflight(script)
        print(term.c("ok", term.GRN))
    except subprocess.TimeoutExpired:
        print(term.c("TIMEOUT (600s)", term.RED))
        any_failed = True
    except Exception as e:
        print(term.c(f"ERROR — {e}", term.RED))
        any_failed = True

if any_failed:
    print(term.c("\nFix the server errors above before continuing.", term.RED))
    sys.exit(1)
print()

# ── Step 1: Orchestrator routing ──────────────────────────────────────────────
print(term.c("Step 1", term.CYN) + " — Orchestrator routing...")
router = OrchestratorRouter(CONFIG_PATH)
requesting_repo = repos[0]["name"]
targets, scores = router.get_relevant_repos(requesting_repo, feature_request)
repo_display = {r["name"]: r.get("display_name", r["name"]) for r in repos}
print(f"  Selected: {term.c(str(targets), term.GRN)}\n")

# ── Step 2: Query every repo ──────────────────────────────────────────────────
print(term.c("Step 2", term.CYN) + " — Querying all repos...")
repo_results: dict[str, str] = {}
for repo in repos:
    name   = repo["name"]
    script = _repo_script(repo)
    tag    = term.c(f"[{name}]", term.BLU)
    if not script:
        print(f"  {tag} {term.c('SKIP', term.YEL)} — mcp_server.py not found")
        continue
    print(f"  {tag} querying...", flush=True)
    result = _mcp_call(str(script), "query_repo", {"feature_request": feature_request}, timeout=600)
    repo_results[name] = result
    preview = result[:80].replace("\n", " ")
    print(f"  {tag} {term.c('done', term.GRN)} — {term.c(preview + '...', term.GRY)}")

# ── Step 3: Build Feature Analysis Document ───────────────────────────────────
print()
print(term.c("Step 3", term.CYN) + " — Building Feature Analysis Document...")

requesting_display = repo_display.get(requesting_repo, requesting_repo)
today = date.today().isoformat()

doc_lines = [
    f"# Feature Analysis: {feature_request}", "",
    f"**Requesting Repo**: {requesting_display}",
    f"**Date**: {today}", "", "---", "", "## Routing", "",
    "The orchestrator ranked repos by how much relevant knowledge was found:", "",
]
for name, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
    marker = "★ selected" if name in targets else "· not selected"
    doc_lines.append(f"- **{repo_display.get(name, name)}** — relevance score: {score:.3f} ({marker})")

doc_lines += ["", "---", "", "## Current State", ""]

for repo in repos:
    name    = repo["name"]
    display = repo_display.get(name, name)
    suffix  = " (Requesting Repo)" if name == requesting_repo else ""
    doc_lines.append(f"### {display}{suffix}")
    doc_lines.append("")
    if name in repo_results:
        result = repo_results[name]
        if "no relevant knowledge found" in result.lower():
            doc_lines.append("_No relevant knowledge found in this repo._")
        else:
            content = result
            if content.upper().startswith("RELEVANT KNOWLEDGE:"):
                content = content[len("RELEVANT KNOWLEDGE:"):].strip()
            doc_lines.append(content)
    else:
        doc_lines.append("_Could not query this repo (mcp_server.py not found)._")
    doc_lines.append("")

doc_lines += [
    "---", "", "## Instructions for Copilot", "",
    "The section above contains retrieved knowledge from all configured repos.",
    "Based **only** on this retrieved knowledge (not your own training data),",
    "please produce a Solution Design covering:", "",
    "- Which components in which repos need to change",
    "- What new interfaces or APIs are needed",
    "- How the repos will coordinate",
    "- Risks or dependencies to consider",
]

document = "\n".join(doc_lines)

# ── Output ────────────────────────────────────────────────────────────────────
sys.path.insert(0, str(FRAMEWORK_DIR / "repo-agent" / "mcp"))
from codegraph.reports import unique_report_path, write_report  # noqa: E402
# a new file per run (results/<date>_<time>_analysis_<question>.md) — earlier results are never overwritten
output_path = unique_report_path(FRAMEWORK_DIR / "results", "analysis", feature_request)
write_report(output_path, document)

_stop_streaming.set()
time.sleep(0.3)   # let streaming thread flush last lines
html_log.close(HTML_FILE)

print(term.c(f"\n{'═'*60}", term.GRY))
print(document)
print(term.c(f"{'═'*60}", term.GRY))
print(f"\n{term.c('Saved:', term.GRN)} {output_path}")
print(f"{term.c('HTML log:', term.GRN)} file://{HTML_FILE}")
print(f"\nSaved to {term.c(str(output_path), term.YEL)}")
