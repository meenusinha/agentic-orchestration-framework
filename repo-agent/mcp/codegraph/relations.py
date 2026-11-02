"""In-repo relations between nodes.

Exact (from parsing/build): CONTAINS, BUILDS, DEPENDS_ON, GENERATED_FROM (file level).
Name-resolved (confidence < 1 when ambiguous): INCLUDES/IMPORTS, DECLARES, CALLS,
INHERITS, TESTS, DOCUMENTS. Unresolved targets are kept with `dst_name`, which the
interface resolver (interfaces.py) uses to link code to IDL across repos.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from .model import (BUILDS, CALLS, CLASS, CONTAINS, DECLARES, DEPENDS_ON, DOC, DOC_SECTION, DOCUMENTS, FILE,
                    FUNCTION, FUNCTION_DECL, GENERATED_FROM, IDL_INTERFACE, IDL_OPERATION, IDL_TYPE, IMPORTS,
                    INCLUDES, INHERITS, METHOD, MODULE, TARGET, TESTS, TYPE, Edge, Node)

_CALLABLE = (FUNCTION, METHOD)
_SELF = {"this", "self", "*this", "obj", ""}


def build_edges(files: dict[str, Node], structure) -> tuple[list[Edge], list[Node]]:
    """files: rel path → file root node (already annotated with role/target).
    Returns edges and the build-target nodes created."""
    edges: list[Edge] = []
    all_nodes = [n for root in files.values() for n in root.walk()]
    by_name: dict[str, list[Node]] = defaultdict(list)
    by_qual: dict[str, list[Node]] = defaultdict(list)
    for n in all_nodes:
        if n.kind in (FILE, DOC):
            continue
        by_name[n.name].append(n)
        by_qual[n.qualname].append(n)

    # CONTAINS ────────────────────────────────────────────────────────────────
    for n in all_nodes:
        for c in n.children:
            edges.append(Edge(n.uid, c.uid, CONTAINS))

    # INCLUDES / IMPORTS ──────────────────────────────────────────────────────
    paths = list(files)
    includes_of: dict[str, set[str]] = defaultdict(set)
    for rel, root in files.items():
        for inc in root.attrs.get("includes", []):
            target = _resolve_include(rel, inc, paths, root.lang)
            et = IMPORTS if root.lang in ("python", "perl", "javascript", "typescript", "tsx", "java", "go",
                                          "rust", "csharp") else INCLUDES
            if target:
                edges.append(Edge(root.uid, files[target].uid, et))
                includes_of[rel].add(target)
            else:
                edges.append(Edge(root.uid, "", et, dst_name=inc, confidence=0.0))

    # DECLARES: header prototype → definition ─────────────────────────────────
    defs = defaultdict(list)
    for n in all_nodes:
        if n.kind in _CALLABLE:
            defs[n.qualname].append(n)
            if "::" in n.qualname:
                defs[n.qualname.split("::", 1)[-1]].append(n)
            defs.setdefault(n.name, [])
    for n in all_nodes:
        if n.kind != FUNCTION_DECL:
            continue
        cands = defs.get(n.qualname) or [d for d in by_name.get(n.name, []) if d.kind in _CALLABLE and
                                         (n.lang in ("c",) or d.lang in ("c",) or "::" not in n.qualname)]
        for d in cands[:3]:
            edges.append(Edge(n.uid, d.uid, DECLARES, confidence=1.0 if len(cands) == 1 else 0.5))

    # CALLS ───────────────────────────────────────────────────────────────────
    file_of = {}
    for rel, root in files.items():
        for n in root.walk():
            file_of[n.uid] = rel
    for n in all_nodes:
        if n.kind not in _CALLABLE:
            continue
        cls = _owner_class(n, by_qual)
        seen = set()
        for callee, recv in n.attrs.get("calls", []):
            key = (callee, recv)
            if key in seen or not callee:
                continue
            seen.add(key)
            cands = [c for c in by_name.get(callee, []) if c.kind in _CALLABLE + (FUNCTION_DECL,) and c is not n]
            pick = _pick(cands, n, cls, recv, file_of, includes_of)
            if pick:
                for c, conf in pick:
                    edges.append(Edge(n.uid, c.uid, CALLS, dst_name=callee, confidence=conf))
            else:
                edges.append(Edge(n.uid, "", CALLS, dst_name=f"{recv}.{callee}" if recv and recv not in _SELF
                                  else callee, confidence=0.0))

    # INHERITS ────────────────────────────────────────────────────────────────
    for n in all_nodes:
        for b in n.attrs.get("bases", []):
            simple = re.sub(r"<.*", "", b).split("::")[-1].split(".")[-1].strip()
            cands = [c for c in by_qual.get(b, []) + by_name.get(simple, []) if c.kind in (CLASS, IDL_INTERFACE,
                                                                                            TYPE)]
            if cands:
                edges.append(Edge(n.uid, cands[0].uid, INHERITS, dst_name=b,
                                  confidence=1.0 if len({c.uid for c in cands}) == 1 else 0.6))
            else:
                edges.append(Edge(n.uid, "", INHERITS, dst_name=b, confidence=0.0))

    # TESTS (evidence only): a test function calling product code ─────────────
    for e in [e for e in edges if e.type == CALLS and e.dst]:
        src = _node(all_nodes, e.src)
        dst = _node(all_nodes, e.dst)
        if src is not None and dst is not None and src.role == "test" and dst.role != "test":
            edges.append(Edge(src.uid, dst.uid, TESTS, dst_name=dst.qualname, confidence=e.confidence))

    # DOCUMENTS: doc sections that name a symbol ──────────────────────────────
    names = {k: v for k, v in by_name.items() if len(k) >= 4 and len({x.uid for x in v}) <= 3 and
             any(x.kind in (CLASS, FUNCTION, METHOD, IDL_INTERFACE, IDL_OPERATION, IDL_TYPE, TYPE, MODULE)
                 for x in v)}
    for n in all_nodes:
        if n.kind != DOC_SECTION:
            continue
        own = n.text if not n.children else n.text[:n.children[0].start_byte - n.start_byte]
        for word in set(re.findall(r"`([A-Za-z_][\w:.]*)`|\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+|\w+_\w+)\b", own)):
            w = (word[0] or word[1]).split("::")[-1].split(".")[-1].rstrip("()")
            for target in names.get(w, [])[:2]:
                if target.kind not in (DOC, DOC_SECTION):
                    edges.append(Edge(n.uid, target.uid, DOCUMENTS, dst_name=w, confidence=0.8))

    # BUILD TARGETS: target → file, target → linked target ───────────────────
    target_nodes = []
    by_target_name = {}
    for t in structure.targets.values():
        tn = Node(TARGET, t.name, f"target:{t.name}", t.defined_in, "build", 1, 1)
        tn.attrs.update({"target_kind": t.kind, "links": sorted(t.links)})
        tn.role = "build"
        tn.text = f"{t.kind} {t.name} (defined in {t.defined_in})\nsources: " + ", ".join(sorted(t.sources)) + \
            ("\nlinks: " + ", ".join(sorted(t.links)) if t.links else "")
        target_nodes.append(tn)
        by_target_name[t.name] = tn
        by_target_name[_libkey(t.name)] = tn
        for s in sorted(t.sources):
            if s in files:
                edges.append(Edge(tn.uid, files[s].uid, BUILDS))
    for tn in target_nodes:
        for link in tn.attrs["links"]:
            dep = by_target_name.get(link) or by_target_name.get(_libkey(link))
            edges.append(Edge(tn.uid, dep.uid if dep is not None and dep is not tn else "", DEPENDS_ON,
                              dst_name=link, confidence=1.0 if dep is not None else 0.0))

    # GENERATED_FROM (file level, same repo) ──────────────────────────────────
    for gen, idl in structure.generated_from.items():
        if gen in files and idl in files:
            edges.append(Edge(files[gen].uid, files[idl].uid, GENERATED_FROM))
    return _dedupe(edges), target_nodes


def _libkey(name: str) -> str:
    """libfoo.a / libfoo.so.1 / -lfoo / foo.lib → foo"""
    n = PurePosixPath(name).name
    n = re.sub(r"\.(a|so|dylib|lib|dll)(\.\d+)*$", "", n)
    return n[3:] if n.startswith("lib") and len(n) > 3 else n


def _node(nodes, uid, _cache={}):
    key = id(nodes)
    if _cache.get("key") != key:
        _cache.clear()
        _cache["key"] = key
        _cache["map"] = {n.uid: n for n in nodes}
    return _cache["map"].get(uid)


def _owner_class(n: Node, by_qual) -> Node | None:
    if n.parent is not None and n.parent.kind == CLASS:
        return n.parent
    owner = n.attrs.get("owner")
    if owner:
        for sep in ("::", "."):
            if sep in n.qualname:
                cls_q = n.qualname.rsplit(sep, 1)[0]
                for c in by_qual.get(cls_q, []):
                    if c.kind == CLASS:
                        return c
    return None


def _pick(cands, caller, cls, recv, file_of, includes_of):
    """Rank candidate callees: same class (self/this) > same file > included file > unique."""
    if not cands:
        return []
    defs_only = [c for c in cands if c.kind in _CALLABLE]
    if defs_only:                      # a call goes to the body, not to the header prototype
        cands = defs_only
    if cls is not None and recv in _SELF:
        own = [c for c in cands if c.parent is cls or (c.attrs.get("owner") and cls.name == c.attrs["owner"].split(
            "::")[-1])]
        if own:
            return [(own[0], 1.0)]
    here = file_of.get(caller.uid)
    same = [c for c in cands if file_of.get(c.uid) == here]
    if same:
        return [(same[0], 1.0)]
    inc = includes_of.get(here, set())
    via = [c for c in cands if file_of.get(c.uid) in inc]
    if len(via) == 1:
        return [(via[0], 0.9)]
    defs = [c for c in cands if c.kind in (FUNCTION, METHOD)]
    pool = defs or cands
    uniq = {c.uid: c for c in pool}
    if len(uniq) == 1:
        return [(pool[0], 0.8 if recv and recv not in _SELF else 0.9)]
    if len(uniq) <= 3:
        return [(c, 0.4) for c in uniq.values()]
    return []


def _resolve_include(rel: str, inc: str, paths: list[str], lang: str) -> str:
    inc = inc.strip().strip('"<>')
    if not inc:
        return ""
    base = PurePosixPath(rel).parent
    if lang in ("python",):
        mod = inc.split(" import ")[0].replace("from ", "").strip()
        rel_parts = mod.lstrip(".").replace(".", "/")
        cands = [f"{rel_parts}.py", f"{rel_parts}/__init__.py"]
        if mod.startswith("."):
            up = base
            for _ in range(len(mod) - len(mod.lstrip(".")) - 1):
                up = up.parent
            cands = [str(up / c) for c in cands] + cands
        for c in cands:
            for p in paths:
                if p == c or p.endswith("/" + c):
                    return p
        return ""
    if lang == "perl":
        c = inc.replace("::", "/") + ".pm"
        return next((p for p in paths if p.endswith(c)), "")
    direct = str(PurePosixPath(base) / inc)
    if direct in paths:
        return direct
    suffix = "/" + inc
    matches = [p for p in paths if p == inc or p.endswith(suffix)]
    return matches[0] if len(matches) == 1 else (min(matches, key=len) if matches else "")


def _dedupe(edges: list[Edge]) -> list[Edge]:
    seen, out = set(), []
    for e in edges:
        k = (e.src, e.dst, e.type, e.dst_name if not e.dst else "")
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
