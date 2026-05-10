# Multi-Repo Agentic Orchestration Framework

Give your existing repos cross-repo AI awareness. When a developer asks Copilot
for a feature in one repo, the agent automatically queries peer repos' knowledge
bases to understand cross-cutting concerns, then produces a full Feature Analysis
Document covering all affected repos.

---

## How it works

```
Developer → feature request in VS Code Copilot (Agent mode)
               │
               ▼
    ┌─────────────────────┐
    │   Repo Agent (MCP)  │  ← runs in the active VS Code session
    └─────────────────────┘
               │
    Step 1 ────┤── orchestrator_router.get_relevant_repos()
               │       Orchestrator calls each peer repo's RAG via MCP.
               │       Ranks by how much relevant content is found.
               │       Returns the 2 most relevant peer repo names.
               │
    Step 2 ────┤── <this_repo>.query_repo()
               │       Searches own knowledge/ docs + source code.
               │
    Step 3 ────┤── <peer1>.query_repo()  +  <peer2>.query_repo()
               │       Searches each selected peer's knowledge + source.
               │
    Step 4 ─────── Synthesise → Feature Analysis Document
```

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.11 or newer |
| VS Code | Any recent version |
| GitHub Copilot extension | With Agent mode enabled |

---

## Step-by-step setup

### Step 1 — Get the framework

Clone this repo to any location on your machine:

```bash
git clone https://github.com/meenusinha/agentic-orchestration-framework.git agentic-orchestration
cd agentic-orchestration
```

#### Install on Mac / Linux

Check you have Python 3.11 or newer:

```bash
python3 --version
```

If not installed, on Mac:
```bash
brew install python@3.11
```

On Ubuntu / Debian:
```bash
sudo apt update && sudo apt install python3.11 python3.11-venv
```

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Important**: always activate this venv (`source .venv/bin/activate`) before
> running `setup.py` or any MCP server manually. VS Code must also be launched
> from a terminal where the venv is active so it picks up the right Python.

#### Install on Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

### Step 2 — Tell the orchestrator which repos to connect

Open `orchestrator/mcp/config.yaml` and list your repos:

```yaml
repos:
  - name: frontend                       # unique id — no spaces
    display_name: Frontend               # shown in routing output
    path: /Users/alice/projects/frontend # absolute path to your repo

  - name: backend
    display_name: Backend
    path: /Users/alice/projects/backend

  - name: payments
    display_name: Payments Service
    path: /Users/alice/projects/payments

log_file: /tmp/mcp-orchestration.log    # where live logs are written
```

> **Paths** can be absolute or relative to the `orchestrator/mcp/` directory.
> Add as many repos as you like.

---

### Step 3 — Add the agent files to each of your repos

For **each repo** listed in step 2, copy three directories into it:

```bash
# Replace /path/to/my-repo with your actual repo path — repeat for every repo

cp -r repo-agent/mcp/        /path/to/my-repo/mcp/
cp -r repo-agent/knowledge/  /path/to/my-repo/knowledge/
cp -r repo-agent/.github/    /path/to/my-repo/.github/
```

Then open `/path/to/my-repo/mcp/config.yaml` and fill in the three required fields:

```yaml
repo_name: frontend          # must EXACTLY match the name in orchestrator/mcp/config.yaml
display_name: Frontend
components:
  - AuthModule               # key classes / modules in this repo (shown to the AI)
  - Router
  - SessionManager

src_path: ./src              # path to source code (relative to repo root, or absolute)
                             # set to null to skip source indexing
knowledge_path: ./knowledge  # where you drop your .md docs (default is fine)
```

---

### Step 4 — Drop documentation into each repo's `knowledge/` folder

Add `.md` files that describe the repo's architecture, components, and interfaces.
The AI will retrieve from these when answering feature requests.

```bash
# Example — frontend repo
cat > /path/to/frontend/knowledge/architecture.md << 'EOF'
# Frontend Architecture

The frontend is a React SPA. Core modules:

## AuthModule
Handles login, JWT refresh, and session persistence.
Calls /api/auth endpoints. State managed in Redux slice `authSlice`.

## Router
Uses React Router v6. Protected routes check `authSlice.isAuthenticated`.

## SessionManager
Manages idle timeout and token expiry. Fires SESSION_EXPIRED action.
EOF
```

More specific docs = better retrieval. See `repo-agent/knowledge/README.md` for tips.

---

### Step 5 — Run setup (generates all VS Code config files)

```bash
cd orchestrator/
python setup.py
```

Expected output:

```
Orchestrator setup — 3 repo(s) found in config

  ✓ frontend
      mcp.json             → /Users/alice/projects/frontend/.vscode/mcp.json
      copilot-instructions → /Users/alice/projects/frontend/.github/copilot-instructions.md

  ✓ backend
      mcp.json             → /Users/alice/projects/backend/.vscode/mcp.json
      copilot-instructions → /Users/alice/projects/backend/.github/copilot-instructions.md

  ✓ payments
      mcp.json             → /Users/alice/projects/payments/.vscode/mcp.json
      copilot-instructions → /Users/alice/projects/payments/.github/copilot-instructions.md

  ✓ orchestrator
      mcp.json             → /Users/alice/agentic-orchestration/orchestrator/.vscode/mcp.json

Setup complete.
```

This generates two files per repo:
- **`.vscode/mcp.json`** — wires up all MCP servers with absolute paths (no env vars needed)
- **`.github/copilot-instructions.md`** — repo-specific agent instructions for Copilot

> **Re-run `setup.py`** any time you add or remove a repo from `orchestrator/mcp/config.yaml`.

---

### Step 6 — Open each repo in VS Code

Open a new VS Code window for each repo. You can do this from any terminal (env vars
are not required — everything uses absolute paths from step 5):

```bash
code /path/to/frontend
code /path/to/backend
code /path/to/payments
```

VS Code automatically loads `.vscode/mcp.json` from the workspace folder.
All MCP servers (orchestrator + all repos) are available immediately.

> **First-time MCP activation**: VS Code may show a notification asking you to allow
> the MCP servers. Click **Allow** for each one.

---

### Step 7 — Give a feature request

1. Open the **Copilot Chat** panel in any repo's VS Code window
2. Click the mode selector and switch to **Agent** mode
3. Type your feature request, for example:

   > Add real-time validation feedback to the login form that checks password strength

4. Copilot will call the MCP tools in sequence — you'll see tool calls in the chat.

---

### Step 8 — Watch the live log (optional but great for demos)

Open a terminal and run:

```bash
tail -f /tmp/mcp-orchestration.log
```

You'll see every MCP call, RAG query, and routing decision in real time:

```
[12:34:01.123] [frontend              ] [TOOL_CALL ] query_repo: 'Add real-time validation...'
[12:34:01.456] [orchestrator_router  ] [ROUTING   ] Routing: 'Add real-time validation...'
[12:34:01.460] [orchestrator_router  ] [ROUTING   ]   Calling Backend MCP → query_repo...
[12:34:01.700] [backend              ] [RAG       ] Searching docs (top_k=3)...
[12:34:01.750] [backend              ] [RESULT    ] query_repo → 480 chars returned
[12:34:01.751] [orchestrator_router  ] [ROUTING   ]   backend: relevance=0.600
[12:34:01.760] [orchestrator_router  ] [ROUTING   ]   Calling Payments Service MCP → query_repo...
[12:34:01.900] [payments             ] [RAG       ] Docs thin — searching source code...
[12:34:01.921] [payments             ] [RESULT    ] query_repo → 90 chars returned
[12:34:01.922] [orchestrator_router  ] [ROUTING   ]   payments: relevance=0.113
[12:34:01.923] [orchestrator_router  ] [RESULT    ] Selected: ['backend', 'payments']
```

---

## Adding a new repo later

1. Add an entry to `orchestrator/mcp/config.yaml`
2. Copy `repo-agent/mcp/`, `knowledge/`, `.github/` into the new repo
3. Edit the new repo's `mcp/config.yaml`
4. Run `python orchestrator/setup.py` — it regenerates mcp.json for **all** repos
5. Open the new repo in VS Code

---

## Re-indexing after changing docs or source code

The RAG index is cached in each repo's `.chroma_db/` directory.
To force a full rebuild after updating docs:

```bash
rm -rf /path/to/my-repo/.chroma_db
```

The next time the MCP server starts (next VS Code session or MCP restart), it rebuilds.

---

## File reference

```
agentic-orchestration-framework/        ← this repo (cloned once)
├── requirements.txt                     ← install once, shared by all repos
├── orchestrator/                        ← never copied into your repos
│   ├── mcp/
│   │   ├── config.yaml                  ← ★ EDIT THIS: list your repos + paths
│   │   ├── router.py                    ← generic routing logic (do not edit)
│   │   ├── router_mcp_server.py         ← MCP server (do not edit)
│   │   └── demo_logger.py               ← logging (do not edit)
│   └── setup.py                         ← ★ RUN THIS after editing config.yaml
│
└── repo-agent/                          ← copy contents into each of your repos
    ├── mcp/
    │   ├── config.yaml                  ← ★ EDIT THIS per repo (repo_name, components, src_path)
    │   ├── mcp_server.py                ← generic (do not edit)
    │   ├── repo_rag.py                  ← generic (do not edit)
    │   └── demo_logger.py               ← generic (do not edit)
    ├── knowledge/
    │   └── README.md                    ← ★ DROP YOUR .md DOCS HERE
    └── .github/
        └── copilot-instructions.md      ← template; setup.py overwrites with repo-specific version
```

**Files marked ★ are the only ones you ever touch.**

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| MCP servers not appearing in Copilot | Reload VS Code window (`Cmd+Shift+P` → Reload Window) |
| `ModuleNotFoundError: yaml` | Run `pip install pyyaml` in your venv |
| `ModuleNotFoundError: chromadb` | Run `pip install -r requirements.txt` in your venv |
| VS Code launched from wrong terminal (wrong Python) | Run `source .venv/bin/activate` then `code .` from that same terminal |
| RAG returns nothing | Check that `knowledge/` has `.md` files; delete `.chroma_db/` to re-index |
| `path does not exist` warning in setup.py | The path in `orchestrator/mcp/config.yaml` is wrong or the repo isn't cloned yet |
