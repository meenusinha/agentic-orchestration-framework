# MCP Process Model & Shared Resource Concurrency

How processes are spawned, how they communicate, and what protects shared resources
when multiple VS Code sessions run against the same repos on the same machine.

---

## Scenario: 3 repos + 1 orchestrator, each in its own VS Code window

---

## Part 1 — Process Model (VS Code as MCP client)

### What `setup.py` generates

Running `python orchestrator/setup.py` writes an **identical** `.vscode/mcp.json` into every repo directory and the orchestrator directory. Every window's `mcp.json` lists all 4 servers:

```json
{
  "servers": {
    "orchestrator_router": { "type": "stdio", "command": "python", "args": ["/abs/path/orchestrator/mcp/router_mcp_server.py"] },
    "repo-a":              { "type": "stdio", "command": "python", "args": ["/abs/path/repo-a/mcp/mcp_server.py"] },
    "repo-b":              { "type": "stdio", "command": "python", "args": ["/abs/path/repo-b/mcp/mcp_server.py"] },
    "repo-c":              { "type": "stdio", "command": "python", "args": ["/abs/path/repo-c/mcp/mcp_server.py"] }
  }
}
```

### Who starts which process

**VS Code is the MCP client.** On startup it reads `mcp.json` and spawns each listed server as a child process — one `python` subprocess per entry. Since every window has the same `mcp.json`, each window independently starts all 4 servers:

```
VS Code window (repo-a)
  ├── spawns → python router_mcp_server.py      [PID 1001]  permanent
  ├── spawns → python repo-a/mcp/mcp_server.py  [PID 1002]  permanent
  ├── spawns → python repo-b/mcp/mcp_server.py  [PID 1003]  permanent
  └── spawns → python repo-c/mcp/mcp_server.py  [PID 1004]  permanent

VS Code window (repo-b)
  ├── spawns → python router_mcp_server.py      [PID 1011]  permanent
  ├── spawns → python repo-a/mcp/mcp_server.py  [PID 1012]  permanent
  ├── spawns → python repo-b/mcp/mcp_server.py  [PID 1013]  permanent
  └── spawns → python repo-c/mcp/mcp_server.py  [PID 1014]  permanent

VS Code window (repo-c)       → same pattern, 4 more PIDs
VS Code window (orchestrator) → same pattern, 4 more PIDs
```

**Total permanent Python processes: 4 windows × 4 servers = 16**

Each server process lives as long as the VS Code window that owns it is open.

### How VS Code communicates with each server (stdio MCP)

Each spawned server talks to its parent VS Code window over a **private stdin/stdout pipe**. There is no network socket, no port, no shared memory between servers.

```
VS Code (MCP client)
  │
  │  stdin  ──────►  python mcp_server.py
  │                      reads JSON-RPC messages line by line
  │                      processes tool call
  │  stdout ◄──────      writes JSON-RPC response line by line
```

The wire protocol is three JSON-RPC messages per session:

| # | Direction | Message | Purpose |
|---|---|---|---|
| 1 | client → server | `initialize` | Handshake, exchange capabilities |
| 2 | client → server | `notifications/initialized` | No reply needed |
| 3 | client → server | `tools/call` | The actual tool invocation |

When Copilot calls `repo-a.query_repo(...)`, VS Code writes that JSON-RPC message to repo-a server's **stdin pipe** and reads the result from its **stdout pipe**. Each of the 4 servers in a window has its own dedicated pipe — they never share stdin/stdout.

### The orchestrator spawns additional ephemeral processes

When Copilot calls `orchestrator_router.get_relevant_repos(requesting_repo="repo-a", ...)`, the router does **not** talk to the already-running repo server processes. Instead, `router.py:_mcp_call()` uses `subprocess.run()` to spawn fresh, short-lived copies:

```
VS Code (repo-a window)
  └── stdin/stdout pipe ──► router_mcp_server.py  [PID 1001]  permanent
                                │
                                │  _mcp_call() via subprocess.run()
                                ├── spawns → python repo-b/mcp/mcp_server.py  [PID 2001]  ephemeral
                                └── spawns → python repo-c/mcp/mcp_server.py  [PID 2002]  ephemeral
                                    (each receives 3 JSON-RPC messages on stdin, returns result, exits)
```

These ephemeral processes exist only for the duration of one routing call (seconds). During a `get_relevant_repos` call the total count momentarily reaches **16 permanent + 2 ephemeral = 18 processes**.

### Do the 4 VS Code windows communicate with each other?

**No — not directly.** There is no IPC between VS Code windows. The apparent cross-repo coordination happens because the orchestrator router in one window directly spawns the peer repo servers as new child processes. The already-running instances in other windows are invisible to it.

The only passive coordination is via the **filesystem**: all processes share the same ChromaDB cache files and the same log file on disk.

### Process count summary

| What | Count | Lifetime |
|---|---|---|
| Permanent MCP servers per VS Code window | 4 | Lives as long as the window |
| VS Code windows | 4 | User-managed |
| **Total permanent Python processes** | **16** | Always running while windows open |
| Ephemeral router subprocesses per routing call | 2 (= peer repo count) | Seconds |

---

## Part 2 — Shared Resource Concurrency

With 16 processes all pointing at the same repo directories, two resources are shared: the ChromaDB index and the log files.

### ChromaDB

#### What opens it

Every `mcp_server.py` calls:

```python
self._client = chromadb.PersistentClient(path=str(chroma_persist_dir))
```

This opens the same `.chroma_db/chroma.sqlite3` file. With 4 VS Code windows, repo-a's ChromaDB file is held open by **4 permanent processes** (one per window) plus any ephemeral copies spawned by the router.

#### What ChromaDB / SQLite provides

ChromaDB's PersistentClient uses SQLite in **WAL (Write-Ahead Log)** mode. SQLite WAL gives:

- **Concurrent reads — safe.** Multiple readers can query simultaneously without blocking.
- **Writes — serialized.** SQLite acquires an exclusive file lock; a second writer blocks until the first finishes.

There is **no application-level lock** in `repo_rag.py`. The code relies entirely on SQLite's built-in file locking.

#### The real race condition: check-then-create

`repo_rag.py` uses this pattern to decide whether to build or load the index:

```python
if self._collection_exists(name):      # ← check
    self._docs_collection = self._client.get_collection(name=name)
    return
# ... build index ...
self._docs_collection = self._client.create_collection(name=name)   # ← create
```

The check and create are **not atomic**. If two processes both start cold at the same time (e.g. all 4 VS Code windows opened simultaneously before the index has ever been built), both can see `_collection_exists` return `False`, then both attempt `create_collection`. The second call will throw a ChromaDB uniqueness error. There is no retry, no lock file, no application-level guard around this path.

**Why it rarely matters in practice:**

- The first process to start wins and builds the index — this takes 10–60 seconds.
- By the time a second VS Code window opens and its server starts, the collection already exists on disk and every subsequent process takes the `get_collection` (read-only) path.
- The race only triggers if all windows are opened together on a cold (never-indexed) repo.

#### Normal operation (index already built)

All processes take the `get_collection` read path. SQLite WAL handles concurrent reads correctly. All 16 permanent processes and any ephemeral router subprocesses query safely and simultaneously.

#### Embedding model RAM cost

Each process loads its own copy of the `all-MiniLM-L6-v2` model (~90 MB) into RAM. There is no model sharing between processes:

```
16 processes × ~90 MB = ~1.4 GB RAM for embedding models alone
```

### Log file

`demo_logger.py` writes with:

```python
with open(_LOG_FILE, "a", encoding="utf-8") as f:
    f.write(line + "\n")
```

**No explicit lock.** This relies on the OS append-mode guarantee: on Linux and macOS, `write()` calls smaller than `PIPE_BUF` (~4 KB) to a file opened with `O_APPEND` are **atomic at the kernel level** — the kernel ensures each write lands contiguously without interleaving with another process's write. Individual log lines (all well under 4 KB) are safe in practice.

The HTML log file receives the same treatment.

**Theoretical gap:** if a message were longer than the kernel's atomic write threshold, lines from two concurrent processes could interleave mid-message. This does not happen with the current log format (all lines are short), but it is not guaranteed by the application.

---

## Summary

| Resource | Protection mechanism | Real gap |
|---|---|---|
| ChromaDB reads | SQLite WAL — correct concurrent reads | None in normal operation |
| ChromaDB index build (writes) | SQLite exclusive file lock — serializes writers | `check-then-create` is not atomic; race condition on simultaneous cold start |
| Log file (text + HTML) | OS `O_APPEND` kernel atomicity for short writes | No explicit lock; very long lines could interleave (does not occur in practice) |
| Embedding model | Each process loads its own copy | No gap — but costs ~90 MB RAM per process (1.4 GB total across 16 processes) |

**Practical recommendation:** if multiple developers will open all repos simultaneously for the first time, run `python mcp/mcp_server.py` manually in each repo once to pre-build the index before opening VS Code. After that, all 16 processes operate read-only against ChromaDB and concurrency is fully safe.

---

## Part 3 — Process Limits and What Happens When They Are Exceeded

### OS process limits (macOS)

Every OS enforces a hard ceiling on the number of processes that can exist simultaneously:

| Limit | Value (this machine) | Scope |
|---|---|---|
| `kern.maxproc` | 8,000 | System-wide, all users combined |
| `kern.maxprocperuid` | 5,333 | Per user — you hit this first |
| Processes currently running | ~690 | Across all users at time of check |

### The quadratic growth problem

Because every VS Code window spawns **all** servers (not just its own), process count grows as **(R+1)²** where R = number of repos:

| Repos | Windows | Servers per window | Permanent Python procs | VS Code procs (≈26 each) | Total OS processes |
|---|---|---|---|---|---|
| 3 | 4 | 4 | 16 | 104 | ~120 |
| 5 | 6 | 6 | 36 | 156 | ~192 |
| 10 | 11 | 11 | 121 | 286 | ~407 |
| 20 | 21 | 21 | 441 | 546 | ~987 |
| ~65 | 66 | 66 | ~4,356 | ~1,716 | ≈5,333 — per-user limit |

With browser, Slack, terminal, and other apps consuming processes, the real practical ceiling is closer to **30–40 repos** before things start failing.

### What happens when the limit is hit

There are two spawn sites in the framework, and they fail differently:

**VS Code spawning permanent MCP servers (startup)**

VS Code calls Node.js `child_process.spawn()`, which calls the kernel `fork()`. If `fork()` returns `EAGAIN` (process table full), VS Code shows an error in its MCP/Output panel — something like "MCP server failed to start". That server's tool becomes unavailable to Copilot. The window still works for regular editing.

**Orchestrator's ephemeral `subprocess.run()` calls (`router.py:32`)**

```python
try:
    proc = subprocess.run([python, server_script], ...)
except subprocess.TimeoutExpired:
    return "(MCP timeout)"
except Exception as e:
    return f"(MCP error: {e})"   # ← OSError: [Errno 11] EAGAIN lands here
```

The `except Exception` catches the `OSError` from `fork()` failing and returns `"(MCP error: [Errno 11] Resource temporarily unavailable)"`. The scorer in `_score_response()` sees `"(mcp"` in the string and returns `0.0`. That repo is silently excluded from routing — **the system degrades without any visible error to the user**.

### RAM hits the ceiling before process count does

Each `mcp_server.py` loads `all-MiniLM-L6-v2` (~90 MB) independently — no sharing across processes:

| Repos | Permanent Python procs | Embedding model RAM |
|---|---|---|
| 10 | 121 | ~10.9 GB |
| 20 | 441 | ~39.7 GB |

A 16 GB machine starts swap-thrashing well before hitting the process limit.

### Architectural fix: linear instead of quadratic

The root cause is that `setup.py` writes the same `mcp.json` (all servers) to every repo. Each window only needs **its own server + the orchestrator** — the orchestrator already reaches peer servers directly via `subprocess.run()` when routing.

Changing `setup.py` to write a repo-specific `mcp.json` per window reduces permanent processes from **(R+1)²** to **2(R+1)**:

```
Current:   repo-a window lists orchestrator + repo-a + repo-b + repo-c   (4 servers)
Fixed:     repo-a window lists orchestrator + repo-a only                 (2 servers)
```

This halves RAM consumption and makes scaling linear.

---

## Part 4 — How Child Process Output Is Received

### VS Code receiving output from permanent MCP servers

VS Code holds the stdout pipe **open for the lifetime of the process** and reads in a streaming, event-driven manner. As soon as `FastMCP` writes a JSON-RPC response line to `sys.stdout` and flushes, VS Code's pipe handle receives it. Our server side:

```python
# mcp_server.py:99
mcp.run(transport="stdio")   # FastMCP writes responses to sys.stdout and flushes
```

VS Code's MCP client layer parses each newline-delimited JSON object off the pipe and matches it to the pending request by `id`. Neither side polls — the OS pipe buffer handles flow control.

**Model: streaming, pipe stays open, one response per tool call.**

### Orchestrator receiving output from ephemeral subprocesses

`subprocess.run(..., capture_output=True)` at `router.py:32` **buffers all stdout in memory** and only returns after the child process exits. Then `_mcp_call()` scans the buffer line by line for the message with `id=2`:

```python
# router.py:41-52
for line in proc.stdout.splitlines():   # iterate over fully-buffered stdout
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
        if msg.get("id") == 2:          # find the tools/call response
            content = msg.get("result", {}).get("content", [])
            if content:
                return content[0].get("text", "(empty response)")
    except json.JSONDecodeError:
        continue
return "(no response)"
```

**Model: buffered, waits for process exit, then scans all stdout at once.**

### Comparison

| | VS Code ← permanent server | Orchestrator ← ephemeral server |
|---|---|---|
| Read model | Streaming — reads lines as they arrive | Buffered — reads everything after child exits |
| When data available | As soon as server writes each line | Only after child process terminates |
| Mechanism | OS pipe + Node.js event loop | `subprocess.run(capture_output=True)` → `proc.stdout` string |
| Finds response by | JSON-RPC `id` matching via FastMCP client internals | Manual scan of `proc.stdout.splitlines()` for `id == 2` |
| Implication | Low latency per tool call | Orchestrator blocks for full RAG query duration — hence 600 s default timeout on first run |
