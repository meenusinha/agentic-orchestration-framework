"""Pick the extractor for a file: code grammar, IDL, Markdown, or whole-file text.

Precedence: the repo manifest's `file_types` (what the developer told us) →
known extensions → nothing (unknown types are reported by scan.py so setup can
ask the developer what they contain)."""
from __future__ import annotations

from .docs import parse_markdown
from .idl import IDL_EXTENSIONS, parse_idl
from .langs import GENERIC_EXTENSIONS, SPECS, parse_generic
from .model import FILE, TEXT, Node
from .parsers import EXTENSION_LANG, parse_file

DOC_EXTENSIONS = {".md", ".markdown", ".rmd", ".qmd"}
PARSE_AS = {"c", "cpp", "python", "idl", "markdown", "text", *SPECS}


def language_for(rel_path: str, file_types: dict | None = None) -> str:
    """'' when the file should not be indexed."""
    name = rel_path.rsplit("/", 1)[-1]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    ft = (file_types or {}).get(ext) or (file_types or {}).get(name)
    if ft is not None:
        if ft.get("kind") == "ignore":
            return ""
        return ft.get("parse_as", "text")
    if ext in EXTENSION_LANG:
        return EXTENSION_LANG[ext]
    if ext in GENERIC_EXTENSIONS:
        return GENERIC_EXTENSIONS[ext]
    if ext in IDL_EXTENSIONS:
        return "idl"
    if ext in DOC_EXTENSIONS:
        return "markdown"
    return ""


def extract(rel_path: str, source: str, lang: str) -> Node:
    if lang in ("c", "cpp", "python"):
        return parse_file(rel_path, source, lang)
    if lang in SPECS:
        return parse_generic(rel_path, source, lang)
    if lang == "idl":
        return parse_idl(rel_path, source)
    if lang == "markdown":
        return parse_markdown(rel_path, source)
    # plain text / config / anything the developer classified as content: one whole node
    root = Node(FILE, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, "text", 1, source.count("\n") + 1,
                0, len(source.encode()), f"// file: {rel_path}")
    root.add(Node(TEXT, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, "text", 1, source.count("\n") + 1,
                  0, len(source.encode()), source))
    return root
