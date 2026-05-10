# Agentic Orchestration Framework — Claude Code Context

This file gives Claude Code full context for working in this repo.
Read it entirely before doing anything.

---

## What this repo is

A plug-and-play framework that gives existing software repos cross-repo AI awareness
via MCP (Model Context Protocol). When a developer types a feature request in VS Code
Copilot (Agent mode), their repo's AI agent:

1. Calls the **orchestrator** to find the 2 most relevant peer repos
2. Queries its **own** knowledge base (docs + source code via RAG)
3. Queries each **peer repo's** knowledge base
4. Synthesises everything into a **Feature Analysis Document**

The orchestrator routes by querying each peer repo's RAG via MCP subprocess calls and
ranking by how much relevant content is returned — NOT by description embeddings.
Score = `min(content_length / 800, 1.0)`, 0.0 if "no relevant knowledge found".

---

## Repo structure

```
agentic-orchestration-framework/
├── README.md                        ← user-facing setup guide (keep accurate)
├── requirements.txt                 ← mcp[cli], fastmcp, chromadb, sentence-transformers, pyyaml
├── orchestrator/                    ← cloned once by the user; never copied into their repos
│   ├── mcp/
│   │   ├── config.yaml              ← USER EDITS THIS: lists all repos + their paths
│   │   ├── router.py                ← OrchestratorRouter class (reads config.yaml)
│   │   ├── router_mcp_server.py     ← FastMCP server exposing get_relevant_repos tool
│   │   └── demo_logger.py           ← shared logger → DEMO_LOG_FILE env var
│   └── setup.py                     ← generates .vscode/mcp.json + copilot-instructions.md per repo
│
└── repo-agent/                      ← user copies this into each of their repos
    ├── mcp/
    │   ├── config.yaml              ← USER EDITS THIS per repo: repo_name, components, src_path
    │   ├── mcp_server.py            ← FastMCP server exposing query_repo tool (reads config.yaml)
    │   ├── repo_rag.py              ← RepoRAG class: indexes knowledge/ + source, serves queries
    │   └── demo_logger.py           ← same logger as orchestrator (copy — no shared import)
    ├── knowledge/
    │   └── README.md                ← instructions for dropping .md docs
    └── .github/
        └── copilot-instructions.md  ← template; setup.py overwrites with repo-specific version
```

---

## Key design decisions (do not reverse without good reason)

**Embedded, not sidecar**: Agent files (`mcp/`, `knowledge/`, `.github/`) live inside the
user's real repos. They are not separate clones alongside the real repos. This keeps each
repo self-contained.

**config.yaml as single touchpoint**: The only file a user edits per repo is `mcp/config.yaml`.
The MCP server, RAG engine, and copilot instructions all read from it. No code changes needed.

**setup.py generates everything**: Running `python orchestrator/setup.py` generates:
- `.vscode/mcp.json` for every listed repo (absolute paths, no env vars needed)
- `.github/copilot-instructions.md` for every listed repo (repo-specific, no placeholders)
It must be re-run whenever repos are added/removed.

**Absolute paths in mcp.json**: Generated mcp.json files use absolute paths so VS Code
can be opened from any terminal without setting env vars. This was a deliberate choice
over the env-var approach used in the earlier lithography demo.

**No cross-repo Python imports**: Each repo's `mcp/` directory is fully self-contained.
`demo_logger.py` and `repo_rag.py` are copied (not imported) into each repo's `mcp/`.
`sys.path.insert(0, str(Path(__file__).parent))` handles local imports.

**RAG routing, not embedding routing**: The orchestrator does NOT use description embeddings
to route. It calls each peer repo's `query_repo` MCP tool as a subprocess and scores the
response by content length. This ensures routing is based on actual knowledge, not
keyword similarity on repo descriptions.

**Two-tier RAG**: `repo_rag.py` searches docs first. If the result is thin (< 100 chars),
it falls back to source code. Returns `RELEVANT KNOWLEDGE:\n...` prefix for scorer to detect.

---

## How the MCP stdio protocol works

`_mcp_call()` in `router.py` spawns a subprocess running the target MCP server and sends
three JSON-RPC messages over stdin:
1. `initialize` (id=1)
2. `notifications/initialized` (no id)
3. `tools/call` (id=2) — the actual query

It parses stdout line-by-line looking for the message with `id=2` and returns its
`result.content[0].text`. Timeout is 60s by default.

---

## Files the user should NEVER edit

- `orchestrator/mcp/router.py`
- `orchestrator/mcp/router_mcp_server.py`
- `orchestrator/mcp/demo_logger.py`
- `repo-agent/mcp/mcp_server.py`
- `repo-agent/mcp/repo_rag.py`
- `repo-agent/mcp/demo_logger.py`
- Any generated `.vscode/mcp.json` or `.github/copilot-instructions.md`

## Files the user MUST edit

- `orchestrator/mcp/config.yaml` — list of all repos + paths
- `repo-agent/mcp/config.yaml` (copied into each repo) — repo identity + src path

---

## Source file extensions indexed by default

`.py .js .ts .jsx .tsx .java .go .rs .cpp .c .h .cc .cxx .cs .rb .swift .kt .php .scala .thrift .proto .graphql`

Configurable via `extra_extensions` in `mcp/config.yaml`.

---

## Live logging

All MCP interactions are logged to `DEMO_LOG_FILE` (default: `/tmp/mcp-orchestration.log`).
Watch with `tail -f /tmp/mcp-orchestration.log`. Log levels: TOOL_CALL, ROUTING, RAG, RESULT, INDEX, INFO, WARN, ERROR.

---

## Git

- Main branch: `main`
- This is a standalone public repo: https://github.com/meenusinha/agentic-orchestration-framework
- It was extracted from `SoftwareTeam/framework/` on branch `master_generic-orchestration-framework`
- All commits go directly to `main` (no agent branch workflow here — this is not a SoftwareTeam project)

---

## What is NOT in this repo

- The lithography demo (scan_manager, illumination, expose_sequence) — that lives in
  `SoftwareTeam` repo on branch `master_distributed-repo-agents`
- The SoftwareTeam agent workflow (product owner, architect, developer, tester agents)
- Any hardcoded domain knowledge — this repo is fully generic
