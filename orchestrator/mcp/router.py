"""OrchestratorRouter — routes by querying each repo's RAG via MCP.

Routing is based on actual content returned by each repo's RAG (docs, interfaces,
source code), not description embeddings. Config is read from config.yaml.
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from demo_logger import log


def _mcp_call(server_script: str, tool_name: str, arguments: dict, timeout: int = 60) -> str:
    """Send a single tool call to an MCP server over stdio and return the text result."""
    python = sys.executable
    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "orchestrator_router", "version": "1.0"}}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": tool_name, "arguments": arguments}})
        + "\n"
    )
    try:
        proc = subprocess.run(
            [python, server_script],
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "(MCP timeout)"
    except Exception as e:
        return f"(MCP error: {e})"

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("id") == 2:
                content = msg.get("result", {}).get("content", [])
                if content:
                    return content[0].get("text", "(empty response)")
        except json.JSONDecodeError:
            continue
    return "(no response)"


def _score_response(response: str) -> float:
    """Score relevance: 0.0 if no content found, else min(content_length/800, 1.0)."""
    if not response:
        return 0.0
    lower = response.lower()
    if "no relevant knowledge found" in lower:
        return 0.0
    if "(mcp" in lower or "(no response" in lower:
        return 0.0
    if "relevant knowledge:" in lower:
        idx = lower.index("relevant knowledge:")
        content = response[idx + len("relevant knowledge:"):].strip()
    else:
        content = response.strip()
    return min(len(content) / 800.0, 1.0)


class OrchestratorRouter:
    """
    Routes a feature request to the most relevant repos by querying each repo's
    RAG via MCP and ranking by how much relevant content is returned.
    Configuration is read from config.yaml in the same directory.
    """

    def __init__(self, config_path: Path):
        with open(config_path) as f:
            config = yaml.safe_load(f)

        self._repo_display: dict[str, str] = {}
        self._repo_mcp_scripts: dict[str, str] = {}

        for repo in config.get("repos", []):
            name = repo["name"]
            display = repo.get("display_name", name)
            raw_path = repo["path"]

            repo_path = Path(raw_path)
            if not repo_path.is_absolute():
                repo_path = (config_path.parent / repo_path).resolve()

            script = repo_path / "mcp" / "mcp_server.py"
            self._repo_display[name] = display
            self._repo_mcp_scripts[name] = str(script)

        log("orchestrator_router", "INFO",
            f"Router ready — will query {len(self._repo_mcp_scripts)} repo MCP servers")

    def get_relevant_repos(
        self, requesting_repo: str, feature_description: str, top_k: int = 2
    ) -> tuple[list[str], dict[str, float]]:
        """
        Query each peer repo's RAG via MCP and rank by content relevance.
        Returns (selected_repo_names, all_scores).
        """
        log("orchestrator_router", "ROUTING", f"Routing: '{feature_description[:70]}...'")
        peer_count = sum(1 for n in self._repo_mcp_scripts if n != requesting_repo)
        log("orchestrator_router", "ROUTING", f"Querying {peer_count} peer repos via MCP...")

        scores: dict[str, float] = {}
        for repo_name, script_path in self._repo_mcp_scripts.items():
            if repo_name == requesting_repo:
                log("orchestrator_router", "ROUTING", f"  {repo_name}: excluded (requesting repo)")
                continue
            log("orchestrator_router", "ROUTING",
                f"  Calling {self._repo_display.get(repo_name, repo_name)} MCP → query_repo...")
            response = _mcp_call(script_path, "query_repo", {"feature_request": feature_description})
            score = _score_response(response)
            scores[repo_name] = score
            log("orchestrator_router", "ROUTING",
                f"  {repo_name}: relevance={score:.3f} ({len(response)} chars returned)")

        sorted_repos = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        targets = [n for n, s in sorted_repos[:top_k] if s > 0]
        if len(targets) < top_k:
            for name, _ in sorted_repos:
                if name not in targets:
                    targets.append(name)
                if len(targets) >= top_k:
                    break

        log("orchestrator_router", "RESULT", f"Selected: {targets}")
        return targets, scores
