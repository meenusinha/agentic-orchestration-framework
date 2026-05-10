#!/usr/bin/env python3
"""Generic repo agent MCP server — reads all configuration from mcp/config.yaml.

This file never needs to be edited. Just update config.yaml and run setup.py.

Launch: python mcp/mcp_server.py   (done automatically by VS Code via mcp.json)
"""
import glob as _glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from mcp.server.fastmcp import FastMCP
from repo_rag import RepoRAG
from demo_logger import log

# ── Load config ──────────────────────────────────────────────────────────────
CONFIG_PATH = Path(__file__).parent / "config.yaml"
with open(CONFIG_PATH) as f:
    _cfg = yaml.safe_load(f)

REPO_NAME   = _cfg["repo_name"]
DISPLAY     = _cfg.get("display_name", REPO_NAME)

REPO_ROOT   = Path(__file__).parent.parent.resolve()

def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()

def _expand_src_path(raw) -> list[Path]:
    """Resolve src_path to a list of concrete directories, expanding glob patterns."""
    if raw is None:
        return []
    entries = raw if isinstance(raw, list) else [raw]
    result = []
    for entry in entries:
        if any(c in str(entry) for c in ("*", "?", "[")):
            matches = _glob.glob(str(REPO_ROOT / entry), recursive=True)
            result.extend(Path(m) for m in sorted(matches) if Path(m).is_dir())
        else:
            result.append(_resolve(str(entry)))
    return result

SRC_DIRS      = _expand_src_path(_cfg.get("src_path"))
KNOWLEDGE_DIR = _resolve(_cfg.get("knowledge_path", "./knowledge"))
CHROMA_DB     = str(REPO_ROOT / ".chroma_db")
EXTRA_EXT       = _cfg.get("extra_extensions", [])
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL_PATH", "all-MiniLM-L6-v2")

# ── Build RAG index ───────────────────────────────────────────────────────────
log(REPO_NAME, "INFO", f"Starting {DISPLAY} MCP server")
rag = RepoRAG(
    repo_name=REPO_NAME,
    knowledge_path=str(KNOWLEDGE_DIR),
    src_paths=[str(d) for d in SRC_DIRS] if SRC_DIRS else None,
    chroma_persist_dir=CHROMA_DB,
    extra_extensions=EXTRA_EXT,
    embedding_model=EMBEDDING_MODEL,
)
rag.build_or_load_index()

# ── MCP server ────────────────────────────────────────────────────────────────
mcp = FastMCP(DISPLAY)


@mcp.tool()
def query_repo(feature_request: str) -> str:
    """
    Query this repo's knowledge base for documentation and source code relevant
    to the feature request. Returns the most relevant content found, or
    '(no relevant knowledge found)' if nothing matches.
    """
    log(REPO_NAME, "TOOL_CALL", f"query_repo: '{feature_request[:80]}'")
    result = rag.query(feature_request)
    log(REPO_NAME, "RESULT",    f"query_repo → {len(result)} chars returned")
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
