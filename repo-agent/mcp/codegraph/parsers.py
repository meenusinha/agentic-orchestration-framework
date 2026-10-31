"""Tree-sitter extraction: source file → tree of whole-definition Nodes.

Each language extractor walks the syntax tree and emits:
  - definition nodes (functions, methods, types, macros, ...) whose `text` is the
    exact, complete source span — never split, never truncated;
  - container nodes (file, namespace, class, module) whose `text` is a skeleton:
    their header/comments plus member signatures, with member bodies elided and
    a pointer to the member node.
Per-node `attrs` also record what the relation extractor needs later: call sites,
includes/imports, base classes and preprocessor conditions.
"""
from __future__ import annotations

import importlib
from functools import lru_cache

from .model import (CLASS, FIELD, FILE, FUNCTION, FUNCTION_DECL, MACRO, METHOD,
                    NAMESPACE, TYPE, VARIABLE, Node)

# extension → language
EXTENSION_LANG = {
    ".c": "c", ".h": "cpp",                      # headers: C++ grammar accepts C; overridden by compile db
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".h++": "cpp", ".ipp": "cpp", ".tpp": "cpp", ".inl": "cpp",
    ".cu": "cpp", ".cuh": "cpp",                 # CUDA: C++ plus kernel qualifiers the C++ grammar tolerates
    ".py": "python", ".pyw": "python",
}

_GRAMMAR = {"c": "tree_sitter_c", "cpp": "tree_sitter_cpp", "python": "tree_sitter_python"}


@lru_cache(maxsize=None)
def _parser(lang: str):
    import warnings
    from tree_sitter import Language, Parser
    mod = importlib.import_module(_GRAMMAR[lang])
    with warnings.catch_warnings():  # older grammar wheels hand over an int pointer
        warnings.simplefilter("ignore", DeprecationWarning)
        return Parser(Language(mod.language()))


def supported(lang: str) -> bool:
    return lang in _GRAMMAR


def parse_file(rel_path: str, source: str, lang: str) -> Node:
    src = source.encode("utf-8")
    tree = _parser(lang).parse(src)
    ctx = _Ctx(rel_path, src, lang)
    root = Node(FILE, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, lang,
                1, source.count("\n") + 1, 0, len(src))
    root.attrs["parse_errors"] = bool(tree.root_node.has_error)
    if lang in ("c", "cpp"):
        fam = _CFamily(ctx)
        fam.guard = _include_guard(ctx, tree.root_node)
        fam.walk_scope(tree.root_node, root, scope=[])
    elif lang == "python":
        _Python(ctx).walk_block(tree.root_node, root, scope=[], in_class=False)
    _recover_missing(ctx, tree.root_node, root)
    root.text = _file_skeleton(ctx, root)
    return root


_FUNC_TS = {"c": ("function_definition",), "cpp": ("function_definition",), "python": ("function_definition",)}


def _recover_missing(ctx: _Ctx, ts_root, root: Node) -> None:
    """Guarantee: every function definition in the syntax tree is inside some node's
    complete text. Anything an extractor did not anticipate is added whole, marked
    `recovered`, under the innermost container whose span encloses it."""
    covered = [(n.start_byte, n.end_byte) for n in root.walk()
               if n is not root and n.kind in (FUNCTION, METHOD)]
    containers = [n for n in root.walk() if n.kind in (FILE, NAMESPACE, CLASS)]
    stack, found = [ts_root], []
    while stack:
        t = stack.pop()
        if t.type in _FUNC_TS.get(ctx.lang, ()):
            found.append(t)
            continue                     # nested ones are inside this text anyway
        stack.extend(t.children)
    for t in found:
        if any(a <= t.start_byte and t.end_byte <= b for a, b in covered):
            continue
        holder = root
        for c in containers:
            if c is not root and c.start_byte <= t.start_byte and t.end_byte <= c.end_byte:
                if holder is root or (c.end_byte - c.start_byte) < (holder.end_byte - holder.start_byte):
                    holder = c
        n = ctx.node(FUNCTION, "?", "?", t)
        if ctx.lang in ("c", "cpp"):
            fam = _CFamily(ctx)
            name = fam.decl_name(_child(t, "declarator")) or "?"
            body = _child(t, "body")
            n.signature = ctx.text(t, end=body.start_byte).strip() if body is not None else ""
            n.attrs["calls"] = _calls(ctx, body, ctx.lang) if body is not None else []
        else:
            nm = _child(t, "name")
            name = ctx.text(nm) if nm is not None else "?"
        n.name, n.qualname = name.rsplit("::", 1)[-1], (holder.qualname + "::" + name) if holder is not root else name
        n.kind = METHOD if holder.kind == CLASS else FUNCTION
        n.attrs["recovered"] = True
        holder.add(n)


# ── shared helpers ────────────────────────────────────────────────────────────
class _Ctx:
    def __init__(self, path: str, src: bytes, lang: str):
        self.path, self.src, self.lang = path, src, lang

    def text(self, n, start=None, end=None) -> str:
        return self.src[n.start_byte if start is None else start:
                        n.end_byte if end is None else end].decode("utf-8", "replace")

    def node(self, kind, name, qual, ts, text=None, start_byte=None) -> Node:
        sb = ts.start_byte if start_byte is None else start_byte
        eb = ts.end_byte
        while eb > sb and self.src[eb - 1:eb] in (b"\n", b"\r"):
            eb -= 1
        return Node(kind, name, qual, self.path, self.lang,
                    self.src[:sb].count(b"\n") + 1, self.src[:eb].count(b"\n") + 1, sb, ts.end_byte,
                    text if text is not None else self.text(ts, start=sb))


def _leading_comments(ctx: _Ctx, ts) -> tuple[str, int | None]:
    """Contiguous comments directly above a definition (doc comments)."""
    parts, start, prev = [], None, ts.prev_named_sibling
    cur_row = ts.start_point[0]
    while prev is not None and prev.type == "comment" and prev.end_point[0] >= cur_row - 1:
        parts.insert(0, ctx.text(prev))
        start, cur_row, prev = prev.start_byte, prev.start_point[0], prev.prev_named_sibling
    return "\n".join(parts), start


def _child(ts, field):
    return ts.child_by_field_name(field)


def _calls(ctx: _Ctx, ts, lang: str) -> list[list[str]]:
    """[(callee, receiver)] for every call site inside `ts` (for CALLS / CALLS_OPERATION)."""
    out, stack = [], [ts]
    call_type = "call" if lang == "python" else "call_expression"
    while stack:
        n = stack.pop()
        if n.type == call_type:
            fn = _child(n, "function")
            if fn is not None:
                callee, recv = _callee(ctx, fn)
                if callee:
                    out.append([callee, recv])
        stack.extend(n.children)
    return out


def _callee(ctx: _Ctx, fn) -> tuple[str, str]:
    t = fn.type
    if t in ("identifier", "field_identifier"):
        return ctx.text(fn), ""
    if t in ("field_expression",):                       # obj.f / obj->f
        f, a = _child(fn, "field"), _child(fn, "argument")
        return (ctx.text(f) if f else ""), (ctx.text(a) if a else "")
    if t == "attribute":                                  # python obj.f
        f, o = _child(fn, "attribute"), _child(fn, "object")
        return (ctx.text(f) if f else ""), (ctx.text(o) if o else "")
    if t in ("qualified_identifier", "scoped_identifier"):  # ns::f / Class::f
        full = ctx.text(fn)
        return full.rsplit("::", 1)[-1], full.rsplit("::", 1)[0] if "::" in full else ""
    if t == "template_function":
        n = _child(fn, "name")
        return (ctx.text(n).rsplit("::", 1)[-1] if n else ""), ""
    return "", ""


def _file_skeleton(ctx: _Ctx, root: Node) -> str:
    lines = [f"// file: {root.file}"] if ctx.lang != "python" else [f"# file: {root.file}"]
    for inc in root.attrs.get("includes", []):
        lines.append(f'#include "{inc}"' if ctx.lang != "python" else f"import {inc}")
    if root.doc:
        lines.append(root.doc)
    for c in root.children:
        lines.append(_member_line(c))
    return "\n".join(lines)


def _member_line(n: Node) -> str:
    sig = (n.signature or n.text.split("\n", 1)[0]).strip()
    return f"{sig}    // → {n.kind} {n.qualname}" if n.kind in (FUNCTION, METHOD, CLASS, NAMESPACE) else sig


# ── C / C++ ───────────────────────────────────────────────────────────────────
def _has_descendant(ts, node_type: str) -> bool:
    stack = list(ts.children)
    while stack:
        n = stack.pop()
        if n.type == node_type:
            return True
        if n.type != "lambda_expression":
            stack.extend(n.children)
    return False


def _include_guard(ctx: _Ctx, ts_root) -> str:
    """Name of the classic include guard (#ifndef X / #define X …) wrapping a header."""
    top = [n for n in ts_root.named_children if n.type != "comment"]
    if len(top) == 1 and top[0].type == "preproc_ifdef" and ctx.text(top[0]).startswith("#ifndef"):
        nm = _child(top[0], "name")
        body = [g for g in top[0].named_children if g != nm and g.type != "comment"]  # == not `is`: fresh wrappers
        if nm is not None and body and body[0].type == "preproc_def" and \
                ctx.text(_child(body[0], "name") or body[0]) == ctx.text(nm):
            return ctx.text(nm)
    return ""



_CLASS_SPECS = ("class_specifier", "struct_specifier", "union_specifier")
_TYPE_SPECS = ("struct_specifier", "union_specifier", "enum_specifier")
_PREPROC_BLOCKS = ("preproc_if", "preproc_ifdef", "preproc_else", "preproc_elif", "preproc_elifdef")


class _CFamily:
    def __init__(self, ctx: _Ctx):
        self.c = ctx
        self.cpp = ctx.lang == "cpp"
        self.guard = ""

    # name of a (possibly nested) declarator: int *(*foo)(…) → foo ; A::b → A::b
    def decl_name(self, d) -> str:
        while d is not None:
            if d.type in ("identifier", "field_identifier", "qualified_identifier", "destructor_name",
                          "operator_name", "type_identifier", "template_function", "operator_cast"):
                return self.c.text(d)
            nxt = _child(d, "declarator")
            if nxt is None:
                for ch in d.named_children:
                    if ch.type.endswith("declarator") or ch.type in ("identifier", "field_identifier",
                                                                      "qualified_identifier"):
                        nxt = ch
                        break
            d = nxt
        return ""

    def var_types(self, ts) -> dict:
        """{variable: type} for parameters and local declarations under `ts`."""
        out, stack = {}, [ts] if ts is not None else []
        while stack:
            n = stack.pop()
            if n.type in ("parameter_declaration", "declaration", "optional_parameter_declaration"):
                ty = _child(n, "type")
                for d in ([_child(n, "declarator")] if n.type != "declaration"
                          else [g for g in n.named_children if g.type.endswith("declarator") or g.type == "identifier"]):
                    nm = self.decl_name(d) if d is not None else ""
                    if nm and ty is not None:
                        out[nm] = self.c.text(ty)
            stack.extend(g for g in n.children if g.type not in ("lambda_expression",))
        return out

    def initializer(self, decl) -> list:
        """[[field_or_index, identifier], …] from `= { .f = fn, … }` / `= { fn1, fn2 }`."""
        if decl is None or decl.type != "init_declarator":
            return []
        val = _child(decl, "value")
        if val is None or val.type != "initializer_list":
            return []
        out = []
        for i, item in enumerate(val.named_children):
            if item.type == "initializer_pair":
                des = _child(item, "designator")
                v = _child(item, "value")
                key = self.c.text(des).lstrip(".") if des is not None else str(i)
                if v is not None and v.type in ("identifier", "qualified_identifier", "pointer_expression"):
                    out.append([key, self.c.text(v).lstrip("&")])
            elif item.type in ("identifier", "pointer_expression"):
                out.append([str(i), self.c.text(item).lstrip("&")])
        return out

    def has_function_declarator(self, d) -> bool:
        while d is not None:
            if d.type == "function_declarator":
                return True
            if d.type in ("init_declarator",):
                d = _child(d, "declarator")
                continue
            nxt = _child(d, "declarator")
            d = nxt
        return False

    def walk_scope(self, ts, parent: Node, scope: list[str], cond: str = "", extern_c=False, access=None):
        for ch in ts.named_children:
            self.visit(ch, parent, scope, cond, extern_c, access)

    def visit(self, ch, parent: Node, scope, cond, extern_c, access):
        t, c = ch.type, self.c
        if t == "preproc_include":
            p = _child(ch, "path")
            if p is not None:
                self._root(parent).attrs.setdefault("includes", []).append(c.text(p).strip('"<>'))
        elif t == "preproc_else":                         # condition supplied by the #if that owns it
            for g in ch.named_children:
                self.visit(g, parent, scope, cond, extern_c, access)
        elif t in _PREPROC_BLOCKS:
            cname = _child(ch, "name") or _child(ch, "condition")
            sub = (("!" if c.text(ch).startswith("#ifndef") else "") + c.text(cname)) if cname else ""
            if self.guard and sub == "!" + self.guard:
                sub = ""                                      # include guard: not a real condition
            body_cond = (f"{cond} && {sub}" if cond else sub) if sub else cond
            alt = _child(ch, "alternative")              # #else / #elif branch: outer condition only
            for g in ch.named_children:
                if g == cname:
                    continue
                if alt is not None and g == alt:          # #else / #elif: NOT this branch's condition
                    neg = f"!({sub})" if sub and not sub.startswith("!") else (sub[1:] if sub else "")
                    self.visit(g, parent, scope, f"{cond} && {neg}" if cond and neg else (neg or cond),
                               extern_c, access)
                else:                                     # body, including nested #if blocks
                    self.visit(g, parent, scope, body_cond, extern_c, access)
        elif t == "friend_declaration":
            inner = next((g for g in ch.named_children if g.type in ("function_definition", "template_declaration")),
                         None)
            if inner is not None and inner.type == "template_declaration":
                inner = next((g for g in inner.named_children if g.type == "function_definition"), None)
            if inner is not None:
                before = len(parent.children)
                self.function(inner, parent, scope, cond, extern_c, access, ch)
                if len(parent.children) > before:
                    parent.children[-1].attrs["friend"] = True
        elif t == "linkage_specification":
            body = _child(ch, "body")
            if body is not None and body.type == "declaration_list":
                self.walk_scope(body, parent, scope, cond, True, access)
            elif body is not None:
                self.visit(body, parent, scope, cond, True, access)
        elif t == "namespace_definition":
            self.namespace(ch, parent, scope, cond)
        elif t == "ERROR":
            # tree-sitter error recovery (often macro-heavy code): keep looking for valid
            # definitions inside; the completeness pass below catches anything left
            self.walk_scope(ch, parent, scope, cond, extern_c, access)
        elif t == "template_declaration":
            inner = ch
            while inner is not None and inner.type == "template_declaration":   # template<…> template<…>
                inner = next((g for g in inner.named_children if g.type in (
                    "template_declaration", "function_definition", "class_specifier", "struct_specifier",
                    "declaration", "alias_declaration", "concept_definition")), None)
            if inner is not None:
                self.definition(inner, parent, scope, cond, extern_c, access, span=ch)
        elif t in ("function_definition", "declaration", "field_declaration", "type_definition",
                   "struct_specifier", "class_specifier", "union_specifier", "enum_specifier",
                   "preproc_def", "preproc_function_def", "alias_declaration"):
            self.definition(ch, parent, scope, cond, extern_c, access)
        elif t == "expression_statement" and ch.named_children and ch.named_children[0].type in _TYPE_SPECS:
            self.definition(ch.named_children[0], parent, scope, cond, extern_c, access, span=ch)

    def _root(self, n: Node) -> Node:
        while n.parent is not None:
            n = n.parent
        return n

    def _attach(self, parent, node, ts, cond, extern_c, access, span=None):
        doc, dstart = _leading_comments(self.c, span or ts)
        if doc:
            node.doc = doc
            node.text = self.c.text(span or ts, start=dstart)
        if cond:
            node.attrs["ifdef"] = cond
        if extern_c:
            node.attrs["extern_c"] = True
        if access:
            node.visibility = access
        return parent.add(node)

    def namespace(self, ch, parent, scope, cond):
        nm = _child(ch, "name")
        name = self.c.text(nm) if nm is not None else "(anonymous)"
        body = _child(ch, "body")
        node = self.c.node(NAMESPACE, name, "::".join(scope + [name]), ch)
        self._attach(parent, node, ch, cond, False, None)
        if body is not None:
            self.walk_scope(body, node, scope + [name], cond)
        node.signature = f"namespace {name}"
        node.text = "\n".join([f"namespace {name} {{"] + ["  " + _member_line(m) for m in node.children] + ["}"])

    def definition(self, ch, parent, scope, cond, extern_c, access, span=None):
        c, t = self.c, ch.type
        outer = span or ch
        if t in ("preproc_def", "preproc_function_def"):
            nm = _child(ch, "name")
            name = c.text(nm) if nm else "?"
            if name == self.guard:
                return
            n = c.node(MACRO, name, name, outer)
            n.signature = c.text(ch).split("\n", 1)[0]
            self._attach(parent, n, ch, cond, extern_c, access, span)
            return
        if t == "function_definition":
            self.function(ch, parent, scope, cond, extern_c, access, outer)
            return
        if t in _CLASS_SPECS + ("enum_specifier",):
            self.type_or_class(ch, parent, scope, cond, extern_c, access, outer)
            return
        if t in ("declaration", "field_declaration", "type_definition", "alias_declaration"):
            # a declaration may carry a struct/class body: struct S {…} s;  typedef struct {…} T;
            spec = _child(ch, "type")
            if spec is not None and spec.type in _CLASS_SPECS + ("enum_specifier",) and \
                    _child(spec, "body") is not None:
                alias = self.decl_name(_child(ch, "declarator")) if t == "type_definition" else ""
                self.type_or_class(spec, parent, scope, cond, extern_c, access, outer, alias=alias)
                return
            decl = _child(ch, "declarator")
            name = self.decl_name(decl)
            if not name:
                return
            qual = "::".join(scope + [name]) if "::" not in name else name
            storage = [c.text(g) for g in ch.named_children if g.type == "storage_class_specifier"]
            if t in ("type_definition", "alias_declaration"):
                kind = TYPE
            elif self.has_function_declarator(decl):
                kind = FUNCTION_DECL
            else:
                kind = FIELD if t == "field_declaration" else VARIABLE
            n = c.node(kind, name.rsplit("::", 1)[-1], qual, outer)
            n.signature = c.text(ch).strip()
            if spec is not None:
                n.attrs["type"] = c.text(spec)
            init = self.initializer(decl)
            if init:
                n.attrs["init"] = init
            if "static" in storage and kind in (FUNCTION_DECL, VARIABLE) and parent.kind != CLASS:
                n.visibility = "private"
            self._attach(parent, n, ch, cond, extern_c, access, span)
            if "static" in storage and kind in (FUNCTION_DECL, VARIABLE) and parent.kind != CLASS:
                n.visibility = "private"

    def function(self, ch, parent, scope, cond, extern_c, access, outer):
        c = self.c
        decl = _child(ch, "declarator")
        name = self.decl_name(decl) or "?"
        in_class = parent.kind == CLASS
        prefix = "::".join(scope)
        qual = name if (not scope or name.startswith(prefix + "::")) else f"{prefix}::{name}"
        kind = METHOD if (in_class or "::" in name) else FUNCTION
        n = c.node(kind, name.rsplit("::", 1)[-1], qual, outer)
        body = _child(ch, "body")
        n.signature = c.text(outer, end=body.start_byte).strip() if body is not None else c.text(outer).strip()
        storage = [c.text(g) for g in ch.named_children if g.type == "storage_class_specifier"]
        if "static" in storage and not in_class and "::" not in name:
            n.visibility = "private"
        n.attrs["calls"] = _calls(c, body, c.lang) if body is not None else []
        var_types = self.var_types(decl)
        if body is not None:
            var_types.update(self.var_types(body))
        if var_types:
            n.attrs["var_types"] = var_types
        if "::" in name:
            n.attrs["owner"] = name.rsplit("::", 1)[0]        # out-of-class method: Class::method
        self._attach(parent, n, ch, cond, extern_c, access, outer if outer != ch else None)

    def type_or_class(self, spec, parent, scope, cond, extern_c, access, outer, alias=""):
        c = self.c
        nm = _child(spec, "name")
        name = c.text(nm) if nm is not None else (alias or "(anonymous)")
        body = _child(spec, "body")
        qual = "::".join(scope + [name])
        has_methods = body is not None and (any(
            g.type == "function_definition" or (g.type == "field_declaration" and
                                                self.has_function_declarator(_child(g, "declarator")))
            for g in self._members(body)) or _has_descendant(body, "function_definition"))
        is_class = spec.type == "class_specifier" or (self.cpp and has_methods)
        if not is_class or body is None:
            n = c.node(TYPE, name, qual, outer)
            n.signature = c.text(outer, end=body.start_byte).strip() if body is not None else c.text(outer).strip()
            if alias:
                n.attrs["typedef"] = alias
            self._attach(parent, n, spec, cond, extern_c, access, outer)
            return
        n = c.node(CLASS, name, qual, outer)
        bases = _child(spec, "base_class_clause") or next(
            (g for g in spec.named_children if g.type == "base_class_clause"), None)
        if bases is not None:
            n.attrs["bases"] = [c.text(b).split("<", 1)[0].strip() for b in bases.named_children
                                if b.type in ("type_identifier", "qualified_identifier", "template_type")]
        n.signature = c.text(outer, end=body.start_byte).strip()
        self._attach(parent, n, spec, cond, extern_c, access, outer)
        default_access = "private" if spec.type == "class_specifier" else "public"
        self.class_members(body, n, scope + [name], cond, default_access)
        # skeleton: header + members, method bodies elided
        lines = [n.signature + " {"]
        for m in n.children:
            vis = f"[{m.visibility}] " if m.visibility != "public" else ""
            lines.append(f"  {vis}{_member_line(m)}")
        lines.append("};")
        n.text = (n.doc + "\n" if n.doc else "") + "\n".join(lines)

    def _members(self, body):
        for g in body.named_children:
            if g.type in _PREPROC_BLOCKS:
                yield from self._members(g)
            elif g.type == "template_declaration":
                yield from (x for x in g.named_children if x.type in ("function_definition", "field_declaration"))
            else:
                yield g

    def class_members(self, body, cls: Node, scope, cond, access):
        for g in body.named_children:
            if g.type == "access_specifier":
                access = self.c.text(g).rstrip(":").strip()
            elif g.type in _PREPROC_BLOCKS:
                sub_cond = self.c.text(_child(g, "name") or _child(g, "condition") or g).split("\n")[0]
                self.class_members(g, cls, scope, f"{cond} && {sub_cond}" if cond else sub_cond, access)
            else:
                self.visit(g, cls, scope, cond, False, access)


# ── Python ────────────────────────────────────────────────────────────────────
class _Python:
    def __init__(self, ctx: _Ctx):
        self.c = ctx

    def docstring(self, block) -> str:
        if block is None:
            return ""
        for st in block.named_children:
            if st.type == "expression_statement" and st.named_children and st.named_children[0].type == "string":
                return self.c.text(st.named_children[0])
            break
        return ""

    def walk_block(self, block, parent: Node, scope, in_class: bool):
        c = self.c
        if parent.kind == FILE:
            parent.doc = self.docstring(block)
        for st in block.named_children:
            outer, target = st, st
            if st.type == "decorated_definition":
                target = _child(st, "definition") or st.named_children[-1]
            if target.type == "class_definition":
                self.klass(target, outer, parent, scope)
            elif target.type == "function_definition":
                self.function(target, outer, parent, scope, in_class)
            elif st.type in ("import_statement", "import_from_statement"):
                root = parent
                while root.parent is not None:
                    root = root.parent
                mods = [c.text(n) for n in st.named_children if n.type in ("dotted_name", "aliased_import",
                                                                          "relative_import")]
                root.attrs.setdefault("includes", []).extend(m.split(" as ")[0] for m in mods[:1] or [c.text(st)])
            elif st.type == "expression_statement" and st.named_children and \
                    st.named_children[0].type in ("assignment", "augmented_assignment") and \
                    parent.kind in (FILE, CLASS):
                lhs = _child(st.named_children[0], "left")
                if lhs is not None and lhs.type == "identifier":
                    name = c.text(lhs)
                    kind = FIELD if parent.kind == CLASS else VARIABLE
                    n = c.node(kind, name, ".".join(scope + [name]), st)
                    n.signature = c.text(st).split("\n", 1)[0]
                    n.visibility = "private" if name.startswith("_") else "public"
                    parent.add(n)
            elif st.type in ("if_statement", "try_statement", "with_statement", "for_statement",
                             "while_statement") and parent.kind == FILE:
                # module-level guarded definitions (if TYPE_CHECKING:, try/except imports, with …)
                stack = list(st.named_children)
                while stack:
                    blk = stack.pop(0)
                    if blk.type == "block":
                        self.walk_block(blk, parent, scope, in_class)
                    elif blk.type in ("else_clause", "elif_clause", "except_clause", "finally_clause",
                                      "except_group_clause"):
                        stack.extend(blk.named_children)

    def var_types(self, params, body) -> dict:
        """{name: type} from annotated parameters and `x = SomeType(...)` assignments."""
        out = {}
        for prm in (params.named_children if params is not None else []):
            if prm.type in ("typed_parameter", "typed_default_parameter"):
                ty = _child(prm, "type")
                nm = next((g for g in prm.named_children if g.type == "identifier"), None)
                if nm is not None and ty is not None:
                    out[self.c.text(nm)] = self.c.text(ty)
        stack = [body] if body is not None else []
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                lhs, rhs = _child(n, "left"), _child(n, "right")
                if lhs is not None and lhs.type == "identifier" and rhs is not None and rhs.type == "call":
                    fn = _child(rhs, "function")
                    if fn is not None and fn.type in ("identifier", "attribute"):
                        out[self.c.text(lhs)] = self.c.text(fn).rsplit(".", 1)[-1]
            if n.type not in ("function_definition", "class_definition"):
                stack.extend(n.children)
        return out

    def klass(self, ts, outer, parent, scope):
        c = self.c
        name = c.text(_child(ts, "name"))
        n = c.node(CLASS, name, ".".join(scope + [name]), outer)
        sup = _child(ts, "superclasses")
        if sup is not None:
            n.attrs["bases"] = [c.text(b) for b in sup.named_children if b.type in ("identifier", "attribute")]
        body = _child(ts, "body")
        n.signature = c.text(outer, end=body.start_byte).rstrip().rstrip(":") + ":"
        n.doc = self.docstring(body)
        n.visibility = "private" if name.startswith("_") else "public"
        parent.add(n)
        self.walk_block(body, n, scope + [name], in_class=True)
        lines = [n.signature]
        if n.doc:
            lines.append("    " + n.doc)
        lines += ["    " + _member_line(m) for m in n.children]
        n.text = "\n".join(lines)

    def function(self, ts, outer, parent, scope, in_class):
        c = self.c
        name = c.text(_child(ts, "name"))
        kind = METHOD if in_class else FUNCTION
        n = c.node(kind, name, ".".join(scope + [name]), outer)
        body = _child(ts, "body")
        n.signature = c.text(outer, end=body.start_byte).rstrip().rstrip(":")
        n.doc = self.docstring(body)
        n.visibility = "private" if name.startswith("_") and not name.startswith("__") else "public"
        n.attrs["calls"] = _calls(c, body, "python")
        vt = self.var_types(_child(ts, "parameters"), body)
        if vt:
            n.attrs["var_types"] = vt
        parent.add(n)
        # nested defs become children too; the parent's text still contains them whole
        for st in body.named_children:
            target = _child(st, "definition") if st.type == "decorated_definition" else st
            if target is not None and target.type == "function_definition":
                self.function(target, st, n, scope + [name], in_class=False)
            elif target is not None and target.type == "class_definition":
                self.klass(target, st, n, scope + [name])
