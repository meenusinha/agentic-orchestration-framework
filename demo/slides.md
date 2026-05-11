---
marp: true
theme: default
paginate: true
style: |
  section {
    background: #0d1117;
    color: #e6edf3;
    font-family: 'Segoe UI', Arial, sans-serif;
  }
  h1 { color: #58a6ff; font-size: 2em; }
  h2 { color: #f0f6fc; font-size: 1.5em; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
  h3 { color: #58a6ff; font-size: 1.1em; }
  strong { color: #58a6ff; }
  code { background: #161b22; color: #3fb950; padding: 2px 6px; border-radius: 4px; }
  pre  { background: #161b22; border: 1px solid #30363d; border-radius: 8px; }
  pre code { color: #3fb950; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #161b22; color: #58a6ff; border: 1px solid #30363d; padding: 8px 12px; }
  td { border: 1px solid #30363d; padding: 8px 12px; color: #c9d1d9; font-size: 0.85em; }
  .lead h1 { font-size: 2.4em; }
  a { color: #58a6ff; }
  section::after { color: #8b949e; font-size: 0.75em; }
  blockquote { border-left: 4px solid #58a6ff; color: #8b949e; background: #161b22; padding: 10px 16px; border-radius: 0 6px 6px 0; }
---

<!-- _class: lead -->
<!-- _paginate: false -->

# Multi-Repo Agentic Orchestration Framework

### Give your AI assistant cross-repo awareness

&nbsp;

`MCP` &nbsp;·&nbsp; `RAG` &nbsp;·&nbsp; `ChromaDB` &nbsp;·&nbsp; `sentence-transformers` &nbsp;·&nbsp; `FastMCP` &nbsp;·&nbsp; `GitHub Copilot`

---

## The Problem — Copilot sees only one repo

&nbsp;

```
  📦 Frontend       🤖 Copilot        📦 Backend        📦 Services
  ─────────────     ─────────────     ─────────────     ─────────────
  your code          only sees          invisible         invisible
                    this repo              ❌                ❌
```

&nbsp;

- 🔴 Feature requests span **multiple repos** — but Copilot answers from one
- 🔴 Suggested changes **break contracts** with other repos it cannot see
- 🔴 Developer must **manually gather context** before every cross-repo question
- 🔴 Solutions are **incomplete** — only one piece of the puzzle

---

## The Solution — An orchestra of specialised agents

&nbsp;

```
          🤖 GitHub Copilot  (Agent mode)
                    │
                    ▼
           ┌────────────────┐
           │  Orchestrator  │  ← ranks repos by RAG relevance score
           └────────────────┘
            /       |       \
           ▼        ▼        ▼
       📦 Repo A  📦 Repo B  📦 Repo C
       (agent)   (agent)   (agent)
       RAG index  RAG index  RAG index
```

&nbsp;

> Each repo has its own knowledge base. The orchestrator routes intelligently.
> Copilot synthesises everything into a **complete cross-repo answer**.

---

## Architecture — How it all fits together

&nbsp;

| Layer | Component | Role |
|---|---|---|
| **AI Interface** | GitHub Copilot + Agent mode | Developer's entry point |
| **Instructions** | `copilot-instructions.md` | Tells Copilot to call MCP tools |
| **Tool Config** | `.vscode/mcp.json` (auto-generated) | Wires up all MCP servers |
| **Orchestrator** | `router_mcp_server.py` | Routes to relevant repos |
| **Repo Agents** | `mcp_server.py` (per repo) | Exposes `query_repo` tool |
| **Knowledge** | `repo_rag.py` + ChromaDB | Indexes docs + source, serves queries |
| **Embedding** | `all-MiniLM-L6-v2` (local) | Converts text to vectors |

&nbsp;

> **One `setup.py` generates everything** — no manual wiring needed

---

## Technology Stack

&nbsp;

| Technology | Purpose | Why |
|---|---|---|
| **MCP (Model Context Protocol)** | Agent ↔ tool communication | VS Code standard, stdio, no ports |
| **FastMCP** | MCP server framework | Simple Python decorators |
| **ChromaDB** | Vector database | Local, persistent, no server needed |
| **sentence-transformers** | Text → embeddings | Local model, no API calls |
| **all-MiniLM-L6-v2** | Embedding model (~90 MB) | Fast, accurate, fully offline |
| **pysqlite3-binary** | SQLite for ChromaDB | Bundles modern SQLite on older OS |
| **PyYAML** | Config parsing | Human-editable `config.yaml` |
| **Python 3.11+** | Runtime | Type hints, performance |

---

## Workflow — 4 steps, fully automatic

&nbsp;

```
Developer types a feature request in Copilot chat
              │
    ┌─────────▼──────────┐
    │  STEP 1 — Route    │  orchestrator_router.get_relevant_repos()
    │                    │  → calls each repo's RAG, scores by content length
    │                    │  → returns top 2 most relevant repos
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 2 — Own repo │  <this_repo>.query_repo()
    │                    │  → searches own docs + source via RAG
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 3 — Peers    │  <peer1>.query_repo()  +  <peer2>.query_repo()
    │                    │  → retrieves relevant knowledge from each
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 4 — Synthesise│  Feature Analysis Document
    │                    │  → Current State per repo + Solution Design
    └────────────────────┘
```

---

## RAG — How each agent "knows" its codebase

&nbsp;

**Phase 1 — Indexing** *(runs once on first start)*

```
📄 .md docs  ──┐
               ├──▶  split into chunks  ──▶  embed (all-MiniLM-L6-v2)  ──▶  💾 ChromaDB
💻 source files┘
```

&nbsp;

**Phase 2 — Query** *(every call to `query_repo`)*

```
❓ feature request
      │
      ▼
  embed query  ──▶  search docs collection (top 3 chunks)
                          │
              thin (<100 chars)?  ──▶  search source collection
                          │
                          ▼
             📤  RELEVANT KNOWLEDGE: ...
```

> Routing score = `min(content_length / 800, 1.0)` — longer response = more relevant repo

---

## MCP — How agents communicate

&nbsp;

**Each MCP server is a Python subprocess. Communication is over stdin/stdout.**

```
VS Code / test_mcp.py
      │
      │  stdin ──────────────────────────────────────────────────────▶
      │                                                         MCP server
      │  1.  { "method": "initialize" }                        (Python process)
      │  2.  { "method": "notifications/initialized" }
      │  3.  { "method": "tools/call", "name": "query_repo" }
      │
      │  stdout ◀────────────────────────────────────────────────────
      │                  { "result": { "content": [{ "text": "..." }] } }
```

&nbsp;

| MCP Server | Tool exposed | Called by |
|---|---|---|
| `router_mcp_server.py` | `get_relevant_repos` | Copilot (step 1) |
| `mcp_server.py` (per repo) | `query_repo` | Copilot + orchestrator |

---

## Setup — What the user does (once)

&nbsp;

```
1.  git clone <this repo>

2.  Copy models/all-MiniLM-L6-v2/ into the repo  ← local embedding model

3.  pip install -r requirements.txt

4.  Edit orchestrator/mcp/config.yaml  ← list your repos + paths

5.  For each repo:
      cp repo-agent/mcp/        /path/to/repo/mcp/
      cp repo-agent/knowledge/  /path/to/repo/knowledge/
      Edit /path/to/repo/mcp/config.yaml  ← repo name + src_path

6.  python orchestrator/setup.py  ← generates mcp.json + copilot-instructions.md

7.  source .venv/bin/activate
    code /path/to/repo  ← VS Code picks up mcp.json automatically
```

> **Only files marked ★ are ever edited by the user — everything else is generated**

---

<!-- _class: lead -->
<!-- _paginate: false -->

# Live Demo

&nbsp;

### Run the MCP simulation

```bash
# Terminal 1 — watch live logs
tail -f /tmp/mcp-orchestration.log

# Terminal 2 — run the demo
python test_mcp.py "authentication session token management"
```

&nbsp;

Output saved to **`feature_analysis.md`**
Paste into Copilot chat → **cross-repo Solution Design**
