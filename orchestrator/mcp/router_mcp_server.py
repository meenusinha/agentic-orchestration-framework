#!/usr/bin/env python3
"""Orchestrator router MCP server — generic, reads config from config.yaml.

Routes feature requests by querying each repo's RAG via MCP and ranking by
how much relevant content is found across docs and source code.

Launch: python orchestrator/mcp/router_mcp_server.py
"""
import sys
from pathlib import Path

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

_require_supported_os("the orchestrator MCP server")
_require_venv("the orchestrator MCP server")

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from mcp.server.fastmcp import FastMCP
from router import OrchestratorRouter
from demo_logger import log, content

CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    _config = yaml.safe_load(f)

log("orchestrator_router", "INFO", "Starting Orchestrator Router MCP server")
router = OrchestratorRouter(CONFIG_PATH)
_repo_display = {r["name"]: r.get("display_name", r["name"]) for r in _config.get("repos", [])}

mcp = FastMCP("Orchestrator Router")


@mcp.tool()
def get_relevant_repos(requesting_repo: str, feature_description: str) -> str:
    """
    Return the names of repos most relevant to the given feature description.
    Excludes the requesting repo. Queries each peer repo's RAG via MCP and ranks
    by how much relevant content is found across docs and source code.
    Call this FIRST before querying individual repo MCP servers.
    """
    log("orchestrator_router", "TOOL_CALL", f"get_relevant_repos from '{requesting_repo}'")
    log("orchestrator_router", "TOOL_CALL", f"Feature: {content(feature_description[:80])}")

    targets, all_scores = router.get_relevant_repos(requesting_repo, feature_description, top_k=2)

    lines = [
        f"Routing result for: '{feature_description[:70]}...'",
        f"Requesting repo: {requesting_repo}",
        "",
        "Relevant repos (by RAG content relevance):",
    ]
    reasons = getattr(router, "last_reasons", {})
    for name, score in sorted(all_scores.items(), key=lambda x: (x[0] not in targets, -x[1])):
        marker = "★" if name in targets else "·"
        why = "; ".join(reasons.get(name, []))
        lines.append(f"  {marker} {_repo_display.get(name, name):25s} — score: {score:.3f}" + (f" — {why}" if why else ""))

    lines += [
        "",
        "Consult these repos next (query_repo, or search_code / trace_interface for structured results):",
        *[f"  • {_repo_display.get(t, t)}" for t in targets],
    ]
    result = "\n".join(lines)
    log("orchestrator_router", "RESULT", f"Routing complete — selected: {targets}")
    return result


if __name__ == "__main__":
    # Load the embedding model now, on the main thread. FastMCP runs tool calls in a worker
    # thread, and a first `import torch` from a worker thread can deadlock on Windows.
    try:
        router._embedder()
    except Exception as e:                      # routing falls back to query_repo when the model is unusable
        log("orchestrator_router", "WARN", f"embedding model not loaded at startup: {e}")
    mcp.run(transport="stdio")
