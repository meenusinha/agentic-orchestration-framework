# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [1.0.0] — unreleased

First release for VS Code with GitHub Copilot, on Windows and Linux, under the Apache License 2.0.

### Added

- **Setup page** (`setup_gui.py`), a local web page on `127.0.0.1` with a per-session
  token. Steps: install (hash-locked, into its own virtual environment), copy the approved
  embedding model (hash-verified, never downloaded), add repositories (an existing folder,
  or cloned from a Git URL and branch into a chosen folder), review (scan, folder roles,
  unknown file types), configure, create the GraphRAG.
- **Code knowledge graph** per repository: whole functions, classes and IDL operations in
  their hierarchy. Calls, includes, inheritance and test evidence. Processes and libraries
  from build files (read only). IDL interfaces linked across repositories to the processes
  that serve and call them, including in-house IDL dialects and IDL attributes.
- **MCP servers for GitHub Copilot** (stdio): `search_code`, `trace_interface`,
  `impact_of`, `get_node`, `get_children`, `find_references`, `query_repo`, and a router
  that picks the relevant repositories.
- **Agent skill** `/cross-repo-feature-analysis` for Copilot's Agent mode.
- **Queries without Copilot** (search, trace interface, impact of change). Each result is
  saved as a new report.
- **Knowledge-graph tree view** with the complete code and the links of every node,
  across repositories.
- **Security controls**: offline operation, approved model only, secret exclusion and
  redaction, private storage, untrusted-content framing, dependency audit (`tests/run_all.py`).
- **Bundled embedding model** (`models/`, Apache 2.0, unmodified, every file hash-pinned):
  works right after cloning, nothing to download.
- **Settings page**: GraphRAG creation (folders, exclude globs, file size, batch size, refresh)
  and query settings (top_k, budget, candidate pool, rank fusion, impact depth, role weights),
  router settings; defaults shown, values validated, comments in config files kept.
- **Typed paths** for the model folder (used in place, hash-checked) and for repositories.
- **Time estimate and batches** (step 6): each repository is divided into components; the
  time is estimated per component from a sample of the real code embedded on this computer,
  and files already in the GraphRAG are recognised. Large GraphRAGs can be created in
  batches; components left for later are kept in the `deferred` setting and added in later
  batches, reusing everything built before.
- **Progress bar** while the GraphRAG is created: repository, stage, units done, time left;
  weighted by the estimated work per repository and stage, and by the measured cost of
  embedding long texts.
- **Estimates calibrate themselves**: each larger build records this computer's real speed
  (numbers only), and later estimates use it; tests and external code are not counted as
  embedding work.
- **Include or exclude folders** in step 4 (Review): an Index checkbox per folder with
  **Select all** / **Exclude all**, plus sub-folders or file patterns typed or chosen with
  **Browse…**; saved as the `exclude` setting. Step 6 has **Select all** / **Select none**
  per repository and for all. `configure.py` gains `--exclude`.
- **Parallel file processing**: threads for file checks, reading and hashing (scan, estimate,
  build); worker processes for skip rules, redaction and parsing in the setup page's and command
  line's build — 2.7× faster reading, parsing and linking on a 26 MB repository, identical graph.
  Setting `workers` (0 = automatic, 1 = off). Node ids are cached while linking.
- **Configurable chunking** per repository: knowledge graph (whole units, default), sliding window
  (chunks of N lines with overlap, linked to the code they cover) or hybrid (whole units, long ones
  also searched in windows); chunk size and overlap configurable. Graph links are built in every mode.
- **Skip rules**: content never indexed — the top-of-file comment block containing given words
  (e.g. Author, Created) and regular expressions; blanked so line numbers stay correct; preview
  before saving; slow patterns refused. Settings page, `graphrag.py chunking` / `skip`.
- **Map file types in bulk** (step 4): tick any of the file types found in all repositories,
  choose the kind and language once, and every row for them is filled in. Rows show whether
  they need an answer (red), hold the scan's suggestion (amber) or are accepted (green).
- **`/graphrag-setup` agent skill**: the whole setup from Copilot Chat — install, repositories,
  folders to index or exclude, file types and roles, what to overwrite, estimate, batches and
  build with a progress line — through `graphrag.py` (status, folders, estimate, build), which
  shares its code with the setup page. Installed into every connected repository; the
  `/cross-repo-feature-analysis` skill now points to it for setup.
- **Running VS Code servers follow changed indexing settings** (components added or left
  for later, exclusions) at their next refresh.
- **Setting up again** asks before overwriting: keep or reinstall packages; keep or replace
  the model; disconnect removed repositories; per repository, overwrite or keep folder roles
  and file types, GraphRAG settings, Copilot instructions and the agent skill; update or start
  a GraphRAG from scratch. `configure.py` gains `--replace-roles`, `--reset-settings`,
  `--keep` and `--drop`; `orchestrator/setup.py` gains `--keep`.
- **Documentation**: architecture, security considerations, security evidence.
