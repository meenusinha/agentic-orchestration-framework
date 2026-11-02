"""Code-graph data model: nodes are whole definitions, edges are typed relations.

Invariant: a definition node's `text` is always the complete source of that
definition (functions, methods, IDL operations, doc sections, ...). Container
nodes (classes, modules, files) carry a *skeleton* — header, comments and member
signatures — and point to their members through CONTAINS edges.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ── Node kinds ────────────────────────────────────────────────────────────────
FILE = "file"
MODULE = "module"            # C module (header+source pair), Perl package, IDL module
NAMESPACE = "namespace"
CLASS = "class"              # class / struct-with-methods / interface (code) / MATLAB classdef
TYPE = "type"                # struct / union / enum / typedef (data definitions)
FUNCTION = "function"
METHOD = "method"
FUNCTION_DECL = "function_decl"  # C/C++ prototype (header declaration)
FIELD = "field"              # member variable / property / attribute
VARIABLE = "variable"        # global / module-level variable
MACRO = "macro"
IDL_INTERFACE = "idl_interface"
IDL_OPERATION = "idl_operation"
IDL_TYPE = "idl_type"
IDL_ATTRIBUTE = "idl_attribute"
DOC = "doc"
DOC_SECTION = "doc_section"
TEXT = "text"                # whole file indexed as one unit (unknown/plain types)
TARGET = "target"            # build target: executable / library (a process when executable)

CONTAINERS = {FILE, MODULE, NAMESPACE, CLASS, IDL_INTERFACE, DOC, TARGET}

# ── Edge types ────────────────────────────────────────────────────────────────
CONTAINS = "CONTAINS"
DECLARES = "DECLARES"            # header prototype → definition
INCLUDES = "INCLUDES"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
INHERITS = "INHERITS"
TESTS = "TESTS"                  # test node → code under test (evidence only)
DOCUMENTS = "DOCUMENTS"          # doc section → symbol it names
BUILDS = "BUILDS"                # target → file
DEPENDS_ON = "DEPENDS_ON"        # target → target / library
GENERATED_FROM = "GENERATED_FROM"
IMPLEMENTS = "IMPLEMENTS"        # server code → IDL interface / operation
CALLS_OPERATION = "CALLS_OPERATION"  # client code → IDL operation
PROVIDES = "PROVIDES"            # target/process → IDL interface (server side)
CONSUMES = "CONSUMES"            # target/process → IDL interface (client side)

# ── Roles (from manifest > build facts > heuristics) ──────────────────────────
ROLES = ("interface", "implementation", "internal", "external", "library", "test",
         "build", "generated", "docs", "tooling", "config", "data", "unknown")


@dataclass
class Node:
    kind: str
    name: str
    qualname: str
    file: str                     # repo-relative path, "/" separators
    lang: str
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0
    text: str = ""                # full source (definitions) or skeleton (containers)
    signature: str = ""
    doc: str = ""
    visibility: str = "public"    # public / private / protected / internal
    attrs: dict = field(default_factory=dict)   # e.g. {"ifdef": "FEATURE_X", "generated": True}
    parent: "Node | None" = None
    children: list = field(default_factory=list)
    # filled by structure resolution
    role: str = "unknown"
    target: str = ""

    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child

    @property
    def uid(self) -> str:
        """Stable id across rebuilds: file + kind + qualname + start line."""
        raw = f"{self.file}\x00{self.kind}\x00{self.qualname}\x00{self.start_line}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Edge:
    src: str          # node uid
    dst: str          # node uid, or "" when unresolved
    type: str
    dst_name: str = ""   # symbol name when dst is unresolved / for display
    confidence: float = 1.0
