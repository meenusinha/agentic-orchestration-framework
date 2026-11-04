# Agentic Orchestration Framework — VS Code + GitHub Copilot

Cross-repository code knowledge for GitHub Copilot in VS Code.

Large systems are often split across several repositories, and across processes that
talk to each other through IDL interfaces. Copilot normally sees only the repository
you have open. This framework builds a **local knowledge graph (GraphRAG)** of all the
repositories you connect, and gives Copilot tools to search it. It can then answer
questions that span repositories, for example:

- *Which processes are affected if this constant changes?*
- *Who implements this IDL interface, and which clients call it?*
- *What would it take to add this feature, and in which repositories?*

You can also run the same searches yourself in a local web page, without Copilot.

**Supported platforms:** Windows and Linux.

---

## Contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Install and set up](#install-and-set-up)
4. [Use it in VS Code with Copilot](#use-it-in-vs-code-with-copilot)
5. [Search without Copilot: queries and the knowledge graph](#search-without-copilot)
6. [What gets indexed](#what-gets-indexed)
7. [Day-to-day operation](#day-to-day-operation)
8. [Files the framework adds to your repositories](#files-the-framework-adds-to-your-repositories)
9. [Command-line alternative](#command-line-alternative)
10. [Testing](#testing)
11. [Security](#security)
12. [Troubleshooting](#troubleshooting)
13. [License](#license)

---

## How it works

```
 Repositories you connect                  This framework (one installation per developer)
 ┌──────────────────────┐                  ┌─────────────────────────────────────────────┐
 │ source, docs, IDL,   │── parsed and ──▶ │ knowledge graph per repository               │
 │ build files          │   embedded       │  • whole functions, classes, IDL operations │
 └──────────────────────┘   locally        │  • calls, includes, inheritance, tests      │
                                           │  • processes and libraries (from builds)    │
                                           │  • who serves / calls each IDL interface,   │
                                           │    across repositories                      │
                                           └──────────────────┬──────────────────────────┘
                                                              │ MCP tools (stdio, local)
                                           VS Code ── GitHub Copilot (Agent mode)
```

- **Whole units, never cut.** Every function, method, class and IDL operation is stored
  complete, inside its hierarchy (file → namespace or class → member).
- **Structure-aware.** Folder roles (interface, implementation, internal, external,
  library, test, generated, docs, …) come from build files, then from the folder layout;
  you confirm them during setup. Tests are used as evidence only. External and vendored
  code contributes signatures only.
- **Across processes.** IDL interfaces, including in-house IDL dialects, are linked to
  the processes that implement them and the code that calls them, in any connected
  repository.
- **Local and offline.** Parsing, linking, embedding and search run on your machine.
  The only part that leaves it is what Copilot sends to its AI service as part of its
  normal operation.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.11 or newer | On **Windows or Linux** — the supported platforms |
| VS Code with GitHub Copilot | Copilot Chat in **Agent** mode, with MCP servers allowed in your VS Code settings |
| The embedding model | **Included** in `models/` (`ibm-granite/granite-embedding-small-english-r2`, revision `2ab6fa8ea2d674564defd37171ae19079b864b33`, Apache 2.0); every file is hash-pinned. Nothing to download |
| Git | Only for cloning repositories from the setup page; you can also add folders you've already cloned |
| Access to a Python package index | Only during installation. You can point pip at a package mirror (`PIP_INDEX_URL` or `pip.conf`) |

---

## Install and set up

Get the framework, then start the setup page:

```bash
git clone <URL of this repository> agentic-orchestration
cd agentic-orchestration
python3 setup_gui.py          # Windows: py setup_gui.py
```

The script creates its own virtual environment (`.venv`) and opens a local page in your
browser. It runs on `127.0.0.1` only and is protected by a one-time token. Six steps:

| Step | What you do |
|---|---|
| **1. Install** | Click **Install**. The hash-locked packages go into `.venv`; nothing is installed into your system Python |
| **2. Model** | Nothing to do: the included model is checked file by file against its pinned fingerprints (see [Model integrity](#model-integrity-hash-pinning)) and the step completes by itself. Only if `models/` is missing does it ask you to point to another copy |
| **3. Repositories** | Add each repository to connect, at least two. Either **Choose folder…** for a repository you've already cloned, or **Clone from Git**: enter the URL and branch and choose the folder to clone into. If that folder already holds a clone of the same repository, it's updated and switched to the branch instead |
| **4. Review** | Check what the scan found: languages, processes and libraries, IDL interfaces and the role of each folder. If a file type is unknown, say what it contains (implementation, interface, configuration, …) and which language to read it as |
| **5. Configure** | Click **Configure**. This writes the VS Code configuration into each repository (see [Files the framework adds](#files-the-framework-adds-to-your-repositories)) |
| **6. Create GraphRAG** | Click **Yes, create the GraphRAG**. The first build takes a few minutes per large repository. The page then shows which processes serve and call each IDL interface |

When setup is done, the page stays open for searching (see
[Search without Copilot](#search-without-copilot)). Running `python3 setup_gui.py` again
later opens straight on the search page.

---

## Use it in VS Code with Copilot

1. Open one of the connected repositories in VS Code (reload the window if it was
   already open).
2. Allow the MCP servers when VS Code asks. **MCP: List Servers** in the Command Palette
   shows their status.
3. In Copilot Chat, switch to **Agent** mode.
4. Start your request with **`/cross-repo-feature-analysis`**, for example:

   > /cross-repo-feature-analysis What would it take to show the password policy in the
   > login dialog? Which repositories and processes are affected?

Copilot picks the relevant repositories, searches them, follows IDL interfaces across
processes, and writes a feature analysis that cites files and line numbers.

Starting with `/cross-repo-feature-analysis` matters: without it, some models answer from
the open files only and skip the tools.

---

## Search without Copilot

The setup page has a **query field at the top of every page**:

| Mode | Enter | You get |
|---|---|---|
| **Search** | A question | The most relevant complete functions, classes, IDL operations and documentation sections from every repository, plus the interfaces they touch |
| **Trace interface** | An interface or operation name | Its IDL definition, the process that serves it, and every caller in every repository |
| **Impact of change** | A function, method or operation name | Its callers, test evidence, affected processes, and clients in other repositories |

Every result is also saved as a **new** Markdown file in `results/` (for example
`2026-11-01_142305_search_password-policy.md`); earlier results are never overwritten.
The **Queries** page lists the saved results.

**Knowledge graph** (left menu) shows each repository as a tree: processes and IDL
interfaces, then folders (with their role), files, namespaces and classes, down to every
method, function, field and IDL operation. Click a node to see its complete code and its
links (calls, implements, called through IDL by, tested by, …), including links to and
from other repositories. Search results have a **show in graph** link.

---

## What gets indexed

| Area | Supported |
|---|---|
| Languages | C, C++ (including mixed C/C++ and pure-C modules), Python, MATLAB, Perl, Java, JavaScript, TypeScript, Go, Rust, C# |
| Interfaces | IDL families (CORBA/OMG, DDS, MIDL, AIDL, Protocol Buffers, Thrift, ROS) and in-house dialects, recognised from their content or declared in step 4 |
| Build facts | Makefiles, CMake, `compile_commands.json` — **read only, never executed** — to find processes and libraries |
| Documentation | Markdown, by heading section |
| Never indexed | Secret-store files (`.env`, keys, keystores, credential files); dependency, build-output and cache folders. Secret-looking values elsewhere are replaced with `[REDACTED]` |

---

## Day-to-day operation

| Task | What to do |
|---|---|
| Code or docs changed | Nothing. The servers check for changes at most once a minute and update the graph, re-embedding only what changed |
| Add or remove a repository | Run `python3 setup_gui.py`, go to **Repositories**, then **Review**, **Configure** and **Create GraphRAG**. Reload VS Code afterwards |
| Change a folder role or file type | Same as above; the graph rebuilds |
| Force a full rebuild | Delete `<repo>/.rag_index/`; it's rebuilt on the next start |
| Remove all derived data | Delete `<repo>/.rag_index/` in each repository and `results/` in the framework folder |
| Logs | `~/.agentic-orchestration/logs/` (private; code text is hidden unless `log_content: true` in `orchestrator/mcp/config.yaml`) |

---

## Files the framework adds to your repositories

| Path | Purpose | Commit it? |
|---|---|---|
| `mcp/` | The repository's MCP server and its settings (`mcp/config.yaml`: folders to index, file types, folder roles) | Optional — useful to share settings in a team |
| `knowledge/` | Optional extra documentation to index | Optional |
| `.vscode/mcp.json` | Tells VS Code how to start the MCP servers; contains absolute paths on your machine | **No** — it's specific to your machine |
| `.github/copilot-instructions.md` | Repository-specific instructions for Copilot | Optional |
| `.github/skills/cross-repo-feature-analysis/` | The agent skill behind `/cross-repo-feature-analysis` | Optional |
| `.rag_index/` | The knowledge graph (contains code excerpts) | **No** — it's added to `.gitignore` automatically |

---

## Command-line alternative

The setup page is the recommended way. The same steps are available as scripts, run with
the framework's virtual-environment Python (`.venv/bin/python`; on Windows
`.venv\Scripts\python.exe`):

```bash
# Inspect repositories (read-only): languages, processes, IDL, folder roles, unknown types
.venv/bin/python skills/cross-repo-feature-analysis/scripts/scan.py --framework . /path/to/repo-a /path/to/repo-b

# Configure them (one --repo per repository; answers for unknown types and roles as flags)
.venv/bin/python skills/cross-repo-feature-analysis/scripts/configure.py --framework . \
    --repo repo-a=/path/to/repo-a --src repo-a=. \
    --repo repo-b=/path/to/repo-b --src repo-b=. \
    --file-type 'repo-b=.xidl:interface:idl' --role 'repo-a=third_party/**:external:signatures'

# Ask a cross-repository question from the terminal (saves a new report in results/)
.venv/bin/python test_mcp.py "what would it take to add a password policy hint to login?"
```

---

## Testing

One command runs every check: an end-to-end test of the setup page, a vulnerability
audit of the locked dependencies, and a test of the MCP servers as VS Code starts them.

```bash
python3 tests/run_all.py          # Windows: py tests\run_all.py
```

The tests build two small sample repositories locally from `tests/fixtures/`; they don't
touch your own repositories. Run the checks after installing or upgrading, and on a
schedule (for example weekly) so new advisories for the pinned packages are noticed.

---

## Security

Built for environments with strict security requirements:

- No code leaves the machine during indexing or searching (measured: zero network attempts).
- Repository code is never executed.
- Dependencies are hash-locked and audited for known vulnerabilities on every change and weekly.
- Only the approved embedding model is loaded, with every one of its files hash-pinned (below).
- Secrets are excluded or redacted.
- The graph, saved results and logs are private to your user account.

### Model integrity (hash-pinning)

A **hash** is a fingerprint of a file: a 64-character value (SHA-256) calculated from every
byte of the file. Changing even one byte gives a completely different value.

**Hash-pinning** means the expected fingerprint of each file is recorded in the framework's
code. The embedding model consists of eight files: the weights, the model and pipeline
settings, and the tokenizer. All eight are pinned. Each file is recalculated and compared:

- when the setup checks the model (step 2), and
- every time an MCP server starts.

If any file is missing or differs — corrupted, replaced or edited — the model is refused and
the message names the file. One deliberate tolerance: in the seven JSON text files, Windows
line endings (CRLF) are treated the same as Unix ones (LF), so copying the folder with a tool
that converts line endings doesn't cause a false alarm. The weights file is compared byte
for byte. A changed configuration or tokenizer file therefore can't alter
how code is processed, and can't carry settings aimed at the software that loads the model.

The recorded fingerprints are listed in
[docs/security-evidence.md](docs/security-evidence.md#23-embedding-model). The dependencies
are protected the same way: every package in `requirements.lock` is pinned by its SHA-256.

For details:

- [docs/architecture.md](docs/architecture.md) — components, processes, storage, cost
- [docs/security-considerations.md](docs/security-considerations.md) —
  data flow, every control, residual risks and recommendations
- [docs/security-evidence.md](docs/security-evidence.md) — evidence for
  each component of the technology stack (versions, licenses, vulnerability status,
  measurements)
- [SECURITY.md](SECURITY.md) — supported versions and how dependencies are kept up to date

---

## Troubleshooting

| Problem | Fix |
|---|---|
| The MCP servers don't appear in Copilot | Reload the VS Code window; check **MCP: List Servers**; make sure MCP servers are allowed in your VS Code settings |
| Copilot answers without using the tools | Use **Agent** mode and start the request with `/cross-repo-feature-analysis`, or choose a stronger model |
| Servers stopped after re-configuring | Reload the VS Code window |
| `ModuleNotFoundError` in the MCP server output | The configuration points at the wrong Python: run **Configure** again from the setup page |
| `Refusing to load …` / model not found | A model file is missing or changed. Restore the included model with `git checkout -- models/`, or point step 2 to another copy of the approved model |
| "already exists and is not a git clone" when cloning | Choose another folder or folder name; existing folders are never overwritten |
| Cloning a private repository fails | Set up Git access first (SSH key or credential manager); the setup page never asks for passwords |
| A repository returns nothing relevant | In **Review**, check for unknown file types set to *ignore* and for folders with the wrong role |
| The first search is slow | Expected — the first search loads the model and graphs; later searches take well under a second |

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
