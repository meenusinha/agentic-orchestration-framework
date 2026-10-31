# Playground

Two small sample repos, shaped like a real multi-process system, wired up from scratch so
you can try the framework end to end in a few minutes.

| Repo | Process | What's inside |
|---|---|---|
| `pw-server` | `authd` (+ library `libpwdpolicy.a`) | IDL `Auth::IPasswordService` · C++ servant · pure-C policy module · generated TAO stubs · Makefile + `compile_commands.json` · tests · vendored code · docs · an unknown `.xcfg` file · a **fake** secret |
| `pw-client` | `ui-shell` | C++ login dialog and C legacy client calling the interface · Python, MATLAB and Perl tools · an in-house `.xidl` callback interface (`ISessionListener`) that the client implements and the server calls |

So links run both ways: the client calls `IPasswordService` on the server, and the
server calls `ISessionListener` back on the client.

## Easiest: the setup GUI

```bash
python3 setup_gui.py
```

In step 3 (Repositories) click **Create playground repos**, then click through the rest.

## Run it from scratch (command line)

Needs Python 3.11+ and git.

```bash
git clone -b add-agent-skill https://github.com/meenusinha/agentic-orchestration-framework.git
cd agentic-orchestration-framework
python3 playground/setup_playground.py --download-model
```

- `--download-model` fetches the approved embedding model (~95 MB, pinned revision).
  Already have it? Use `--model-from /path/to/granite-embedding-small-english-r2` instead.
- `--dir PATH` puts the two repos somewhere other than `~/ao-playground`.
- `--reset` starts over (it only ever deletes a folder it created).
- Everything runs inside the framework's virtual environment (`.venv`): the script
  creates it with the standard library only, then re-launches itself inside it, so
  nothing is installed into your system Python. `--no-venv` opts out deliberately and
  installs into the Python you ran it with.

The script creates the two repos, installs the hash-locked requirements into `.venv`,
scans and configures both repos (answering the scan's questions the way a developer
would), then runs a smoke test through the real MCP servers:

```
   PASS  IPasswordService: defined in pw-server, served by process authd
   PASS  IPasswordService: C++, C and Python clients in pw-client
   PASS  ISessionListener (in-house .xidl): implemented by pw-client
   PASS  ISessionListener: called back by pw-server
   PASS  impact of pwd_policy_check reaches pw-client through the IDL
   ...
```

## Try it

```bash
cd ~/ao-playground/pw-client && claude      # approve the project MCP servers
```

- *What would it take to show the policy hint in the login dialog's strength meter?*
- *Which processes are affected if `PWD_MIN_LEN` changes?*
- *Trace the `ISessionListener` interface — who implements it and who calls it?*
- *Add a new IDL operation `isCompromised(pw)` — what has to change, where, in what order?*
- *Where are database credentials configured?* (watch for `[REDACTED]`)

In VS Code (`code ~/ao-playground/pw-client`), allow the MCP servers and start requests
with `/cross-repo-feature-analysis`. Without an editor:
`.venv/bin/python test_mcp.py "your question"`.

## Try the guided setup instead

```bash
python3 playground/setup_playground.py --guided --reset
cd ~/ao-playground/pw-client && claude
```

Then ask: *"Set up cross-repo analysis between this repo and ../pw-server. The framework
is at <this clone>."* The agent scans both repos, asks you what `.xcfg` and `.xidl` files
contain, asks about the embedding model, and configures everything.

## Things to edit and watch

- Change `PWD_MIN_LEN` or add an operation to `pw-server/idl/auth.idl` — within a minute
  the servers rebuild (only changed code is re-embedded) and `trace_interface` reports
  e.g. *"not implemented by any provider: isCompromised"* until the servant implements it.
- `tail -f ~/.agentic-orchestration/logs/mcp-orchestration.log` shows every tool call
  (code text is hidden unless `log_content: true` in `orchestrator/mcp/config.yaml`).
