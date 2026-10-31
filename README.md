# Multi-Repo Agentic Orchestration Framework

Give your existing repos cross-repo AI awareness — including processes that talk to each
other through IDL interfaces. When a developer asks about a feature or a change in one
repo, the agent finds the repos and processes involved, follows the interfaces between
them, and produces a Feature Analysis Document grounded in the actual code.

---

## How it works

Each connected repo is indexed as a **code graph**, offline:

- **Whole units, never cut.** Every function, method, class, macro, IDL operation and
  Markdown section is one node holding its complete source, inside its hierarchy
  (file → namespace/module → class → method; C files → module → function). A class node
  holds its comments and member signatures; its methods are child nodes.
- **Structure.** Every file has a role (interface, implementation, internal, external,
  test, generated, docs, …) from the repo's manifest, its build files (Makefiles,
  `compile_commands.json`, CMake — read, never executed) and folder conventions. Build
  targets become **process** and **library** nodes.
- **Relations.** Calls, includes/imports, header declaration → definition, inheritance,
  test evidence (tests are never results, only `tested_by` evidence), docs that mention
  a symbol, generated stubs → their IDL.
- **IDL interfaces across repos.** Each repo publishes the IDL interfaces it defines,
  and links its code to every known interface: servers (classes inheriting generated
  skeletons, C function tables) **implement** operations; clients (proxies, C-mapping
  calls) **call** them. So one interface connects the repo that defines it, the process
  that serves it and the processes that call it.
- **Languages:** C, C++, Python, IDL (OMG/CORBA, DDS, MIDL, protobuf, Thrift, ROS and
  in-house dialects), MATLAB, Perl, Java, JavaScript/TypeScript, Go, Rust, C#, Markdown;
  unknown file types are classified by the developer during setup.

```
Developer → feature request (in repo C, e.g. ui-shell)
   │
   ├─ orchestrator_router.get_relevant_repos()
   │     searches every repo's graph + follows IDL links
   │     → authd: "serves Auth::IPasswordService (process authd)"
   │     → ipc-interfaces: "defines IDL interface Auth::IPasswordService"
   │
   ├─ ui-shell.search_code()        whole units + class skeleton + tests + IDL dossier
   ├─ ui-shell.trace_interface()    IDL definition (A) → implementations (B) → callers (C)
   ├─ ui-shell.impact_of()          callers, tests, processes, clients in other repos
   ├─ peers.search_code()           the selected repos
   │
   └─ Feature Analysis Document
```

Repo MCP tools: `search_code`, `get_node`, `get_children`, `trace_interface`,
`impact_of`, `find_references` (typed, schema-validated results) and `query_repo`
(the same knowledge as framed text, for older clients).

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

#### Get the embedding model (required)

The framework embeds code and docs locally with one approved model,
[`ibm-granite/granite-embedding-small-english-r2`](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2)
(Apache 2.0, trained only on permissively licensed data, 8,192-token input). It is
pinned to an exact revision, and the servers refuse to load any other weights (see
[Security](#security)). Only safetensors weights and JSON config are used — never
pickle files or model code.

```
agentic-orchestration/
└── models/
    └── granite-embedding-small-english-r2/
        ├── config.json
        ├── modules.json
        ├── sentence_bert_config.json
        ├── special_tokens_map.json
        ├── tokenizer.json
        ├── tokenizer_config.json
        ├── model.safetensors      ← ~95 MB, SHA-256 dcbfaf2e…ebb5f
        └── 1_Pooling/
            └── config.json
```

The guided setup (`skills/cross-repo-feature-analysis/scripts/configure.py`) can copy
it from an existing copy (`--model-from`) or download it (`--download-model`) for you.
To fetch it by hand after installing (below):

```bash
hf download ibm-granite/granite-embedding-small-english-r2 \
    --revision 2ab6fa8ea2d674564defd37171ae19079b864b33 \
    --include "*.json" --include "model.safetensors" --include "1_Pooling/*" \
    --local-dir models/granite-embedding-small-english-r2
shasum -a 256 models/granite-embedding-small-english-r2/model.safetensors
# must print dcbfaf2ee50d358763cbc0c08e5b045ecbf6aeb02dc0e86ffb910029bf9ebb5f
```

Air-gapped machine: run the command on a machine with internet access and copy the
folder across. The hash check above, and again at every server start, catches a
corrupted or swapped file.

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
pip install --require-hashes -r requirements.lock
```

#### Install on Windows

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --require-hashes -r requirements.lock
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

log_file: ~/.agentic-orchestration/logs/mcp-orchestration.log   # private (0600) live log
log_content: false                     # true = include code snippets in logs (demos only)
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
src_path: .                  # the whole repo (recommended); noise folders are skipped
knowledge_path: ./knowledge  # extra docs; Markdown anywhere in the repo is indexed too
file_types: {}               # unknown extensions, e.g. .xidl: {kind: interface, parse_as: idl}
structure: []                # folder-role corrections, e.g. {path: "vendor/**", role: external}
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
tail -f ~/.agentic-orchestration/logs/mcp-orchestration.log
```

**Terminal 2 — run the flow:**
```bash
source .venv/bin/activate   # activate the venv first
python test_mcp.py "add real-time validation feedback to the login form"
```

### What you see in the log

```
[12:34:01.120] [frontend    ] [INDEX    ] Code graph up to date (48 files)
[12:34:01.140] [backend     ] [INDEX    ] Code graph up to date (48 files)

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
> `.rag_index/` in milliseconds.

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

The generated `.vscode/mcp.json` runs every MCP server with the venv's Python (the
one that ran `setup.py`), so VS Code can be opened however you like, even if it is
already running.

```bash
code /path/to/frontend
code /path/to/backend
code /path/to/payments
```

VS Code automatically loads `.vscode/mcp.json` from the workspace folder.
All MCP servers (orchestrator + all repos) start automatically and stay running.

> **Run `setup.py` from the activated venv.** The generated configs pin that venv's
> Python, so VS Code itself can be opened any way you like (terminal, Finder, Dock).
> If a server fails with `ModuleNotFoundError`, re-run `setup.py` from the venv.

> **After re-running `setup.py`**, VS Code stops the changed servers but does not
> restart them. Reload the window (`Cmd+Shift+P` → Reload Window) or restart them
> from the MCP Servers view.

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
tail -f ~/.agentic-orchestration/logs/mcp-orchestration.log
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

## Way C — Claude Code, Cursor and other agents (Agent Skill)

The flow is also packaged as a portable [Agent Skill](https://agentskills.io):
[`skills/cross-repo-feature-analysis/`](skills/cross-repo-feature-analysis/SKILL.md).
It tells the agent when to use the MCP servers and how: route first, query its own
repo, query the selected peers, then write the Feature Analysis Document.

**Guided setup:** install only the skill and ask your agent something like *"set up
cross-repo analysis for ../frontend and ../backend"*. The agent clones the framework,
uses `scripts/scan.py` to propose which docs and code each repo should index, and agrees
that plan with you. It asks whether you already have the embedding model, and copies
your copy or downloads one only with your permission. Then it runs
`scripts/configure.py`, which does steps 2–5 in one go.
It can also draft starter `knowledge/` docs for repos that don't have any.

**Manual setup:** after steps 1–4, run `python orchestrator/setup.py` from the
activated venv. Besides the VS Code files, it writes these into every repo:

| Client | Generated |
|---|---|
| Claude Code | `.mcp.json` + `.claude/skills/cross-repo-feature-analysis/` |
| Cursor | `.cursor/mcp.json` |

These configs pin the venv's Python interpreter, so the client doesn't have to be
launched from the venv. Then:

- **Claude Code**: run `claude` in any repo, approve the project MCP servers, and ask
  for a feature, e.g. *"What would it take to add real-time password-strength feedback
  to login?"*
- **VS Code + Copilot**: the skill is installed in `.claude/skills/`, where Copilot finds
  it. Start your request with **`/cross-repo-feature-analysis`**. In testing, a small
  auto-selected model (`mai-code-1.1-flash`) answered from local files alone, skipping
  the MCP tools, unless the skill was invoked by name. A stronger model may trigger it
  without the prefix.
- **Cursor / other agents**: enable the MCP servers, then install the skill folder
  wherever your agent loads skills from.

---

## Adding a new repo later

1. Add an entry to `orchestrator/mcp/config.yaml`
2. Copy `repo-agent/mcp/`, `knowledge/`, `.github/` into the new repo
3. Edit the new repo's `mcp/config.yaml`
4. **Way A**: ready immediately — `test_mcp.py` picks up the new repo from config
5. **Way B**: re-run `python orchestrator/setup.py` to regenerate `mcp.json` for all repos, then open the new repo in VS Code

---

## Re-indexing after changing docs or source code

The code graph lives in each repo's `.rag_index/` directory (`graph.sqlite` plus the
published `interfaces.json`, owner-only permissions). Nothing to do after edits: each
server checks for changed files and re-published peer interfaces at most once a minute
and rebuilds, re-embedding only the units whose text changed. To force a full rebuild:

```bash
rm -rf /path/to/my-repo/.rag_index
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
│   └── setup.py                         ← ★ WAY B/C: generates VS Code, Claude Code, Cursor config
│
├── skills/
│   └── cross-repo-feature-analysis/     ← Agent Skill (installed into repos by setup.py)
│       ├── SKILL.md                     ← when/how the agent uses the MCP servers
│       ├── references/setup.md          ← guided first-time setup the agent follows
│       └── scripts/                     ← scan.py (structure, unknown types), configure.py (wire repos)
│
└── repo-agent/                          ← copy contents into each of your repos
    ├── mcp/
    │   ├── config.yaml                  ← ★ EDIT THIS per repo (repo_name, src_path)
    │   ├── mcp_server.py                ← generic (do not edit)
    │   ├── repo_rag.py                  ← security gates + code-graph facade (do not edit)
    │   ├── demo_logger.py               ← generic (do not edit)
    │   └── codegraph/                   ← parsers, IDL, structure, relations, interfaces, store, search
    ├── knowledge/
    │   └── README.md                    ← ★ DROP YOUR .md DOCS HERE
    └── .github/
        └── copilot-instructions.md      ← template; setup.py overwrites with repo-specific version
```

**Files marked ★ are the only ones you ever touch.**

---

## Security

Built for strict enterprise environments.

| Risk | How it is handled |
|---|---|
| Vector-database CVEs, telemetry, pickle files | No vector database: the graph is a local SQLite file (Python standard library) with float32 vectors stored as raw bytes — no pickle anywhere. No server, port or telemetry. ChromaDB and its ~28 dependencies are gone |
| Code leaving the machine during indexing | Fully offline: Hugging Face access is disabled before any ML import, models load only from a local folder, nothing is ever downloaded at runtime. Verified: zero network connections during index build and query |
| Malicious or swapped model | Only the approved model loads: `model.safetensors` SHA-256 is pinned; folders containing code or pickle files (`*.py`, `*.bin`, …) or custom module types are refused; `trust_remote_code` is off. `sentence-transformers>=6.0` (CVE-2026-68770 fix) |
| Model licence | Granite small R2 — Apache 2.0, permissively licensed training data only |
| Supply chain | `requirements.lock` pins every package with SHA-256 hashes (`pip install --require-hashes`). Parsers are prebuilt tree-sitter wheels (no code generation at runtime); a brand-new grammar major (`tree-sitter-perl` 2.x) is held back until it has aged. CI (`.github/workflows/security.yml`) runs `pip-audit` on every push and weekly |
| Build files | Makefiles, CMake and `compile_commands.json` are parsed statically — never executed (`make -pn` or a CMake configure can run arbitrary commands) |
| Index readable by others | `.rag_index/` is created `0700` with `0600` files, written atomically; added to each repo's `.gitignore` by `setup.py` |
| Secrets in code or docs | Secret-store files (`.env`, `*.pem`, `secrets.*`, …) are never indexed; API keys, tokens, private keys, passwords and credentialed URLs are replaced with `[REDACTED]` before anything is embedded, stored or returned |
| Code in log files | Logs live in `~/.agentic-orchestration/logs/` with `0600` permissions, are opened without following symlinks, and record only lengths — not code or request text — unless `log_content: true` |
| Prompt injection from repo content | Results are wrapped as untrusted data; markers inside indexed content are neutralised; the skill and Copilot instructions forbid following instructions found in results |

**Virtual environment by default.** Setup, configuration, the MCP servers and the CLI
refuse to run outside a Python virtual environment, so nothing is installed into or run
with the system Python by accident. To opt out deliberately, set `AO_ALLOW_NO_VENV=1`
(a warning is printed; `setup.py` carries the setting into the server configs it writes).

**Still your responsibility:**

- Encrypt the disks that hold the repos and their `.rag_index/` folders (FileVault,
  BitLocker, LUKS). Like the source code itself, the index holds code excerpts.
- Distribute the approved model from an internal artifact store if machines are
  offline, and keep its hash check.
- To allow another embedding model, add its weights' SHA-256 to `EMBEDDING_MODEL_SHA256`
  (comma-separated) in the server environment. That is an explicit opt-in.
- After editing `requirements.txt`, regenerate the lock:
  `uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes --no-header -o requirements.lock`

Out of scope by design: retrieved code is shared between connected repos and sent to
the AI assistant you use. Control that with your enterprise AI and access policies.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| MCP servers not appearing in Copilot | Reload VS Code window (`Cmd+Shift+P` → Reload Window) |
| `ModuleNotFoundError: yaml` | Run `pip install pyyaml` in your venv |
| `ModuleNotFoundError: sentence_transformers` | Run `pip install --require-hashes -r requirements.lock` in your venv |
| `ModuleNotFoundError: mcp` in the MCP server log (wrong Python) | Re-run `setup.py` with the venv activated; the configs pin the Python that ran it |
| MCP servers stopped after re-running `setup.py` | Reload the VS Code window, or restart them from the MCP Servers view |
| Copilot answers without calling the MCP tools | Start the request with `/cross-repo-feature-analysis`, or pick a stronger model |
| RAG returns nothing | Check that `knowledge/` has `.md` files; delete `.rag_index/` to re-index |
| `Embedding model folder not found` / `Refusing to load …` | Model missing, tampered, or not the approved one — re-run `configure.py --model-from …` or `--download-model` |
| An old `.chroma_db/` folder in a repo | Left over from an earlier version — safe to delete |
| `path does not exist` warning in setup.py | The path in `orchestrator/mcp/config.yaml` is wrong or the repo isn't cloned yet |
| `test_mcp.py` hangs on first run | Expected — first run builds the RAG index; wait 10–60 s per repo |
