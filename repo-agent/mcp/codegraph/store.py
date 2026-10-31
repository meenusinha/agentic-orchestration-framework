"""Private, local graph store: <repo>/.rag_index/graph.sqlite (+ interfaces.json).

- SQLite from the Python standard library: no server, no network, no pickle.
- Created owner-only (directory 0700, files 0600, including SQLite's -wal/-shm).
- Writes happen in one transaction under an exclusive lock, so several editor
  windows opening the same repo never see a half-built index.
- Embeddings are float32 BLOBs keyed by the node text's SHA-256, so unchanged
  nodes are never re-embedded.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta  (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT, lang TEXT, role TEXT, targets TEXT,
                                  generated TEXT, parse_errors INTEGER, uid TEXT);
CREATE TABLE IF NOT EXISTS nodes (uid TEXT PRIMARY KEY, file TEXT, kind TEXT, name TEXT, qualname TEXT, lang TEXT,
                                  start_line INTEGER, end_line INTEGER, parent TEXT, role TEXT, visibility TEXT,
                                  target TEXT, signature TEXT, doc TEXT, text TEXT, attrs TEXT, text_sha TEXT,
                                  tokens INTEGER);
CREATE INDEX IF NOT EXISTS nodes_file ON nodes(file);
CREATE INDEX IF NOT EXISTS nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS nodes_qual ON nodes(qualname);
CREATE TABLE IF NOT EXISTS edges (src TEXT, dst TEXT, type TEXT, dst_name TEXT, confidence REAL);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, type);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);
CREATE TABLE IF NOT EXISTS embeddings (text_sha TEXT PRIMARY KEY, dim INTEGER, vec BLOB);
CREATE TABLE IF NOT EXISTS iface_refs (json TEXT);
"""
_FTS = """CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
             uid UNINDEXED, name, qualname, signature, doc, text, tokenize='unicode61 tokenchars ''_''')"""


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def write_private_text(path, text: str) -> None:
    path = Path(path)
    private_dir(path.parent)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")      # mkstemp → 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Store:
    @classmethod
    def open_readonly(cls, index_dir: Path) -> "Store | None":
        """Read another repo's graph without creating or modifying anything."""
        path = Path(index_dir) / "graph.sqlite"
        if not path.exists():
            return None
        self = cls.__new__(cls)
        self.dir, self.path = Path(index_dir), path
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60, isolation_level=None)
        try:
            self.db.execute("SELECT 1 FROM nodes_fts LIMIT 1")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        return self

    def __init__(self, index_dir: Path):
        self.dir = Path(index_dir)
        private_dir(self.dir)
        self.path = self.dir / "graph.sqlite"
        old = os.umask(0o077)                           # SQLite creates -wal/-shm with the process umask
        try:
            self.db = sqlite3.connect(self.path, timeout=600, isolation_level=None)
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executescript(_SCHEMA)
        try:
            self.db.execute(_FTS)
            self.fts = True
        except sqlite3.OperationalError:                # SQLite built without FTS5 (old RHEL): fall back
            self.fts = False

    @contextmanager
    def write(self):
        """Exclusive write transaction: other processes wait, then see the finished index."""
        self.db.execute("BEGIN EXCLUSIVE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ── meta ──────────────────────────────────────────────────────────────
    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, db, key, value):
        db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    # ── files ─────────────────────────────────────────────────────────────
    def file_hashes(self) -> dict:
        return {p: s for p, s in self.db.execute("SELECT path, sha256 FROM files")}

    # ── embeddings ────────────────────────────────────────────────────────
    def cached_embeddings(self, shas) -> dict:
        out = {}
        shas = list(shas)
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            q = "SELECT text_sha, dim, vec FROM embeddings WHERE text_sha IN (%s)" % ",".join("?" * len(chunk))
            for sha, dim, vec in self.db.execute(q, chunk):
                out[sha] = np.frombuffer(vec, dtype=np.float32, count=dim)
        return out

    def put_embeddings(self, db, items: dict) -> None:
        db.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
                       [(sha, int(v.shape[0]), np.asarray(v, dtype=np.float32).tobytes()) for sha, v in items.items()])

    def load_vectors(self):
        """(uids, matrix) for every embedded, searchable node."""
        rows = self.db.execute(
            "SELECT n.uid, e.dim, e.vec FROM nodes n JOIN embeddings e ON e.text_sha = n.text_sha").fetchall()
        if not rows:
            return [], np.zeros((0, 0), dtype=np.float32)
        uids = [r[0] for r in rows]
        mat = np.vstack([np.frombuffer(r[2], dtype=np.float32, count=r[1]) for r in rows])
        return uids, mat

    # ── reads ─────────────────────────────────────────────────────────────
    _COLS = ("uid", "file", "kind", "name", "qualname", "lang", "start_line", "end_line", "parent", "role",
             "visibility", "target", "signature", "doc", "text", "attrs", "text_sha", "tokens")

    def node(self, uid: str) -> dict | None:
        row = self.db.execute(f"SELECT {','.join(self._COLS)} FROM nodes WHERE uid=?", (uid,)).fetchone()
        return self._row(row) if row else None

    def nodes(self, uids) -> dict:
        uids = list(uids)
        out = {}
        for i in range(0, len(uids), 500):
            chunk = uids[i:i + 500]
            q = f"SELECT {','.join(self._COLS)} FROM nodes WHERE uid IN ({','.join('?' * len(chunk))})"
            for row in self.db.execute(q, chunk):
                out[row[0]] = self._row(row)
        return out

    def _row(self, row) -> dict:
        d = dict(zip(self._COLS, row))
        d["attrs"] = json.loads(d["attrs"] or "{}")
        return d

    def children(self, uid: str) -> list[dict]:
        rows = self.db.execute(f"SELECT {','.join(self._COLS)} FROM nodes WHERE parent=? ORDER BY start_line",
                               (uid,)).fetchall()
        return [self._row(r) for r in rows]

    def edges_from(self, uid, types=None):
        q = "SELECT src, dst, type, dst_name, confidence FROM edges WHERE src=?"
        args = [uid]
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"
            args += list(types)
        return self.db.execute(q, args).fetchall()

    def edges_to(self, uid, types=None):
        q = "SELECT src, dst, type, dst_name, confidence FROM edges WHERE dst=?"
        args = [uid]
        if types:
            q += f" AND type IN ({','.join('?' * len(types))})"
            args += list(types)
        return self.db.execute(q, args).fetchall()

    def find(self, name: str, limit=20) -> list[dict]:
        rows = self.db.execute(f"SELECT {','.join(self._COLS)} FROM nodes WHERE name=? OR qualname=? OR qualname LIKE ? "
                               f"LIMIT ?", (name, name, f"%::{name}", limit)).fetchall()
        return [self._row(r) for r in rows]

    def keyword(self, query: str, limit=30) -> list[tuple[str, float]]:
        terms = [t for t in __import__("re").findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)]
        if not terms:
            return []
        if self.fts:
            q = " OR ".join(f'"{t}"' for t in terms[:12])
            try:
                return self.db.execute("SELECT uid, bm25(nodes_fts, 0, 8, 6, 3, 2, 1) FROM nodes_fts "
                                       "WHERE nodes_fts MATCH ? ORDER BY 2 LIMIT ?", (q, limit)).fetchall()
            except sqlite3.OperationalError:
                pass
        like = " OR ".join("name LIKE ? OR text LIKE ?" for _ in terms[:6])
        args = [a for t in terms[:6] for a in (f"%{t}%", f"%{t}%")]
        return [(r[0], 0.0) for r in self.db.execute(f"SELECT uid FROM nodes WHERE {like} LIMIT ?", args + [limit])]

    def close(self):
        self.db.close()
