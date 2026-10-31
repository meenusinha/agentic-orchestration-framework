"""IDL interfaces across repos: what each repo defines, implements and consumes.

Every repo publishes `.rag_index/interfaces.json`:
  defines    — IDL interfaces with their operations (full text) and a fingerprint
  links      — its code resolved against all known interfaces (own + peers'):
               implements (server classes / C function tables → interface, method → op)
               consumes   (client call sites → op)
               generated  (generated stubs/skeletons → interface)
The orchestrator and every repo server read all registries to trace one interface
across the repo that defines it, the processes that serve it and the ones that call it.

Matching is by name, normalised for the shapes IDL compilers and middleware use:
POA_M::I, I_var/_ptr/_out, IProxy/IStub/ISkeleton/IServant/IImpl, M_I (C mapping),
M_I_op(...) C calls, I_EPV / I_Vtbl function tables, and an optional 'I' prefix.
Each link carries a confidence; ambiguous names fall back to operation overlap.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from .model import (CALLS_OPERATION, CLASS, CONSUMES, FUNCTION, FUNCTION_DECL, GENERATED_FROM, IDL_ATTRIBUTE,
                    IDL_INTERFACE, IDL_OPERATION, IMPLEMENTS, METHOD, MODULE, PROVIDES, TYPE, VARIABLE, Edge, Node)

_SUFFIXES = ("_var_type", "_var", "_ptr", "_out", "_slice", "_proxy", "Proxy", "_stub", "Stub", "_skel", "Skeleton",
             "Skel", "_servant", "Servant", "_impl", "Impl", "_client", "Client", "_server", "_EPV", "_epv", "EPV",
             "_vtbl", "Vtbl", "_ops", "Ops", "_tie", "_Tie", "Interface", "_i", "_base", "Base", "Handler",
             "_handler", "Ref", "_ref")
_GENERIC_OPS = {"get", "set", "init", "start", "stop", "run", "open", "close", "name", "reset", "read", "write",
                "update", "create", "destroy", "send", "recv", "call", "invoke", "execute", "process", "handle",
                "load", "save", "add", "remove", "clear", "size", "count", "find", "list", "next", "value"}


# ── definitions ───────────────────────────────────────────────────────────────
def collect_defines(files: dict[str, Node]) -> list[dict]:
    out = []
    for rel, root in files.items():
        for n in root.walk():
            if n.kind != IDL_INTERFACE:
                continue
            module = n.qualname.rsplit("::", 1)[0] if "::" in n.qualname else ""
            ops = [c for c in n.children if c.kind == IDL_OPERATION]
            attrs = [c for c in n.children if c.kind == IDL_ATTRIBUTE]
            canon = "\n".join(sorted(re.sub(r"\s+", " ", o.signature) for o in ops + attrs))
            out.append({
                "id": n.qualname, "name": n.name, "module": module, "file": rel, "uid": n.uid,
                "lines": [n.start_line, n.end_line], "bases": n.attrs.get("bases", []), "doc": n.doc,
                "fingerprint": hashlib.sha256(canon.encode()).hexdigest()[:16], "text": n.text,
                "operations": [{"name": o.name, "uid": o.uid, "signature": o.signature, "text": o.text,
                                "returns": o.attrs.get("returns", ""), "direction": o.attrs.get("direction", ""),
                                "lines": [o.start_line, o.end_line]} for o in ops],
                "attributes": [{"name": a.name, "uid": a.uid, "signature": a.signature} for a in attrs],
            })
    return out


# ── references found in code ──────────────────────────────────────────────────
def collect_references(files: dict[str, Node]) -> list[dict]:
    refs = []
    methods_by_owner = defaultdict(list)          # Class name → out-of-class method definitions
    funcs_by_name = defaultdict(list)
    for root in files.values():
        for n in root.walk():
            if n.kind in (METHOD, FUNCTION) and n.attrs.get("owner"):
                methods_by_owner[n.attrs["owner"].split("::")[-1]].append(n)
            if n.kind in (FUNCTION, METHOD):
                funcs_by_name[n.name].append(n)
    for rel, root in files.items():
        for n in root.walk():
            if n.role == "test":
                continue
            if n.kind in (CLASS, TYPE) and n.role == "generated":
                # generated stub/skeleton: identified by its own name (POA_Auth::IFoo, IFooProxy, …)
                refs.append(_ref("generated", n.qualname, n, rel))
            elif n.kind == CLASS and n.attrs.get("bases"):
                members = {c.name: c for c in n.children if c.kind in (METHOD, FUNCTION_DECL, FUNCTION)}
                for m in methods_by_owner.get(n.name, []):
                    members[m.name] = m                 # prefer the definition (has the body)
                # class declared in a header outside any build target → take the process from
                # the files that define its methods (server.h + server.cpp → authd)
                target = n.target or next((m.target for m in members.values() if m.target), "")
                for b in n.attrs["bases"]:
                    r = _ref("server", b, n, rel, ops={k: v.uid for k, v in members.items()})
                    r["target"] = target
                    refs.append(r)
            if n.kind == VARIABLE and n.attrs.get("init") and n.attrs.get("type"):
                ops = {}
                for field_name, fn in n.attrs["init"]:
                    tgt = funcs_by_name.get(fn.split("::")[-1], [])
                    if tgt:
                        ops[field_name if not field_name.isdigit() else tgt[0].name] = tgt[0].uid
                if ops:
                    refs.append(_ref("server_table", n.attrs["type"], n, rel, ops=ops))
            if n.kind in (FUNCTION, METHOD):
                vt = n.attrs.get("var_types", {})
                for callee, recv in n.attrs.get("calls", []):
                    base_recv = recv.split("->")[0].split(".")[0].lstrip("*&(").strip()
                    if base_recv in vt:
                        r = _ref("client", vt[base_recv], n, rel, op=callee)
                        r["fallback_weak"] = len(callee) >= 5 and callee not in _GENERIC_OPS
                        refs.append(r)
                    elif "_" in callee and len(callee) > 6 and not recv:
                        refs.append(_ref("client_c", callee, n, rel, op=callee))
                    elif recv and len(callee) >= 5 and callee not in _GENERIC_OPS:
                        refs.append(_ref("client_weak", "", n, rel, op=callee))
    return refs


def _ref(kind, type_, n: Node, rel, ops=None, op=None) -> dict:
    r = {"kind": kind, "type": type_, "uid": n.uid, "qualname": n.qualname, "file": rel,
         "lines": [n.start_line, n.end_line], "target": n.target, "node_kind": n.kind}
    if ops is not None:
        r["ops"] = ops
    if op is not None:
        r["op"] = op
    return r


# ── resolution ────────────────────────────────────────────────────────────────
def type_keys(raw: str) -> list[tuple[str, float]]:
    """Candidate interface ids for a type string, with match confidence."""
    t = raw.strip()
    inner = re.findall(r"<\s*([^<>]+?)\s*>", t)         # shared_ptr<IFooProxy>, resolve<Auth::IFoo>
    if inner:
        t = inner[-1]
    t = re.sub(r"\b(const|volatile|struct|class|typename|virtual|public|private|protected)\b", " ", t)
    t = t.replace("*", " ").replace("&", " ").strip().split()[-1] if t.strip() else ""
    t = t.lstrip(":").replace(".", "::")
    parts = [p for p in t.split("::") if p]
    if not parts:
        return []
    if parts[0].startswith("POA_"):                     # POA_Auth::IFoo → Auth::IFoo
        parts[0] = parts[0][4:]
    simple, changed = parts[-1], True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if simple.endswith(suf) and len(simple) > len(suf) + 1:
                simple, changed = simple[:-len(suf)], True
                break
    if simple.startswith("POA_"):
        simple = simple[4:]
    keys = []
    module = "::".join(parts[:-1])
    if module:
        keys.append((f"{module}::{simple}", 0.95))
    keys.append((simple, 0.9))
    if "_" in simple:                                   # C mapping: Auth_IFoo
        head, _, tail = simple.rpartition("_")
        keys.append((f"{head.replace('_', '::')}::{tail}", 0.9))
        keys.append((tail, 0.8))
    if not simple.startswith("I"):
        keys.append(("I" + simple, 0.7))
    return keys


class Resolver:
    def __init__(self, defines_by_repo: dict[str, list[dict]]):
        self.by_id, self.by_name = {}, defaultdict(list)
        self.op_index = defaultdict(list)               # op name → [(repo, iface)]
        for repo, defs in defines_by_repo.items():
            for d in defs:
                entry = dict(d, repo=repo)
                self.by_id.setdefault(d["id"], entry)
                self.by_name[d["name"]].append(entry)
                if d["name"].startswith("I"):
                    self.by_name[d["name"][1:]].append(entry)
                for o in d["operations"]:
                    self.op_index[o["name"]].append(entry)

    def match_type(self, raw: str):
        for key, conf in type_keys(raw):
            if key in self.by_id:
                return self.by_id[key], conf
            cands = {e["id"]: e for e in self.by_name.get(key.split("::")[-1], [])}
            if len(cands) == 1:
                return next(iter(cands.values())), conf * (0.95 if "::" in key else 1.0)
            if len(cands) > 1 and "::" in key:          # module-qualified key disambiguates
                mod = key.rsplit("::", 1)[0]
                for e in cands.values():
                    if e["module"].endswith(mod):
                        return e, conf
        return None, 0.0

    def match_c_call(self, name: str):
        """Auth_IPasswordService_checkStrength → (iface, op)."""
        parts = name.split("_")
        for i in range(len(parts) - 1, 0, -1):
            prefix, op = "_".join(parts[:i]), "_".join(parts[i:])
            iface, conf = self.match_type(prefix)
            if iface is not None and any(o["name"] == op for o in iface["operations"]):
                return iface, op, conf
        return None, None, 0.0

    def resolve(self, refs: list[dict]) -> dict:
        implements, consumes, generated = [], [], []
        for r in refs:
            k = r["kind"]
            if k in ("server", "server_table", "generated"):
                iface, conf = self.match_type(r["type"])
                if iface is None:
                    continue
                if k == "generated":
                    generated.append(_link(r, iface, conf))
                    continue
                op_names = {o["name"] for o in iface["operations"]}
                ops = {name: uid for name, uid in r.get("ops", {}).items() if name in op_names}
                if k == "server" and r.get("ops") and not ops and conf < 0.9:
                    continue                            # weak name match with no overlapping operations
                link = _link(r, iface, min(1.0, conf + (0.05 if ops else 0)))
                link["ops"] = ops
                implements.append(link)
                # IDL inheritance: implementing a derived interface implements its bases too
                seen, todo = {iface["id"]}, list(iface.get("bases", []))
                while todo:
                    b = todo.pop()
                    base, bconf = self.match_type(b)
                    if base is None or base["id"] in seen:
                        continue
                    seen.add(base["id"])
                    bops = {o["name"]: r.get("ops", {})[o["name"]] for o in base["operations"]
                            if o["name"] in r.get("ops", {})}
                    blink = _link(r, base, round(min(link["confidence"], bconf), 2))
                    blink["ops"], blink["via"] = bops, iface["id"]
                    implements.append(blink)
                    todo += base.get("bases", [])
            elif k == "client":
                iface, conf = self.match_type(r["type"])
                if iface is not None and any(o["name"] == r["op"] for o in iface["operations"]):
                    consumes.append(dict(_link(r, iface, conf), op=r["op"]))
                elif r.get("fallback_weak"):        # e.g. svc = connect("AuthService"): type unknown
                    owners = {e["id"]: e for e in self.op_index.get(r["op"], [])}
                    if len(owners) == 1:
                        consumes.append(dict(_link(r, next(iter(owners.values())), 0.5), op=r["op"]))
            elif k == "client_c":
                iface, op, conf = self.match_c_call(r["op"])
                if iface is not None:
                    consumes.append(dict(_link(r, iface, conf), op=op))
            elif k == "client_weak":
                owners = {e["id"]: e for e in self.op_index.get(r["op"], [])}
                if len(owners) == 1:                    # op name unique across all known interfaces
                    consumes.append(dict(_link(r, next(iter(owners.values())), 0.5), op=r["op"]))
        return {"implements": _best(implements), "consumes": _best(consumes, key_op=True),
                "generated": _best(generated)}


def _link(r, iface, conf):
    return {"interface": iface["id"], "interface_repo": iface["repo"], "uid": r["uid"], "qualname": r["qualname"],
            "file": r["file"], "lines": r["lines"], "target": r.get("target", ""), "kind": r["kind"],
            "confidence": round(conf, 2)}


def _best(links, key_op=False):
    best = {}
    for l in links:
        k = (l["uid"], l["interface"], l.get("op") if key_op else None)
        if k not in best or l["confidence"] > best[k]["confidence"]:
            best[k] = l
    return list(best.values())


def link_edges(repo: str, resolved: dict, defines_by_repo: dict[str, list[dict]]) -> list[Edge]:
    """Graph edges for this repo's resolved links. Remote ends are '@repo:uid'."""
    op_uid = {}
    for r, defs in defines_by_repo.items():
        for d in defs:
            op_uid[(d["id"], None)] = (r, d["uid"])
            for o in d["operations"]:
                op_uid[(d["id"], o["name"])] = (r, o["uid"])
    def ref(iface, op=None):
        r, uid = op_uid.get((iface, op), op_uid.get((iface, None), ("", "")))
        return uid if r == repo else f"@{r}:{uid}"
    edges = []
    for l in resolved["implements"]:
        edges.append(Edge(l["uid"], ref(l["interface"]), IMPLEMENTS, dst_name=l["interface"],
                          confidence=l["confidence"]))
        for op, impl_uid in l.get("ops", {}).items():
            edges.append(Edge(impl_uid, ref(l["interface"], op), IMPLEMENTS, dst_name=f"{l['interface']}::{op}",
                              confidence=l["confidence"]))
        if l["target"]:
            edges.append(Edge(f"target:{l['target']}", ref(l["interface"]), PROVIDES, dst_name=l["interface"],
                              confidence=l["confidence"]))
    for l in resolved["consumes"]:
        edges.append(Edge(l["uid"], ref(l["interface"], l["op"]), CALLS_OPERATION,
                          dst_name=f"{l['interface']}::{l['op']}", confidence=l["confidence"]))
        if l["target"]:
            edges.append(Edge(f"target:{l['target']}", ref(l["interface"]), CONSUMES, dst_name=l["interface"],
                              confidence=l["confidence"]))
    for l in resolved["generated"]:
        edges.append(Edge(l["uid"], ref(l["interface"]), GENERATED_FROM, dst_name=l["interface"],
                          confidence=l["confidence"]))
    return edges


def write_registry(path, repo: str, defines: list[dict], resolved: dict) -> None:
    from .store import write_private_text
    write_private_text(path, json.dumps({"format": 1, "repo": repo, "defines": defines, "links": resolved},
                                        indent=1, ensure_ascii=False))
