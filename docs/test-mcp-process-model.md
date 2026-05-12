# `test_mcp.py` — Process Model & Mechanics

How processes are spawned, how output is received, and how this compares
to the VS Code + MCP server model. No VS Code involved.

---

## The fundamental difference

In the VS Code case, **VS Code is the MCP client** — it reads `mcp.json`,
spawns server processes, and keeps them alive. In the `test_mcp.py` case,
**`test_mcp.py` itself is the MCP client**. No `mcp.json` is read. No
permanent server processes exist.

---

## How many processes, and who spawns them

There is exactly **one permanent process**: `python test_mcp.py`. Everything
else is ephemeral — spawned and destroyed within that single process via
`subprocess.run()`, the same `_mcp_call()` function used by the orchestrator
router.

The script runs in three sequential phases:

```
python test_mcp.py "feature request"         ← 1 permanent process (you start this)
  │
  ├─ Phase 0: Pre-flight  (test_mcp.py:121)
  │    subprocess.run(repo-a/mcp/mcp_server.py)   [born → sends initialize → exits]
  │    subprocess.run(repo-b/mcp/mcp_server.py)   [born → sends initialize → exits]
  │    subprocess.run(repo-c/mcp/mcp_server.py)   [born → sends initialize → exits]
  │
  ├─ Phase 1: Routing  (test_mcp.py:177 → router.py:_mcp_call())
  │    subprocess.run(repo-b/mcp/mcp_server.py)   [born → full RAG query → exits]
  │    subprocess.run(repo-c/mcp/mcp_server.py)   [born → full RAG query → exits]
  │    (repo-a excluded — it is the requesting repo)
  │
  └─ Phase 2: Query all repos  (test_mcp.py:192 → router.py:_mcp_call())
       subprocess.run(repo-a/mcp/mcp_server.py)   [born → full RAG query → exits]
       subprocess.run(repo-b/mcp/mcp_server.py)   [born → full RAG query → exits]
       subprocess.run(repo-c/mcp/mcp_server.py)   [born → full RAG query → exits]
```

`subprocess.run()` is **blocking** — each child must exit before the next
one is spawned. So at any instant there are at most **2 OS processes**:
`test_mcp.py` + 1 child.

Total subprocess invocations for R repos: `R (preflight) + (R−1) (routing) + R (query all) = 3R − 1`  
For 3 repos: **8 sequential subprocess calls**.

---

## There is also one thread (not a process)

`test_mcp.py:87` starts a daemon thread:

```python
threading.Thread(target=_stream_logs, daemon=True).start()
```

This tails the plain-text log file and colour-codes lines to your terminal
while subprocesses run. It is a thread inside the main process — not an OS
process. It dies automatically when the main process exits. `_stop_streaming.set()`
at line 252 signals it to stop before the script finishes.

---

## How output is received

Every call goes through `_mcp_call()` in `router.py` using
`subprocess.run(capture_output=True)`. The entire stdout of the child is
**buffered in memory** and returned only after the child exits. Then
`_mcp_call()` scans `proc.stdout.splitlines()` looking for the JSON-RPC
message with `id == 2`:

```python
# router.py:41-52
for line in proc.stdout.splitlines():   # fully-buffered stdout
    ...
    if msg.get("id") == 2:              # find the tools/call response
        return content[0].get("text", ...)
```

There is no streaming, no open pipe, no event loop. The main process simply
blocks for the full duration of each child's execution (index load + model
load + RAG query), then reads the result in one shot.

---

## Process count vs VS Code

| | VS Code (3 repos) | `test_mcp.py` (3 repos) |
|---|---|---|
| MCP client | VS Code (Node.js / Electron) | `test_mcp.py` (Python) |
| Permanent Python processes | 16 | 1 |
| VS Code / Electron processes | ~32 | 0 |
| **Peak OS processes at any instant** | **~50** | **2** |
| Server process lifetime | Permanent (lives with window) | Ephemeral (one call, then exit) |
| Spawning model | Parallel (each window spawns all servers at once) | Sequential (one at a time, blocking) |
| Output reception | Streaming pipe, event-driven | Buffered, after child exits |
| Embedding model RAM | 16 × ~90 MB ≈ 1.4 GB | ~90 MB peak (one child at a time) |
| Model load cost | Paid once per server at startup | Paid on **every** subprocess call |
| OS process limit risk | Hits ~30–40 repos on a 16 GB machine | Immune — always 2 processes regardless of repo count |

---

## The RAM vs speed trade-off

VS Code keeps all server processes alive permanently. The embedding model is
loaded once at startup and reused across every Copilot query — fast at query
time, expensive at startup.

`test_mcp.py` is RAM-efficient (one model copy at a time, ~90 MB peak) but
each subprocess call pays the full model load penalty — typically 5–15
seconds — even when the ChromaDB index is already cached. For 3 repos with
8 calls that is 40–120 seconds of model loading alone across the full run.

---

## Why the embedding model cannot be cached like ChromaDB

ChromaDB IS cached on disk (`.chroma_db/chroma.sqlite3`). The check in
`repo_rag.py` detects the existing collection and skips re-indexing — the
next process to open the same path reads pre-computed vectors from SQLite in
milliseconds.

The embedding model weights are also already on disk (`models/all-MiniLM-L6-v2/`).
They do not get re-downloaded. **But "loading" the model is not the same as
reading stored data.** What happens when a new process calls
`SentenceTransformer(embedding_model)` is:

1. PyTorch reads the weight files from disk into RAM
2. Deserializes them from the on-disk format (safetensors / pickle)
3. Allocates and populates tensor memory
4. Instantiates the model architecture (layers, attention heads, etc.)
5. Runs warm-up operations

This is not reading stored results — it is **reconstructing a live
computational object**. There is no equivalent of "just open the SQLite
file".

ChromaDB stores **data** (vectors = plain floats in a database). The
embedding model is a **computation engine** (a neural network that must be
activated). Databases are designed for fast cold-start reads from disk.
Neural network runtimes are not — their on-disk format is optimised for
portability and correctness, not for instantiation speed.

The closest real solutions would be:

| Approach | What it means | Trade-off |
|---|---|---|
| **Model server** (Triton, TorchServe, or a simple persistent Python process) | Keep the model loaded in one long-lived process; subprocesses call it over a socket or pipe | Extra infrastructure; adds a network/IPC hop per embedding call |
| **Shared memory tensors** (`torch.multiprocessing.shared_memory`) | Map model weights into a shared memory region all child processes read | Only works within a single Python process tree; complex to set up |
| **ONNX + optimised runtime** | Export model to ONNX; load with ONNX Runtime (faster deserialisation) | Reduces load time by ~40–60%; does not eliminate it |

None of these is a drop-in equivalent of ChromaDB's on-disk persistence. The
fundamental reason is that ChromaDB solved a solved problem (database
caching), while embedding model instantiation speed is still an active area
in the ML infrastructure ecosystem.
