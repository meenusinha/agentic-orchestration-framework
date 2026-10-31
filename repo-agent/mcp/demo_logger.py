"""Live demo logger — plain-text log for terminal streaming + HTML log for browser tree view.

Privacy defaults (enterprise):
  - Log files live in a per-user folder (~/.agentic-orchestration/logs/) unless
    DEMO_LOG_FILE says otherwise, and are always created/kept at mode 0600 and
    opened without following symlinks (no /tmp symlink tricks).
  - Code/doc snippets and feature-request text are NOT logged unless
    DEMO_LOG_CONTENT=1 (e.g. for a demo). Otherwise only their length is logged.
"""
import os
from datetime import datetime

DEFAULT_LOG_FILE = "~/.agentic-orchestration/logs/mcp-orchestration.log"
_LOG_FILE  = os.path.expanduser(os.environ.get("DEMO_LOG_FILE") or DEFAULT_LOG_FILE)
_HTML_FILE = _LOG_FILE.rsplit(".", 1)[0] + ".html" if "." in os.path.basename(_LOG_FILE) else _LOG_FILE + ".html"
LOG_CONTENT = os.environ.get("DEMO_LOG_CONTENT", "").strip().lower() in ("1", "true", "yes")

# HTML colours (same palette, hex)
_HTML_COLOR = {
    "INDEX":     "#58a6ff",
    "RAG":       "#3fb950",
    "RESULT":    "#f0883e",
    "ARCH":      "#bc8cff",
    "ROUTING":   "#58a6ff",
    "TOOL_CALL": "#58a6ff",
    "INFO":      "#8b949e",
    "WARN":      "#d29922",
    "ERROR":     "#ff7b72",
}



def open_private(path: str, truncate: bool = False):
    """Open `path` for appending (or truncating) as a 0600 file, refusing symlinks."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    flags |= os.O_TRUNC if truncate else os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)  # tighten files created earlier with looser modes
    except (AttributeError, OSError):
        pass
    return os.fdopen(fd, "w", encoding="utf-8")


def content(text: str) -> str:
    """Repo content or user text for a log line: shown only when DEMO_LOG_CONTENT=1."""
    return repr(text) if LOG_CONTENT else f"<{len(text)} chars hidden>"


def log(server: str, event: str, message: str) -> None:
    ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{server:<22}] [{event:<10}] {message}"

    # ── plain-text log (read by streaming thread in test_mcp.py) ─────────────
    try:
        with open_private(_LOG_FILE) as f:
            f.write(line + "\n")
    except OSError:
        pass

    # ── HTML log (tree view in browser) ──────────────────────────────────────
    safe_msg = (message
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
    col  = _HTML_COLOR.get(event, "#e6edf3")
    html = (
        f'<div class="e" data-r="{server.strip()}" data-l="{event}">'
        f'<span class="ts">{ts}</span>'
        f'<span class="lv" style="color:{col}">[{event}]</span>'
        f'<span class="mg">{safe_msg}</span>'
        f'</div>\n'
    )
    try:
        with open_private(_HTML_FILE) as f:
            f.write(html)
    except OSError:
        pass
