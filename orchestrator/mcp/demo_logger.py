"""Live demo logger — writes to DEMO_LOG_FILE for tail -f viewing during demos."""
import os
import sys
from datetime import datetime

_LOG_FILE = os.environ.get("DEMO_LOG_FILE", "/tmp/mcp-orchestration.log")

_COLORS = {
    "TOOL_CALL": "\033[1;36m",
    "ROUTING":   "\033[1;33m",
    "RAG":       "\033[1;35m",
    "RESULT":    "\033[1;32m",
    "INDEX":     "\033[0;34m",
    "ERROR":     "\033[1;31m",
    "INFO":      "\033[0;37m",
    "WARN":      "\033[1;33m",
}
_RESET = "\033[0m"


def log(server: str, event: str, message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{server:<22}] [{event:<10}] {message}"
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    color = _COLORS.get(event, "")
    sys.stderr.write(f"{color}{line}{_RESET}\n")
    sys.stderr.flush()
