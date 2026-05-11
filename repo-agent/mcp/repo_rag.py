"""Generic RepoRAG — indexes knowledge/ docs and source code, no external dependencies."""
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# On systems with sqlite3 < 3.35.0 (e.g. RHEL8), swap in pysqlite3-binary
# before chromadb loads. Install with: pip install pysqlite3-binary
try:
    import pysqlite3
    sys.modules["sqlite3"] = pysqlite3
except ImportError:
    pass  # system sqlite3 is new enough, no swap needed

import chromadb
from sentence_transformers import SentenceTransformer

from demo_logger import log

# Default source file extensions to index
_DEFAULT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h", ".cc", ".cxx",
    ".cs", ".rb", ".swift", ".kt", ".php", ".scala",
    ".thrift", ".proto", ".graphql",
}


class RepoRAG:
    def __init__(
        self,
        repo_name: str,
        knowledge_paths: list[str] = None,
        src_paths: list[str] = None,
        embedding_model: str = "all-MiniLM-L6-v2",
        chroma_persist_dir: str = ".chroma_db",
        top_k: int = 3,
        similarity_threshold: float = None,
        extra_extensions: list[str] = None,
    ):
        self.repo_name = repo_name
        self.knowledge_paths = [Path(p) for p in knowledge_paths] if knowledge_paths else []
        self.src_paths = [Path(p) for p in src_paths] if src_paths else []
        self.top_k = top_k
        self._threshold = similarity_threshold   # L2 distance ceiling; None = no filtering
        self._extensions = _DEFAULT_EXTENSIONS | set(extra_extensions or [])

        log(repo_name, "INDEX", f"Initializing ChromaDB at {chroma_persist_dir}")
        self._client = chromadb.PersistentClient(path=str(chroma_persist_dir))
        log(repo_name, "INDEX", f"Loading embedding model: {embedding_model}")
        self._model = SentenceTransformer(embedding_model)
        self._docs_collection = None
        self._code_collection = None

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, logging progress via demo_logger (visible in live log stream)."""
        _BATCH = 512
        _LOG_EVERY = 2000   # log a line roughly every this many chunks
        results = []
        for start in range(0, len(texts), _BATCH):
            batch = texts[start:start + _BATCH]
            results.extend(
                self._model.encode(batch, normalize_embeddings=True,
                                   show_progress_bar=False).tolist()
            )
            done = min(start + _BATCH, len(texts))
            # log at each _LOG_EVERY boundary and at the very end
            if len(texts) > _BATCH and (done % _LOG_EVERY < _BATCH or done == len(texts)):
                log(self.repo_name, "INDEX",
                    f"  Embedding: {done}/{len(texts)} chunks done...")
        return results

    def _collection_name(self, kind: str) -> str:
        return f"repo_{self.repo_name}_{kind}"

    def _collection_exists(self, name: str) -> bool:
        return name in [c.name for c in self._client.list_collections()]

    def _chroma_add(self, collection, docs: list[str], embeddings: list, ids: list[str]) -> None:
        """Add documents to ChromaDB in batches — ChromaDB max is 5461 per call."""
        _MAX = 5000
        for start in range(0, len(docs), _MAX):
            collection.add(
                documents=docs[start:start + _MAX],
                embeddings=embeddings[start:start + _MAX],
                ids=ids[start:start + _MAX],
            )
            log(self.repo_name, "INDEX",
                f"  Stored {min(start + _MAX, len(docs))}/{len(docs)} chunks in ChromaDB...")

    # ── Docs index ────────────────────────────────────────────────────────────

    def _index_docs(self) -> None:
        name = self._collection_name("docs")
        if self._collection_exists(name):
            log(self.repo_name, "INDEX", "Docs collection loaded from cache")
            self._docs_collection = self._client.get_collection(name=name)
            return

        active = [p for p in self.knowledge_paths if p.exists()]
        log(self.repo_name, "INDEX", f"Building docs index from {len(active)} path(s)")
        self._docs_collection = self._client.create_collection(name=name)

        docs, ids = [], []
        seen_ids: set[str] = set()
        for knowledge_dir in active:
            for md_file in sorted(knowledge_dir.rglob("*.md")):
                if md_file.name.lower() == "readme.md":
                    continue
                text = md_file.read_text(encoding="utf-8", errors="ignore")
                chunks = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
                for i, chunk in enumerate(chunks):
                    uid = f"doc_{md_file.stem}_{i}_{len(docs)}"
                    if uid not in seen_ids:
                        docs.append(chunk)
                        ids.append(uid)
                        seen_ids.add(uid)
                log(self.repo_name, "INDEX", f"  {md_file.name}: {len(chunks)} chunks")

        if docs:
            log(self.repo_name, "INDEX", f"Embedding {len(docs)} doc chunks...")
            self._chroma_add(self._docs_collection, docs, self._embed(docs), ids)
        log(self.repo_name, "INDEX", f"Docs index ready: {len(docs)} chunks total")

    # ── Code index ────────────────────────────────────────────────────────────

    def _read_file(self, src_file: Path) -> list[str]:
        """Read and chunk one source file. Called in parallel — thread-safe (read-only)."""
        try:
            text = src_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        return [c.strip() for c in text.split("\n\n") if len(c.strip()) > 40]

    def _index_code(self) -> None:
        active = [p for p in self.src_paths if p.exists()]
        if not active:
            log(self.repo_name, "INDEX", "No source path configured — skipping code index")
            return

        name = self._collection_name("code")
        if self._collection_exists(name):
            log(self.repo_name, "INDEX", "Code collection loaded from cache")
            self._code_collection = self._client.get_collection(name=name)
            return

        log(self.repo_name, "ARCH",
            "Design note: ideally code search would be LLM-driven (deep semantic + context understanding).")
        log(self.repo_name, "ARCH",
            "Demo constraint: LLM call not available here — indexing code into RAG for similarity search instead.")

        # Collect every matching file across all src dirs first
        all_files = [
            f
            for src_dir in active
            for f in sorted(src_dir.rglob("*"))
            if f.suffix in self._extensions
        ]

        workers = os.cpu_count() or 4
        log(self.repo_name, "INDEX",
            f"Scanning {len(all_files)} files across {len(active)} path(s) "
            f"(workers={workers}, all CPU cores)...")

        all_chunks: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for chunks in ex.map(self._read_file, all_files):
                all_chunks.extend(chunks)
        total_files = len(all_files)

        log(self.repo_name, "INDEX",
            f"Scanned {total_files} files → {len(all_chunks)} chunks.")

        if not all_chunks:
            log(self.repo_name, "INDEX", "Code index ready: 0 chunks total")
            return

        # Sequential IDs — uniqueness guaranteed because we own the list
        all_ids = [f"code_{i}" for i in range(len(all_chunks))]

        log(self.repo_name, "INDEX",
            f"Embedding {len(all_chunks)} chunks (batch_size=512)...")
        self._code_collection = self._client.create_collection(name=name)
        self._chroma_add(self._code_collection, all_chunks, self._embed(all_chunks), all_ids)
        log(self.repo_name, "INDEX", f"Code index ready: {len(all_chunks)} chunks total")

    # ── Public API ────────────────────────────────────────────────────────────

    def build_or_load_index(self) -> None:
        log(self.repo_name, "INDEX", "Building/loading RAG index...")
        self._index_docs()
        self._index_code()
        log(self.repo_name, "INDEX", "RAG index ready")

    def _apply_threshold(self, snippets: list[str], distances: list[float]) -> list[str]:
        """Drop results whose L2 distance exceeds the configured threshold."""
        if self._threshold is None:
            return snippets
        kept = [s for s, d in zip(snippets, distances) if d <= self._threshold]
        dropped = len(snippets) - len(kept)
        if dropped:
            log(self.repo_name, "RAG",
                f"  Threshold {self._threshold}: dropped {dropped} low-similarity result(s)")
        return kept

    def _keyword_search(self, collection, question: str, already: set[str]) -> list[str]:
        """Substring search for significant terms from the query.

        Builds two levels of search terms:
        1. Whole tokens (whitespace-split, underscores kept) — e.g. "uniformity_refresh"
           directly matches robust_uniformity_refresh, KEMIxTExUR_uniformity_refresh_cnp
        2. Sub-parts (split on _ - .) for broader coverage
        More specific terms are searched first so the limit catches the right chunks.
        """
        # Level 1: whole tokens — keep underscores (most specific)
        tokens = [t for t in re.split(r'\s+', question) if len(t) >= 4]
        # Level 2: sub-parts — split on identifier delimiters
        parts  = [p for t in tokens for p in re.split(r'[_\-./]+', t) if len(p) >= 4]
        # Most specific first, deduped, preserving order
        seen_terms: list[str] = []
        for term in tokens + parts:
            if term not in seen_terms:
                seen_terms.append(term)

        extras = []
        seen_docs = set(already)
        kw_limit = min(self.top_k * 3, 30)   # wider limit so specific chunks aren't crowded out
        for term in seen_terms:
            try:
                res = collection.get(
                    where_document={"$contains": term},
                    limit=kw_limit,
                )
                for doc in (res.get("documents") or []):
                    if doc not in seen_docs:
                        extras.append(doc)
                        seen_docs.add(doc)
            except Exception:
                pass   # $contains not supported in this ChromaDB version — skip
        return extras

    def query(self, question: str) -> str:
        if self._docs_collection is None:
            raise RuntimeError("Call build_or_load_index() before query()")

        q_emb = self._embed([question])
        threshold_note = f", threshold={self._threshold}" if self._threshold else ""

        # ── Docs: semantic search ─────────────────────────────────────────────
        log(self.repo_name, "RAG", f"Searching docs (top_k={self.top_k}{threshold_note})...")
        doc_res  = self._docs_collection.query(query_embeddings=q_emb, n_results=self.top_k,
                                               include=["documents", "distances"])
        doc_snip = doc_res["documents"][0] if doc_res["documents"] else []
        doc_dist = doc_res["distances"][0] if doc_res.get("distances") else [0.0] * len(doc_snip)
        doc_snip = self._apply_threshold(doc_snip, doc_dist)
        doc_text = "\n---\n".join(doc_snip)
        log(self.repo_name, "RAG", f"Docs: {len(doc_snip)} results, {len(doc_text)} chars")

        # ── Code: semantic search + keyword augmentation ──────────────────────
        code_text = ""
        if self._code_collection is not None:
            log(self.repo_name, "RAG",
                "[Demo] Code search: in production this would call an LLM for deep code understanding.")
            log(self.repo_name, "RAG",
                f"Searching source code — semantic (top_k={self.top_k}{threshold_note}) + keyword...")
            code_res  = self._code_collection.query(query_embeddings=q_emb, n_results=self.top_k,
                                                    include=["documents", "distances"])
            code_snip = code_res["documents"][0] if code_res["documents"] else []
            code_dist = code_res["distances"][0] if code_res.get("distances") else [0.0] * len(code_snip)
            code_snip = self._apply_threshold(code_snip, code_dist)

            # Keyword augmentation — catches identifiers semantic search misses
            kw_extras = self._keyword_search(self._code_collection, question, set(code_snip))
            if kw_extras:
                log(self.repo_name, "RAG",
                    f"  Keyword search added {len(kw_extras)} extra chunk(s) not found semantically")
            code_snip = code_snip + kw_extras
            code_text = "\n---\n".join(code_snip)
            log(self.repo_name, "RAG", f"Code: {len(code_snip)} results, {len(code_text)} chars")
        else:
            log(self.repo_name, "RAG", "No code collection — skipping source search")

        if not doc_text and not code_text:
            log(self.repo_name, "RESULT", "No relevant knowledge found")
            return "(no relevant knowledge found)"

        parts = []
        if doc_text:
            parts.append(f"[From documentation]\n{doc_text}")
        if code_text:
            parts.append(f"[From source code]\n{code_text}")
        result = "RELEVANT KNOWLEDGE:\n" + "\n\n".join(parts)
        log(self.repo_name, "RESULT", f"Returning docs+code: {len(result)} chars")
        return result
