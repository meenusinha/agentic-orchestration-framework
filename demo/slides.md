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
  a { color: #58a6ff; }
  section::after { color: #8b949e; font-size: 0.75em; }
  blockquote { border-left: 4px solid #58a6ff; color: #8b949e; background: #161b22; padding: 10px 16px; border-radius: 0 6px 6px 0; }
---

<!-- _class: lead -->
<!-- _paginate: false -->

# Multi-Repo Agentic Orchestration Framework

### Cross-repo AI awareness for lithography software development

&nbsp;

`MCP` &nbsp;·&nbsp; `RAG` &nbsp;·&nbsp; `ChromaDB` &nbsp;·&nbsp; `sentence-transformers` &nbsp;·&nbsp; `FastMCP` &nbsp;·&nbsp; `GitHub Copilot`

---

## The Problem — Copilot sees only one repo

&nbsp;

```
  📦 scan_manager   🤖 Copilot       📦 illumination   📦 expose_sequence
  ──────────────    ─────────────    ───────────────   ──────────────────
  your code          only sees          invisible            invisible
                    this repo              ❌                   ❌
```

&nbsp;

- 🔴 A dose control change in `expose_sequence` must align with `scan_manager` timing — Copilot cannot see this
- 🔴 Illumination parameter updates affect exposure logic in a separate repo — invisible to the AI
- 🔴 Developer must **manually gather context** from all lithography subsystem repos before every question
- 🔴 Solutions are **incomplete** — only one subsystem's perspective

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
    📦 scan_manager  📦 illumination  📦 expose_sequence
       (agent)          (agent)           (agent)
       RAG index        RAG index         RAG index
```

&nbsp;

> Each lithography subsystem repo has its own knowledge base.
> The orchestrator routes intelligently to the most relevant ones.
> Copilot synthesises everything into a **complete cross-subsystem answer**.

---

## Architecture — How it all fits together

&nbsp;

| Layer | Component | Role |
|---|---|---|
| **AI Interface** | GitHub Copilot + Agent mode | Developer's entry point |
| **Instructions** | `copilot-instructions.md` | Tells Copilot to call MCP tools |
| **Tool Config** | `.vscode/mcp.json` (auto-generated) | Wires up all MCP servers |
| **Orchestrator** | `router_mcp_server.py` | Routes to relevant subsystem repos |
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
| **pysqlite3-binary** | SQLite for ChromaDB | Bundles modern SQLite on RHEL8 |
| **PyYAML** | Config parsing | Human-editable `config.yaml` |
| **Python 3.11+** | Runtime | Type hints, performance |

---

## Workflow — 4 steps, fully automatic

&nbsp;

```
Developer: "Add real-time dose control feedback during wafer exposure"
              │
    ┌─────────▼──────────┐
    │  STEP 1 — Route    │  orchestrator_router.get_relevant_repos()
    │                    │  → queries scan_manager, illumination, expose_sequence RAGs
    │                    │  → returns top 2: expose_sequence (0.91), illumination (0.74)
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 2 — Own repo │  scan_manager.query_repo()
    │                    │  → retrieves scan timing, stage control docs + source
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 3 — Peers    │  expose_sequence.query_repo() + illumination.query_repo()
    │                    │  → retrieves dose calculation, pulse control knowledge
    └─────────┬──────────┘
    ┌─────────▼──────────┐
    │  STEP 4 — Synthesise│  Feature Analysis Document
    │                    │  → Current State + cross-subsystem Solution Design
    └────────────────────┘
```

---

## RAG — How each agent "knows" its subsystem

&nbsp;

**Phase 1 — Indexing** *(runs once on first start)*

```
📄 subsystem docs (.md)  ──┐
                            ├──▶  split into chunks  ──▶  embed  ──▶  💾 ChromaDB
💻 source code              ┘
   (e.g. expose_sequence/*/com/*)
```

&nbsp;

**Phase 2 — Query** *(every call to `query_repo`)*

```
❓ "dose control feedback during exposure"
      │
      ▼
  embed query  ──▶  search docs  (top 3 chunks: DoseController, ExposureLoop ...)
                          │
              thin (<100 chars)?  ──▶  search source code
                          │
                          ▼
             📤  RELEVANT KNOWLEDGE: DoseController adjusts pulse width ...
```

> Routing score = `min(content_length / 800, 1.0)`

---

## MCP — How agents communicate

&nbsp;

**Each MCP server is a Python subprocess. Communication is over stdin/stdout — no network.**

```
VS Code / test_mcp.py
      │
      │  stdin ──────────────────────────────────────────────────────▶
      │                                                         MCP server
      │  1.  { "method": "initialize" }                        (Python process)
      │  2.  { "method": "notifications/initialized" }
      │  3.  { "method": "tools/call", "name": "query_repo",
      │          "arguments": { "feature_request": "dose control..." } }
      │
      │  stdout ◀────────────────────────────────────────────────────
      │      { "result": { "content": [{ "text": "RELEVANT KNOWLEDGE:..." }] } }
```

&nbsp;

| MCP Server | Tool exposed | Called by |
|---|---|---|
| `router_mcp_server.py` | `get_relevant_repos` | Copilot (step 1) |
| `mcp_server.py` (per subsystem repo) | `query_repo` | Copilot + orchestrator |

---

## Setup — What the user does (once)

&nbsp;

```
1.  git clone <this repo>

2.  Copy models/all-MiniLM-L6-v2/ into the repo  ← local embedding model

3.  pip install -r requirements.txt

4.  Edit orchestrator/mcp/config.yaml
      repos:
        - name: scan_manager     path: /path/to/scan_manager
        - name: illumination     path: /path/to/illumination
        - name: expose_sequence  path: /path/to/expose_sequence

5.  For each repo:
      cp repo-agent/mcp/       /path/to/repo/mcp/
      cp repo-agent/knowledge/ /path/to/repo/knowledge/
      Edit mcp/config.yaml  ←  repo_name, src_path: ./BB-*/*/com

6.  python orchestrator/setup.py  ← generates mcp.json for all repos

7.  source .venv/bin/activate  &&  code /path/to/repo
```

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
python test_mcp.py "dose control feedback wafer exposure scan timing"
```

&nbsp;

Output saved to **`feature_analysis.md`**
Paste into Copilot chat → **cross-subsystem Solution Design**
