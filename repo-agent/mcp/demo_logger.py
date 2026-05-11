"""Live demo logger — plain-text log for terminal streaming + HTML log for browser tree view."""
import os
from datetime import datetime

_LOG_FILE  = os.environ.get("DEMO_LOG_FILE", "/tmp/mcp-orchestration.log")
_HTML_FILE = _LOG_FILE.rsplit(".", 1)[0] + ".html" if "." in _LOG_FILE else _LOG_FILE + ".html"

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


def log(server: str, event: str, message: str) -> None:
    ts   = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{server:<22}] [{event:<10}] {message}"

    # ── plain-text log (read by streaming thread in test_mcp.py) ─────────────
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
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
        with open(_HTML_FILE, "a", encoding="utf-8") as f:
            f.write(html)
    except OSError:
        pass
