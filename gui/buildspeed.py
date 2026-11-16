"""This computer's real GraphRAG build speed, recorded by each build and used by later time
estimates (more reliable than the estimate's small sample).

Stored in ~/.agentic-orchestration/build-speed.json, private like the logs. It holds numbers
only: seconds per byte for reading and parsing, seconds per unit of embedding work (see
codegraph/embedcost.py), and how much work they were measured on. No code, no paths.
"""
import json
import time
from pathlib import Path

PATH = Path.home() / ".agentic-orchestration" / "build-speed.json"
MIN_WORK = 3e6        # embedding work a build must have done to be a reliable measurement
MIN_BYTES = 500_000   # source read and parsed, likewise


def record(speeds: list, model_sha: str) -> bool:
    """speeds: the "speed" entries of the builds just done. Saves them if large enough."""
    from codegraph.store import write_private_text
    work = sum(s.get("embed_work", 0) for s in speeds)
    nbytes = sum(s.get("parse_bytes", 0) for s in speeds)
    if work < MIN_WORK or nbytes < MIN_BYTES:
        return False
    data = {"model": model_sha, "recorded": time.time(), "embed_work": work, "parse_bytes": nbytes,
            "embed_seconds_per_work": sum(s.get("embed_seconds", 0) for s in speeds) / work,
            "parse_seconds_per_byte": sum(s.get("parse_seconds", 0) for s in speeds) / nbytes}
    write_private_text(PATH, json.dumps(data, indent=1))
    return True


def load(model_sha: str) -> dict | None:
    """The last recorded speed for this model, or None."""
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = data.get("model") == model_sha and data.get("embed_seconds_per_work", 0) > 0 \
        and data.get("parse_seconds_per_byte", 0) > 0
    return data if ok else None
