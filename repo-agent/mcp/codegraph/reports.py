"""Query reports: every result is saved under a new, unique name — never overwritten."""
from __future__ import annotations

import re
import time
from pathlib import Path

RESULTS_DIR_NAME = "results"


def unique_report_path(results_dir: Path, mode: str, text: str) -> Path:
    """results/2026-10-31_142305_search_policy-hint-login.md (suffix -2, -3 … if taken)."""
    from .store import private_dir
    private_dir(results_dir)                     # 0700: reports contain code excerpts, like the index
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].rstrip("-") or "query"
    base = f"{time.strftime('%Y-%m-%d_%H%M%S')}_{mode}_{slug}"
    path, n = results_dir / f"{base}.md", 2
    while path.exists():
        path, n = results_dir / f"{base}-{n}.md", n + 1
    return path


def write_report(path: Path, text: str) -> None:
    """Write a report privately (0600, atomic), like the index files."""
    from .store import write_private_text
    write_private_text(path, text)
