#!/usr/bin/env python3
"""
Orchestrator setup — run this ONCE after editing mcp/config.yaml,
and again whenever you add or remove repos.

What it does:
  1. Reads mcp/config.yaml
  2. For every listed repo: generates .vscode/mcp.json (absolute paths, no env vars)
  3. For every listed repo: generates .github/copilot-instructions.md (repo-specific)
  4. Generates .vscode/mcp.json for the orchestrator itself
  5. Prints a summary

Usage:
  cd orchestrator/
  python setup.py
"""
import json
import sys
from pathlib import Path
from textwrap import dedent

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed.  Run: pip install pyyaml")
    sys.exit(1)

ORCHESTRATOR_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = ORCHESTRATOR_DIR / "mcp" / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"ERROR: Config not found at {CONFIG_PATH}")
    print("       Have you edited orchestrator/mcp/config.yaml?")
    sys.exit(1)

with open(CONFIG_PATH) as f:
    config = yaml.safe_load(f)

repos_cfg = config.get("repos", [])
log_file = config.get("log_file", "/tmp/mcp-orchestration.log")

if not repos_cfg:
    print("ERROR: No repos listed in mcp/config.yaml")
    sys.exit(1)

# ── Resolve all repo paths ────────────────────────────────────────────────────
resolved: list[dict] = []
for repo in repos_cfg:
    raw = repo["path"]
    p = Path(raw)
    if not p.is_absolute():
        p = (CONFIG_PATH.parent / p).resolve()
    if not p.exists():
        print(f"  WARN: path does not exist: {p}  (repo: {repo['name']})")
    resolved.append({
        "name":         repo["name"],
        "display_name": repo.get("display_name", repo["name"]),
        "path":         p,
    })

orchestrator_script    = str(ORCHESTRATOR_DIR / "mcp" / "router_mcp_server.py")
orchestrator_pythonpath = str(ORCHESTRATOR_DIR / "mcp")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _all_servers() -> dict:
    """Full servers dict: orchestrator + every repo."""
    servers = {
        "orchestrator_router": {
            "type": "stdio",
            "command": "python",
            "args": [orchestrator_script],
            "env": {
                "PYTHONPATH": orchestrator_pythonpath,
                "DEMO_LOG_FILE": log_file,
            },
        }
    }
    for r in resolved:
        servers[r["name"]] = {
            "type": "stdio",
            "command": "python",
            "args": [str(r["path"] / "mcp" / "mcp_server.py")],
            "env": {
                "PYTHONPATH": str(r["path"] / "mcp"),
                "DEMO_LOG_FILE": log_file,
            },
        }
    return servers


def _write_mcp_json(target_dir: Path) -> Path:
    vscode_dir = target_dir / ".vscode"
    vscode_dir.mkdir(exist_ok=True)
    out = vscode_dir / "mcp.json"
    with open(out, "w") as f:
        json.dump({"servers": _all_servers()}, f, indent=2)
    return out


def _write_copilot_instructions(repo: dict) -> Path:
    """Generate a repo-specific copilot-instructions.md with no placeholders."""
    name         = repo["name"]
    display      = repo["display_name"]
    repo_path    = repo["path"]

    # Read components from the repo's own config.yaml (best-effort)
    repo_config_path = repo_path / "mcp" / "config.yaml"
    components_str = ""
    if repo_config_path.exists():
        with open(repo_config_path) as f:
            rc = yaml.safe_load(f)
        comps = rc.get("components", [])
        if comps:
            components_str = f"\nYour components: **{'**, **'.join(comps)}**\n"

    # Peer repos (all repos except this one)
    peers = [r for r in resolved if r["name"] != name]
    peer_query_examples = "\n".join(
        f"```\n{p['name']}.query_repo(feature_request = \"<the feature request>\")\n```"
        for p in peers
    )
    peer_names_list = " / ".join(p["name"] for p in peers)

    content = dedent(f"""\
        # {display} Repo Agent

        You are the AI agent for the **{display}** repository.
        {components_str}
        ---

        ## Feature Request Agent Flow

        When a developer gives you a **feature request**, act as an autonomous agent
        and follow these steps IN ORDER. Do NOT skip steps. Do NOT answer from your
        own knowledge — you MUST call the MCP tools.

        ### Step 1 — Ask the Orchestrator which repos are relevant

        ```
        orchestrator_router.get_relevant_repos(
          requesting_repo = "{name}",
          feature_description = "<the feature request>"
        )
        ```

        The router queries every peer repo's knowledge base and returns the 2 most
        relevant ones. Note them.

        ### Step 2 — Query your own repo's knowledge

        ```
        {name}.query_repo(feature_request = "<the feature request>")
        ```

        ### Step 3 — Query each peer repo identified in Step 1

        Call only the repos the orchestrator selected (from: {peer_names_list}):

        {peer_query_examples}

        ### Step 4 — Generate the Feature Analysis Document

        ```markdown
        # Feature Analysis: <feature name>

        **Requesting Repo**: {display}
        **Date**: <today's date>

        ---

        ## Current State

        ### {display} (This Repo)
        <summarize what your own query_repo returned>

        ### <PeerRepo1>
        <summarize what that repo's query_repo returned>

        ### <PeerRepo2> (if applicable)
        <summarize what that repo's query_repo returned>

        ---

        ## Solution Design

        - Which components in which repos need to change
        - What new interfaces or APIs are needed
        - How the repos will coordinate
        - Risks or dependencies to consider
        ```

        ---

        ## Rules

        - ALWAYS call `get_relevant_repos` first — never skip the orchestrator
        - ALWAYS call your own `{name}.query_repo` even if not in the orchestrator's list
        - Base the Solution Design on retrieved knowledge, not assumptions
        - If a tool returns no relevant knowledge, note it in Current State
    """)

    github_dir = repo_path / ".github"
    github_dir.mkdir(exist_ok=True)
    out = github_dir / "copilot-instructions.md"
    with open(out, "w") as f:
        f.write(content)
    return out


# ── Run ───────────────────────────────────────────────────────────────────────
print(f"\nOrchestrator setup — {len(resolved)} repo(s) found in config\n")

for r in resolved:
    mcp_json_path = _write_mcp_json(r["path"])
    instructions_path = _write_copilot_instructions(r)
    print(f"  ✓ {r['name']}")
    print(f"      mcp.json            → {mcp_json_path}")
    print(f"      copilot-instructions→ {instructions_path}")

# Orchestrator's own mcp.json
orch_out = _write_mcp_json(ORCHESTRATOR_DIR)
print(f"\n  ✓ orchestrator")
print(f"      mcp.json            → {orch_out}")

print(f"""
Setup complete.

Next:
  1. Open each repo in VS Code (no terminal env vars needed)
  2. Switch Copilot chat to Agent mode
  3. Type a feature request
  4. Watch the live log:

       tail -f {log_file}
""")
