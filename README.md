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
   - [Large repositories: time estimate, batches, progress](#large-repositories-time-estimate-batches-progress)
   - [Setting up again: what is kept and what is overwritten](#setting-up-again-what-is-kept-and-what-is-overwritten)
   - [Set up from Copilot Chat: /graphrag-setup](#set-up-from-copilot-chat-graphrag-setup)
4. [Use it in VS Code with Copilot](#use-it-in-vs-code-with-copilot)
5. [Search without Copilot: queries and the knowledge graph](#search-without-copilot)
6. [What gets indexed](#what-gets-indexed)
7. [Settings](#settings)
8. [Day-to-day operation](#day-to-day-operation)
9. [Files the framework adds to your repositories](#files-the-framework-adds-to-your-repositories)
10. [Command-line alternative](#command-line-alternative)
11. [Testing](#testing)
12. [Security](#security)
13. [Troubleshooting](#troubleshooting)
14. [License](#license)

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

There are two ways to set up: the **setup page** below, or **Copilot Chat** with the
`/graphrag-setup` agent skill (see [Set up from Copilot Chat](#set-up-from-copilot-chat-graphrag-setup)).
Both ask the same questions and give the same result.

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
| **1. Install** | Click **Install**. The hash-locked packages go into `.venv`; nothing is installed into your system Python. If they're already installed, choose **Keep and continue** or **Reinstall all packages** |
| **2. Model** | Nothing to do: the included model is checked file by file against its pinned fingerprints (see [Model integrity](#model-integrity-hash-pinning)). Keep it, or type another folder holding the approved model to replace it. Only if `models/` is missing does it ask you to point to another copy |
| **3. Repositories** | Add each repository to connect, at least two. Either type its folder path (or **Choose folder…**) for a repository you've already cloned, or **Clone from Git**: enter the URL and branch and choose the folder to clone into. If that folder already holds a clone of the same repository, it's updated and switched to the branch instead. Repositories connected before are listed; removing one disconnects it at step 5 (its files stay) |
| **4. Review** | Check what the scan found: languages, size, processes and libraries, IDL interfaces and the role of each folder. **Choose which folders to index**: untick a folder to exclude it (**Select all** / **Exclude all** per repository), and add sub-folders or file patterns to exclude, typed or with **Browse…**. If a file type is unknown, say what it contains (implementation, interface, configuration, …) and which language to read it as; types found only in excluded folders aren't asked. **Map several at once:** the bar at the top lists every file type found, in all repositories — tick some (**Select unanswered** / **Select suggested** / **Select all**), choose the kind and language, **Apply**, and all their rows are filled in. Each row shows its state: red *needs an answer*, amber *suggested* (the scan's guess, not confirmed yet), green *accepted* Answers saved by an earlier setup are shown and can be changed |
| **5. Configure** | Click **Configure**. This writes the VS Code configuration into each repository (see [Files the framework adds](#files-the-framework-adds-to-your-repositories)). For a repository set up before, you first choose what to overwrite (see [Setting up again](#setting-up-again-what-is-kept-and-what-is-overwritten)) |
| **6. Create GraphRAG** | The page first estimates how long it takes on this computer, per component. Create everything, or choose fewer components as a first batch and add the rest later (see [Large repositories](#large-repositories-time-estimate-batches-progress)). A progress bar shows how far it is and the time left. The page then shows which processes serve and call each IDL interface |

When setup is done, the page stays open for searching (see
[Search without Copilot](#search-without-copilot)). Running `python3 setup_gui.py` again
later opens straight on the search page.

### Large repositories: time estimate, batches, progress

Creating the GraphRAG for a very large repository can take hours, almost all of it spent
turning code into vectors (embedding) on the CPU. Step 6 therefore starts with an
estimate and lets you build in batches:

1. **Components.** Each repository is divided into components: its top-level folders. A
   folder that holds most of a large repository (for example one huge `src/`) is split into
   its sub-folders. Top-level files, such as the build files that tell which processes
   exist, are always included.
2. **Estimate.** The page lists every file that would be indexed, checks which are already
   in the GraphRAG with unchanged content (their vectors are reused), and resolves folder
   roles (tests are never embedded; external code only by its signatures). It parses a
   sample of your real code from every component, and embeds it with the real model on this
   computer. From that it shows the time per component, per repository and in total.
   Nothing is written while estimating.
   - Embedding cost grows faster than the text length (long functions cost more per
     character), and the estimate takes that into account.
   - Every larger build records this computer's real speed (numbers only, in
     `~/.agentic-orchestration/build-speed.json`), and later estimates use it. The page says
     which speed it used. In tests on two repositories of 26 MB and 6.5 MB of real code, the
     estimate was 7% off with the sample's speed, and 10% off with a recorded build's speed;
     with the recorded speed, the time left shown halfway through was within about 12%.
3. **Choose.** Tick the components to create now: **Select all** / **Select none** per
   repository or for all of them. If everything takes more than 15 minutes, the page
   suggests a batch: **Create all**, or **Suggest a first batch** (picks what fits in about 15
   minutes), then **Create batch 1**. Unticked components are *left for later*:
   they are saved in the repository's `deferred` setting, and every MCP server, the query
   field and the router leave them out the same way.
4. **Progress.** While building, a progress bar shows the repository, the stage (reading,
   parsing, embedding, saving, then linking across repositories), the units done, and the
   time left. The bar is weighted by the work each repository and stage needs (from the
   estimate), and the time left follows the real speed as the build goes.
5. **Next batch.** When a batch is done, the page lists what's still left and asks:
   **Create all remaining**, **Choose the next batch**, or **Stop here — add them later**.
   Copilot and the query field already work with what has been built. Each new batch only
   embeds the new components; everything built before is reused.

You can come back to step 6 at any time to add the remaining components, or to leave some
out again. *Left for later* is different from *excluded* (step 4): an excluded folder is
never indexed, in any batch, until you include it again. VS Code servers that are already
running follow both changes at their next refresh.

### Setting up again: what is kept and what is overwritten

You can run the setup again for the same repositories at any time. Nothing is
overwritten without asking:

| Step | Already done before | Your choice |
|---|---|---|
| 1. Install | Packages installed | **Keep and continue**, or **Reinstall all packages** (hash-verified again) |
| 2. Model | Model verified | **Keep and continue**, or type another folder to replace it (checked first) |
| 3. Repositories | Repositories connected | Keep them, add more, or remove one; a removed repository is disconnected at step 5 and its files are left as they are |
| 4. Review | Folders to index, folder roles and file-type answers saved | Excluded folders are shown unticked, saved roles as *saved*, file types as *answered before*; all can be changed |
| 5. Configure | Settings and generated files in the repository | Per repository, or for all at once (**Overwrite all** / **Keep all**): **folders to index, roles and file types** (replace with step 4's answers, or keep), **GraphRAG and query settings** (reset to defaults, or keep), **Copilot instructions** (regenerate, or keep your edited file), **agent skill** (reinstall, or keep) |
| 6. Create GraphRAG | A GraphRAG exists | Per repository: **update** it (the default: unchanged code is reused), or **start from scratch** (its stored graph and cached vectors are removed and everything is embedded again) |

Always updated, because they must match this framework version and this computer: the
MCP server code in each repository's `mcp/` folder and the VS Code configuration
`.vscode/mcp.json`. Your source code and your `knowledge/` folder are never changed.

---

### Set up from Copilot Chat: /graphrag-setup

The framework includes a second agent skill, **`/graphrag-setup`**, that does the whole setup
from Copilot Chat instead of the setup page:

1. Clone the framework and open its folder in VS Code (the skill is in
   `.github/skills/graphrag-setup/`, where Copilot finds it).
2. In Copilot Chat, switch to **Agent** mode and ask, for example:

   > /graphrag-setup connect ../billing-server and ../billing-client and create the GraphRAG

Copilot then works through the same steps as the setup page, running the commands in the
terminal and asking you every decision:

- **Install:** the hash-locked packages into `.venv`, or keep or reinstall an existing one.
- **Repositories** to connect, and a clone of any not cloned yet.
- **Review:** which folders to index or exclude, what unknown file types contain, folder roles.
- **Configure:** for repositories set up before, what to overwrite or keep.
- **Estimate**, per component, on this computer.
- **Batch:** everything now, the suggested first batch, or your own choice.
- **Build**, with a progress line every few seconds (percentage, time left, stage), then
  the next batch or stop.

Configure also installs `/graphrag-setup` into every connected repository, so later changes
(add the components left for later, exclude a folder, rebuild a repository) can be asked for
from any of them.

The skill drives a command-line tool you can also use yourself, with the framework's
virtual-environment Python:

```bash
GR=.github/skills/graphrag-setup/scripts/graphrag.py
.venv/bin/python $GR status                                  # repositories, GraphRAG, exclusions, batches
.venv/bin/python $GR folders server --exclude third_party    # or --include, --exclude-all, --include-all
.venv/bin/python $GR chunking server --mode hybrid --lines 60 --overlap 15
.venv/bin/python $GR skip server --header-word Author --header-word Created --preview-only   # then without --preview-only
.venv/bin/python $GR estimate                                # time per component; suggests a first batch
.venv/bin/python $GR build --suggested                       # or --all, --only R:C, --add R:C, --later R:C, --fresh REPO
```

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

## Settings

The setup page has a **Settings** page (left menu, under Explore) for how the GraphRAG is
created and how queries behave. Every field shows its default and allowed range. Choose a
repository, or tick **apply to all repositories**, then **Save**. **Reset to defaults**
fills in the defaults.

| Group | Setting | Default | Range |
|---|---|---|---|
| **GraphRAG creation** (per repository) | Folders to index (`src_path`) | `.` (the whole repository) | paths |
| | Extra documentation folders (`knowledge_path`) | `./knowledge` | paths |
| | Exclude (`exclude`) | none | globs, e.g. `build/**`, `*.generated.c`; also set in step 4 (untick a folder) |
| | Components left for a later batch (`deferred`) | none (everything) | folders; filled in by step 6 |
| | Chunking method (`chunking`) | `graph` | `graph` · `sliding` · `hybrid` (see [Chunking and skip rules](#chunking-and-skip-rules)) |
| | Chunk size (`chunk_lines`) | 60 lines | 5–2000; sliding window and hybrid |
| | Chunk overlap (`chunk_overlap`) | 15 lines | 0 to chunk size − 1 |
| | Skip header words (`skip_header_words`) | none | e.g. `Author, Created, Date, Copyright` |
| | Skip patterns (`skip_patterns`) | none | regular expressions, one per line |
| | Extra file extensions indexed as text (`extra_extensions`) | none | e.g. `.tmpl` |
| | Maximum file size (`max_file_kb`) | 2000 KB | 10–50000 |
| | Embedding batch size (`embed_batch_size`) | 64 | 1–512 |
| | Change check interval (`refresh_seconds`) | 60 s | 10–3600 |
| **Queries** (per repository) | Results per search (`top_k`) | 5 | 1–20 |
| | Answer size budget (`result_budget_tokens`) | 6000 | 500–100000 |
| | Candidates from vector and from keyword search (`candidate_pool`) | 60 | 10–500 |
| | Rank-fusion constant (`rrf_k`) | 60 | 1–1000 |
| | Impact analysis depth, in caller levels (`impact_depth`) | 2 | 1–5 |
| | Ranking weight per folder role (`role_weights`) | interface 1.15 · implementation, internal, library, docs, config 1.0 · tooling 0.85 · example 0.7 · external, build 0.6 · generated 0.5 · test 0 | 0–3 each |
| **Router** (all repositories) | Repositories picked per request (`router_top_repos`) | 2 | 1–10 |
| | Search hits per repository used for routing (`router_hits_per_repo`) | 6 | 1–50 |

- **Creation settings** take effect when the GraphRAG is rebuilt. After saving, the page
  offers **Rebuild GraphRAG now**.
- **Query and router settings** apply to the query field at once, and to Copilot after
  reloading VS Code.
- The values are stored in each repository's `mcp/config.yaml` and in the framework's
  `orchestrator/mcp/config.yaml`; you can also edit them there. Saving from the page keeps
  the files' comments.

### Chunking and skip rules

**Chunking** decides how code is cut into the pieces that are searched:

| Mode | Search results | Use it when |
|---|---|---|
| `graph` (default) | Whole functions, classes, IDL operations and documentation sections, never cut, with their class or module context | Almost always: answers contain complete units |
| `sliding` | Chunks of *chunk size* lines that overlap by *overlap* lines, with file and line range; each chunk lists the code it covers and their calls, IDL links and tests | The code has few clear units (long scripts, generated or legacy code), or you prefer fixed-size pieces |
| `hybrid` | Whole units, as in `graph`; units longer than the chunk size are also searched in overlapping windows, so a match deep inside a long function is found | The code has very long functions |

The graph itself (calls, includes, IDL links, test evidence, processes) is built the same way in
every mode, so trace interface and impact of change work in all three.

**Skip rules** keep content out of the GraphRAG entirely — for example file headers with author
names and creation dates:

- **Header words:** if the comment block at the top of a file (`/* … */`, `//`, `#`, `--`, `%`,
  `;`, or a Python docstring) contains any of these words, the block is skipped. A shebang or
  encoding line before it is kept.
- **Patterns:** regular expressions, one per line (multi-line mode: `^` and `$` match at every
  line); every match is skipped anywhere in a file.
- Skipped text is replaced by blank lines, so line numbers in results stay correct, and it never
  reaches parsing, embedding or storage.
- **Preview skip rules** (Settings page) shows, before saving, how many files each rule matches
  and an example of what it removes. Patterns that could take extremely long, such as `(a+)+`,
  are refused.

Both are per repository, take effect when the GraphRAG is rebuilt (unchanged text keeps its
vectors), and can also be set from Copilot Chat with `/graphrag-setup` or from the command line
(`graphrag.py chunking …`, `graphrag.py skip …`).

### Typing paths

Every path can be typed or pasted, as well as chosen with **Browse…**:

- **Model folder** (step 2): type the path of a folder holding the approved model and click
  **Use this folder**. It's used where it is, nothing is copied, after every file is checked
  against its fingerprint. **Use the included model instead** switches back.
- **Repositories** (step 3): type a repository's folder path and click **Add**, or type the
  folder to clone into.

---

## Day-to-day operation

| Task | What to do |
|---|---|
| Code or docs changed | Nothing. The servers check for changes at most once a minute and update the graph, re-embedding only what changed |
| Add or remove a repository | Run `python3 setup_gui.py`, go to **Repositories**, then **Review**, **Configure** and **Create GraphRAG**. Reload VS Code afterwards |
| Change a folder role or file type | Same as above, and choose to overwrite *folder roles and file types* in step 5; the graph rebuilds |
| Add the components left for later | Step 6 (**Create GraphRAG**): tick them, or **Create all remaining** |
| Change creation or query settings | **Settings** page (see [Settings](#settings)) |
| Force a full rebuild | Step 6: tick **Start this repository from scratch**. Or delete `<repo>/.rag_index/` while VS Code is closed; it's rebuilt on the next start |
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
| `.github/skills/graphrag-setup/SKILL.md` | The agent skill behind `/graphrag-setup` (its scripts stay in the framework) | Optional |
| `.rag_index/` | The knowledge graph (contains code excerpts) | **No** — it's added to `.gitignore` automatically |

---

## Command-line alternative

The setup page or the `/graphrag-setup` skill is the recommended way. Creating the GraphRAG
from the command line is described in [Set up from Copilot Chat](#set-up-from-copilot-chat-graphrag-setup)
(`graphrag.py`). The other steps are available as scripts, run with
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

# Configure again, choosing what to overwrite for repositories set up before:
#   --exclude NAME=GLOB        folder or file pattern never indexed (repeatable; NAME= clears)
#   --replace-roles NAME       roles and file types become exactly the --role/--file-type given
#   --reset-settings NAME      GraphRAG and query settings back to their defaults
#   --keep NAME=instructions   keep the edited .github/copilot-instructions.md
#   --keep NAME=skill          keep the installed agent skill
#   --drop NAME                disconnect a repository (its files are left as they are)

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
- [docs/requirements.md](docs/requirements.md) — every requirement with its status, open items and design decisions
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
| Creating the GraphRAG would take hours | Create it in batches (step 6): start with the components you need first, and add the rest later. Excluding generated or vendored folders (**Settings → Exclude**) also helps |
| A component is missing from search results | It may be *left for later*: tick it in step 6, or remove it from **Settings → Components left for a later batch** |

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
