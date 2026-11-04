# First-time setup

Follow this procedure when the MCP servers are not connected and the developer has
agreed to set them up. Run the commands yourself. Before any step that installs
something or writes into a repo, say what it will do and get the developer's
go-ahead. You can confirm several steps at once. (The setup page, `python setup_gui.py`
in the framework folder, does all of this without an assistant.)

Supported platforms: **Windows and Linux**. The commands below are for Linux. On
Windows, the venv Python is `.venv\Scripts\python.exe` instead of `.venv/bin/python`,
and paths use `%USERPROFILE%\agentic-orchestration` instead of `~/agentic-orchestration`.

## 1. Ask which repos to connect

Ask the developer for the repos that should know about each other. You need at least
two. Make it easy for them:

- Suggest the current repo, plus any sibling folders next to it that are git repos
  (`ls ..`, then look for a `.git` folder in each). A repo that only holds IDL files
  (the interfaces between processes) counts — include it.
- Each repo gets a short id. By default it's the folder name, lower-cased (for example
  `payments-service`). Only ask about ids if two folders would end up with the same id.

## 2. Find or install the framework

Check for an existing install, in this order:

1. The `orchestrator_router` entry in any `.vscode/mcp.json`. Its `args` path is
   `<framework>/orchestrator/mcp/router_mcp_server.py`.
2. `~/agentic-orchestration`.
3. Ask the developer whether they have a clone somewhere else.

If there's no install, clone one and install it **into its own virtual environment** —
never into the system Python (the framework's scripts and servers refuse to run outside
a venv unless the developer explicitly sets `AO_ALLOW_NO_VENV=1`). This needs Python
3.11+, and the `pip install` step takes a few minutes the first time:

```bash
git clone <URL of the framework repository> ~/agentic-orchestration
python3 -m venv ~/agentic-orchestration/.venv
~/agentic-orchestration/.venv/bin/pip install --require-hashes -r ~/agentic-orchestration/requirements.lock
```

## 3. Scan each repo and agree on its structure

Each repo becomes a **code graph**: every function, method, class, IDL operation and
Markdown section is indexed whole, and every file gets a **role**. Run the read-only
scanner with the framework venv's Python — it uses the indexer itself, so it shows
exactly what will be indexed:

```bash
~/agentic-orchestration/.venv/bin/python <this-skill>/scripts/scan.py \
    --framework ~/agentic-orchestration /abs/path/to/server /abs/path/to/client
```

For each repo it reports languages, build files and the **processes and libraries**
they build (Makefiles, `compile_commands.json`, CMake — read, never executed), IDL
interfaces, generated code, the **proposed role of every top-level folder**, and any
**unknown file types**. Show the developer a short summary per repo, for example:

| server | |
|---|---|
| Processes | `billingd` (executable), `libtax.a` (library) |
| IDL | uses `Billing::IInvoiceService` (defined in interfaces) |
| Roles | `include/` interface · `src/`, `policy/` implementation · `gen/` generated · `tests/` test (evidence only) · `third_party/` external (signatures only) |
| Unknown types | `.pc` (looks like code), `.xidl` (looks like an IDL dialect) |

Then:

- **Ask about every unknown file type.** The scan shows what each looks like (an
  IDL-like interface file, source code, configuration, docs). Ask the developer what
  the files contain and how they should be read, and record each answer as a
  `--file-type` flag:

  | Developer says | Flag |
  |---|---|
  | "Our in-house IDL" | `--file-type 'name=.xidl:interface:idl'` |
  | "Pro*C — it's C with embedded SQL" | `--file-type 'name=.pc:implementation:c'` |
  | "Server configuration" | `--file-type 'name=.xcfg:server-config:text'` |
  | "Generated, ignore it" | `--file-type 'name=.tmp:ignore'` |

  `parse_as` is one of: c, cpp, python, idl, perl, matlab, java, javascript,
  typescript, go, rust, csharp, markdown, text. Never guess the language for code —
  ask. `configure.py` refuses to run while any unknown type is unanswered.
- **Ask the developer to confirm the folder roles**, and record corrections with
  `--role`: `--role 'name=vendor_libs/**:external:signatures'`,
  `--role 'name=api/**:interface'`. Roles: interface, implementation, internal,
  external, library, test, build, generated, docs, tooling, config, data, example.
  `external` code is indexed as signatures only; tests are indexed only as evidence.

Index the whole repo (`--src name=.`) — the indexer skips dependencies, build output,
virtual environments and caches by itself, and roles keep tests, generated and vendored
code out of the results.

## 4. Get the embedding model

The index needs the approved embedding model,
`ibm-granite/granite-embedding-small-english-r2` (pinned revision and weights hash), in
`<framework>/models/granite-embedding-small-english-r2/`. **It is included in the framework
repository**, so normally there is nothing to do. Every file is hash-pinned and checked
automatically.

Only if that folder is missing or a file was changed (the scripts say which), restore it
with `git checkout -- models/` in the framework folder, or ask the developer for another
copy of the approved model and pass it with `--model-from <path>` in the next step; any
other model is refused. The framework never downloads a model.

## 5. Configure every repo in one command

Run `configure.py` with the **framework venv's Python**, passing the agreed answers.
Keep the quotes the scan printed, so the shell doesn't expand globs.

```bash
~/agentic-orchestration/.venv/bin/python <this-skill>/scripts/configure.py \
    --framework ~/agentic-orchestration \
    --repo interfaces=/abs/path/to/interfaces --src interfaces=. \
    --repo server=/abs/path/to/server --src server=. \
      --role 'server=third_party/**:external:signatures' \
    --repo client=/abs/path/to/client --src client=. \
      --file-type 'client=.xidl:interface:idl' --file-type 'client=.pc:implementation:c'
```

The script:

- refuses to start while any repo has unclassified file types, or while the model is
  missing or changed (unless `--model-from` points to a valid copy)
- adds the repos to `orchestrator/mcp/config.yaml`
- copies the repo agent (`mcp/`, including its `codegraph/` package) and `knowledge/`
  into each repo, and writes `repo_name`, the paths, `file_types` and `structure` into
  that repo's `mcp/config.yaml`
- runs `orchestrator/setup.py`, which writes `.vscode/mcp.json`,
  `.github/copilot-instructions.md` and this skill (`.github/skills/`) into every repo, tells
  every server which repos are connected (so IDL interfaces can be followed across
  them), and adds `.rag_index/` to each git repo's `.gitignore`

Flags are per repo, use the repo's id, and can be repeated:

| Flag | Sets |
|---|---|
| `--src name=PATH` | folders to index (`.` = the whole repo, recommended) |
| `--knowledge name=PATH` | extra doc folders (Markdown anywhere under `--src` is already indexed) |
| `--file-type name=.EXT:KIND[:PARSE_AS]` | the developer's answer for an unknown file type |
| `--role name=GLOB:ROLE[:INDEX]` | a folder-role correction for the structure manifest |

A repo with no flags keeps its current settings. When settings change, the graph
rebuilds itself on the next start and reuses its cached embeddings.

The script refuses to touch a repo that already has an unrelated `mcp/` folder. If
that happens, explain the clash and let the developer decide what to do.

## 6. Seed the docs (optional)

All Markdown in a repo — READMEs included — is indexed by heading section. For a repo
with little documentation, offer to draft `knowledge/architecture.md` and do it if they
agree. Read the repo's README, layout, build files and main entry points, and write
what's actually there:

- what the repo does, and which processes it builds
- its main components, and where they live
- the IDL interfaces it defines, serves or calls, and which repos are on the other side

Use concrete names: class names, functions, interfaces, file paths. Suggest the
developer review the draft. Never put real secrets in drafted docs (the indexer
redacts secret-looking values, but it's better if they're never there).

## 7. Load the servers

VS Code starts the MCP servers listed in `.vscode/mcp.json` when the workspace opens.
Tell the developer to reload the VS Code window and allow the servers when asked
(**MCP: List Servers** shows their status). VS Code stops changed servers after a
re-configure without restarting them, so reload after every `configure.py` run. Tell the
developer to keep Copilot Chat in **Agent** mode and to start requests with
`/cross-repo-feature-analysis`, because small auto-selected models may otherwise skip the
MCP tools.

Start the servers of repos that define IDL interfaces first if you can: each repo
publishes its interfaces when its graph is built, and the others link to them (links
are re-resolved automatically once a peer publishes). The first start of each repo
takes longest while its graph is built and embedded.

## Later changes

- **Add a repo**: scan it, ask about its unknown file types and roles, then re-run
  `configure.py` with only the new `--repo` and its flags. Every repo's config is
  regenerated to include the new peer.
- **Change roles or file types**: re-run `configure.py` with that repo's `--repo` and
  the new `--role` / `--file-type` flags. The graph rebuilds on next start.
- **Code or docs changed**: nothing to do — each server checks for changed files at
  most once a minute and rebuilds, re-embedding only what changed.

## Troubleshooting

- **`ModuleNotFoundError` when a server starts**: the configs point at the wrong
  Python. Re-run `configure.py` with the framework venv's Python.
- **An interface shows no providers or consumers**: start (or restart) the servers of
  the repos involved so they publish their links; check that the server repo was
  connected; for code that reaches the interface in an unusual way, see whether
  `find_references` shows the call.
- **A repo returns nothing relevant**: run `scan.py` on it — check for unknown file
  types that were set to `ignore` and for folders with the wrong role.
- **Results full of vendored or generated code**: give those folders the `external` or
  `generated` role with `--role`.
