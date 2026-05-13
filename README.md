# Multi-Repo Agentic Orchestration Framework

Give your existing repos cross-repo AI awareness. When a developer asks about a feature
in one repo, the agent automatically queries peer repos' knowledge bases to understand
cross-cutting concerns, then produces a full Feature Analysis Document covering all
affected repos.

---

## How it works

```
Developer → feature request
               │
               ▼
    ┌─────────────────────┐
    │   Repo Agent (MCP)  │
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

## Two ways to use this framework

Both ways run the same agent pipeline. The difference is how you trigger it.

| | Way A — CLI (`test_mcp.py`) | Way B — VS Code + Copilot |
|---|---|---|
| **Trigger** | `python test_mcp.py "feature request"` | Type in Copilot Chat (Agent mode) |
| **VS Code required** | No | Yes |
| **GitHub Copilot required** | No | Yes |
| **Output** | `feature_analysis.md` file | Copilot synthesises inline in chat |
| **Best for** | Quick tests, demos, CI, no IDE | Day-to-day development workflow |
| **Extra setup needed** | None beyond steps 1–4 | Run `setup.py` + open VS Code (steps 5–6) |

**Complete steps 1–4 first.** After that, follow Way A or Way B (or both).

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.11+ | Required for both ways |
| VS Code | Way B only |
| GitHub Copilot extension (Agent mode) | Way B only |

---

## Common setup — steps 1–4 (required for both ways)

### Step 1 — Get the framework

Clone this repo to any location on your machine:

```bash
git clone https://github.com/meenusinha/agentic-orchestration-framework.git agentic-orchestration
cd agentic-orchestration
```

#### Download the embedding model (required)

The framework uses a local embedding model for RAG. Copy the model files into the repo
**before running anything**. Without them the MCP servers will fail to start.

Copy the `all-MiniLM-L6-v2` model folder into the repo so the layout looks like this:

```
agentic-orchestration/
└── models/
    └── all-MiniLM-L6-v2/
        ├── config.json
        ├── tokenizer.json
        ├── tokenizer_config.json
        ├── vocab.txt
        ├── special_tokens_map.json
        ├── sentence_bert_config.json
        ├── modules.json
        ├── pytorch_model.bin      ← ~90 MB
        └── 1_Pooling/
            └── config.json
```

If you have internet access on the machine, download directly:

```bash
pip install huggingface_hub
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2 \
    --local-dir models/all-MiniLM-L6-v2
```

If not (e.g. an air-gapped office laptop), download on a machine with internet using
the command above, then copy the entire `models/` folder across via USB or shared drive.

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

Then open `/path/to/my-repo/mcp/config.yaml` and fill in the required fields:

```yaml
repo_name: frontend          # must EXACTLY match the name in orchestrator/mcp/config.yaml
display_name: Frontend
src_path: ./src              # path, glob (e.g. ./modules/*/src), or list of paths
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

## Way A — CLI with `test_mcp.py`

Once steps 1–4 are complete, you can run the full agent pipeline from the terminal —
no VS Code, no Copilot extension needed.

`test_mcp.py` drives all three phases (preflight → routing → per-repo RAG) and writes
`feature_analysis.md` ready to paste into any LLM or Copilot chat.

### The three phases

```
python test_mcp.py "your feature request"
         │
         ├── Phase 0 · Preflight
         │      Pings each repo's MCP server to confirm it responds.
         │
         ├── Phase 1 · Routing
         │      Spawns each peer repo's MCP server as a subprocess.
         │      Each returns relevant knowledge. Router scores by content length.
         │      Top-2 most relevant repos are selected.
         │
         └── Phase 2 · Query all repos
                Queries own repo + all peer repos for the full knowledge set.
                Writes everything to feature_analysis.md.
```

### Run it

**Terminal 1 — watch the live log:**
```bash
tail -f /tmp/mcp-orchestration.log
```

**Terminal 2 — run the flow:**
```bash
source .venv/bin/activate   # activate the venv first
python test_mcp.py "add real-time validation feedback to the login form"
```

### What you see in the log

```
[12:34:01.120] [frontend    ] [INDEX    ] Index already cached — loading from .chroma_db/
[12:34:01.140] [backend     ] [INDEX    ] Index already cached — loading from .chroma_db/

[12:34:01.160] [orchestrator] [ROUTING  ] Routing: 'add real-time validation feedback...'
[12:34:01.165] [orchestrator] [ROUTING  ]   Calling Backend MCP → query_repo...
[12:34:01.420] [backend     ] [RAG      ] Searching docs (top_k=3)...
[12:34:01.480] [backend     ] [RESULT   ] query_repo → 640 chars returned
[12:34:01.481] [orchestrator] [ROUTING  ]   backend: relevance=0.800
[12:34:01.485] [orchestrator] [ROUTING  ]   Calling Payments Service MCP → query_repo...
[12:34:01.700] [payments    ] [RAG      ] Docs thin — searching source code...
[12:34:01.760] [payments    ] [RESULT   ] query_repo → 92 chars returned
[12:34:01.761] [orchestrator] [ROUTING  ]   payments: relevance=0.115
[12:34:01.763] [orchestrator] [RESULT   ] Selected: ['backend', 'payments']

[12:34:01.780] [frontend    ] [RAG      ] Searching docs (top_k=3) + source code (top_k=3)...
[12:34:01.940] [backend     ] [RAG      ] Searching docs (top_k=3) + source code (top_k=3)...
[12:34:02.110] [payments    ] [RAG      ] Searching docs (top_k=3) + source code (top_k=3)...

✅  feature_analysis.md written
```

### What you get

`feature_analysis.md` in the working directory — a cross-repo document with routing
scores, relevant knowledge from every repo, and instructions for an LLM to generate a
Solution Design. Paste it straight into Copilot Chat or any other LLM.

```markdown
# Feature Analysis: add real-time validation feedback to the login form

## Routing
Selected repos (by RAG relevance): backend (0.800), payments (0.115)

## frontend — own repo
[From documentation]
AuthModule handles login, JWT refresh, and session persistence...

## backend — peer repo
[From documentation]
Validation middleware applies to all /api/auth endpoints...

## Solution Design Instructions
Based on the above cross-repo knowledge, implement ...
```

> **First run is slower** — each MCP server builds its RAG index on the first call
> (~10–60 s per repo depending on size). Subsequent runs load the cached index from
> `.chroma_db/` in milliseconds.

---

## Way B — VS Code + GitHub Copilot

Once steps 1–4 are complete, two more steps wire everything into VS Code and Copilot.

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

You must launch VS Code **from the terminal where your venv is active** — VS Code
inherits the Python from that terminal, and all MCP servers run with it.

```bash
# Make sure the venv is active first
source /path/to/agentic-orchestration/.venv/bin/activate   # Mac / Linux
# or
/path/to/agentic-orchestration/.venv/Scripts/activate       # Windows

# Then open each repo
code /path/to/frontend
code /path/to/backend
code /path/to/payments
```

VS Code automatically loads `.vscode/mcp.json` from the workspace folder.
All MCP servers (orchestrator + all repos) start automatically and stay running.

> **Do not open VS Code from Finder or the Dock** — it won't inherit the venv and
> the MCP servers will fail with `ModuleNotFoundError`.

> **First-time MCP activation**: VS Code may show a notification asking you to allow
> the MCP servers. Click **Allow** for each one.

---

### Step 7 — Give a feature request in Copilot

1. Open the **Copilot Chat** panel in any repo's VS Code window
2. Click the mode selector and switch to **Agent** mode
3. Type your feature request, for example:

   > Add real-time validation feedback to the login form that checks password strength

4. Copilot calls the MCP tools in sequence — you'll see tool calls in the chat.

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
4. **Way A**: ready immediately — `test_mcp.py` picks up the new repo from config
5. **Way B**: re-run `python orchestrator/setup.py` to regenerate `mcp.json` for all repos, then open the new repo in VS Code

---

## Re-indexing after changing docs or source code

The RAG index is cached in each repo's `.chroma_db/` directory.
To force a full rebuild after updating docs:

```bash
rm -rf /path/to/my-repo/.chroma_db
```

The next MCP server start rebuilds the index automatically.

---

## File reference

```
agentic-orchestration-framework/        ← this repo (cloned once)
├── requirements.txt                     ← install once, shared by all repos
├── test_mcp.py                          ← ★ WAY A: run this directly
├── orchestrator/                        ← never copied into your repos
│   ├── mcp/
│   │   ├── config.yaml                  ← ★ EDIT THIS: list your repos + paths
│   │   ├── router.py                    ← generic routing logic (do not edit)
│   │   ├── router_mcp_server.py         ← MCP server (do not edit)
│   │   └── demo_logger.py               ← logging (do not edit)
│   └── setup.py                         ← ★ WAY B: run this to generate VS Code config
│
└── repo-agent/                          ← copy contents into each of your repos
    ├── mcp/
    │   ├── config.yaml                  ← ★ EDIT THIS per repo (repo_name, src_path)
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
|---|---|
| MCP servers not appearing in Copilot | Reload VS Code window (`Cmd+Shift+P` → Reload Window) |
| `ModuleNotFoundError: yaml` | Run `pip install pyyaml` in your venv |
| `ModuleNotFoundError: chromadb` | Run `pip install -r requirements.txt` in your venv |
| VS Code launched from wrong terminal (wrong Python) | Run `source .venv/bin/activate` then `code .` from that same terminal |
| RAG returns nothing | Check that `knowledge/` has `.md` files; delete `.chroma_db/` to re-index |
| `OSError: No such file or directory: '.../all-MiniLM-L6-v2'` | Model files missing — copy `models/all-MiniLM-L6-v2/` into the repo root |
| `path does not exist` warning in setup.py | The path in `orchestrator/mcp/config.yaml` is wrong or the repo isn't cloned yet |
| `test_mcp.py` hangs on first run | Expected — first run builds the RAG index; wait 10–60 s per repo |
