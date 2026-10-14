# First-time setup

Follow this procedure when the MCP servers are not connected and the developer has
agreed to set them up. Run the commands yourself. Before any step that downloads
something or writes into a repo, say what it will do and get the developer's
go-ahead. You can confirm several steps at once.

The commands below are for macOS and Linux. On Windows, the venv Python is
`.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## 1. Ask which repos to connect

Ask the developer for the repos that should know about each other. You need at least
two. Make it easy for them:

- Suggest the current repo, plus any sibling folders next to it that are git repos
  (`ls ..`, then look for a `.git` folder in each).
- Each repo gets a short id. By default it's the folder name, lower-cased (for example
  `payments-service`). Only ask about ids if two folders would end up with the same id.

## 2. Find or install the framework

Check for an existing install, in this order:

1. The `orchestrator_router` entry in any `.mcp.json`. Its `args` path is
   `<framework>/orchestrator/mcp/router_mcp_server.py`.
2. `~/agentic-orchestration`.
3. Ask the developer whether they have a clone somewhere else.

If there's no install, clone one and install it. This needs Python 3.11+, and the
`pip install` step takes a few minutes the first time:

```bash
git clone https://github.com/meenusinha/agentic-orchestration-framework.git ~/agentic-orchestration
python3 -m venv ~/agentic-orchestration/.venv
~/agentic-orchestration/.venv/bin/pip install -r ~/agentic-orchestration/requirements.txt
```

## 3. Decide what to index in each repo

The analysis is only as good as what each repo's search index contains, so agree on
this with the developer before configuring anything. Run the read-only scanner with
the framework venv's Python:

```bash
~/agentic-orchestration/.venv/bin/python <this-skill>/scripts/scan.py \
    /abs/path/to/frontend backend=/abs/path/to/api
```

For each repo it lists doc folders, source folders, test folders, file types the index
skips by default, and the current settings if the repo is already connected. It ends
with suggested `configure.py` flags. Turn that into a short plan per repo and show it to
the developer, for example:

| frontend | Indexed |
|---|---|
| Docs | `./knowledge`, `./docs` (12 .md) |
| Code | `./packages/*/src`, `./apps/*/src` (.ts, .tsx) |
| Extra types | `.vue` |
| Left out | `./tests`, `docs/legacy.rst` (not .md) |

Ask what they want to change, and apply their judgement on top of the scan:

- **Include** folders that explain behaviour: architecture docs, ADRs, API specs, and
  the main application and library code.
- **Leave out** tests (unless they are the best record of behaviour), generated code,
  migrations, fixtures, sample data and vendored code. These add noise that pushes
  real answers out of the top results.

The indexer has some hard limits, so steer around them:

- **Only `.md` files are read as docs, and every `README.md` is skipped.** Important
  content in a README, or in `.rst` or `.adoc` files, needs to be summarised or
  converted into a `.md` file under `knowledge/` (see step 6).
- **Folders are read recursively with no exclusions.** Never use the repo root (`.`), or
  any folder the scan marks ✗ because it contains `node_modules/`, build output or a
  venv. Use the narrower paths or `*/src` globs the scan suggests instead.
- **Keep doc folders separate.** Don't list a folder together with one of its
  subfolders (for example `./docs` and `./docs/api`), or the same files are indexed
  twice.

## 4. Get the embedding model

The search index needs the `all-MiniLM-L6-v2` embedding model in
`<framework>/models/all-MiniLM-L6-v2/`. If it's already there, skip this step.
Otherwise, see what's already on the machine:

```bash
~/agentic-orchestration/.venv/bin/python <this-skill>/scripts/configure.py \
    --framework ~/agentic-orchestration --find-model
```

Then **ask the developer** whether they already have the model. Mention any copies
the command found (for example in the Hugging Face cache), and ask whether they have
one somewhere else, such as a shared drive on an offline machine.

- **They have a copy:** use `--model-from <path>` in the next step. The script checks
  it's a complete all-MiniLM-L6-v2 before copying it.
- **They don't:** ask for permission to download it (about 90 MB from Hugging Face).
  Use `--download-model` only after they say yes.

`configure.py` never downloads without `--download-model`. If neither flag is given
and the model is missing, it stops before changing anything and lists the copies it
found.

## 5. Configure every repo in one command

Run `configure.py` from this skill's `scripts/` folder with the **framework venv's
Python**, passing the agreed plan as flags. That way the generated MCP configs point at
an interpreter that has the dependencies installed. Keep glob values quoted, as the
scan prints them, so the shell doesn't expand them.

```bash
~/agentic-orchestration/.venv/bin/python <this-skill>/scripts/configure.py \
    --framework ~/agentic-orchestration --model-from <path-from-step-4> \
    --repo /abs/path/to/frontend \
      --src 'frontend=./packages/*/src' --knowledge frontend=./docs --ext frontend=.vue \
    --repo backend=/abs/path/to/api \
      --src backend=./app --knowledge backend=./adr
```

The script:

- copies (`--model-from`) or downloads (`--download-model`) the embedding model if
  it's missing
- adds the repos to `orchestrator/mcp/config.yaml`, and drops the template's
  placeholder repos
- copies `mcp/` and `knowledge/` into each repo, then writes `repo_name`,
  `display_name` and the indexing settings into that repo's `mcp/config.yaml`
- runs `orchestrator/setup.py`, which writes `.mcp.json`, `.cursor/mcp.json`,
  `.vscode/mcp.json`, the Copilot instructions and this skill into every repo

The indexing flags are per repo, use the repo's id, and can be repeated:

| Flag | Sets | Notes |
|---|---|---|
| `--src name=PATH` | `src_path` | Folder or quoted glob. `name=null` turns off code indexing. |
| `--knowledge name=PATH` | `knowledge_path` | Extra `.md` doc folders. `./knowledge` is always kept. |
| `--ext name=.EXT` | `extra_extensions` | Extra file types to index as code. `name=null` clears them. |

A repo with no flags keeps its current settings. A new repo with no `--src` gets a
guessed source folder. If a repo's indexing settings change, the script deletes its
`.chroma_db/` cache so the index is rebuilt on the next query.

Check the script's summary line for each repo against the agreed plan.

The script refuses to touch a repo that already has an unrelated `mcp/` folder. If
that happens, explain the clash and let the developer decide what to do.

## 6. Seed the knowledge docs

Retrieval is only as good as each repo's `.md` docs. For every repo with 0 knowledge
docs, or whose key docs are in a README or a non-`.md` format, offer to draft a starter
`knowledge/architecture.md`, then do it if they agree. Read the repo's README,
top-level layout and main entry points, and write down what's actually there:

- what the repo does
- its main components, and where they live
- the APIs, events or schemas it exposes or consumes, and which other repos it talks to

Use concrete names: endpoints, class names, file paths. Those are what the search
matches on. Then suggest the developer review the draft.

New docs are only picked up once the index is rebuilt. If the repo already has a
`.chroma_db/` folder, delete it after seeding.

## 7. Load the servers

The agent only loads MCP servers when it starts. Tell the developer to:

- **Claude Code**: restart it in the repo and approve the project MCP servers when
  asked. `/mcp` shows their status.
- **Cursor**: enable the servers in Settings → MCP.
- **VS Code + Copilot**: reload the window and allow the servers. VS Code stops changed
  servers after a re-configure without restarting them, so reload after every
  `configure.py` run. Tell the developer to start requests with
  `/cross-repo-feature-analysis`, because small auto-selected models may otherwise
  skip the MCP tools.

After the restart, the feature request can go through the normal workflow. The first
query in each repo is slow (10–60 s) while its search index is built.

## Later changes

- **Add a repo**: run `scan.py` on it, then re-run `configure.py` with only the new
  `--repo` and its indexing flags. Existing repos keep their settings, and every repo's
  config is regenerated to include the new peer.
- **Change what a repo indexes**: re-run `configure.py` with that repo's `--repo` and
  the new `--src`, `--knowledge` or `--ext` flags. Each flag type you pass replaces that
  setting in full. The cache is cleared automatically.
- **Docs or code changed a lot**: delete `.chroma_db/` in that repo. The index is
  rebuilt on the next query.
- **Each repo gets a `.chroma_db/` index folder.** Suggest adding it to that repo's
  `.gitignore`.

## Troubleshooting

- **`ModuleNotFoundError` when a server starts**: the configs point at the wrong
  Python. Re-run `configure.py` with the framework venv's Python.
- **A repo returns nothing relevant**: run `scan.py` on it and compare with its
  `mcp/config.yaml`. Usually the docs aren't in `.md`, they're in a README, or
  `src_path` points at the wrong folder.
- **Answers are full of irrelevant code**: the index includes tests, generated code
  or vendored code. Narrow `--src` and re-run `configure.py`.
