# Setting up the MCP servers

The skill needs the MCP servers from
[agentic-orchestration-framework](https://github.com/meenusinha/agentic-orchestration-framework).
Here is a short version of the setup. The project README has the full guide.

## 1. Get the framework and the embedding model

```bash
git clone https://github.com/meenusinha/agentic-orchestration-framework.git agentic-orchestration
cd agentic-orchestration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2 --local-dir models/all-MiniLM-L6-v2
```

## 2. List your repos

Edit `orchestrator/mcp/config.yaml`:

```yaml
repos:
  - name: frontend
    display_name: Frontend
    path: /abs/path/to/frontend
  - name: backend
    display_name: Backend
    path: /abs/path/to/backend
log_file: /tmp/mcp-orchestration.log
```

## 3. Add the agent files to each repo

```bash
cp -r repo-agent/mcp/       /abs/path/to/frontend/mcp/
cp -r repo-agent/knowledge/ /abs/path/to/frontend/knowledge/
```

In each repo, set `repo_name` in `mcp/config.yaml` to match the `name` in step 2, and
set `src_path`. Then put architecture and interface `.md` docs into `knowledge/`.

## 4. Generate the client config

From the venv:

```bash
python orchestrator/setup.py
```

It writes these files into every repo:

| Client | Files |
|---|---|
| Claude Code | `.mcp.json`, `.claude/skills/cross-repo-feature-analysis/` |
| Cursor | `.cursor/mcp.json` |
| VS Code + Copilot | `.vscode/mcp.json`, `.github/copilot-instructions.md` |

For any other agent that supports MCP, copy the `mcpServers` block from `.mcp.json`
into that agent's MCP config, and install this skill folder wherever the agent loads
skills from.

Re-run `setup.py` whenever you add or remove a repo.

## Troubleshooting

- **Tools don't appear**: restart the client or reload the MCP servers. In Claude Code,
  run `/mcp` to check the server status.
- **`ModuleNotFoundError`**: the server is running with the wrong Python. Re-run
  `setup.py` from the activated venv. The Claude Code and Cursor configs pin that
  interpreter.
- **Empty results**: check that `knowledge/` contains `.md` files. Delete `.chroma_db/`
  in the repo to force a re-index.
