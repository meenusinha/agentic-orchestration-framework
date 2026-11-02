"""Table-driven extraction for MATLAB, Perl, JavaScript/TypeScript, Java, Go, Rust, C#.

Same rules as the C/C++/Python extractors: definition nodes carry their exact,
complete source; containers carry a skeleton; a completeness pass guarantees no
function in the syntax tree is left out. Each language only declares which
syntax node types play which role.
"""
from __future__ import annotations

import importlib
import warnings
from functools import lru_cache

from .model import CLASS, FIELD, FILE, FUNCTION, FUNCTION_DECL, METHOD, MODULE, NAMESPACE, TYPE, Node

GENERIC_EXTENSIONS = {
    ".m": "matlab",
    ".pl": "perl", ".pm": "perl", ".t": "perl",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go", ".rs": "rust", ".cs": "csharp",
}

_GRAMMARS = {
    "matlab": ("tree_sitter_matlab", "language"), "perl": ("tree_sitter_perl", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "java": ("tree_sitter_java", "language"), "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"), "csharp": ("tree_sitter_c_sharp", "language"),
}

_JS = {
    "containers": {"class_declaration": CLASS, "abstract_class_declaration": CLASS, "class": CLASS,
                   "interface_declaration": CLASS, "internal_module": NAMESPACE, "module": NAMESPACE},
    "whole_types": {"enum_declaration", "type_alias_declaration"},
    "functions": {"function_declaration", "generator_function_declaration", "method_definition",
                  "function_signature", "method_signature", "abstract_method_signature"},
    "fields": {"public_field_definition", "field_definition", "property_signature"},
    "unwrap": {"export_statement", "ambient_declaration"},
    "calls": {"call_expression": ("function", ("property", "object"))},
    "bases": ("class_heritage", "extends_clause", "implements_clause", "extends_type_clause"),
}
SPECS = {
    "javascript": _JS, "typescript": _JS, "tsx": _JS,
    "java": {
        "containers": {"class_declaration": CLASS, "interface_declaration": CLASS, "record_declaration": CLASS,
                       "annotation_type_declaration": CLASS, "enum_declaration": CLASS},
        "whole_types": set(),
        "transparent": {"enum_body", "enum_body_declarations", "static_initializer"},
        "functions": {"method_declaration", "constructor_declaration", "compact_constructor_declaration"},
        "fields": {"field_declaration", "constant_declaration"},
        "calls": {"method_invocation": ("name", ("name", "object"))},
        "bases": ("superclass", "super_interfaces", "extends_interfaces"),
    },
    "csharp": {
        "containers": {"namespace_declaration": NAMESPACE, "file_scoped_namespace_declaration": NAMESPACE,
                       "class_declaration": CLASS, "interface_declaration": CLASS, "struct_declaration": CLASS,
                       "record_declaration": CLASS},
        "whole_types": {"enum_declaration", "delegate_declaration"},
        "functions": {"method_declaration", "constructor_declaration", "destructor_declaration",
                      "operator_declaration", "local_function_statement"},
        "fields": {"field_declaration", "property_declaration", "event_field_declaration"},
        "calls": {"invocation_expression": ("function", ("name", "expression"))},
        "bases": ("base_list",),
    },
    "go": {
        "containers": {},
        "whole_types": set(),
        "functions": {"function_declaration", "method_declaration"},
        "fields": set(),
        "calls": {"call_expression": ("function", ("field", "operand"))},
        "bases": (),
    },
    "rust": {
        "containers": {"trait_item": CLASS, "impl_item": CLASS, "mod_item": NAMESPACE},
        "whole_types": {"struct_item", "enum_item", "union_item", "type_item", "const_item", "static_item",
                        "macro_definition"},
        "functions": {"function_item", "function_signature_item"},
        "fields": set(),
        "calls": {"call_expression": ("function", ("field", "value")),
                  "macro_invocation": ("macro", (None, None))},
        "bases": (),
    },
    "matlab": {
        "containers": {"class_definition": CLASS},
        "whole_types": set(),
        "functions": {"function_definition"},
        "fields": {"property"},
        "calls": {"function_call": ("name", (None, None))},
        "bases": ("superclasses",),
        "transparent": {"methods", "properties", "events", "enumeration"},   # classdef blocks
    },
    "perl": {
        "containers": {},
        "whole_types": set(),
        "functions": {"subroutine_declaration_statement", "method_declaration_statement"},
        "fields": set(),
        "calls": {"method_call_expression": ("method", (None, "invocant")),
                  "function_call_expression": ("function", (None, None)),
                  "ambiguous_function_call_expression": ("function", (None, None))},
        "bases": (),
    },
}
_COMMENTS = {"comment", "line_comment", "block_comment", "documentation_comment"}


@lru_cache(maxsize=None)
def _parser(lang: str):
    from tree_sitter import Language, Parser
    mod, fn = _GRAMMARS[lang]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return Parser(Language(getattr(importlib.import_module(mod), fn)()))


def parse_generic(rel_path: str, source: str, lang: str) -> Node:
    src = source.encode("utf-8")
    tree = _parser(lang).parse(src)
    x = _Extractor(rel_path, src, lang)
    root = Node(FILE, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, lang, 1, source.count("\n") + 1, 0, len(src))
    root.attrs["parse_errors"] = bool(tree.root_node.has_error)
    if lang == "perl":
        x.perl(tree.root_node, root)
    else:
        x.scope_walk(tree.root_node, root, [])
    x.recover(tree.root_node, root)
    root.text = "\n".join([f"// file: {rel_path}"] + [_line(c) for c in root.children])
    return root


def _line(n: Node) -> str:
    sig = (n.signature or n.text.split("\n", 1)[0]).strip()
    return f"{sig}    // → {n.kind} {n.qualname}" if n.kind in (FUNCTION, METHOD, CLASS, NAMESPACE, MODULE) else sig


class _Extractor:
    def __init__(self, path, src, lang):
        self.path, self.src, self.lang, self.spec = path, src, lang, SPECS[lang]
        self.sep = "::" if lang in ("rust", "perl") else "."

    def text(self, ts, start=None, end=None):
        return self.src[ts.start_byte if start is None else start:
                        ts.end_byte if end is None else end].decode("utf-8", "replace")

    def mk(self, kind, name, qual, ts, start_byte=None) -> Node:
        sb = ts.start_byte if start_byte is None else start_byte
        eb = ts.end_byte
        while eb > sb and self.src[eb - 1:eb] in (b"\n", b"\r"):
            eb -= 1
        return Node(kind, name, qual, self.path, self.lang, self.src[:sb].count(b"\n") + 1,
                    self.src[:eb].count(b"\n") + 1, sb, ts.end_byte, self.text(ts, start=sb))

    def doc(self, ts):
        parts, start, prev = [], None, ts.prev_named_sibling
        row = ts.start_point[0]
        while prev is not None and prev.type in _COMMENTS and prev.end_point[0] >= row - 1:
            parts.insert(0, self.text(prev))
            start, row, prev = prev.start_byte, prev.start_point[0], prev.prev_named_sibling
        return "\n".join(parts), start

    def name_of(self, ts) -> str:
        nm = ts.child_by_field_name("name")
        if nm is not None:
            return self.text(nm)
        if ts.type == "impl_item":                      # rust: impl Trait for Type
            ty, tr = ts.child_by_field_name("type"), ts.child_by_field_name("trait")
            return self.text(ty) if ty is not None else "impl"
        for ch in ts.named_children:                    # java/c# fields: declarator → name
            if ch.type in ("variable_declarator", "variable_declaration"):
                return self.name_of(ch) or ""
        for ch in ts.named_children:
            if ch.type in ("identifier", "property_identifier", "type_identifier", "field_identifier"):
                return self.text(ch)
        return ""

    def calls(self, ts) -> list:
        out, stack = [], [ts] if ts is not None else []
        spec = self.spec["calls"]
        while stack:
            n = stack.pop()
            if n.type in spec:
                fn_field, (member_field, recv_field) = spec[n.type]
                fn = n.child_by_field_name(fn_field)
                if fn is not None:
                    callee, recv = self.text(fn), ""
                    if member_field and fn.child_by_field_name(member_field) is not None:
                        callee = self.text(fn.child_by_field_name(member_field))
                        r = fn.child_by_field_name(recv_field) if recv_field else None
                        recv = self.text(r) if r is not None else ""
                    elif recv_field and n.child_by_field_name(recv_field) is not None:   # java / perl
                        recv = self.text(n.child_by_field_name(recv_field))
                    out.append([callee.rsplit(".", 1)[-1].rsplit("::", 1)[-1].lstrip("&"), recv])
            stack.extend(n.children)
        return out

    def add(self, parent, kind, ts, scope, outer=None):
        outer = outer or ts
        name = self.name_of(ts) or "?"
        qual = self.sep.join(scope + [name])
        d, dstart = self.doc(outer)
        n = self.mk(kind, name, qual, outer, start_byte=dstart)
        n.doc = d
        body = ts.child_by_field_name("body") or next(
            (g for g in ts.named_children if g.type in ("block", "statement_block", "declaration_list",
                                                       "class_body", "compound_statement")), None)
        n.signature = (self.text(outer, end=body.start_byte) if body is not None else
                       self.text(outer).split("\n", 1)[0]).strip()
        mods = self.text(outer)[:120]
        if any(w in mods.split("(")[0].split() for w in ("private", "protected", "internal")):
            n.visibility = next(w for w in ("private", "protected", "internal") if w in mods.split("(")[0].split())
        elif self.lang in ("go",) and name[:1].islower():
            n.visibility = "private"                    # go: lower-case = unexported
        elif self.lang == "perl" and name.startswith("_"):
            n.visibility = "private"
        return parent.add(n), body

    def scope_walk(self, ts, parent: Node, scope: list):
        spec = self.spec
        for ch in ts.named_children:
            t = ch.type
            if t in spec.get("unwrap", ()) or t in spec.get("transparent", ()):
                decl = ch.child_by_field_name("declaration")
                self.scope_walk(ch if decl is None else _Wrap(decl), parent, scope)
            elif t in spec["containers"]:
                self.container(ch, parent, scope, spec["containers"][t])
            elif t in spec["functions"]:
                kind = METHOD if parent.kind == CLASS else FUNCTION
                if t in ("function_signature", "method_signature", "abstract_method_signature",
                         "function_signature_item"):
                    kind = FUNCTION_DECL
                n, body = self.add(parent, kind, ch, scope)
                if t == "method_declaration" and self.lang == "go":
                    recv = ch.child_by_field_name("receiver")
                    owner = self.text(recv).strip("()").split()[-1].lstrip("*") if recv is not None else ""
                    n.kind, n.attrs["owner"] = METHOD, owner
                    n.qualname = f"{owner}.{n.name}" if owner else n.qualname
                n.attrs["calls"] = self.calls(body)
                if body is not None:                   # nested functions: children, parent text stays whole
                    self.scope_walk(body, n, scope + [n.name])
            elif t in spec["whole_types"] or (self.lang == "go" and t == "type_declaration"):
                self.whole_type(ch, parent, scope)
            elif t in spec["fields"] and parent.kind in (CLASS,):
                n, _ = self.add(parent, FIELD, ch, scope)
            elif t in ("lexical_declaration", "variable_declaration") and self.lang in ("javascript", "typescript",
                                                                                       "tsx"):
                for dec in ch.named_children:          # const f = (x) => … / function () {…}
                    val = dec.child_by_field_name("value") if dec.type == "variable_declarator" else None
                    if val is not None and val.type in ("arrow_function", "function_expression", "function"):
                        n, _ = self.add(parent, FUNCTION, dec, scope, outer=ch)
                        n.attrs["calls"] = self.calls(val.child_by_field_name("body"))
            elif t == "expression_statement" and self.lang in ("javascript", "typescript", "tsx") and \
                    parent.kind == FILE and self._callback_call(ch):
                call = ch.named_children[0]
                fn = call.child_by_field_name("function")
                args = call.child_by_field_name("arguments")
                label = next((self.text(a).strip("'\"`") for a in (args.named_children if args else [])
                              if a.type in ("string", "template_string")), "")
                name = f"{self.text(fn)}({label!r})" if label else self.text(fn)
                n = self.mk(FUNCTION, name, name, ch)
                n.signature = self.text(ch).split("\n", 1)[0].strip()
                n.attrs["calls"] = self.calls(args)
                n.attrs["callback_call"] = True
                parent.add(n)
            elif t in ("declaration_list", "class_body", "block", "statement_block") and parent.kind != FILE:
                self.scope_walk(ch, parent, scope)
            elif t == "ERROR":
                self.scope_walk(ch, parent, scope)

    @staticmethod
    def _callback_call(stmt) -> bool:
        if not stmt.named_children or stmt.named_children[0].type not in ("call_expression", "await_expression"):
            return False
        call = stmt.named_children[0]
        if call.type == "await_expression" and call.named_children:
            call = call.named_children[0]
        args = call.child_by_field_name("arguments") if call.type == "call_expression" else None
        return args is not None and any(a.type in ("arrow_function", "function_expression", "function", "object")
                                        for a in args.named_children)

    def container(self, ch, parent, scope, kind):
        n, body = self.add(parent, kind, ch, scope)
        if ch.type == "impl_item":
            tr = ch.child_by_field_name("trait")
            n.attrs["impl_of"] = n.name
            if tr is not None:
                n.attrs["bases"] = [self.text(tr)]
                n.signature = f"impl {self.text(tr)} for {n.name}"
        bases = []
        for b in self.spec.get("bases", ()):
            for g in [ch.child_by_field_name(b)] + [x for x in ch.named_children if x.type == b]:
                if g is not None:
                    bases += [self.text(t) for t in _idents(g)]
        if bases:
            n.attrs["bases"] = sorted(set(bases))
        if ch.type == "interface_declaration":
            n.attrs["code_interface"] = True
        inner = body if body is not None else ch
        self.scope_walk(inner, n, scope + [n.name])
        n.text = (n.doc + "\n" if n.doc else "") + n.signature + " {\n" + \
            "\n".join("  " + _line(m) for m in n.children) + "\n}"

    def whole_type(self, ch, parent, scope):
        if self.lang == "go":                           # type X struct{…} / type I interface{…}
            for spec in ch.named_children:
                if spec.type != "type_spec":
                    continue
                ty = spec.child_by_field_name("type")
                if ty is not None and ty.type == "interface_type":
                    n, _ = self.add(parent, CLASS, spec, scope, outer=ch)
                    n.attrs["code_interface"] = True
                    for m in ty.named_children:
                        if m.type in ("method_elem", "method_spec"):
                            self.add(n, FUNCTION_DECL, m, scope + [n.name])
                    n.text = self.text(ch)              # small: keep the whole interface
                else:
                    self.add(parent, TYPE, spec, scope, outer=ch)
            return
        self.add(parent, TYPE, ch, scope)

    # perl: `package X;` starts a section that runs to the next package statement
    def perl(self, ts_root, root: Node):
        current, scope = root, []
        for ch in ts_root.named_children:
            if ch.type == "package_statement":
                name = self.name_of(ch) or self.text(ch).split()[1].rstrip(";")
                blk = next((g for g in ch.named_children if g.type == "block"), None)
                mod = self.mk(MODULE, name, name, ch)
                mod.signature = f"package {name};"
                root.add(mod)
                if blk is not None:                     # package X { … }
                    self._perl_items(blk, mod, [name])
                    self._perl_skeleton(mod)
                    current, scope = root, []
                else:
                    current, scope = mod, [name]
            elif ch.type in self.spec["functions"]:
                self._perl_items(_Single(ch), current, scope)
            elif ch.type in ("block_statement", "block", "phaser_statement"):
                inner = next((g for g in ch.named_children if g.type == "block"), ch) if ch.type == "phaser_statement" \
                    else ch
                sub_root = Node(MODULE, "", "", self.path, self.lang, 1, 1)
                self.perl(inner, sub_root)                 # may define packages and subs
                for m in sub_root.children:
                    (root if m.kind == MODULE else current).add(m)
            elif ch.type == "use_statement":
                mod = ch.child_by_field_name("module")
                if mod is not None:
                    root.attrs.setdefault("includes", []).append(self.text(mod))
        for m in root.children:
            if m.kind == MODULE and not m.text.startswith("package "):
                pass
            if m.kind == MODULE:
                self._perl_skeleton(m)

    def _perl_items(self, ts, parent, scope):
        for ch in ts.named_children:
            if ch.type in self.spec["functions"]:
                n, body = self.add(parent, FUNCTION, ch, scope)
                n.attrs["calls"] = self.calls(body)

    def _perl_skeleton(self, mod: Node):
        if mod.children:
            mod.end_line = max(mod.end_line, mod.children[-1].end_line)
        mod.text = (mod.signature + "\n" + "\n".join("  " + _line(c) for c in mod.children))

    def recover(self, ts_root, root: Node):
        """Completeness guarantee, as in parsers._recover_missing."""
        covered = [(n.start_byte, n.end_byte) for n in root.walk() if n is not root and
                   n.kind in (FUNCTION, METHOD, FUNCTION_DECL)]
        holders = [n for n in root.walk() if n.kind in (FILE, CLASS, NAMESPACE, MODULE)]
        stack, found = [ts_root], []
        while stack:
            t = stack.pop()
            if t.type in self.spec["functions"]:
                found.append(t)
                continue
            stack.extend(t.children)
        for t in found:
            if any(a <= t.start_byte and t.end_byte <= b for a, b in covered):
                continue
            holder = root
            for c in holders:
                if c is not root and c.start_byte <= t.start_byte and t.end_byte <= c.end_byte and \
                        (holder is root or c.end_byte - c.start_byte < holder.end_byte - holder.start_byte):
                    holder = c
            n, body = self.add(holder, METHOD if holder.kind == CLASS else FUNCTION, t,
                               holder.qualname.split(self.sep) if holder is not root else [])
            n.attrs["calls"] = self.calls(body)
            n.attrs["recovered"] = True


class _Wrap:
    """Present a single syntax node as a scope with one child."""
    def __init__(self, n):
        self.named_children = [n]


_Single = _Wrap


def _idents(ts):
    out, stack = [], [ts]
    while stack:
        n = stack.pop()
        if n.type in ("type_identifier", "identifier", "scoped_type_identifier", "generic_type",
                      "property_name", "qualified_name"):
            out.append(n)
            continue
        stack.extend(n.named_children)
    return out
