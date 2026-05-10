#!/usr/bin/env python3
"""
End-to-end MCP simulation — runs without VS Code.

Queries all configured repos via MCP and produces a Feature Analysis
Document formatted as markdown, ready to paste into Copilot chat.

Usage (from repo root, with venv active):
  python test_mcp.py "your feature request here"

Output is printed to stdout AND saved to feature_analysis.md
Paste feature_analysis.md content into Copilot chat for analysis.
"""
import os
import sys
from datetime import date
from pathlib import Path

FRAMEWORK_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(FRAMEWORK_DIR / "orchestrator" / "mcp"))

# Remove system PYTHONPATH so subprocesses use only the venv's packages.
# On some systems (e.g. RHEL with scientific software stacks) PYTHONPATH points
# to system packages compiled for a different Python, causing import errors.
os.environ.pop("PYTHONPATH", None)

# Set env vars before any subprocess calls — inherited by all spawned MCP servers
os.environ["EMBEDDING_MODEL_PATH"] = str(FRAMEWORK_DIR / "models" / "all-MiniLM-L6-v2")

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

import json
import subprocess
from router import _mcp_call, OrchestratorRouter

CONFIG_PATH = Path(__file__).parent / "orchestrator" / "mcp" / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"ERROR: Config not found at {CONFIG_PATH}")
    print("       Have you edited orchestrator/mcp/config.yaml?")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

repos = config.get("repos", [])
if not repos:
    print("ERROR: No repos listed in orchestrator/mcp/config.yaml")
    sys.exit(1)

os.environ["DEMO_LOG_FILE"] = config.get("log_file", "/tmp/mcp-orchestration.log")

if len(sys.argv) < 2:
    print("Usage: python test_mcp.py \"your feature request here\"")
    sys.exit(1)

feature_request = " ".join(sys.argv[1:])

# ── Helpers ───────────────────────────────────────────────────────────────────
def _repo_script(repo: dict) -> Path | None:
    raw = repo["path"]
    p = Path(raw) if Path(raw).is_absolute() else (CONFIG_PATH.parent / raw).resolve()
    script = p / "mcp" / "mcp_server.py"
    return script if script.exists() else None

def _diagnose_server(script: Path) -> None:
    """Run the MCP server and print stderr so startup errors are visible."""
    init_msg = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "test", "version": "1.0"}}})
        + "\n"
    )
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=init_msg, capture_output=True, text=True, timeout=30,
        env=os.environ,
    )
    if proc.stderr.strip():
        print(f"  SERVER STDERR:\n{proc.stderr.strip()}")
    if proc.stdout.strip():
        print(f"  SERVER STDOUT:\n{proc.stdout.strip()[:300]}")

# ── Pre-flight: check each MCP server starts cleanly ─────────────────────────
print("\nPre-flight check — testing each MCP server startup...")
any_failed = False
for repo in repos:
    name = repo["name"]
    script = _repo_script(repo)
    if not script:
        print(f"  [{name}] SKIP — mcp_server.py not found at expected path")
        continue
    print(f"  [{name}] starting server...", end=" ", flush=True)
    try:
        _diagnose_server(script)
        print("ok")
    except subprocess.TimeoutExpired:
        print("TIMEOUT — server did not respond within 30s")
        any_failed = True
    except Exception as e:
        print(f"ERROR — {e}")
        any_failed = True

if any_failed:
    print("\nFix the server errors above before continuing.")
    sys.exit(1)
print()

# ── Step 1: Run orchestrator routing ─────────────────────────────────────────
print(f"\nQuerying orchestrator router...", flush=True)
router = OrchestratorRouter(CONFIG_PATH)
requesting_repo = repos[0]["name"]
targets, scores = router.get_relevant_repos(requesting_repo, feature_request)

repo_display = {r["name"]: r.get("display_name", r["name"]) for r in repos}

print(f"Selected repos: {targets}\n")

# ── Step 2: Query every repo ──────────────────────────────────────────────────
repo_results: dict[str, str] = {}
for repo in repos:
    name = repo["name"]
    script = _repo_script(repo)
    if not script:
        print(f"[{name}] SKIP — mcp_server.py not found", flush=True)
        continue
    print(f"[{name}] Querying...", flush=True)
    result = _mcp_call(str(script), "query_repo", {"feature_request": feature_request})
    repo_results[name] = result

# ── Step 3: Build Feature Analysis Document ───────────────────────────────────
requesting_display = repo_display.get(requesting_repo, requesting_repo)
today = date.today().isoformat()

lines = [
    f"# Feature Analysis: {feature_request}",
    f"",
    f"**Requesting Repo**: {requesting_display}",
    f"**Date**: {today}",
    f"",
    f"---",
    f"",
    f"## Routing",
    f"",
    f"The orchestrator ranked repos by how much relevant knowledge was found:",
    f"",
]
for name, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
    marker = "★ selected" if name in targets else "· not selected"
    lines.append(f"- **{repo_display.get(name, name)}** — relevance score: {score:.3f} ({marker})")

lines += [
    f"",
    f"---",
    f"",
    f"## Current State",
    f"",
]

for repo in repos:
    name = repo["name"]
    display = repo_display.get(name, name)
    suffix = " (Requesting Repo)" if name == requesting_repo else ""
    lines.append(f"### {display}{suffix}")
    lines.append(f"")
    if name in repo_results:
        result = repo_results[name]
        if "no relevant knowledge found" in result.lower():
            lines.append("_No relevant knowledge found in this repo._")
        else:
            # Strip the RELEVANT KNOWLEDGE: prefix if present
            content = result
            if content.upper().startswith("RELEVANT KNOWLEDGE:"):
                content = content[len("RELEVANT KNOWLEDGE:"):].strip()
            lines.append(content)
    else:
        lines.append("_Could not query this repo (mcp_server.py not found)._")
    lines.append(f"")

lines += [
    f"---",
    f"",
    f"## Instructions for Copilot",
    f"",
    f"The section above contains retrieved knowledge from all configured repos.",
    f"Based **only** on this retrieved knowledge (not your own training data),",
    f"please produce a Solution Design covering:",
    f"",
    f"- Which components in which repos need to change",
    f"- What new interfaces or APIs are needed",
    f"- How the repos will coordinate",
    f"- Risks or dependencies to consider",
]

document = "\n".join(lines)

# ── Output ────────────────────────────────────────────────────────────────────
output_path = Path(__file__).parent / "feature_analysis.md"
output_path.write_text(document, encoding="utf-8")

print("\n" + "=" * 60)
print(document)
print("=" * 60)
print(f"\nSaved to: {output_path}")
print("Paste the contents of feature_analysis.md into Copilot chat.")
