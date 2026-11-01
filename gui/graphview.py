"""Read-only tree view of the knowledge graph for the setup GUI. Standard library only.

Every repo's graph is <repo>/.rag_index/graph.sqlite (built by the GraphRAG step). The tree is

  repository
  ├── Processes & libraries        (build targets)
  ├── IDL interfaces               (defined in this repo)
  └── folders → files → namespaces / classes / modules → methods, functions, fields, IDL operations …

Nodes are loaded one level at a time. `detail()` returns a node's complete text and all of
its links — including links from and to other repos (stored as "@repo:uid"). Databases are
opened read-only (mode=ro); nothing is ever written.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path, PurePosixPath

_COLS = ("uid", "file", "kind", "name", "qualname", "lang", "start_line", "end_line", "parent", "role",
         "visibility", "target", "signature", "doc", "text", "attrs")
_LIST_COLS = ("uid", "file", "kind", "name", "qualname", "start_line", "end_line", "role", "target", "signature")
EDGE_LABELS = {   # type → (outgoing label, incoming label)
    "CALLS": ("calls", "called by"),
    "CALLS_OPERATION": ("calls IDL operation", "called through IDL by"),
    "IMPLEMENTS": ("implements", "implemented by"),
    "PROVIDES": ("serves", "served by"),
    "CONSUMES": ("uses", "used by"),
    "INHERITS": ("inherits from", "inherited by"),
    "DECLARES": ("declares", "declared by"),
    "INCLUDES": ("includes", "included by"),
    "IMPORTS": ("imports", "imported by"),
    "TESTS": ("tests", "tested by"),
    "DOCUMENTS": ("documents", "documented by"),
    "BUILDS": ("builds", "built into"),
    "DEPENDS_ON": ("depends on", "needed by"),
    "GENERATED_FROM": ("generated from", "generates"),
}
_UID = re.compile(r"^[0-9a-f]{8,64}$")
_FIND_ORDER = {"target": 0, "idl_interface": 0, "class": 1, "idl_operation": 1, "idl_attribute": 1, "module": 2,
               "method": 2, "function": 2, "type": 3, "idl_type": 3, "macro": 3, "field": 4, "variable": 4,
               "namespace": 4, "function_decl": 6, "doc_section": 7, "text": 8}


class GraphView:
    def __init__(self, repos: list[dict]):
        """repos: [{"name", "path"}] — the configured repositories."""
        self.repos = {r["name"]: Path(r["path"]).expanduser() for r in repos}
        self._db: dict[str, sqlite3.Connection] = {}

    # ── plumbing ──────────────────────────────────────────────────────────────
    def db(self, repo: str) -> sqlite3.Connection:
        if repo not in self.repos:
            raise ValueError(f"unknown repository {repo!r}")
        if repo not in self._db:
            path = self.repos[repo] / ".rag_index" / "graph.sqlite"
            if not path.exists():
                raise ValueError(f"{repo} has no knowledge graph yet — create the GraphRAG first")
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False,
                                  timeout=30)
            con.row_factory = sqlite3.Row
            self._db[repo] = con
        return self._db[repo]

    def close(self):
        for con in self._db.values():
            con.close()
        self._db.clear()

    def _rows(self, repo, sql, args=()) -> list[dict]:
        return [dict(r) for r in self.db(repo).execute(sql, args).fetchall()]

    def _brief(self, repo: str, uid: str) -> dict | None:
        """Short description of a node, resolving "@peer:uid" and "target:name" references."""
        if uid.startswith("@"):
            peer, _, puid = uid[1:].partition(":")
            if peer not in self.repos:
                return {"repo": peer, "uid": puid, "qualname": "(not connected)", "kind": "", "missing": True}
            try:
                return self._brief(peer, puid)
            except ValueError:
                return {"repo": peer, "uid": puid, "qualname": "(no graph)", "kind": "", "missing": True}
        if uid.startswith("target:"):
            r = self._rows(repo, "SELECT uid FROM nodes WHERE kind='target' AND name=?", (uid[7:],))
            if not r:
                return {"repo": repo, "uid": uid, "qualname": uid[7:], "kind": "target"}
            uid = r[0]["uid"]
        r = self._rows(repo, f"SELECT {','.join(_LIST_COLS)} FROM nodes WHERE uid=?", (uid,))
        return dict(r[0], repo=repo) if r else None

    # ── overview ──────────────────────────────────────────────────────────────
    def overview(self) -> list[dict]:
        out = []
        for name, path in self.repos.items():
            item = {"repo": name, "path": str(path), "ok": False}
            try:
                con = self.db(name)
            except ValueError as e:
                out.append(dict(item, error=str(e)))
                continue
            kinds = dict(con.execute("SELECT kind, count(*) FROM nodes GROUP BY kind").fetchall())
            item.update(ok=True, files=con.execute("SELECT count(*) FROM files").fetchone()[0],
                        nodes=sum(kinds.values()), kinds=kinds,
                        links=con.execute("SELECT count(*) FROM edges").fetchone()[0],
                        cross_repo=con.execute("SELECT count(*) FROM edges WHERE dst LIKE '@%'").fetchone()[0])
            out.append(item)
        return out

    # ── tree ──────────────────────────────────────────────────────────────────
    def children(self, repo: str, node: str = "") -> list[dict]:
        """Children of a tree node. node: "" (repo root) | "dir:<path>" | "group:targets" |
        "group:interfaces" | a node uid."""
        con = self.db(repo)
        if node in ("", "dir:"):
            out = []
            n_t = con.execute("SELECT count(*) FROM nodes WHERE kind='target'").fetchone()[0]
            n_i = con.execute("SELECT count(*) FROM nodes WHERE kind='idl_interface'").fetchone()[0]
            if n_t:
                out.append({"id": "group:targets", "kind": "group", "name": "Processes & libraries",
                            "count": n_t, "has_children": True})
            if n_i:
                out.append({"id": "group:interfaces", "kind": "group", "name": "IDL interfaces defined here",
                            "count": n_i, "has_children": True})
            return out + self._dir(repo, "")
        if node.startswith("dir:"):
            return self._dir(repo, node[4:])
        if node == "group:targets":
            rows = self._rows(repo, f"SELECT {','.join(_LIST_COLS)} FROM nodes WHERE kind='target' ORDER BY name")
            return [self._item(repo, r) for r in rows]
        if node == "group:interfaces":
            rows = self._rows(repo, f"SELECT {','.join(_LIST_COLS)} FROM nodes WHERE kind='idl_interface' "
                                    "ORDER BY qualname")
            return [self._item(repo, r) for r in rows]
        if not _UID.match(node):
            raise ValueError("bad node id")
        rows = self._rows(repo, f"SELECT {','.join(_LIST_COLS)} FROM nodes WHERE parent=? "
                                "ORDER BY start_line, name", (node,))
        return [self._item(repo, r) for r in rows]

    def _item(self, repo, r) -> dict:
        n = self.db(repo).execute("SELECT count(*) FROM nodes WHERE parent=?", (r["uid"],)).fetchone()[0]
        lines = f"{r['start_line']}–{r['end_line']}" if r.get("start_line") else ""
        return {"id": r["uid"], "kind": r["kind"], "name": r["name"] or r["qualname"] or r["file"],
                "qualname": r["qualname"], "role": r["role"], "target": r["target"], "file": r["file"],
                "lines": lines, "count": n, "has_children": n > 0}

    def _dir(self, repo: str, prefix: str) -> list[dict]:
        con = self.db(repo)
        prefix = prefix.strip("/")
        like = (prefix + "/%") if prefix else "%"
        rows = con.execute("SELECT path, role, lang, uid FROM files WHERE path LIKE ? ORDER BY path",
                           (like,)).fetchall()
        dirs, files = {}, []
        for path, role, lang, uid in rows:
            rest = path[len(prefix) + 1:] if prefix else path
            if "/" in rest:
                d = rest.split("/", 1)[0]
                e = dirs.setdefault(d, {"files": 0, "roles": {}})
                e["files"] += 1
                e["roles"][role] = e["roles"].get(role, 0) + 1
            else:
                fuid = uid or (con.execute("SELECT uid FROM nodes WHERE kind='file' AND file=?",
                                           (path,)).fetchone() or [None])[0]
                n = con.execute("SELECT count(*) FROM nodes WHERE parent=?", (fuid,)).fetchone()[0] if fuid else 0
                files.append({"id": fuid or f"file:{path}", "kind": "file", "name": rest, "file": path,
                              "role": role, "lang": lang, "count": n, "has_children": n > 0})
        out = []
        for d, e in sorted(dirs.items()):
            role = max(e["roles"].items(), key=lambda kv: kv[1])[0] if e["roles"] else ""
            out.append({"id": "dir:" + (f"{prefix}/{d}" if prefix else d), "kind": "folder", "name": d + "/",
                        "role": role, "mixed": len(e["roles"]) > 1, "count": e["files"], "has_children": True})
        return out + files

    # ── details ───────────────────────────────────────────────────────────────
    def detail(self, repo: str, uid: str) -> dict:
        if uid.startswith("target:"):
            b = self._brief(repo, uid)
            uid = b["uid"] if b else uid
        if not _UID.match(uid):
            raise ValueError("bad node id")
        rows = self._rows(repo, f"SELECT {','.join(_COLS)} FROM nodes WHERE uid=?", (uid,))
        if not rows:
            raise ValueError("no such node")
        n = rows[0]
        n["attrs"] = json.loads(n["attrs"] or "{}")
        n["repo"] = repo
        out_links, in_links = {}, {}
        refs = [uid] + ([f"target:{n['name']}"] if n["kind"] == "target" else [])
        for ref in refs:
            for e in self._rows(repo, "SELECT src, dst, type, dst_name, confidence FROM edges WHERE src=?", (ref,)):
                if e["type"] == "CONTAINS":
                    continue
                b = self._brief(repo, e["dst"]) or {"repo": repo, "uid": e["dst"], "qualname": e["dst_name"] or
                                                   e["dst"], "kind": "", "missing": True}
                out_links.setdefault(EDGE_LABELS.get(e["type"], (e["type"].lower(), ""))[0], []).append(
                    dict(b, confidence=e["confidence"], via=e["dst_name"]))
            for e in self._rows(repo, "SELECT src, dst, type, dst_name, confidence FROM edges WHERE dst=?", (ref,)):
                if e["type"] == "CONTAINS":
                    continue
                b = self._brief(repo, e["src"])
                if b:
                    in_links.setdefault(EDGE_LABELS.get(e["type"], ("", e["type"].lower()))[1], []).append(
                        dict(b, confidence=e["confidence"]))
        for peer in self.repos:                         # links from other repos into this node
            if peer == repo:
                continue
            try:
                rows = self._rows(peer, "SELECT src, type, confidence FROM edges WHERE dst=?", (f"@{repo}:{uid}",))
            except ValueError:
                continue
            for e in rows:
                b = self._brief(peer, e["src"])
                if b:
                    in_links.setdefault(EDGE_LABELS.get(e["type"], ("", e["type"].lower()))[1], []).append(
                        dict(b, confidence=e["confidence"]))
        for d in (out_links, in_links):
            for k in d:
                seen, uniq = set(), []
                for x in d[k]:
                    key = (x.get("repo"), x.get("uid"))
                    if key not in seen:
                        seen.add(key)
                        uniq.append(x)
                d[k] = sorted(uniq, key=lambda x: (x.get("repo") != repo, x.get("qualname") or ""))
        n["links_out"], n["links_in"] = out_links, in_links
        n["path"] = self.ancestry(repo, n)
        n["is_container"] = n["kind"] in ("file", "module", "namespace", "class", "idl_interface", "doc", "target")
        return n

    def ancestry(self, repo: str, n: dict) -> list[str]:
        """Tree ids from the repo root down to this node (to reveal it in the tree)."""
        chain, cur, guard = [], n, 0
        while cur and cur.get("parent") and guard < 50:
            chain.append(cur["parent"])
            r = self._rows(repo, "SELECT uid, parent, kind, file FROM nodes WHERE uid=?", (cur["parent"],))
            cur = r[0] if r else None
            guard += 1
        chain.reverse()
        if n["kind"] == "target":
            return ["group:targets"]
        dirs = []
        parts = PurePosixPath(n.get("file") or "").parts[:-1]
        for i in range(len(parts)):
            dirs.append("dir:" + "/".join(parts[:i + 1]))
        return dirs + chain

    def find(self, query: str, limit: int = 40) -> list[dict]:
        q = query.strip()
        if not q:
            return []
        out = []
        for repo in self.repos:
            try:
                rows = self._rows(repo, f"SELECT {','.join(_LIST_COLS)} FROM nodes WHERE kind NOT IN ('file') AND "
                                        "(name = ? OR qualname = ? OR name LIKE ? OR qualname LIKE ?) "
                                        "ORDER BY (name = ?) DESC, length(qualname) LIMIT ?",
                                  (q, q, f"%{q}%", f"%{q}%", q, limit))
            except ValueError:
                continue
            out += [dict(r, repo=repo) for r in rows]
        out.sort(key=lambda r: (r["name"] != q, _FIND_ORDER.get(r["kind"], 5), len(r["qualname"] or "")))
        return out[:limit]
