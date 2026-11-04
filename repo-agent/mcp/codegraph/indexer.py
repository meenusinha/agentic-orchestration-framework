"""Build or refresh a repo's code graph.

Pipeline: collect files → skip secret files / redact secret values → parse into
whole-definition nodes → resolve structure (roles, targets, generated code) →
in-repo relations → interface links against this repo's and peers' IDL →
embed new/changed nodes only → write the store in one transaction → publish
interfaces.json.

The graph is rebuilt when any indexed file, the config or the model changes;
parsing is fast, and embeddings are cached by text hash, so a rebuild after a
small change only embeds what changed.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import time
from pathlib import Path, PurePosixPath

from . import interfaces as ifc
from .extract import language_for, extract
from .model import (CLASS, CONTAINERS, DOC_SECTION, FIELD, FILE, FUNCTION, FUNCTION_DECL, IDL_ATTRIBUTE,
                    IDL_INTERFACE, IDL_OPERATION, IDL_TYPE, MACRO, METHOD, MODULE, NAMESPACE, TARGET, TEXT, TYPE,
                    VARIABLE, Node)
from .relations import build_edges
from .security import is_secret_file, redact
from .store import Store
from .structure import resolve

INDEX_FORMAT = 1
_NOT_INDEXED_MARK = b"<!-- agentic-orchestration: framework template, not indexed -->"
EMBED_KINDS = {FUNCTION, METHOD, CLASS, TYPE, MACRO, IDL_INTERFACE, IDL_OPERATION, IDL_ATTRIBUTE, IDL_TYPE, DOC_SECTION, TEXT,
               MODULE, NAMESPACE, TARGET}
NOT_SEARCHED_ROLES = {"test"}          # tests are evidence (TESTS edges), never results
_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".rag_index",
              ".mypy_cache", ".pytest_cache", ".tox", "mcp", ".vscode", ".idea",
              # build output: never source of truth
              "build", "dist", "out", "target", "obj", ".gradle", "coverage", "htmlcov", ".next", ".turbo",
              "site-packages", "bower_components", "Pods", "DerivedData", ".terraform", "knowledge.bak"}
_BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".gz", ".tgz", ".bz2", ".xz",
               ".7z", ".tar", ".jar", ".war", ".class", ".o", ".obj", ".a", ".so", ".dylib", ".dll", ".exe", ".bin",
               ".pyc", ".whl", ".mat", ".fig", ".mp4", ".mov", ".mp3", ".wav", ".woff", ".woff2", ".ttf", ".otf",
               ".npy", ".npz", ".pkl", ".pickle", ".h5", ".onnx", ".pt", ".pth", ".safetensors", ".db", ".sqlite",
               ".lock", ".svg", ".ipynb", ".pptx", ".docx", ".xlsx"}
# Common config / data formats: known, so never "unclassified", but not indexed unless the
# developer opts in with file_types (e.g. ".yaml": {kind: config, parse_as: text}).
_KNOWN_DATA_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".properties", ".csv",
                   ".tsv", ".env", ".lock", ".log", ".txt", ".rst", ".adoc", ".html", ".css", ".scss", ".sql",
                   ".sh", ".bash", ".zsh", ".bat", ".ps1", ".cmake", ".mk", ".mak", ".gradle", ".in", ".am",
                   ".gitignore", ".gitattributes", ".editorconfig", ".dockerignore",
                   # project / build-system formats (structure comes from Makefiles, CMake, compile db)
                   ".sln", ".vcxproj", ".vcproj", ".csproj", ".vbproj", ".props", ".targets", ".filters",
                   ".pbxproj", ".xcconfig", ".ac", ".m4", ".def", ".rc", ".manifest", ".hcl", ".tf",
                   ".tfvars", ".dockerfile", ".bazel", ".bzl", ".gn", ".gni", ".ninja", ".spec", ".pc.in"}


class IndexConfig:
    def __init__(self, repo_name: str, repo_root: Path, roots: list[Path], file_types: dict | None = None,
                 structure: list | None = None, extra_extensions: list | None = None, peers: list | None = None,
                 max_file_bytes: int = 2_000_000, exclude: list | None = None):
        self.repo_name, self.repo_root = repo_name, Path(repo_root).resolve()
        self.roots = [Path(r).resolve() for r in roots]
        self.file_types = {k.lower() if k.startswith(".") else k: v for k, v in (file_types or {}).items()}
        for ext in extra_extensions or []:              # legacy setting: treat as plain text units
            self.file_types.setdefault(ext.lower(), {"kind": "implementation", "parse_as": "text"})
        self.structure = structure or []
        self.peers = peers or []                         # [{"name":…, "path":…}]
        self.max_file_bytes = max_file_bytes
        self.exclude = [e.replace("\\", "/").strip("/") for e in (exclude or []) if e.strip()]

    def fingerprint(self, model_sha: str) -> str:
        return hashlib.sha256(json.dumps({
            "format": INDEX_FORMAT, "code": _code_version(), "model": model_sha,
            "roots": [str(r) for r in self.roots],
            "file_types": self.file_types, "structure": self.structure, "exclude": self.exclude,
            "max_file_bytes": self.max_file_bytes}, sort_keys=True).encode()).hexdigest()

    def excluded(self, rel: str) -> bool:
        """rel matches an exclude glob, or lies inside a folder that does (build/** or build)."""
        parts = rel.split("/")
        for pat in self.exclude:
            if fnmatch.fnmatch(rel, pat) or any(fnmatch.fnmatch("/".join(parts[:i]), pat.rstrip("/*").rstrip("/") or pat)
                                                for i in range(1, len(parts))):
                return True
        return False


def _code_version() -> str:
    """Hash of the code-graph package: a framework upgrade rebuilds every graph
    (cached embeddings are reused), so no repo keeps links made by older logic."""
    h = hashlib.sha256()
    for f in sorted(Path(__file__).parent.glob("*.py")):
        h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()[:16]


def collect_files(cfg: IndexConfig):
    """→ (indexable [(rel, abs, lang)], unclassified {ext: [sample rel paths]}, skipped_secret count)"""
    out, unknown, secrets, seen = [], {}, 0, set()
    for root in cfg.roots:
        if not root.exists():
            continue
        walk = [(str(root.parent), [], [root.name])] if root.is_file() else os.walk(root)
        for dp, dn, fns in walk:
            dn[:] = sorted(d for d in dn if d not in _SKIP_DIRS and not d.startswith((".rag", "cmake-build-")))
            for fn in sorted(fns):
                ab = Path(dp) / fn
                try:
                    rel = ab.resolve().relative_to(cfg.repo_root).as_posix()
                except ValueError:
                    continue
                if rel in seen:
                    continue
                seen.add(rel)
                if cfg.excluded(rel):
                    continue
                if is_secret_file(ab):
                    secrets += 1
                    continue
                ext = PurePosixPath(fn).suffix.lower()
                if ext in _BINARY_EXT:
                    continue
                if _is_framework_file(ab):                # our own templates / generated instructions
                    continue
                lang = language_for(rel, cfg.file_types)
                if not lang:
                    ft = cfg.file_types.get(ext) or cfg.file_types.get(fn)
                    if ft is None and ext and ext not in _KNOWN_DATA_EXT and _looks_textual(ab):
                        unknown.setdefault(ext, []).append(rel)
                    continue
                out.append((rel, ab, lang))
    return out, unknown, secrets


def _is_framework_file(path: Path) -> bool:
    if path.suffix.lower() not in (".md", ".markdown", ".txt"):
        return False
    try:
        with open(path, "rb") as f:
            return _NOT_INDEXED_MARK in f.read(400)
    except OSError:
        return False


def _looks_textual(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return b"\x00" not in head and bool(head.strip())


def build(cfg: IndexConfig, store: Store, embedder, model_sha: str, log=lambda *a: None, force=False) -> dict:
    t0 = time.time()
    files, unknown, secret_files = collect_files(cfg)
    hashes = {}
    sources = {}
    for rel, ab, lang in files:
        try:
            raw = ab.read_bytes()
        except OSError:
            continue
        if len(raw) > cfg.max_file_bytes:
            log("WARN", f"  skipping {rel}: larger than {cfg.max_file_bytes} bytes")
            continue
        if _NOT_INDEXED_MARK in raw[:300]:         # framework's own instruction templates
            continue
        hashes[rel] = hashlib.sha256(raw).hexdigest()
        sources[rel] = (raw.decode("utf-8", "replace"), lang)
    fp = cfg.fingerprint(model_sha)
    peer_sig = _peer_signature(cfg)
    if not force and store.meta("fingerprint") == fp and store.file_hashes() == hashes and \
            store.meta("peer_signature") == peer_sig:
        log("INDEX", f"Code graph up to date ({len(hashes)} files)")
        return {"rebuilt": False, "files": len(hashes), "unclassified": store.meta("unclassified", {})}

    # ── parse ────────────────────────────────────────────────────────────────
    st = resolve(cfg.repo_root, sorted(sources), cfg.structure)
    trees: dict[str, Node] = {}
    redactions = 0
    for rel, (text, lang) in sources.items():
        if st.index_mode.get(rel) == "skip":           # developer's manifest: not indexed at all
            continue
        text, n = redact(text)
        redactions += n
        lang = st.lang_override.get(rel, lang) if lang in ("c", "cpp") else lang
        try:
            trees[rel] = extract(rel, text, lang)
        except Exception as e:                          # never let one odd file stop the index
            log("WARN", f"  could not parse {rel}: {type(e).__name__}")
            continue
    if secret_files:
        log("WARN", f"  Skipped {secret_files} secret-store file(s)")
    if redactions:
        log("WARN", f"  Redacted {redactions} secret-looking value(s) before indexing")

    # ── structure onto nodes ─────────────────────────────────────────────────
    for rel, root in trees.items():
        role = st.roles.get(rel, "implementation")
        ft = cfg.file_types.get(PurePosixPath(rel).suffix.lower()) or cfg.file_types.get(PurePosixPath(rel).name)
        if ft and rel not in st.from_manifest and ft.get("kind"):   # developer's file-type answer
            role = _KIND_ROLE.get(ft["kind"].lower(), role)
        targets = st.file_targets.get(rel, [])
        mode = st.index_mode.get(rel, "full")
        vis = st.visibility.get(rel)
        for n in root.walk():
            n.role = role
            n.target = targets[0] if targets else ""
            if vis and n.visibility == "public":
                n.visibility = vis
            if role == "generated":
                n.attrs["generated"] = st.generated.get(rel, True)
            if mode == "signatures" and n.kind in (FUNCTION, METHOD) and n.signature:
                n.attrs["body_omitted"] = "external code: signatures only"   # by manifest, not truncation
                n.text = n.signature + " { … }"

    edges, target_nodes = build_edges(trees, st)

    # ── interfaces: own definitions + peers' → links ─────────────────────────
    defines = ifc.collect_defines(trees)
    refs = ifc.collect_references(trees)
    defines_by_repo = {cfg.repo_name: defines}
    defines_by_repo.update(load_peer_defines(cfg))
    resolved = ifc.Resolver(defines_by_repo).resolve(refs)
    tmap = {f"target:{t.name}": t.uid for t in target_nodes}
    for e in ifc.link_edges(cfg.repo_name, resolved, defines_by_repo):
        e.src = tmap.get(e.src, e.src)
        edges.append(e)

    # ── embeddings (only new/changed text) ───────────────────────────────────
    all_nodes = [n for r in trees.values() for n in r.walk()] + target_nodes
    to_embed = {}
    for n in all_nodes:
        n.attrs.pop("calls", None) if n.kind not in (FUNCTION, METHOD) else None
        if n.kind in EMBED_KINDS and n.role not in NOT_SEARCHED_ROLES and n.kind != FILE:
            to_embed[n.uid] = _embed_text(n)
    shas = {uid: hashlib.sha256(t.encode()).hexdigest() for uid, t in to_embed.items()}
    cached = store.cached_embeddings(set(shas.values()))
    missing = {sha: to_embed[uid] for uid, sha in shas.items() if sha not in cached}
    new_vecs = {}
    if missing and embedder is not None:
        log("INDEX", f"Embedding {len(missing)} new/changed node(s) ({len(cached)} reused)...")
        items = sorted(missing.items(), key=lambda kv: len(kv[1]))   # length-sorted batches are faster
        vecs = embedder([t for _, t in items])
        new_vecs = {sha: v for (sha, _), v in zip(items, vecs)}

    # ── write ────────────────────────────────────────────────────────────────
    with store.write() as db:
        for t in ("files", "nodes", "edges", "iface_refs"):
            db.execute(f"DELETE FROM {t}")
        if store.fts:
            db.execute("DELETE FROM nodes_fts")
        for rel, root in trees.items():
            db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?)",
                       (rel, hashes.get(rel, ""), root.lang, st.roles.get(rel, ""),
                        json.dumps(st.file_targets.get(rel, [])), st.generated.get(rel, ""),
                        int(bool(root.attrs.get("parse_errors"))), root.uid))
        rows, fts_rows = [], []
        for n in all_nodes:
            text_sha = shas.get(n.uid, "")
            attrs = {k: v for k, v in n.attrs.items() if k != "includes" or n.kind == FILE}
            rows.append((n.uid, n.file, n.kind, n.name, n.qualname, n.lang, n.start_line, n.end_line,
                         n.parent.uid if n.parent is not None else "", n.role, n.visibility, n.target,
                         n.signature, n.doc, n.text, json.dumps(attrs, ensure_ascii=False), text_sha,
                         _tokens(n.text)))
            if store.fts and n.kind != FILE:
                fts_rows.append((n.uid, n.name, n.qualname, n.signature, n.doc, n.text if n.kind not in CONTAINERS
                                 else n.signature))
        db.executemany(f"INSERT OR REPLACE INTO nodes VALUES ({','.join('?' * 18)})", rows)
        if fts_rows:
            db.executemany("INSERT INTO nodes_fts VALUES (?,?,?,?,?,?)", fts_rows)
        db.executemany("INSERT INTO edges VALUES (?,?,?,?,?)",
                       [(e.src, e.dst, e.type, e.dst_name, e.confidence) for e in edges])
        db.executemany("INSERT INTO iface_refs VALUES (?)", [(json.dumps(r),) for r in refs])
        store.put_embeddings(db, new_vecs)
        live = set(shas.values())                       # drop embeddings no node uses any more
        db.execute("CREATE TEMP TABLE IF NOT EXISTS live (sha TEXT PRIMARY KEY)")
        db.execute("DELETE FROM live")
        db.executemany("INSERT OR IGNORE INTO live VALUES (?)", [(s,) for s in live])
        db.execute("DELETE FROM embeddings WHERE text_sha NOT IN (SELECT sha FROM live)")
        store.set_meta(db, "fingerprint", fp)
        store.set_meta(db, "peer_signature", peer_sig)
        store.set_meta(db, "unclassified", {k: v[:5] for k, v in unknown.items()})
        store.set_meta(db, "built_at", time.time())
        store.set_meta(db, "stats", {"files": len(trees), "nodes": len(all_nodes), "edges": len(edges),
                                     "interfaces_defined": len(defines),
                                     "implements": len(resolved["implements"]),
                                     "consumes": len(resolved["consumes"])})
    ifc.write_registry(store.dir / "interfaces.json", cfg.repo_name, defines, resolved)
    log("INDEX", f"Code graph built: {len(trees)} files, {len(all_nodes)} nodes, {len(edges)} edges, "
                 f"{len(defines)} IDL interface(s), {len(resolved['implements'])} implementation link(s), "
                 f"{len(resolved['consumes'])} client link(s) in {time.time() - t0:.1f}s")
    if unknown:
        log("WARN", f"  Unclassified file types not indexed: {', '.join(sorted(unknown))} "
                    "(classify them in mcp/config.yaml → file_types)")
    return {"rebuilt": True, "files": len(trees), "nodes": len(all_nodes), "edges": len(edges),
            "unclassified": {k: v[:5] for k, v in unknown.items()}}


_KIND_ROLE = {"interface": "interface", "idl": "interface", "implementation": "implementation",
              "server": "implementation", "client": "implementation", "internal": "internal",
              "config": "config", "server-config": "config", "client-config": "config", "configuration": "config",
              "docs": "docs", "documentation": "docs", "data": "data", "test": "test", "build": "build",
              "tooling": "tooling", "script": "tooling", "generated": "generated", "external": "external"}


def _embed_text(n: Node) -> str:
    head = f"{n.kind} {n.qualname}" + (f" ({n.file})" if n.file else "")
    parts = [head]
    if n.signature and n.signature not in n.text[:len(n.signature) + 5]:
        parts.append(n.signature)
    if n.doc and n.doc not in n.text:
        parts.append(n.doc)
    parts.append(n.text)
    return "\n".join(parts)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def load_peer_defines(cfg: IndexConfig) -> dict:
    out = {}
    for p in cfg.peers:
        if p.get("name") == cfg.repo_name:
            continue
        reg = Path(p["path"]) / ".rag_index" / "interfaces.json"
        try:
            data = json.loads(reg.read_text())
            out[p["name"]] = data.get("defines", [])
        except (OSError, ValueError):
            continue
    return out


def _peer_signature(cfg: IndexConfig) -> str:
    """Changes when a peer republishes its interfaces → our links get re-resolved."""
    h = hashlib.sha256()
    for p in sorted(cfg.peers, key=lambda x: x.get("name", "")):
        if p.get("name") == cfg.repo_name:
            continue
        reg = Path(p["path"]) / ".rag_index" / "interfaces.json"
        try:
            data = json.loads(reg.read_text())
            h.update(json.dumps([(d["id"], d["fingerprint"]) for d in data.get("defines", [])]).encode())
        except (OSError, ValueError):
            h.update(b"-")
    return h.hexdigest()
