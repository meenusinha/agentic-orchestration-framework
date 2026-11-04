# Architecture

How the framework is put together: its components, the processes that run, how data is
stored and shared, and what each part costs in time and memory.

Supported platforms: **Windows and Linux**.

---

## 1. Components

| Component | Location | Role |
|---|---|---|
| Setup page | `setup_gui.py`, `gui/` | Local web page (standard library HTTP server on `127.0.0.1`): install, model, repositories, review, configure, create the GraphRAG; then queries and the knowledge-graph view |
| Code graph engine | `repo-agent/mcp/codegraph/` | Parses repositories into a graph, links IDL interfaces across repositories, stores and searches it |
| Repository server | `repo-agent/mcp/mcp_server.py` (copied into each repository's `mcp/`) | MCP server for one repository: search, trace, impact, navigation |
| Router | `orchestrator/mcp/router_mcp_server.py` | MCP server that picks the repositories relevant to a request |
| Configuration | `skills/cross-repo-feature-analysis/scripts/` (`scan.py`, `configure.py`), `orchestrator/setup.py` | Scan repositories, write their settings, generate the VS Code configuration |
| Agent skill | `skills/cross-repo-feature-analysis/` (installed into each repository's `.github/skills/`) | Tells Copilot how to use the tools (`/cross-repo-feature-analysis`) |

### Code graph engine modules

| Module | Responsibility |
|---|---|
| `parsers.py`, `langs.py` | Extract complete functions, methods, classes and members (tree-sitter) for C, C++, Python, MATLAB, Perl, Java, JavaScript, TypeScript, Go, Rust, C# |
| `idl.py` | Tolerant parser for IDL families and in-house dialects |
| `docs.py` | Markdown, by heading section |
| `structure.py` | Folder and file roles (manifest > build facts > layout); processes and libraries from Makefiles, CMake and `compile_commands.json`, read statically |
| `relations.py` | Contains, includes/imports, declares, calls, inherits, tests, documents, builds, depends-on |
| `interfaces.py` | IDL definitions, implementations and calls → implements, calls-operation, provides, consumes; per-repository `interfaces.json` registry |
| `indexer.py` | Pipeline and incremental rebuild (file hashes, configuration fingerprint, peer registries) |
| `store.py` | SQLite graph store |
| `search.py` | Hybrid ranking (vectors + keywords), interface tracing, impact analysis, whole-unit result assembly |
| `security.py` | Secret-file exclusion and redaction |
| `settings.py` | Every creation, query and router setting with its default and range; used by every program that builds or searches a graph, so they always build identical graphs |

---

## 2. The knowledge graph

- **Nodes** are files, modules, namespaces, classes, functions, methods, fields,
  variables, macros, IDL interfaces, operations, attributes and types, documentation
  sections, and build targets (processes and libraries).
- **Whole units.** A definition node holds its complete source text. Containers (files,
  classes, namespaces) hold an outline. Results are assembled from whole units only;
  when a result budget is reached, further units are listed by name, never truncated.
- **Edges** include `CONTAINS`, `INCLUDES`, `DECLARES`, `CALLS`, `INHERITS`, `TESTS`,
  `DOCUMENTS`, `BUILDS`, `DEPENDS_ON`, `GENERATED_FROM`, `IMPLEMENTS`, `CALLS_OPERATION`,
  `PROVIDES` and `CONSUMES`. Edges into another repository are stored as references to
  that repository's node.
- **Roles.** Every file has a role. Tests are kept only as evidence (`TESTS` edges), and
  external code contributes signatures only.
- **Interface links** are name-based with a confidence value. Qualified, skeleton, proxy
  and C-mapping matches score 0.9 or higher; a unique operation name alone scores 0.5.
  IDL attributes are matched through their accessor names, and inherited interfaces are
  followed.

---

## 3. Processes

```
VS Code window (one per open repository)
 ├── router MCP server            (1 per window)
 └── repository MCP servers       (1 per connected repository, per window)
        each: loads the model once, opens its graph, refreshes it at most once a minute

Setup page (only while open)
 ├── HTTP server on 127.0.0.1     (standard library)
 └── query worker                 (loads the model and all graphs once, for the query field)
```

- VS Code starts the servers listed in `.vscode/mcp.json` and talks to them over
  **stdio**. No server opens a network port.
- Each server loads the embedding model once, at startup on the main thread, and shares
  it across everything it serves.
- The router reads the other repositories' graphs **read-only**, to choose the relevant
  repositories and explain why ("serves interface X in process Y").

### Cost

Measured on a small repository:

| | Value |
|---|---|
| Server start (model + graph) | about 5 s |
| Memory per running server | about 0.75 GB peak |
| First query on the setup page | about 4 s (loads the model and graphs); then well under 1 s |
| Incremental update after an edit | proportional to the changed files only (unchanged units keep their cached vectors) |
| First full build | from seconds (small repositories) to a few minutes (large ones) |

Because each connected repository runs its own server in every VS Code window, plan on
roughly 0.75 GB × (number of connected repositories) per open window.

---

## 4. Storage and concurrency

| Data | Where | Format |
|---|---|---|
| Graph and vectors | `<repo>/.rag_index/graph.sqlite` | SQLite (WAL mode); vectors as raw float32 bytes |
| Interface registry | `<repo>/.rag_index/interfaces.json` | JSON: interfaces defined and links found |
| Embedding cache | inside `graph.sqlite` | Vectors keyed by the hash of the embedded text, so unchanged code is never re-embedded |
| Saved query reports | `<framework>/results/` | Markdown, one new file per query |
| Logs | `~/.agentic-orchestration/logs/` | Text and HTML |

- **Several readers, one writer.** Several VS Code windows can run servers for the same
  repository. SQLite's WAL mode lets them all read while one of them updates the graph,
  inside an exclusive write transaction, and the others wait their turn.
- **Atomic files.** JSON files are written to a temporary file and then renamed into
  place, so readers never see a partial file. On Windows the rename is retried briefly
  while another process has the file open.
- **Rebuild triggers.** The graph is updated when files change (content hashes), when
  the repository's settings change (configuration fingerprint), when the framework's
  own code changes, and when a connected repository publishes new interfaces.
- **Privacy.** Folders are `0700` and files `0600`. On Windows, the user profile's access
  control applies.

---

## 5. Request flow in Copilot

1. The developer asks Copilot (Agent mode) a question starting with
   `/cross-repo-feature-analysis`.
2. Copilot calls the router's `get_relevant_repos`. The router ranks the repositories by
   semantic match and interface links, and gives its reasons.
3. Copilot calls `search_code`, `trace_interface`, `impact_of` and `get_node` /
   `get_children` on the relevant repositories' servers. Every result consists of
   complete units, with file and line, role, process and links, and is marked as
   untrusted repository content.
4. Copilot writes the analysis, citing files and line numbers.

The setup page's query field runs the same searches without Copilot. It uses the same
engine in its own worker process, and saves each result as a new report.
