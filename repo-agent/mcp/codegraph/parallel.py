"""Parallel work over the files of a repository (standard library only).

- io_map: threads, for listing, sizing, reading and hashing files. Python threads run in
  parallel while waiting on the disk and while hashing (hashlib releases the interpreter lock),
  so this helps on network drives and cold disk caches and never hurts.
- parse_map: worker processes, for parsing (CPU-bound; tree-sitter keeps the interpreter lock,
  so threads would not help). Used only by the setup page's and the command line's build, never
  inside the VS Code servers: on Windows a new process starts by importing the main program again,
  and a server's main program builds its graph when it is imported. Processes are started with
  "spawn" on every platform (forking a process that already runs threads is unsafe), and only
  for repositories large enough for it to pay off.

Results are always in the same order as the input, so a parallel build is identical to a
sequential one.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

IO_THREADS = 8                       # waiting on the disk: more threads than cores is fine
MIN_PARALLEL_FILES = int(os.environ.get("AO_PARALLEL_MIN_FILES", "200"))   # lowered by the tests only


def cpu_workers(setting: int) -> int:
    """The `workers` setting → number of worker processes (0 = one per CPU core)."""
    return max(1, os.cpu_count() or 1) if not setting else max(1, int(setting))


def io_workers(setting: int) -> int:
    """The `workers` setting → number of I/O threads (0 = automatic, 1 = no parallelism)."""
    return IO_THREADS if not setting else max(1, int(setting))


def io_map(fn, items: list, workers: int) -> list:
    if workers <= 1 or len(items) < 64:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))


def parse_map(fn, items: list, workers: int, on_done=lambda i, n: None, log=lambda msg: None):
    """Yield fn(item) for every item, in order, using worker processes when it pays off. If the
    processes can't run (e.g. no importable main program, or a process is killed), the remaining
    items are done here, one by one — the result is the same."""
    n = len(items)
    done = 0
    if workers > 1 and n >= MIN_PARALLEL_FILES:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool
        ctx = multiprocessing.get_context("spawn")
        workers = min(workers, max(1, n // 25))       # no more processes than useful
        try:
            with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                for r in ex.map(fn, items, chunksize=max(1, n // (workers * 8))):
                    on_done(done, n)
                    done += 1
                    yield r
            return
        except (BrokenProcessPool, OSError, RuntimeError) as e:
            log(f"  parallel parsing unavailable ({type(e).__name__}); continuing one file at a time")
    for i in range(done, n):
        on_done(i, n)
        yield fn(items[i])
