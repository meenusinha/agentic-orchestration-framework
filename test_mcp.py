#!/usr/bin/env python3
"""
End-to-end MCP simulation — runs without VS Code.

Tests the full flow:
  1. Calls query_repo on each configured repo directly via MCP subprocess
  2. Calls the orchestrator router to test routing logic

Usage (from repo root, with venv active):
  python test_mcp.py
  python test_mcp.py "your feature request here"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "orchestrator" / "mcp"))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

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

feature_request = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "add user authentication"

print(f"\nFeature request: '{feature_request}'")
print(f"Repos configured: {[r['name'] for r in repos]}\n")

# ── Test 1: query_repo on each repo directly ──────────────────────────────────
print("=" * 60)
print("TEST 1 — query_repo (direct MCP call to each repo)")
print("=" * 60)

for repo in repos:
    name = repo["name"]
    raw_path = repo["path"]
    repo_path = Path(raw_path) if Path(raw_path).is_absolute() else (CONFIG_PATH.parent / raw_path).resolve()
    script = repo_path / "mcp" / "mcp_server.py"

    if not script.exists():
        print(f"\n[{name}] SKIP — mcp_server.py not found at {script}")
        continue

    print(f"\n[{name}] Calling query_repo...")
    result = _mcp_call(str(script), "query_repo", {"feature_request": feature_request})
    preview = result[:300].replace("\n", " ")
    print(f"[{name}] Response ({len(result)} chars): {preview}{'...' if len(result) > 300 else ''}")

# ── Test 2: orchestrator routing ──────────────────────────────────────────────
print("\n" + "=" * 60)
print("TEST 2 — orchestrator routing")
print("=" * 60)

requesting = repos[0]["name"]
print(f"\nRequesting repo: {requesting}")
print("Calling get_relevant_repos...\n")

router = OrchestratorRouter(CONFIG_PATH)
targets, scores = router.get_relevant_repos(requesting, feature_request)

for name, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
    marker = "★" if name in targets else "·"
    print(f"  {marker} {name:30s} score: {score:.3f}")

print(f"\nSelected: {targets}")
print("\nDone. If you see responses above, MCP servers are working correctly.")
