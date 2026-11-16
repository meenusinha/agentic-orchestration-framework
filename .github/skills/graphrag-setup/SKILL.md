---
name: graphrag-setup
description: Set up the Agentic Orchestration framework and create its GraphRAG (cross-repository code knowledge graph) from Copilot Chat, without the setup page — install it, connect repositories, choose which folders to index or exclude, answer unknown file types and folder roles, configure VS Code, estimate how long creating the GraphRAG takes, create it all at once or in batches with a progress bar, add the remaining batches later, or start a repository from scratch. Use when the developer asks to set up, configure, build, rebuild, extend or check the GraphRAG or the cross-repo MCP servers, to include or exclude folders, or to see how long indexing takes.
license: Apache-2.0
---

# GraphRAG setup

This skill does from chat what the framework's setup page (`setup_gui.py`) does in a
browser. You run the commands in the terminal; the developer makes every decision.
Nothing is downloaded at runtime and no code leaves the computer — say so if asked.

**Ground rules**

- Before anything that installs packages or writes into a repository, say what it will do
  and get the developer's go-ahead. Several steps can be confirmed at once.
- Never guess an answer the developer has to give (folders to exclude, what an unknown file
  type contains, folder roles, what to overwrite, which components to build now). Ask, and
  show the scan's suggestion as a suggestion.
- Show results as short tables, not raw output.
- Supported platforms: **Windows and Linux**. Below, `PY` is the framework's virtual-environment
  Python: `<framework>/.venv/bin/python` on Linux, `<framework>\.venv\Scripts\python.exe` on
  Windows. `GR` is `<framework>/.github/skills/graphrag-setup/scripts/graphrag.py` and
  `SCRIPTS` is `<framework>/skills/cross-repo-feature-analysis/scripts`.

## 0. Find the framework folder

1. If the workspace contains `setup_gui.py` and `orchestrator/`, the workspace is the framework.
2. Otherwise look in `.vscode/mcp.json` for the server `orchestrator_router`: its `args` path is
   `<framework>/orchestrator/mcp/router_mcp_server.py`.
3. Otherwise ask the developer where the framework was cloned.

Then run `PY GR status` to see what's already set up. If repositories are connected and the
developer only wants to add batches, change folders or rebuild, skip to the step needed.

## 1. Install (once)

If `<framework>/.venv` doesn't exist or `PY GR status` fails with a missing module:

```bash
python3 -m venv <framework>/.venv                     # Windows: py -m venv <framework>\.venv
PY -m pip install --require-hashes -r <framework>/requirements.lock
```

Packages are hash-locked; nothing goes into the system Python. If the venv already works,
ask whether to **keep** it or **reinstall** every package
(`PY -m pip install --force-reinstall --no-deps --require-hashes -r <framework>/requirements.lock`).

The embedding model is included in `<framework>/models/` and hash-checked automatically. If a
command reports it missing or changed, restore it with `git checkout -- models/` in the
framework folder, or ask for another copy of the approved model (`--model-from PATH` in step 4).

## 2. Repositories

Ask which repositories to connect — at least two (for example the one with the IDL
interfaces, the server and the clients). Suggest the current repository and sibling folders
that contain a `.git` folder. For a repository that isn't cloned yet, ask for its URL, branch
and the folder to clone into, and clone it with `git clone --branch <branch> <url> <folder>`
(never into a folder that already exists).

## 3. Review: folders, file types, roles

```bash
PY SCRIPTS/scan.py --framework <framework> --json /path/to/repo-a /path/to/repo-b
```

For each repository, show: languages, size (`files`, `bytes`), processes and libraries,
IDL interfaces, and the **folders** with their file counts and proposed roles. Then ask:

1. **Which folders to index.** By default every folder is indexed. Ask whether any should be
   **excluded** (never indexed — e.g. vendored copies, generated output, data dumps, archives),
   and whether deeper folders or file patterns should be excluded (`src/legacy`,
   `*.generated.c`). Offer "include all" and "exclude all" as quick answers. Paths must be
   inside the repository. Record each as `--exclude 'name=PATH'`.
2. **Unknown file types** (`unknown_types`), except those whose `folders` are all excluded:
   ask what each contains and how to read it, and record `--file-type 'name=.EXT:KIND[:PARSE_AS]'`
   (`KIND`: implementation, interface, config, docs, data, … or `ignore`; `PARSE_AS`: c, cpp,
   python, idl, perl, matlab, java, javascript, typescript, go, rust, csharp, markdown, text).
   Never guess the language of source code.
3. **Folder roles**: show the proposed role per folder and record corrections as
   `--role 'name=FOLDER/**:ROLE'` (`external` code: add `:signatures`). Roles: interface,
   implementation, internal, external, library, test, build, generated, docs, tooling,
   config, data, example. Tests are kept as evidence only.

For a repository set up before, the scan shows the saved answers (`saved_types`,
`saved_structure`, `saved_exclude`) — present them as the current choice.

## 4. Configure

For a repository **set up before** (it has `mcp/config.yaml`), ask what to overwrite, one by one
or "overwrite all" / "keep all":

| Item | Overwrite | Keep |
|---|---|---|
| Folders to index, roles and file types | pass `--replace-roles name`, the `--exclude`, `--role` and `--file-type` answers | pass nothing for them (only answers for newly found file types) |
| GraphRAG and query settings | `--reset-settings name` (back to defaults) | nothing |
| Copilot instructions | nothing (regenerated) | `--keep name=instructions` |
| Agent skills | nothing (reinstalled) | `--keep name=skill` |

A repository the developer no longer wants connected: `--drop name` (its files stay).

```bash
PY SCRIPTS/configure.py --framework <framework> \
    --repo server=/abs/path/to/server --exclude 'server=third_party' \
    --repo client=/abs/path/to/client --file-type 'client=.xidl:interface:idl'
```

It refuses to start while an unknown file type in an included folder is unanswered, and
writes nothing until every argument is valid.

## 5. Estimate

```bash
PY GR estimate
```

It lists every component (top-level folders; a folder holding most of a large repository is
split into its sub-folders) with files, size, status (`in GraphRAG`, `new`, `left for later`)
and estimated time, measured on this computer. Show the developer the totals and, for long
builds, the components with the largest times. Tell them which speed it used (`Speed from …`):
after the first larger build, estimates use that build's real speed and are more accurate.

## 6. Choose what to create now

If the whole build is short, ask "create everything now?". If it's long (the estimate then
suggests a first batch), offer:

- **everything now** → `PY GR build --all`
- **the suggested first batch** (about 15 minutes) → `PY GR build --suggested`
- **the developer's own choice** → `PY GR build --only repo:component --only repo:component …`

Components not chosen are **left for later**: not indexed until a later batch. Copilot and
queries work with what has been created so far. For a repository that already has a GraphRAG,
ask whether to **update** it (default: unchanged code is reused) or **start it from scratch**
(`--fresh name`: its stored graph and cached vectors are removed and everything is embedded
again).

## 7. Build, with progress

Run the chosen `build` command. It prints a progress line every few seconds:

```
[##########--------------------]  34% · about 12 min left · Building server: embedding 4,120 / 11,800 units
```

For a build estimated at more than a few minutes, run it as a background terminal command and
check its output now and then; tell the developer the latest progress line and time left.
Don't start a second build while one is running.

When it ends, show the summary: files, units and links per repository, and which processes
serve and call each IDL interface. If components are **still left for later**, ask:

- create **all remaining** now → `PY GR build --all`
- choose the **next batch** → run `PY GR estimate` again (states have changed), then `--add repo:component …`
- **stop here** — they can be added any time with this skill or on the setup page

## 8. Finish

Tell the developer to reload VS Code (VS Code servers that are already running also pick up
new batches and folder changes at their next refresh), allow the MCP servers if asked, and
use Copilot Chat in Agent mode with `/cross-repo-feature-analysis`.

## Later changes

| Developer wants | Do |
|---|---|
| See the state | `PY GR status` |
| Exclude or include a folder | `PY GR folders name --exclude PATH` / `--include PATH` (or `--exclude-all` / `--include-all`), then `PY GR build` |
| Add components left for later | `PY GR build --add repo:component …` or `--all` |
| Leave a component for later | `PY GR build --later repo:component` (its vectors are dropped; re-adding embeds it again) |
| Rebuild one repository completely | `PY GR build --fresh name` |
| Connect or disconnect a repository | steps 2–4 for it (`--drop name` to disconnect), then steps 5–7 |

## Troubleshooting

- **`must run with the framework's virtual-environment Python`**: use `PY`, not `python`.
- **`no connected repository`**: connect it with step 4 first; `PY GR status` lists the names.
- **`has no component`**: run `PY GR estimate` to list the component names.
- **A build stopped with an error**: show the last lines; fix the cause (for example a model
  file reported as changed — restore `models/`) and run the same `build` command again —
  everything already embedded is reused.
