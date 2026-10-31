"""Tolerant IDL-family parser: OMG/CORBA IDL, DDS IDL, MIDL/ODL, AIDL, protobuf and
Thrift services, ROS .msg/.srv, and unknown in-house dialects.

Rather than one grammar per dialect, it recognises the shapes they share:
  - `[annotations] keyword Name [: bases] { body } [;]`  → container
        module / namespace / package             → IDL module
        interface / service / component / ...    → IDL interface
        struct / union / enum / exception / ...  → IDL type (kept whole)
        an unknown keyword is an interface if its body declares operations,
        otherwise a type, so in-house dialects work without configuration
  - inside an interface: `[mods] ret name(params) [raises|throws|returns (...)] ;`
        → IDL operation; `[readonly] attribute T name;` → IDL attribute
Every node's text is the exact, complete source span (annotations and doc
comments included); nothing is split or truncated.
"""
from __future__ import annotations

import re

from .model import FILE, IDL_ATTRIBUTE, IDL_INTERFACE, IDL_OPERATION, IDL_TYPE, MODULE, Node

IDL_EXTENSIONS = {".idl", ".pidl", ".ridl", ".odl", ".midl", ".aidl", ".fidl", ".proto", ".thrift",
                  ".msg", ".srv", ".action", ".rdl", ".ddl"}

_MODULE_KW = {"module", "namespace", "package", "library", "library_block"}
_INTERFACE_KW = {"interface", "service", "component", "home", "porttype", "connector", "dispinterface",
                 "coclass", "protocol", "abstract", "local", "custom"}
_TYPE_KW = {"struct", "union", "enum", "exception", "bitmask", "bitset", "message", "valuetype",
            "eventtype", "typedef", "const", "native", "senum", "oneof"}
_OP_MODIFIERS = {"oneway", "rpc", "async", "sync", "static", "virtual", "stream"}
# words that introduce an operation in some dialects; recorded as the operation's kind
_OP_KEYWORDS = {"method", "rpc", "func", "function", "fn", "operation", "procedure", "call", "request",
                "event", "signal", "callback", "notification", "notify", "broadcast", "slot"}
_NOTIFY_KW = {"event", "signal", "callback", "notification", "notify", "broadcast"}
_STATEMENT_KW = _MODULE_KW | _INTERFACE_KW | _TYPE_KW | {"include", "import", "cpp_include", "syntax", "option"}
_OP_TRAILERS = {"raises", "throws", "returns", "context", "getraises", "setraises", "const", "override", "="}

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<lcomment>//[^\n]*)
  | (?P<bcomment>/\*.*?\*/)
  | (?P<pre>\#[^\n]*(?:\\\n[^\n]*)*)
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*|::[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<num>[0-9][0-9A-Za-z_.xX]*)
  | (?P<punct>[{}()\[\];,:<>=@.*&|+\-/~!?^%])
""", re.S | re.X)


class _Tok:
    __slots__ = ("kind", "text", "start", "end")

    def __init__(self, kind, text, start, end):
        self.kind, self.text, self.start, self.end = kind, text, start, end


def _tokenize(src: str):
    toks, docs, includes, pos = [], {}, [], 0
    pending_doc = None
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if m is None:                         # unknown character: skip it, stay tolerant
            pos += 1
            continue
        kind = m.lastgroup
        if kind in ("lcomment", "bcomment"):
            pending_doc = (m.start(), m.group()) if pending_doc is None else (pending_doc[0], pending_doc[1] + "\n" + m.group())
        elif kind == "pre":
            inc = re.match(r'#\s*(?:include|import)\s*[<"]([^>"]+)[>"]', m.group())
            if inc:
                includes.append(inc.group(1))
            pending_doc = None
        elif kind == "ws":
            if pending_doc is not None and m.group().count("\n") > 1:
                pending_doc = None            # a blank line detaches a comment from what follows
        else:
            if pending_doc is not None:
                docs[len(toks)] = pending_doc
                pending_doc = None
            toks.append(_Tok(kind, m.group(), m.start(), m.end()))
        pos = m.end()
    return toks, docs, includes


_BODY_STATEMENT = re.compile(r"\)\s*(?:const\s*)?\{[^{}]*?(?:return\b|=[^=]|\+\+|--)[^{}]*\}", re.S)


def looks_like_idl(text: str, name: str = "x.idl") -> bool:
    """Content sniff for unknown extensions: interface-like blocks that declare
    operations, and no function bodies with statements (which would mean code)."""
    if _BODY_STATEMENT.search(text):
        return False
    try:
        root = parse_idl(name, text)
    except Exception:
        return False
    return any(n.kind == IDL_INTERFACE and any(c.kind == IDL_OPERATION for c in n.children)
               for n in root.walk())


def parse_idl(rel_path: str, source: str, dialect_hint: str = "") -> Node:
    lines = source.count("\n") + 1
    root = Node(FILE, rel_path.rsplit("/", 1)[-1], rel_path, rel_path, "idl", 1, lines, 0, len(source.encode()))
    ext = "." + rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    if ext in (".msg", ".srv", ".action") and "{" not in source:
        _ros_message(root, rel_path, source, ext)
        root.text = _skeleton(root)
        return root
    toks, docs, includes = _tokenize(source)
    root.attrs["includes"] = includes
    p = _Parser(rel_path, source, toks, docs)
    p.body(0, len(toks), root, scope=[], in_interface=False)
    root.text = _skeleton(root)
    return root


class _Parser:
    def __init__(self, path, src, toks, docs):
        self.path, self.src, self.t, self.docs = path, src, toks, docs
        self._b = src.encode()

    def line(self, char_pos: int) -> int:
        return self.src.count("\n", 0, char_pos) + 1

    def mk(self, kind, name, qual, start_tok, end_tok) -> Node:
        """Node spanning tokens [start_tok, end_tok] inclusive, plus a doc comment directly above."""
        start = self.t[start_tok].start
        doc = self.docs.get(start_tok)
        if doc is not None:
            start = doc[0]
        end = self.t[end_tok].end
        n = Node(kind, name, qual, self.path, "idl", self.line(start), self.line(end),
                 len(self.src[:start].encode()), len(self.src[:end].encode()), self.src[start:end])
        n.doc = doc[1] if doc else ""
        return n

    def match(self, i, open_, close):
        depth = 0
        while i < len(self.t):
            if self.t[i].text == open_:
                depth += 1
            elif self.t[i].text == close:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(self.t) - 1

    def skip_annotations(self, i, end):
        """[uuid(…), oleautomation]  /  @topic(name="x")  — returns (index after, start index)."""
        start = i
        while i < end:
            if self.t[i].text == "[":
                i = self.match(i, "[", "]") + 1
            elif self.t[i].text == "@" and i + 1 < end:
                i += 2
                if i < end and self.t[i].text == "(":
                    i = self.match(i, "(", ")") + 1
            else:
                break
        return i, start

    def body(self, i, end, parent: Node, scope, in_interface):
        while i < end:
            if self.t[i].text in ("}", ";", ","):
                i += 1
                continue
            i, decl_start = self.skip_annotations(i, end)
            if i >= end:
                break
            # scan the declaration head up to '{', ';', ',' or a top-level '(' (operation)
            j, depth = i, 0
            while j < end:
                tx = self.t[j].text
                if j > i and depth == 0 and self.t[j].kind == "ident" and tx in _STATEMENT_KW and \
                        "\n" in self.src[self.t[j - 1].end:self.t[j].start] and \
                        self.t[j - 1].text not in (":", ",", "(", "<", "="):
                    break                      # previous line was an unterminated statement
                if tx in ("<",):
                    depth += 1
                elif tx == ">":
                    depth = max(0, depth - 1)
                if depth == 0 and tx in ("{", ";", "}") or (depth == 0 and tx == "," and in_interface):
                    break
                if depth == 0 and tx == "(" and in_interface:
                    break
                if depth == 0 and tx == "(" and not in_interface and self._is_op_head(i, j):
                    break
                j += 1
            if j >= end:
                break
            stop = self.t[j].text
            if stop not in ("{", ";", ",", "(", "}"):   # unterminated one-line statement: skip it
                i = j
                continue
            head = [x for x in self.t[i:j]]
            words = [x.text for x in head if x.kind == "ident"]
            if stop == "(":
                i = self.operation(decl_start, i, j, end, parent, scope)
            elif stop == "{":
                close = self.match(j, "{", "}")
                last = close + 1 if close + 1 < end and self.t[close + 1].text == ";" else close
                self.container(decl_start, i, j, close, last, words, head, parent, scope)
                i = last + 1
            else:                              # ';' or ',' : simple member / typedef / forward decl
                self.simple(decl_start, i, j, words, parent, scope, in_interface)
                i = j + 1

    def _is_op_head(self, i, j) -> bool:
        words = [x.text for x in self.t[i:j] if x.kind == "ident"]
        return bool(words) and words[0] in _OP_MODIFIERS

    def container(self, decl_start, i, j, close, last, words, head, parent, scope):
        kw = words[0] if words else ""
        kws = {w for w in words}
        # name: last identifier before ':' (bases) or '{'
        colon = next((k for k, x in enumerate(head) if x.text == ":"), None)
        name_toks = [x.text for x in (head[:colon] if colon is not None else head) if x.kind == "ident"]
        name = name_toks[-1] if name_toks else (kw or "(anonymous)")
        bases = [x.text for x in head[colon + 1:] if x.kind == "ident" and x.text not in
                 ("public", "private", "virtual", "extends", "truncatable")] if colon is not None else []
        if "extends" in words:                 # thrift: service A extends B
            k = words.index("extends")
            bases += words[k + 1:k + 2]
            name = words[k - 1]
        qual = "::".join(scope + [name])
        if kw in _MODULE_KW:
            kind = "module"
        elif kws & _INTERFACE_KW:
            kind = "interface"
        elif kw in _TYPE_KW or kws & _TYPE_KW:
            kind = "type"
        else:                                  # unknown dialect keyword: decide by contents
            kind = "interface" if self._declares_operations(j + 1, close) else "type"
        if kind == "type":
            n = self.mk(IDL_TYPE, name, qual, decl_start, last)
            n.signature = " ".join(x.text for x in head)
            n.attrs["keyword"] = kw
            parent.add(n)
            return
        n = self.mk(MODULE if kind == "module" else IDL_INTERFACE, name, qual, decl_start, last)
        n.signature = " ".join(x.text for x in head)
        n.attrs["keyword"] = kw
        if bases:
            n.attrs["bases"] = bases
        parent.add(n)
        self.body(j + 1, close, n, scope + [name], in_interface=(kind == "interface"))
        n.text = (n.doc + "\n" if n.doc else "") + n.signature + " {\n" + \
            "\n".join("  " + (c.signature or c.text.split("\n", 1)[0]).strip() for c in n.children) + "\n}"

    def _declares_operations(self, i, close) -> bool:
        depth = 0
        for k in range(i, close):
            tx = self.t[k].text
            if tx == "{":
                depth += 1
            elif tx == "}":
                depth -= 1
            elif tx == "(" and depth == 0 and k > i and self.t[k - 1].kind == "ident":
                return True
        return False

    def operation(self, decl_start, i, paren, end, parent, scope):
        names = [x for x in self.t[i:paren] if x.kind == "ident"]
        if not names:
            return paren + 1
        name = names[-1].text
        before = [x.text for x in self.t[i:paren - 1]]
        op_kw = next((w for w in before if w in _OP_KEYWORDS), "")
        ret = " ".join(w for w in before if w not in _OP_MODIFIERS and w not in _OP_KEYWORDS) or "void"
        k = self.match(paren, "(", ")")
        params = self.src[self.t[paren].start:self.t[k].end]
        # trailers: raises(...), throws (...), returns (...), const, = 0, { options }
        last = k
        k += 1
        while k < end:
            tx = self.t[k].text
            if tx == "returns" and k + 1 < end and self.t[k + 1].text == "(":   # proto: returns (Reply)
                close = self.match(k + 1, "(", ")")
                ret = self.src[self.t[k + 2].start:self.t[close - 1].end] if close > k + 2 else ret
                k = close
            elif tx == "-" and k + 1 < end and self.t[k + 1].text == ">" or tx == ":" and ret == "void":
                k += 2 if tx == "-" else 1                                       # -> T  /  : T
                rs = k
                while k < end and self.t[k].text not in (";", ",", "{", "}", "raises", "throws"):
                    k += 1
                ret = " ".join(x.text for x in self.t[rs:k]) or ret
                last = k - 1
                continue
            elif tx in _OP_TRAILERS or tx == "(" or self.t[k].kind == "num" or tx == "{":
                if tx == "(":
                    k = self.match(k, "(", ")")
                elif tx == "{":
                    k = self.match(k, "{", "}")
            else:
                break
            last = k
            k += 1
        if k < end and self.t[k].text in (";", ","):
            last = k
        n = self.mk(IDL_OPERATION, name, "::".join(scope + [name]), decl_start, last)
        n.signature = f"{ret} {name}{params}"
        n.attrs["returns"] = ret
        n.attrs["params"] = params
        if op_kw:
            n.attrs["op_kind"] = op_kw
            if op_kw in _NOTIFY_KW:
                n.attrs["direction"] = "server_to_client"
        if any(x.text == "oneway" for x in self.t[i:paren]):
            n.attrs["oneway"] = True
        parent.add(n)
        return last + 1

    def simple(self, decl_start, i, j, words, parent, scope, in_interface):
        if not words:
            return
        if "attribute" in words or "property" in words:
            name = words[-1]
            n = self.mk(IDL_ATTRIBUTE, name, "::".join(scope + [name]), decl_start, j)
            n.attrs["readonly"] = "readonly" in words
            n.signature = " ".join(words)
            parent.add(n)
        elif words[0] in ("typedef", "const", "native", "enum", "struct", "union", "using") and len(words) > 1:
            name = words[-1] if words[0] != "const" else (words[-1] if "=" not in
                                                           [x.text for x in self.t[i:j]] else
                                                           [x.text for x in self.t[i:j] if x.kind == "ident"]
                                                           [-1])
            if words[0] == "const":
                eq = next((k for k in range(i, j) if self.t[k].text == "="), None)
                cand = [x.text for x in self.t[i:eq or j] if x.kind == "ident"]
                name = cand[-1] if cand else name
            if len(words) == 2 and words[0] in ("struct", "union", "interface"):
                return                          # forward declaration
            n = self.mk(IDL_TYPE, name, "::".join(scope + [name]), decl_start, j)
            n.signature = self.src[self.t[i].start:self.t[j].end]
            n.attrs["keyword"] = words[0]
            parent.add(n)
        elif words[0] in ("interface", "service") and len(words) <= 3:
            return                              # forward declaration: interface Foo;
        elif in_interface and len(words) >= 2:
            name = words[-1]                    # data member inside an interface-like block
            n = self.mk(IDL_ATTRIBUTE, name, "::".join(scope + [name]), decl_start, j)
            n.signature = self.src[self.t[i].start:self.t[j].end]
            parent.add(n)


def _ros_message(root: Node, rel_path, source, ext):
    stem = rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if ext == ".msg":
        n = Node(IDL_TYPE, stem, stem, rel_path, "idl", 1, root.end_line, 0, root.end_byte, source)
        n.signature = f"message {stem}"
        root.add(n)
        return
    iface = Node(IDL_INTERFACE, stem, stem, rel_path, "idl", 1, root.end_line, 0, root.end_byte, source)
    iface.signature = f"service {stem}"
    root.add(iface)
    op = Node(IDL_OPERATION, stem, f"{stem}::{stem}", rel_path, "idl", 1, root.end_line, 0, root.end_byte, source)
    parts = source.split("---")
    op.signature = f"{stem}(request) -> response"
    op.attrs["params"] = parts[0].strip()
    op.attrs["returns"] = parts[1].strip() if len(parts) > 1 else ""
    iface.add(op)
    iface.text = f"service {stem} {{\n  {op.signature}\n}}"


def _skeleton(root: Node) -> str:
    lines = [f"// idl file: {root.file}"] + [f'#include "{i}"' for i in root.attrs.get("includes", [])]
    for c in root.children:
        lines.append((c.signature or c.text.split("\n", 1)[0]).strip())
    return "\n".join(lines)
