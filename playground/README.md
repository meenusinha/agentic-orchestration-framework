# Playground

Two small sample repos, shaped like a real multi-process system, to try the framework
end to end. They live in **their own GitHub repositories**, separate from the framework.
Clone them like any other repos you want to connect.

| Repo | Process | What's inside |
|---|---|---|
| [`pw-server`](https://github.com/meenusinha/pw-server) | `authd` (+ library `libpwdpolicy.a`) | IDL `Auth::IPasswordService` · C++ servant · pure-C policy module · generated TAO stubs · Makefile + `compile_commands.json` · tests · vendored code · docs · an unknown `.xcfg` file · a **fake** secret |
| [`pw-client`](https://github.com/meenusinha/pw-client) | `ui-shell` | C++ login dialog and C legacy client calling the interface · Python, MATLAB and Perl tools · an in-house `.xidl` callback interface (`ISessionListener`) that the client implements and the server calls |

So links run both ways: the client calls `IPasswordService` on the server, and the
server calls `ISessionListener` back on the client.

Branches: `main` in both repos. `pw-client` also has `policy-hint`, where the login
dialog shows the password policy when it opens: a new caller of the IDL attribute
`minLength`.

## With the setup GUI

```bash
python3 setup_gui.py          # in the framework clone
```

1. **Step 2 (Model):** point to the folder holding your copy of the model and click **Copy model**.
2. **Step 3 (Repositories):** add each repo in one of two ways.
   - **Already cloned:** click **Choose folder…** and pick your clone.
   - **Clone from Git:** enter the URL (`https://github.com/meenusinha/pw-server.git`) and
     the branch (`main`), and choose the folder to clone into. Then click **Clone and add**.
     The **pw-server · pw-client** links fill in the URL and branch for you. If the folder
     already holds a clone of the same repository, the GUI updates it and switches to the
     branch instead of cloning again.
3. **Step 4 (Review):** say what the unknown types contain. `.xcfg` is server configuration
   (text); `.xidl` is an in-house IDL dialect.
4. **Steps 5 and 6:** click **Configure**, then **Yes, create the GraphRAG**.
5. Ask with the query field at the top, for example:
   - Search: `which processes are affected if PWD_MIN_LEN changes?`
   - Trace interface: `IPasswordService`
   - Impact of change: `pwd_policy_check`

Each result is saved as a new file in `<framework>/results/`.

## From the command line

Needs Python 3.11+ and git.

```bash
python3 playground/setup_playground.py --model-from /path/to/granite-embedding-small-english-r2
```

The script:
1. clones both repos into `--dir` (default `~/ao-playground`), or updates existing clones
2. installs the hash-locked requirements into `.venv`
3. copies the model
4. scans and configures both repos, answering the scan's questions the way a developer would
5. runs a smoke test through the real MCP servers

Options:
- `--dir PATH` and `--branch NAME` choose where to clone and which branch to check out.
- `--server-url` and `--client-url` point at forks or mirrors.
- `--no-venv` opts out of the virtual environment deliberately and installs into the
  Python you ran it with.

## Try it in an AI assistant (optional)

```bash
cd <your clone>/pw-client && claude      # approve the project MCP servers
```

- *What would it take to show the policy hint in the login dialog's strength meter?*
- *Which processes are affected if `PWD_MIN_LEN` changes?*
- *Trace the `ISessionListener` interface — who implements it and who calls it?*
- *Add a new IDL operation `isCompromised(pw)` — what has to change, where, in what order?*
- *Where are database credentials configured?* (watch for `[REDACTED]`)

In VS Code, allow the MCP servers and start requests with `/cross-repo-feature-analysis`.

## Things to edit and watch

- **Switch branches:** move `pw-client` to `policy-hint` (clone it again in the GUI with that
  branch, or run `git switch policy-hint`). Then trace `IPasswordService`: `LoginDialog::onOpen`
  now appears as a caller of `minLength`.
- **Change the interface:** change `PWD_MIN_LEN`, or add an operation to `pw-server/idl/auth.idl`.
  The graph updates within a minute; only changed code is re-embedded. Until the servant
  implements a new operation, `trace_interface` reports e.g. *"not implemented by any
  provider: isCompromised"*.

## Automated end-to-end test (Windows, macOS, Linux)

```bash
python playground/gui_selftest.py --model-from <folder holding the model>
```

Starts the setup GUI as a user would and runs every step through its API: install, model
copy, cloning both repos (including branch switching and the refusal cases), scan,
configure, GraphRAG, queries and restart. It also checks the security guards. GitHub
Actions runs it on Windows, macOS and Linux (`.github/workflows/gui.yml`).
