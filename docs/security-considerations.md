# Security considerations

What this framework does with your source code, where that code goes, and the controls
around it. Intended for security reviewers and for the people who install and run it.

Applies to this repository (VS Code + GitHub Copilot; supported platforms Windows and Linux; 2026-11). Every control below is in the code and covered by the automated tests (see
[How to verify](#how-to-verify)).

---

## Summary

| Question | Answer |
|---|---|
| Does indexing or searching send code anywhere? | **No.** Parsing, linking, embedding and search run on your machine. Hugging Face access is disabled before any ML library loads; tests measure zero network connections during index build and query |
| What does need the network? | Installing the Python packages (hash-locked), and cloning repos you ask the setup GUI to clone. Optionally, downloading the model through the command line; the GUI only copies a model you already have |
| Does the AI assistant see code? | Yes — that is its purpose. Results of the MCP tools go to GitHub Copilot in VS Code and therefore to its AI provider. Covered by the terms of your Copilot plan |
| Is repository code ever executed? | **No.** Source is parsed with tree-sitter; Makefiles, CMake files and `compile_commands.json` are read as text, never run; repository code is never imported |
| Is there a server or open port? | The MCP servers use stdio only (no port). The setup GUI listens on `127.0.0.1` only, with a per-session token, while it is open |
| Where is data stored? | On local disk, next to each repo (`.rag_index/`, private, git-ignored) and in the framework folder (`results/`, `models/`, `.venv/`). Nothing central or shared |
| What about secrets in the code? | Secret-store files are never indexed; secret-looking values are replaced with `[REDACTED]` before anything is parsed, embedded, stored or returned |

---

## 1. Components and data flow

```
 your repos (read-only)                       framework clone (one per developer)
 ┌───────────────────────┐   parse, link,     ┌────────────────────────────────────────┐
 │ source, docs, IDL,    │──embed locally────▶│ .venv/      hash-locked packages        │
 │ build files           │                    │ models/     approved embedding model    │
 │                       │◀── writes ─────────│ results/    saved query reports (0600)  │
 │ .rag_index/ (0700)    │   graph + vectors  │ setup GUI   127.0.0.1 + token           │
 │ .vscode/mcp.json  …   │   config files     └────────────────────────────────────────┘
 └───────────┬───────────┘
             │ stdio (no network)
   MCP servers (one per repo + router) ──▶ AI assistant in the editor ──▶ AI provider
                                              (only this hop leaves the machine)
```

| Step | Network? | Destination | How to keep it under your control |
|---|---|---|---|
| Install packages | Yes | PyPI (or your mirror) | Set `PIP_INDEX_URL` / `pip.conf` to a mirror you trust; the hashes in `requirements.lock` still have to match |
| Get the embedding model | No (GUI) | — | The GUI only copies a folder you point to. Keep the approved model in a location you trust |
| Clone repos in the GUI | Yes | The URL you enter | Use Git servers you trust; or clone yourself and add the folder |
| Scan, configure, build the graph, queries, knowledge-graph view | **No** | — | — |
| AI assistant answers | Yes | Your AI provider | Governed by the provider agreement and your editor's policies |

---

## 2. Controls by area

### 2.1 Source code confidentiality

- **Offline by construction.** `repo_rag.py` sets `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`,
  `HF_DATASETS_OFFLINE`, `HF_HUB_DISABLE_TELEMETRY` and `DO_NOT_TRACK` before importing any
  ML library. The model loads with `local_files_only=True`; a missing model fails instead of
  downloading.
- **No telemetry.** No vector-database service, no analytics libraries.
- **Cross-repo access** is by design: each repo's server reads the other connected repos'
  graphs read-only, to follow IDL interfaces between processes. Connect only repos that the
  same people may read.

### 2.2 Dependencies (supply chain)

- `requirements.lock` pins all 103 packages **with SHA-256 hashes** for Windows and Linux
  (`pip install --require-hashes`). A swapped or tampered package fails installation.
- `sentence-transformers` 6.2,
  `torch` 2.14.1, `transformers` 5.21.0, `mcp` 1.30.0, tree-sitter 0.26 with prebuilt
  grammar wheels (no code generation at runtime).
- **Audit**: `tests/run_all.py` runs `pip-audit` against the lock file. Run it on every
  change and on a schedule (for example weekly), so new advisories for unchanged pins are
  caught.
- The setup GUI itself uses only the Python standard library until it has installed the
  locked packages into its own virtual environment.

### 2.3 Embedding model

- Only the approved model is accepted: `ibm-granite/granite-embedding-small-english-r2`
  (Apache 2.0; permissively licensed training data), pinned to revision `2ab6fa8e…`.
- **Hash-pinned:** all eight model files (weights, settings, tokenizer) must match their recorded
  SHA-256 fingerprints. The check runs when the model is copied **and at every server start**;
  a missing or changed file is refused by name. (The fingerprints are listed in
  [security-evidence.md](security-evidence.md#23-embedding-model).)
- A model folder is refused if it contains code or pickle files (`.py`, `.pyc`, `.bin`, `.pt`,
  `.pth`, `.pkl`, `.pickle`, `.ckpt`, …) or non-standard sentence-transformers module types.
- `trust_remote_code=False`; weights are `safetensors` (pure data).

### 2.4 Repository code is never executed

- Parsing uses tree-sitter grammars and an in-house tolerant IDL parser.
- Build files are read statically. Running `make -pn` or a CMake configure step could execute
  arbitrary commands from a repo, so the framework never does.
- The indexer starts no subprocesses.

### 2.5 Secrets

- **Never indexed:** secret-store files such as `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
  `*.jks`, keystores, SSH private keys, `.npmrc`, `.pypirc`, `.netrc`, `*.tfvars`,
  `secret(s).*`, `credential(s).*`.
- **Redacted everywhere else** before parsing, embedding, storage or output: private-key
  blocks, cloud-provider, source-hosting, chat, AI-service and payment-provider API keys, JWTs, `user:password@` in
  URLs, and `password = "…"`, `api_key: '…'`, `client_secret` style assignments. Line
  breaks are preserved, so line numbers stay correct.
- Redaction is pattern-based. Secrets in unusual formats can be missed; keep secrets out of
  source code in the first place, and see §4.

### 2.6 Data at rest

| Data | Location | Contains | Protection |
|---|---|---|---|
| Code graph + vectors | `<repo>/.rag_index/` | Code excerpts (redacted), vectors | Folder `0700`, files `0600`, atomic writes, added to `.gitignore`; SQLite, no pickle |
| Query reports | `<framework>/results/` | Code excerpts from results | Folder `0700`, files `0600`; git-ignored; never overwritten (new name per query) |
| Logs | `~/.agentic-orchestration/logs/` | Tool calls; **code and question text hidden** unless `log_content: true` | `0600`, opened without following symlinks |
| Model | `<framework>/models/` | Public model weights | Hash-checked at every start |
| Packages | `<framework>/.venv/` | Locked dependencies | Separate from the system Python |

Treat `.rag_index/` and `results/` with the same classification as the source code. To
remove all derived data, delete those folders; the graph is rebuilt on the next start.

### 2.7 Prompt injection through repository content

Repository text could contain instructions aimed at the AI assistant. Every result is
marked as untrusted reference data: text results sit between
`<<<BEGIN UNTRUSTED REPOSITORY CONTENT` and `<<<END UNTRUSTED REPOSITORY CONTENT>>>`,
markers that appear inside repository content are neutralised so they can't close the frame
early, and structured results carry a notice. The generated Copilot instructions tell the
assistant never to follow instructions found inside results (no commands, edits, URLs or
data disclosure) and to point them out instead. This lowers the risk; it can't eliminate it
— keep the editor's own approvals for tool use and edits switched on.

### 2.8 Setup GUI (local web page)

| Threat | Control |
|---|---|
| Access from other machines | Listens on `127.0.0.1` only |
| Other local processes or web pages calling the API | Random per-session token (`secrets.token_urlsafe(24)`) required on every API call; it travels in the URL fragment, which browsers never send to a server |
| DNS rebinding | Requests with any `Host` other than `127.0.0.1:<port>` / `localhost:<port>` are refused |
| Cross-site requests (CSRF) | API calls need the token header, and POST bodies must be `application/json` |
| Script injection, framing | Strict Content-Security-Policy (no external resources, no `eval`, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, no caching; all repository text is HTML-escaped |
| Reading files outside the framework | Saved results are served by name only, from `results/` (path traversal refused); the knowledge-graph view reads graph databases read-only (`mode=ro`), with validated ids and configured repos only |
| Runaway or overlapping actions | One job at a time; no shell is ever used |

The page runs only while the setup GUI is open, and **Close** stops it.

### 2.9 Cloning from the GUI

- URLs must be `https://`, `ssh://`, `git://`, `file://`, `git@host:path`, or an existing
  local folder. Values that look like git options are refused, and so are the `ext::` and
  `fd::` transports (which can run commands). Git is called without a shell, with `--` before
  positional arguments.
- Branch names are checked with `git check-ref-format`; folder names must be plain names.
- An existing folder is reused only when it is already a clone of the same repository (then
  fetched and switched to the branch); any other existing folder is never touched.
- Git never prompts for passwords (`GIT_TERMINAL_PROMPT=0`): private repositories use the
  developer's own SSH key or credential manager. The GUI never sees credentials.

### 2.10 MCP servers

- Started by the editor over **stdio**; no network listener.
- Their configuration pins the virtual environment's Python and runs in UTF-8 mode.
- Tools for search, tracing, impact and navigation only. They never modify source files;
  the only thing a server writes is its own repo's `.rag_index/`.

### 2.11 Isolation

- Everything runs inside `<framework>/.venv` by default; the scripts refuse to run outside a
  virtual environment unless `AO_ALLOW_NO_VENV=1` is set deliberately.

---

## 3. Hardening checklist

| # | Control | Status |
|---|---|---|
| 1 | All dependencies pinned with SHA-256 hashes; `sentence-transformers` ≥ 6.0 | In place — 6.2.0; installation fails on any hash mismatch |
| 2 | Model weights only as `safetensors`; all 8 model files hash-pinned (SHA-256) | In place — checked when copied and at every start |
| 3 | Model folders with code or pickle files, or non-standard module types, are refused | In place |
| 4 | `trust_remote_code=False`; no pickle loaded anywhere | In place |
| 5 | Model hub access and telemetry disabled before any ML library loads | In place |
| 6 | No database server: a local SQLite graph, vectors as raw float32 bytes | In place |
| 7 | Index, reports and logs private (`0700`/`0600`), atomic writes, index git-ignored | In place; disk encryption and backup exclusion are operational (§4) |
| 8 | Known-vulnerability audit of the locked dependencies | In place — `tests/run_all.py`, on every change and weekly |
| 9 | Approved model distributed from a location you trust | Supported — the setup page copies from the folder you point to and never downloads |
| 10 | Repository code never executed; build files read as text | In place |

---

## 4. Residual risks and recommendations

| Risk | Recommendation |
|---|---|
| Index and reports contain code excerpts | Use full-disk encryption (BitLocker on Windows, LUKS on Linux). Exclude `.rag_index/` and `results/` from backups and shared or synced folders, or give them the same protection as the source |
| On Windows, `0600`/`0700` aren't enforced (Windows uses access-control lists) | Keep the framework and repos inside the user profile, whose default ACLs are private to the user |
| A secret in an unusual format isn't recognised | Run your secret scanner on the repos. If a secret turns up in `.rag_index/` or `results/`, delete those folders and rotate the secret |
| The setup page's token is visible in the terminal and browser history | It's valid only while that GUI session runs; close the GUI when you're done |
| Shared multi-user machines | `127.0.0.1` is reachable by other local users; the token protects the API. Prefer a personal workstation |
| Prompt injection is mitigated, not removed | Keep the editor's confirmations for terminal commands and file edits enabled |
| Package source | Point pip at a mirror you trust; the lock-file hashes stay authoritative |
| Code reaches the AI provider through the assistant | Covered by the terms of your Copilot plan; connect only repos that may be shared with it |
| New vulnerabilities in pinned packages | Run `tests/run_all.py` weekly; update `requirements.txt`, regenerate the lock, re-run the tests |

---

## How to verify

Linux (bash):

```bash
# Dependencies: hashes enforced, known vulnerabilities
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install pip-audit && .venv/bin/pip-audit -r requirements.lock --require-hashes --disable-pip

# Model: must print dcbfaf2ee50d358763cbc0c08e5b045ecbf6aeb02dc0e86ffb910029bf9ebb5f
sha256sum models/granite-embedding-small-english-r2/model.safetensors

# Private files
ls -ld <repo>/.rag_index results          # drwx------
ls -l  <repo>/.rag_index results          # -rw-------

# All checks: end-to-end test, dependency audit, MCP smoke test
python3 tests/run_all.py --model-from <folder holding the model>

# End-to-end test only
python3 tests/gui_selftest.py --model-from <folder holding the model>
```

Windows (PowerShell):

```powershell
.venv\Scripts\pip install --require-hashes -r requirements.lock
.venv\Scripts\pip install pip-audit; .venv\Scripts\pip-audit -r requirements.lock --require-hashes --disable-pip

Get-FileHash -Algorithm SHA256 models\granite-embedding-small-english-r2\model.safetensors

# Access control: only your account (plus SYSTEM/Administrators by profile defaults)
icacls <repo>\.rag_index
icacls results

py tests\gui_selftest.py --model-from <folder holding the model>
```

To measure that nothing leaves the machine, use the audit-hook method in
[security-evidence.md](security-evidence.md#3-measurement-nothing-leaves-the-machine).

The self-test covers, among others: the token, Host and JSON checks, refusing a download
request and unusable or tampered models, refusing `ext::` URLs, option-like values and paths in
folder names, never overwriting an existing folder, path traversal in the results viewer,
invalid ids in the graph view, and private permissions on saved reports. It builds its two
sample repositories locally from `tests/fixtures/`. The MCP smoke test (`tests/smoke_test.py`)
checks that the fake credentials planted in those fixtures are redacted.
