# Requirements

The requirements of the framework, with their status and where each is implemented.
Keep this document up to date: add new requirements here first (status *planned* or
*in progress*), and record design decisions in [Decisions](#decisions).

Status: **done** (implemented and tested) · **in progress** · **planned** · **open** (needs a decision or a test).

---

## 1. Scope

| ID | Requirement | Status |
|---|---|---|
| S1 | Supported platforms: **Windows and Linux** only. Other systems are refused (override `AO_ALLOW_UNSUPPORTED_OS=1`, for tests only) | done |
| S2 | Supported editor and assistant: **VS Code with GitHub Copilot** (Agent mode, MCP servers, agent skills) | done |
| S3 | License: **Apache 2.0** | done |
| S4 | Documentation is neutral and written for anyone who uses it: no references to particular users or to sample repositories hosted elsewhere | done |
| S5 | Local and offline: parsing, linking, embedding and search run on the developer's computer; nothing is downloaded at runtime; no code leaves the computer except what Copilot sends as part of its normal operation | done |
| S6 | Code is complete, easy to maintain, and follows SOLID principles; logic shared by several entry points lives in one module | done |

## 2. Installation and model

| ID | Requirement | Status | Where |
|---|---|---|---|
| I1 | One command starts a local setup page (`python3 setup_gui.py`; Windows `py setup_gui.py`) that creates and uses its own virtual environment (`.venv`) | done | `setup_gui.py` |
| I2 | No AI agent or LLM is needed to install, configure, create the GraphRAG or run queries | done | setup page, scripts |
| I3 | Packages are hash-locked (`requirements.lock`) and audited for known vulnerabilities | done | `tests/run_all.py` |
| I4 | The approved embedding model (`ibm-granite/granite-embedding-small-english-r2`, pinned revision) is **included** in the repository (plain git, no LFS) | done | `models/` |
| I5 | Every model file is **hash-pinned** (SHA-256 of all 8 files, JSON compared with line endings normalised); a missing or changed file is refused by name | done | `repo_rag.py`, `configure.py` (tables kept identical; tested) |
| I6 | The model folder can be typed as a path and used in place (hash-checked), or copied; it is never downloaded | done | step 2 |
| I7 | A foreign `PYTHONPATH`/`PYTHONHOME` (e.g. set for other tools, with libraries built for another Python) is ignored by every entry point | done | all entry points; tested with a poisoned `PYTHONPATH` |
| I8 | Running setup again: **keep or reinstall** packages; **keep or replace** the model | done | steps 1–2 |

## 3. Repositories, review and configuration

| ID | Requirement | Status | Where |
|---|---|---|---|
| C1 | Add a repository by typing or choosing its folder, or by **Git URL and branch**, cloned into a folder the user chooses; an existing clone of the same repository is reused and switched to the branch; other existing folders are never overwritten | done | step 3, `gui/gitclone.py` |
| C2 | Review per repository: languages, size, processes and libraries (from build files, read only), IDL interfaces, folder roles | done | step 4, `scan.py` |
| C3 | Unknown file types are never guessed silently: the user says what each contains (kind) and how to read it (language) | done | step 4 |
| C4 | **Map file types in bulk**: a bar at the top lists every file type found in all repositories; tick any, choose kind and language once, Apply fills every row for them; rows show red (needs an answer), amber (scan's suggestion, not confirmed) or green (accepted) | done | step 4 |
| C5 | **Include or exclude folders**: an Index checkbox per folder, **Select all / Exclude all**, plus sub-folders or file patterns (typed or Browse…, inside the repository only); file types found only in excluded folders are not asked | done | step 4, `exclude` setting, `configure.py --exclude` |
| C6 | Running setup again asks **what to overwrite** per repository (or all at once): folders/roles/file types, GraphRAG settings, Copilot instructions, agent skill; repositories removed in step 3 are disconnected, their files left as they are | done | step 5, `configure.py --replace-roles/--reset-settings/--keep/--drop` |
| C7 | Generated files: `.vscode/mcp.json` (always regenerated), `.github/copilot-instructions.md`, agent skills in `.github/skills/`; `.rag_index/` added to `.gitignore` | done | `orchestrator/setup.py` |

## 4. GraphRAG creation

| ID | Requirement | Status | Where |
|---|---|---|---|
| G1 | Per-repository code knowledge graph: whole functions, classes, IDL operations and documentation sections in their hierarchy; calls, includes, inheritance, test evidence; processes and libraries; IDL interfaces linked across repositories | done | `codegraph/` |
| G2 | **Time estimate** before creating: per component, per repository and in total, measured on the computer itself; files already in the GraphRAG are recognised; tests and external code are not counted as embedding work | done | step 6, `gui/estimate.py` |
| G3 | **Batches** for large repositories: create all, a suggested first batch (~15 min), or a chosen set of components; the rest is *left for later* (`deferred` setting) and added in later batches, reusing everything built before | done | step 6, `components.py` |
| G4 | **Progress bar** while creating: repository, stage, units done, percentage, time left; weighted by expected work and by the measured cost of embedding long texts | done | `gui/verify.py`, `embedcost.py` |
| G5 | Estimates **calibrate themselves** from each larger real build (numbers only, `~/.agentic-orchestration/build-speed.json`) | done | `gui/buildspeed.py` |
| G6 | Start a repository **from scratch** (stored graph and cached vectors removed) or update it (default) | done | step 6, `--fresh` |
| G7 | VS Code servers that are already running **follow changed indexing settings** (batches, exclusions) at their next refresh | done | `mcp_server.py` |
| G8 | The framework's own installed files (agent skills, generated instructions) are never indexed | done | `indexer.py` |
| G9 | **Configurable chunking**, per repository: **knowledge graph** (whole units, default), **sliding window** (fixed-size chunks with overlap; results are chunks with file:line ranges), **hybrid** (whole units, plus overlapping windows embedded for long units; a hit returns the whole unit). Graph links are built in every mode. Chunk size and overlap configurable | done | `chunking.py`, Settings, `graphrag.py chunking`; design below |
| G10 | **Skip rules**: content that must never be indexed, per repository — a header rule (remove the comment block at the top of a file if it contains given words, e.g. Author, Created, Date, Copyright; comment styles `/* */`, `//`, `#`, `--`, `%`, `;`) and advanced regex rules. Matched text is blanked before parsing, so line numbers stay correct. Preview (files matched, example) before saving; regexes validated (compile, length, test run) | done | `skiprules.py`, Settings (Preview), `graphrag.py skip`; design below |

### Design of G9 — configurable chunking

| Setting (per repository, `mcp/config.yaml`, Settings page) | Default | Meaning |
|---|---|---|
| `chunking` | `graph` | `graph`: whole units, never cut (functions, classes, IDL operations, documentation sections). `sliding`: every file is cut into chunks of `chunk_lines` lines that overlap by `chunk_overlap` lines; results are chunks with file and line range. `hybrid`: whole units as results, and units longer than `chunk_lines` are also embedded as overlapping windows, so a match anywhere in a long unit finds it; a hit on a window returns its whole unit |
| `chunk_lines` | 60 | Chunk (window) size in lines; 5–2000 |
| `chunk_overlap` | 15 | Lines shared by neighbouring chunks; 0 to `chunk_lines` − 1 |

- The graph (units, calls, includes, IDL links, test evidence, processes) is built in every mode,
  so trace interface and impact of change work the same in all three.
- Sliding window: each chunk is linked to the units it covers (`COVERS`), so a chunk result
  still lists the calls, IDL interfaces and tests of the code inside it. Only chunks are embedded
  and keyword-indexed.
- Hybrid: windows are children of their unit and are never returned themselves.
- Chunk nodes are not shown in the knowledge-graph tree view.
- The time estimate counts the embedding work of the chosen mode (overlap means more text).
- Changing a chunking setting rebuilds the GraphRAG; vectors of unchanged text are reused.

### Design of G10 — skip rules (content never indexed)

| Setting (per repository) | Default | Meaning |
|---|---|---|
| `skip_header_words` | none | Words such as `Author, Created, Date, Copyright`. If the comment block at the top of a file contains any of them (whole word, any case), the block is skipped. Comment styles: `/* … */`, `//`, `#` (not `#include`, `#define`, …), `--`, `%`, `;`, and a leading Python docstring; a shebang or encoding line before it is kept |
| `skip_patterns` | none | Regular expressions (one per line, multi-line mode); every match is skipped anywhere in a file |

- Skipped text is replaced by blank lines (same number of lines) before redaction, parsing,
  embedding and storage, so `file:line` stays correct, and it never reaches the GraphRAG.
- Regexes are validated before saving: they must compile, be at most 300 characters (at most 20
  of them), must not match empty text, and patterns with nested repetition such as `(a+)+` (which
  can take extremely long) are refused.
- **Preview** before saving (Settings page and command line): for each rule, how many files
  it matches and an example of what it removes, run in a separate process with a time limit.
- Command line: `graphrag.py skip REPO --header-words … --pattern … [--preview]`.
- Changing a rule rebuilds the GraphRAG; vectors of unchanged text are reused.

### Performance

| ID | Requirement | Status | Where |
|---|---|---|---|
| P1 | Work over all files of a repository runs **in parallel**: file checks, sizing, reading and hashing with threads — in the scan (file types), the estimate (size, what is already indexed) and every build; skip rules, secret redaction and parsing in worker processes — in the setup page's and command line's build only. Identical result; falls back to one file at a time if worker processes can't start | done | `codegraph/parallel.py`; see [D9](#decisions), [D10](#decisions) |
| P2 | A `workers` setting (0 = automatic: 8 threads, one process per CPU core; 1 = off), measured before being enabled by default | done | 26 MB repository: reading, parsing and linking 23.1 s → 8.6 s (2.7×); 6.5 MB: 4.8 s → 2.3 s; graphs identical |

## 5. Queries and browsing

| ID | Requirement | Status | Where |
|---|---|---|---|
| Q1 | The setup page always has a **query field** (search, trace interface, impact of change), usable without Copilot | done | top bar |
| Q2 | Every query result is saved as a **new** Markdown file (never overwrites earlier results) | done | `results/` |
| Q3 | The **file name** (and line) is returned with every unit, everywhere (results, relations, reports, graph view, MCP tools) | done | tested in all tools |
| Q4 | **Knowledge graph** shown as a tree in the local browser, with complete code and links of every node, across repositories | done | page *Knowledge graph* |
| Q5 | **Settings page** for GraphRAG creation, queries and router, with defaults, ranges, validation and a **hover explanation** for every setting and role | done | page *Settings*, `codegraph/settings.py` |

## 6. Copilot integration

| ID | Requirement | Status | Where |
|---|---|---|---|
| A1 | MCP servers (stdio only, no network port): one per repository plus a router | done | `mcp_server.py`, `router_mcp_server.py` |
| A2 | Agent skill `/cross-repo-feature-analysis` for cross-repository analysis | done | `skills/cross-repo-feature-analysis/` |
| A3 | Agent skill **`/graphrag-setup`**: the whole setup from Copilot Chat (install, repositories, folders, file types, roles, overwrite choices, estimate, batches, build with progress), through `graphrag.py`, sharing its code with the setup page | done | `.github/skills/graphrag-setup/`, `gui/graphrag_ops.py` |

## 7. Security and documentation

| ID | Requirement | Status | Where |
|---|---|---|---|
| D1 | Security considerations and an **evidence document** showing that each component of the technology stack is secure (versions, licenses, vulnerability status, measurements) | done | `docs/security-*.md` |
| D2 | Vulnerability status is stated as **"0 reported CVEs"** for the pinned versions; closed CVEs are not listed | done | `docs/security-evidence.md` |
| D3 | No vulnerability-reporting instructions in `SECURITY.md` | done | `SECURITY.md` |
| D4 | Hash-pinning explained in the README and in the evidence document, including its drawbacks | done | README, evidence |
| D5 | Setup page: `127.0.0.1` only, per-session token, Host check, JSON-only POSTs, strict Content-Security-Policy | done | `setup_gui.py` |
| D6 | Secrets excluded or redacted; graph, results and logs private (`0700`/`0600`) | done | `security.py`, `store.py` |

## 8. Testing

| ID | Requirement | Status | Where |
|---|---|---|---|
| T1 | One command runs every check: end-to-end setup page test, dependency audit, MCP test | done | `tests/run_all.py` |
| T2 | Tested on **Linux** (non-root, poisoned `PYTHONPATH`) before every push | done | Docker, `python:3.11-slim-bookworm` |
| T3 | Tested on **Windows** | **open** | to be run manually: `py tests\run_all.py` |
| T4 | Test before committing; commit and push only when approved | done | process |

## 9. Open items

| Item | Status |
|---|---|
| Windows test run (T3) | open — the developer runs it |
| `test_mcp.py` should honour the `model_path` setting (it always uses the bundled model) | open — offered, not yet approved |

---

## Decisions

| ID | Date | Decision | Why |
|---|---|---|---|
| D1 | 2026-11 | Vectors in SQLite (float32 blobs) with NumPy similarity, plus FTS5 keyword search fused by reciprocal rank | One local file per repository, no server, nothing extra to install or secure |
| D2 | 2026-11 | The embedding model is bundled and hash-pinned file by file | Works right after cloning; any change is refused by name |
| D3 | 2026-11 | Components = top-level folders; a folder holding more than 40% of a repository with 400+ files is split into its sub-folders | Useful batch choices also for monorepos with one large `src/` |
| D4 | 2026-11 | *Left for later* (`deferred`) is separate from *excluded* (`exclude`) | Batches are temporary; exclusions are permanent choices |
| D5 | 2026-11 | Embedding cost model `m·(1 + m/9300)`, `m = min(length, 30000)` characters | Measured with the bundled model: cost per character grows with length, constant beyond the token limit |
| D6 | 2026-11 | Scan suggestions for file types are shown as *suggested* (amber), not accepted | "Nothing is guessed silently": the user confirms |
| D7 | 2026-11-16 | Chunking offers all three modes, selectable per repository: knowledge graph (default), sliding window, hybrid; graph links built in every mode | Chosen by the developer |
| D8 | 2026-11-16 | Skip rules blank the matched text (same number of lines) instead of deleting it | Line numbers in results stay correct |
| D9 | 2026-11-16 | Threads for file I/O and hashing; worker processes for parsing | Python threads run in parallel only while waiting on I/O or in code that releases the interpreter lock (file reads, SHA-256); parsing holds it, so it needs processes to use several cores |
| D10 | 2026-11-16 | Worker processes only in the setup page's and command line's build, started with "spawn", for 200+ files; the VS Code servers parse sequentially | On Windows a process starts by importing the main program again, and a server's main program builds its graph on import; forking a process that already runs threads is unsafe. Measured: threads alone did not speed up parsing (7.3 s → 7.9 s) |
