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
- **Documentation**: architecture, security considerations, security evidence.
