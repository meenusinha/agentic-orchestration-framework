"""Markdown → Doc / Section tree. A section's text is the whole section, from its
heading through its subsections, with code blocks and tables never split."""
from __future__ import annotations

import importlib
import warnings
from functools import lru_cache

from .model import DOC, DOC_SECTION, Node


@lru_cache(maxsize=None)
def _parser():
    from tree_sitter import Language, Parser
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return Parser(Language(importlib.import_module("tree_sitter_markdown").language()))


def parse_markdown(rel_path: str, source: str) -> Node:
    src = source.encode("utf-8")
    tree = _parser().parse(src)
    root = Node(DOC, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, "markdown", 1,
                source.count("\n") + 1, 0, len(src), "")
    _sections(tree.root_node, root, src, [])
    intro = src[:root.children[0].start_byte].decode("utf-8", "replace").strip() if root.children else source.strip()
    root.doc = intro[:2000]
    root.text = "\n".join([f"<!-- doc: {rel_path} -->"] + ([intro] if intro else []) +
                          [("#" * c.attrs.get("level", 1)) + " " + c.name for c in root.walk() if c is not root])
    return root


def _sections(ts, parent: Node, src: bytes, path: list[str]):
    for ch in ts.named_children:
        if ch.type != "section":
            continue
        head = next((g for g in ch.named_children if g.type in ("atx_heading", "setext_heading")), None)
        title = src[head.start_byte:head.end_byte].decode("utf-8", "replace").strip().lstrip("#").strip() \
            if head is not None else "(untitled)"
        if head is not None and head.type == "setext_heading":
            title = title.splitlines()[0].strip()
        level = 1
        if head is not None and head.named_children:
            marker = head.named_children[0].type          # atx_h2_marker
            level = int(marker[5]) if marker.startswith("atx_h") and marker[5:6].isdigit() else 1
        eb = ch.end_byte
        while eb > ch.start_byte and src[eb - 1:eb] in (b"\n", b"\r"):
            eb -= 1
        n = Node(DOC_SECTION, title, " / ".join(path + [title]), parent.file, "markdown",
                 src[:ch.start_byte].count(b"\n") + 1, src[:eb].count(b"\n") + 1, ch.start_byte, ch.end_byte,
                 src[ch.start_byte:ch.end_byte].decode("utf-8", "replace").rstrip())
        n.signature = ("#" * level) + " " + title
        n.attrs["level"] = level
        parent.add(n)
        _sections(ch, n, src, path + [title])
