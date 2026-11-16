"""Chunking modes (setting `chunking`, per repository; standard library only).

- graph:   whole units only — functions, classes, IDL operations, documentation sections.
- sliding: every file is cut into windows of `size` lines overlapping by `overlap` lines.
           Windows are the search results; each is linked to the units it covers (COVERS),
           so it still shows their calls, IDL interfaces and tests.
- hybrid:  whole units are the results; units longer than `size` lines are also embedded as
           overlapping windows (children of the unit), so a match anywhere in a long unit
           finds it — and the whole unit is returned.

The graph itself (units and their links) is built the same way in every mode.
"""
from __future__ import annotations

from .model import (CHUNK, CLASS, CONTAINERS, COVERS, DOC_SECTION, FILE, FUNCTION, IDL_ATTRIBUTE, IDL_INTERFACE,
                    IDL_OPERATION, IDL_TYPE, MACRO, METHOD, MODULE, NAMESPACE, TARGET, TEXT, TYPE, Edge, Node)

MODES = ("graph", "sliding", "hybrid")
UNIT_KINDS = {FUNCTION, METHOD, CLASS, TYPE, MACRO, IDL_INTERFACE, IDL_OPERATION, IDL_ATTRIBUTE, IDL_TYPE, DOC_SECTION,
              TEXT, MODULE, NAMESPACE, TARGET}           # embedded in graph mode (as before)
COVERED_KINDS = UNIT_KINDS - {MODULE, NAMESPACE, TARGET}   # what a sliding window links to


def windows(n_lines: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """1-based inclusive (first, last) line ranges covering n_lines."""
    size = max(int(size), 1)
    step = max(size - max(int(overlap), 0), 1)
    out, a = [], 1
    while a <= n_lines:
        b = min(a + size - 1, n_lines)
        out.append((a, b))
        if b == n_lines:
            break
        a += step
    return out


def _chunk(parent: Node, first: int, last: int, text: str) -> Node:
    return Node(CHUNK, name=f"lines {first}-{last}", qualname=f"{parent.file}:{first}-{last}", file=parent.file,
                lang=parent.lang, start_line=first, end_line=last, text=text, role=parent.role, target=parent.target)


def add_chunks(root: Node, file_text: str, mode: str, size: int, overlap: int) -> list[Edge]:
    """Attach chunk nodes to a parsed file tree (after roles are set). → COVERS edges (sliding)."""
    if mode == "sliding":
        lines = file_text.splitlines()
        units = [n for n in root.walk() if n.kind in COVERED_KINDS]
        edges = []
        for a, b in windows(len(lines), size, overlap):
            text = "\n".join(lines[a - 1:b])
            if not text.strip():
                continue
            c = root.add(_chunk(root, a, b, text))
            for u in units:                           # units overlapping this window
                if u.start_line <= b and u.end_line >= a and u is not root:
                    edges.append(Edge(c.uid, u.uid, COVERS))
        return edges
    if mode == "hybrid":
        for n in [n for n in root.walk() if n.kind in UNIT_KINDS and n.kind not in CONTAINERS]:
            lines = n.text.splitlines()
            if len(lines) <= size:
                continue
            for a, b in windows(len(lines), size, overlap):
                text = "\n".join(lines[a - 1:b])
                if text.strip():
                    n.add(_chunk(n, n.start_line + a - 1, n.start_line + b - 1, text))
    return []


def embeddable(n: Node, mode: str) -> bool:
    """Is this node embedded (and searchable) in this mode?"""
    if n.kind == FILE:
        return False
    if mode == "sliding":
        return n.kind == CHUNK
    return n.kind in UNIT_KINDS or (mode == "hybrid" and n.kind == CHUNK)


def keyword_indexed(n: Node, mode: str) -> bool:
    """Is this node in the keyword (FTS) index? Hybrid windows are not: their unit already is."""
    if n.kind == FILE:
        return False
    return n.kind == CHUNK if mode == "sliding" else n.kind != CHUNK
