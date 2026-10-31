"""Search a repo's code graph, expand along edges, and follow IDL interfaces into peer repos.

- Hybrid ranking: vector similarity + keyword (FTS5 bm25), fused by reciprocal rank,
  weighted by role (interfaces up; generated/external down; tests never returned).
- Expansion: parent skeleton, callers/callees, TESTS evidence, IDL implements/calls,
  and the matching nodes in peer repos (definition / servers / clients).
- Token budget: whole units are added in rank order; a unit that does not fit is
  listed as "more", never cut.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from .model import (CALLS, CALLS_OPERATION, CLASS, CONTAINS, DECLARES, DOCUMENTS, FILE, FUNCTION, FUNCTION_DECL,
                    GENERATED_FROM, IDL_INTERFACE, IDL_OPERATION, IMPLEMENTS, METHOD, MODULE, NAMESPACE, TESTS)
from .store import Store

_ROLE_WEIGHT = {"interface": 1.15, "implementation": 1.0, "internal": 1.0, "library": 1.0, "docs": 1.0,
                "tooling": 0.85, "example": 0.7, "external": 0.6, "generated": 0.5, "build": 0.6, "config": 1.0,
                "test": 0.0}
_PARENT_KINDS = (CLASS, NAMESPACE, MODULE, IDL_INTERFACE)


class PeerView:
    """Read-only access to another repo's published registry and graph store."""

    def __init__(self, name: str, path: str):
        self.name, self.root = name, Path(path)
        self._db = None

    @property
    def registry(self) -> dict:
        try:
            return json.loads((self.root / ".rag_index" / "interfaces.json").read_text())
        except (OSError, ValueError):
            return {}

    def node(self, uid: str) -> dict | None:
        db = self._open()
        if db is None:
            return None
        row = db.execute("SELECT uid,file,kind,name,qualname,start_line,end_line,role,target,signature,text,parent "
                         "FROM nodes WHERE uid=?", (uid,)).fetchone()
        if row is None:
            return None
        keys = ("uid", "file", "kind", "name", "qualname", "start_line", "end_line", "role", "target", "signature",
                "text", "parent")
        return dict(zip(keys, row), repo=self.name)

    def tests_for(self, uid: str) -> list[dict]:
        db = self._open()
        if db is None:
            return []
        rows = db.execute("SELECT n.qualname, n.file, n.start_line FROM edges e JOIN nodes n ON n.uid = e.src "
                          "WHERE e.dst=? AND e.type='TESTS'", (uid,)).fetchall()
        return [{"test": q, "file": f, "line": l} for q, f, l in rows]

    def _open(self):
        if self._db is None:
            p = self.root / ".rag_index" / "graph.sqlite"
            if not p.exists():
                return None
            self._db = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=60)
        return self._db


class GraphSearch:
    def __init__(self, store: Store, repo: str, embed_query, peers: list[dict] | None = None):
        self.s, self.repo, self.embed_query = store, repo, embed_query
        self.peers = {p["name"]: PeerView(p["name"], p["path"]) for p in (peers or []) if p.get("name") != repo}
        self.uids, self.mat = store.load_vectors()
        self.meta = {r[0]: (r[1], r[2], r[3]) for r in
                     store.db.execute("SELECT uid, role, kind, parent FROM nodes")}

    # ── ranking ─────────────────────────────────────────────────────────────
    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        fused: dict[str, float] = {}
        if len(self.uids):
            q = np.asarray(self.embed_query(query), dtype=np.float32)
            scores = self.mat @ q
            top = np.argsort(-scores)[:60]
            for rank, i in enumerate(top):
                fused[self.uids[i]] = fused.get(self.uids[i], 0) + 1.0 / (60 + rank)
        for rank, (uid, _) in enumerate(self.s.keyword(query, limit=60)):
            fused[uid] = fused.get(uid, 0) + 1.0 / (60 + rank)
        ranked = []
        for uid, sc in fused.items():
            role, kind, _ = self.meta.get(uid, ("", "", ""))
            w = _ROLE_WEIGHT.get(role, 1.0)
            if w == 0 or kind == FILE:
                continue
            ranked.append((uid, sc * w))
        ranked.sort(key=lambda x: -x[1])
        # a class/namespace hit whose own member also matched → show the member (its context
        # already carries the class skeleton), so the most specific whole unit is returned
        score = dict(ranked)
        out, used = [], set()
        for uid, sc in ranked:
            if uid in used:
                continue
            kind = self.meta.get(uid, ("", "", ""))[1]
            if kind in _PARENT_KINDS:
                kids = [(c, score[c]) for c, (_, _, parent) in self.meta.items() if parent == uid and c in score]
                kids = [kv for kv in kids if kv[0] not in used]
                if kids:
                    best = max(kids, key=lambda kv: kv[1])
                    used.add(best[0])
                    out.append((best[0], sc))
                    continue
            used.add(uid)
            out.append((uid, sc))
        return out[:k]

    # ── expansion ───────────────────────────────────────────────────────────
    def context(self, uid: str) -> dict:
        n = self.s.node(uid)
        if n is None:
            return {}
        out = {"node": _public(n, self.repo)}
        parent = self.s.node(n["parent"]) if n["parent"] else None
        if parent and parent["kind"] in _PARENT_KINDS:
            out["parent"] = {"qualname": parent["qualname"], "kind": parent["kind"], "skeleton": parent["text"],
                             "file": parent["file"]}
        rel = {}
        for src, dst, t, name, conf in self.s.edges_from(uid, (CALLS, CALLS_OPERATION, IMPLEMENTS, GENERATED_FROM,
                                                              DECLARES)):
            rel.setdefault(t.lower(), []).append(self._ref(dst, name, conf))
        for src, dst, t, name, conf in self.s.edges_to(uid, (CALLS, TESTS, DECLARES, DOCUMENTS)):
            key = {"CALLS": "called_by", "TESTS": "tested_by", "DECLARES": "declared_by",
                   "DOCUMENTS": "documented_in"}[t]
            sn = self.s.node(src)
            if sn and not (key == "called_by" and sn["role"] == "test"):   # tests appear only as evidence
                rel.setdefault(key, []).append({"qualname": sn["qualname"], "file": sn["file"],
                                                "line": sn["start_line"], "confidence": conf})
        if rel:
            out["relations"] = {k: v[:8] for k, v in rel.items()}
        iface_ids = {r["name"].rsplit("::", 1)[0] if r.get("kind") == "op" else r["name"]
                     for r in rel.get("implements", []) + rel.get("calls_operation", [])}
        if n["kind"] in (IDL_INTERFACE, IDL_OPERATION):
            iface_ids.add(n["qualname"] if n["kind"] == IDL_INTERFACE else n["qualname"].rsplit("::", 1)[0])
        if iface_ids:
            out["interfaces"] = [self.trace_interface(i, include_text=False, op=_op_of(n)) for i in sorted(iface_ids)]
        return out

    def _ref(self, dst: str, name: str, conf: float) -> dict:
        if dst.startswith("@"):
            repo, _, uid = dst[1:].partition(":")
            return {"name": name, "repo": repo, "remote": True, "confidence": conf,
                    "kind": "op" if name.count("::") >= 2 or (name.count("::") == 1 and name.split("::")[-1][:1].islower())
                    else "interface"}
        n = self.s.node(dst) if dst else None
        if n is None:
            return {"name": name, "resolved": False, "confidence": conf}
        return {"name": n["qualname"], "file": n["file"], "line": n["start_line"], "kind": n["kind"],
                "confidence": conf}

    # ── cross-repo interface dossier ────────────────────────────────────────
    def _registries(self) -> dict:
        regs = {}
        own = self.s.dir / "interfaces.json"
        try:
            regs[self.repo] = json.loads(own.read_text())
        except (OSError, ValueError):
            pass
        for name, p in self.peers.items():
            r = p.registry
            if r:
                regs[name] = r
        return regs

    def _node_any(self, repo: str, uid: str) -> dict | None:
        if repo == self.repo:
            n = self.s.node(uid)
            return dict(n, repo=repo) if n else None
        p = self.peers.get(repo)
        return p.node(uid) if p else None

    def trace_interface(self, name: str, include_text: bool = True, op: str | None = None) -> dict:
        regs = self._registries()
        defs = [(repo, d) for repo, r in regs.items() for d in r.get("defines", [])
                if d["id"] == name or d["name"] == name or d["id"].endswith("::" + name)]
        if not defs:                                           # maybe an operation name
            defs = [(repo, d) for repo, r in regs.items() for d in r.get("defines", [])
                    if any(o["name"] == name for o in d["operations"])]
            op = op or (name if defs else None)
        if not defs:
            return {"interface": name, "found": False}
        repo_def, d = sorted(defs, key=lambda x: (x[1]["id"] != name, x[0]))[0]
        iid = d["id"]
        out = {"interface": iid, "found": True,
               "defined_in": {"repo": repo_def, "file": d["file"], "lines": d["lines"], "doc": d.get("doc", ""),
                              "fingerprint": d["fingerprint"]},
               "operations": [{"name": o["name"], "signature": o["signature"], "direction": o.get("direction", ""),
                               **({"text": o["text"]} if include_text else {})}
                              for o in d["operations"] if op is None or o["name"] == op],
               "providers": [], "consumers": [], "generated": [], "checks": []}
        if include_text:
            out["definition_text"] = d["text"]
        others = [(r, x) for r, x in defs if x["id"] == iid and r != repo_def]
        for r, x in others:
            if x["fingerprint"] != d["fingerprint"]:
                out["checks"].append(f"signature drift: {iid} in repo '{r}' ({x['file']}) differs from the "
                                     f"definition in '{repo_def}' ({d['file']})")
        implemented = set()
        for repo, r in regs.items():
            links = r.get("links", {})
            for l in links.get("implements", []):
                if l["interface"] != iid:
                    continue
                entry = {"repo": repo, "process": l.get("target", ""), "server_class": l["qualname"], "file": l["file"],
                         "lines": l["lines"], "confidence": l["confidence"], "operations": []}
                for opname, uid in l.get("ops", {}).items():
                    if op and opname != op:
                        continue
                    implemented.add(opname)
                    item = {"operation": opname}
                    node = self._node_any(repo, uid)
                    if node:
                        item.update({"qualname": node["qualname"], "file": node["file"],
                                     "lines": [node["start_line"], node["end_line"]]})
                        if include_text:
                            item["text"] = node["text"]
                        tests = (self.peers[repo].tests_for(uid) if repo in self.peers else
                                 [{"test": self.s.node(s)["qualname"], "file": self.s.node(s)["file"],
                                   "line": self.s.node(s)["start_line"]}
                                  for s, *_ in self.s.edges_to(uid, (TESTS,)) if self.s.node(s)])
                        if tests:
                            item["tested_by"] = tests
                    entry["operations"].append(item)
                out["providers"].append(entry)
            for l in links.get("consumes", []):
                if l["interface"] != iid or (op and l.get("op") != op):
                    continue
                entry = {"repo": repo, "process": l.get("target", ""), "caller": l["qualname"], "file": l["file"],
                         "lines": l["lines"], "operation": l.get("op"), "confidence": l["confidence"]}
                if include_text:
                    node = self._node_any(repo, l["uid"])
                    if node:
                        entry["text"] = node["text"]
                out["consumers"].append(entry)
            for l in links.get("generated", []):
                if l["interface"] == iid:
                    out["generated"].append({"repo": repo, "symbol": l["qualname"], "file": l["file"]})
        pending = sorted(n for n in self.peers if n not in regs)
        if pending:   # a peer that never published can't be ruled out as provider/consumer
            out["checks"].append(f"not indexed yet: {', '.join(pending)} — results may be incomplete "
                                 "(start those repos' servers so they publish their interface links)")
        if not op:
            missing = [o["name"] for o in d["operations"] if o["name"] not in implemented]
            if out["providers"] and missing:
                out["checks"].append(f"not implemented by any provider: {', '.join(missing)}")
            scope = "in the indexed repos" if pending else "in the connected repos"
            if not out["providers"]:
                out["checks"].append(f"no provider found {scope}")
            if not out["consumers"]:
                out["checks"].append(f"no consumer found {scope}")
        return out

    # ── impact ──────────────────────────────────────────────────────────────
    def impact_of(self, symbol: str, depth: int = 2) -> dict:
        nodes = self.s.find(symbol)
        regs_hit = self.trace_interface(symbol, include_text=False)
        out = {"symbol": symbol, "local": [], "interfaces": []}
        if regs_hit.get("found"):
            out["interfaces"].append(regs_hit)
        defined = {n["qualname"] for n in nodes if n["kind"] in (FUNCTION, METHOD)}
        nodes = [n for n in nodes if not (n["kind"] == FUNCTION_DECL and n["qualname"] in defined)]  # decl → its def
        for n in nodes[:5]:
            callers, frontier, seen = [], [n["uid"]], {n["uid"]}
            for level in range(depth):
                nxt = []
                for uid in frontier:
                    for src, *_ in self.s.edges_to(uid, (CALLS,)):
                        if src not in seen:
                            seen.add(src)
                            sn = self.s.node(src)
                            if sn:
                                callers.append({"qualname": sn["qualname"], "file": sn["file"], "line": sn["start_line"],
                                                "process": sn["target"], "level": level + 1, "role": sn["role"]})
                                nxt.append(src)
                frontier = nxt
            tests = [self.s.node(s) for s, *_ in self.s.edges_to(n["uid"], (TESTS,))]
            impl = [self._ref(d, nm, c) for _, d, _, nm, c in self.s.edges_from(n["uid"], (IMPLEMENTS,))]
            for c in callers:                  # a caller that implements an IDL operation carries the
                for cu in self.s.find(c["qualname"], limit=5):   # change to every client of that operation
                    impl += [dict(self._ref(d, nm, cf), via=c["qualname"])   # (declaration and definition
                             for _, d, _, nm, cf in self.s.edges_from(cu["uid"], (IMPLEMENTS,))]  # share a name)
            out["local"].append({"node": _public(n, self.repo, text=False),
                                 "callers": [c for c in callers if c["role"] != "test"],
                                 "tested_by": [{"test": t["qualname"], "file": t["file"]} for t in tests if t],
                                 "implements": impl,
                                 "processes": sorted({c["process"] for c in callers
                                                      if c["process"] and c["role"] != "test"} |
                                                     ({n["target"]} if n["target"] else set()))})
            seen_ops = set()
            for i in impl:
                if i.get("remote") or i.get("kind") in ("op", "interface", "idl_operation", "idl_interface"):
                    is_op = i.get("kind") in ("op", "idl_operation")
                    iid = i["name"].rsplit("::", 1)[0] if is_op else i["name"]
                    op = i["name"].rsplit("::", 1)[1] if is_op else None
                    if (iid, op) in seen_ops:
                        continue
                    seen_ops.add((iid, op))
                    t = self.trace_interface(iid, include_text=False, op=op)
                    if i.get("via"):
                        t["via"] = i["via"]
                    out["interfaces"].append(t)
        return out

    # ── assembly under a token budget (whole units only) ────────────────────
    def assemble(self, query: str, k: int = 5, budget_tokens: int = 6000) -> dict:
        hits = self.search(query, k=k * 2)
        result, used, more = [], 0, []
        seen_parents = set()
        shown = set()
        for uid, score in hits:
            ctx = self.context(uid)
            if not ctx:
                continue
            q = (ctx["node"]["qualname"], ctx["node"]["file"])
            if q in shown:                     # overloads: show one whole, list the rest under "more"
                more.append({"qualname": ctx["node"]["qualname"], "file": ctx["node"]["file"],
                             "id": ctx["node"]["id"], "tokens": ctx["node"]["tokens"], "note": "overload"})
                continue
            shown.add(q)
            cost = ctx["node"]["tokens"] + (0 if not ctx.get("parent") or ctx["parent"]["qualname"] in seen_parents
                                            else len(ctx["parent"]["skeleton"]) // 4)
            if used + cost > budget_tokens and result:
                more.append({"qualname": ctx["node"]["qualname"], "file": ctx["node"]["file"],
                             "id": ctx["node"]["id"], "tokens": ctx["node"]["tokens"]})
                continue
            if ctx.get("parent"):
                if ctx["parent"]["qualname"] in seen_parents:
                    ctx["parent"] = {"qualname": ctx["parent"]["qualname"], "skeleton": "(shown above)"}
                seen_parents.add(ctx["parent"]["qualname"])
            ctx["score"] = round(score, 4)
            result.append(ctx)
            used += cost
            if len(result) >= k:
                break
        return {"repo": self.repo, "query": query, "results": result, "more": more, "tokens": used}


def _op_of(n: dict) -> str | None:
    return n["name"] if n["kind"] == IDL_OPERATION else None


def _public(n: dict, repo: str, text: bool = True) -> dict:
    d = {"id": n["uid"], "repo": repo, "kind": n["kind"], "qualname": n["qualname"], "file": n["file"],
         "lines": [n["start_line"], n["end_line"]], "role": n["role"], "visibility": n["visibility"],
         "process": n["target"], "tokens": n["tokens"] or 0}
    if n.get("attrs", {}).get("ifdef"):
        d["ifdef"] = n["attrs"]["ifdef"]
    if n.get("attrs", {}).get("generated"):
        d["generated"] = True
    if n.get("attrs", {}).get("body_omitted"):
        d["note"] = n["attrs"]["body_omitted"]
    if text:
        d["text"] = n["text"]
    return d
