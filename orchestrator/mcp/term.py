"""ANSI terminal colour helpers for test_mcp.py output."""
import re

_R   = "\033[0m"
_B   = "\033[1m"
CYN  = "\033[1;96m"   # bold cyan   — section headers
BLU  = "\033[1;94m"   # bold blue   — repo names
GRN  = "\033[1;92m"   # bold green  — success
YEL  = "\033[1;93m"   # bold yellow — prompts / warnings
RED  = "\033[1;91m"   # bold red    — errors
GRY  = "\033[0;90m"   # grey        — subdued

_LOG_ANSI = {
    "INDEX":     "\033[0;94m",
    "RAG":       "\033[0;92m",
    "RESULT":    "\033[0;33m",
    "ARCH":      "\033[0;95m",
    "ROUTING":   "\033[1;96m",
    "TOOL_CALL": "\033[1;96m",
    "INFO":      "\033[0;90m",
    "WARN":      "\033[1;93m",
    "ERROR":     "\033[1;91m",
}


def c(text: str, color: str) -> str:
    """Wrap text in an ANSI colour code."""
    return f"{color}{text}{_R}"


def colorize_log(line: str) -> str:
    """Apply ANSI colour to a raw log line based on its [LEVEL] tag."""
    m = re.match(r"(\[[\d:.]+\]) (\[.+?\]) (\[(\w+)\s*\])(.*)", line)
    if m:
        ts, repo, lvl_tag, lvl, rest = m.groups()
        col = _LOG_ANSI.get(lvl, "")
        return f"{GRY}{ts} {repo} {col}{lvl_tag}{_R}{rest}"
    return line
