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
├── setup_gui.py                     ← local wizard + query UI (stdlib HTTP server, 127.0.0.1 + token): install → model → repos → review → configure → create GraphRAG → queries
├── gui/                             ← index.html (no external assets, CSP), verify.py (build + report), query_worker.py (loads graphs once; search / trace / impact; no LLM)
├── results/                         ← query reports, one new file per query (codegraph/reports.py: unique_report_path) — gitignored
├── playground/                      ← setup_playground.py, smoke_test.py, two sample repos
├── requirements.txt                 ← direct deps: mcp[cli]<2, sentence-transformers>=6, numpy, pyyaml
├── requirements.lock                ← hash-locked, universal (uv pip compile --generate-hashes)
├── models/granite-embedding-small-english-r2/  ← approved embedding model (pinned hash)
├── .github/workflows/security.yml   ← pip-audit + lock-consistency CI
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
    │   ├── repo_rag.py              ← RepoRAG: NumPy+JSON index in .rag_index/, model verification, secret redaction
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
- `.mcp.json` (Claude Code) and `.cursor/mcp.json` (Cursor) for every listed repo
- `.claude/skills/cross-repo-feature-analysis/` copied from `skills/` (the Agent Skill,
  which is generic — it reads `repo_name` from the repo's `mcp/config.yaml` at runtime)
It must be re-run whenever repos are added/removed.

**Skill scripts** (`skills/cross-repo-feature-analysis/scripts/`): `scan.py` is read-only
and proposes per-repo `src_path` / `knowledge_path` / `extra_extensions`. Its
`INDEXED_EXT` must stay in sync with `_DEFAULT_EXTENSIONS` in `repo_rag.py`.
`configure.py` writes the orchestrator and per-repo configs from flags, clears a repo's
`.rag_index/` when its indexing settings change, then runs `setup.py`.

**Absolute paths in mcp.json**: Generated mcp.json files use absolute paths so VS Code
can be opened from any terminal without setting env vars. The server `command` is
`sys.executable` (the venv Python that ran setup.py), not a bare `python`. A bare
`python` resolved to the wrong interpreter whenever VS Code was already running,
and every server failed with `ModuleNotFoundError: mcp`. This was a deliberate choice
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

## Code graph (repo-agent/mcp/codegraph/)

| Module | Role |
|---|---|
| `model.py` | Node / Edge, node kinds, edge types, roles |
| `parsers.py` | tree-sitter extraction for C, C++, Python (+ `_recover_missing` completeness pass) |
| `langs.py` | table-driven extraction: MATLAB, Perl, JS/TS, Java, Go, Rust, C# (+ `recover`) |
| `idl.py` | tolerant IDL-family parser (OMG/DDS/MIDL/AIDL/proto/thrift/ROS/in-house dialects) |
| `docs.py` | Markdown → heading sections |
| `extract.py` | picks the extractor per file (manifest `file_types` > extension) |
| `structure.py` | roles (manifest > build facts > heuristics), Makefile/CMake/compile_commands (static, never run), generated code |
| `relations.py` | CONTAINS, INCLUDES/IMPORTS, DECLARES, CALLS, INHERITS, TESTS, DOCUMENTS, BUILDS, DEPENDS_ON |
| `interfaces.py` | IDL defines / references / resolver → IMPLEMENTS, CALLS_OPERATION, PROVIDES, CONSUMES; `interfaces.json` registry |
| `indexer.py` | pipeline + incremental rebuild (file hashes, config fingerprint, peer signature); embeddings cached by text hash |
| `store.py` | SQLite graph store (WAL, exclusive write txn, 0600, FTS5 with fallback); `open_readonly` for peers/router |
| `search.py` | hybrid ranking, expansion, cross-repo `trace_interface`, `impact_of`, budgeted `assemble` |
| `schemas.py` | Pydantic result models for the MCP tools |
| `security.py` | secret-store file skip + newline-preserving redaction |

**Never-cut invariant.** Definition nodes' `text` is the exact, complete source span;
containers hold skeletons. Every extractor ends with a completeness pass that adds any
function the walker missed (marked `recovered`). The budgeted assembler drops whole
units into `more` — it never truncates. Only an *embedding input* may be shortened
(model max 8192 tokens); stored and returned text never is. When you change an
extractor, run a never-cut check over real code (every function-like syntax node must
be inside some function/method node's text) before committing.

**Interface linking is name-based with confidence**: qualified/skeleton/proxy/C-mapping
matches ≥ 0.9, unique-operation-name fallback 0.5. Results always carry the confidence.

## Security invariants (do not weaken)

- **Offline only**: `repo_rag.py` forces `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` before
  importing ML libraries; `mcp_server.py` has no default model name (a missing
  `EMBEDDING_MODEL_PATH` must fail, never download).
- **Approved model only**: `verify_model_dir()` checks the pinned SHA-256 of
  `model.safetensors`, rejects code/pickle files and non-standard module types, and the
  model loads with `local_files_only=True, trust_remote_code=False`. Keep
  `APPROVED_MODELS` (repo_rag.py) and `MODEL_SHA256` (configure.py) in sync.
- **No pickle**: the graph is SQLite; vectors are raw float32 bytes.
- **Private files**: `.rag_index/` is 0700/0600, written atomically; logs are 0600.
- **Secrets**: `is_secret_file()` + `redact()` run before anything is embedded or
  stored. Bump `INDEX_FORMAT` when selection or redaction rules change.
- **Untrusted output**: `query_repo` wraps results in BEGIN/END UNTRUSTED markers and
  neutralises `<<<`/`>>>` inside content; `router._score_response` ignores marker lines.
- **Dependencies**: change `requirements.txt`, then regenerate `requirements.lock`.

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

All MCP interactions are logged to `DEMO_LOG_FILE` (default:
`~/.agentic-orchestration/logs/mcp-orchestration.log`, mode 0600, opened with O_NOFOLLOW).
Code snippets and request text are logged only when `DEMO_LOG_CONTENT=1` (orchestrator
config `log_content: true`); otherwise use `content()` from demo_logger, which logs lengths.
Log levels: TOOL_CALL, ROUTING, RAG, RESULT, INDEX, INFO, WARN, ERROR.

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
