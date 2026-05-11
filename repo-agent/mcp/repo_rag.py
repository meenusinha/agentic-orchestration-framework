"""Generic RepoRAG — indexes knowledge/ docs and source code, no external dependencies."""
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
        extra_extensions: list[str] = None,
    ):
        self.repo_name = repo_name
        self.knowledge_paths = [Path(p) for p in knowledge_paths] if knowledge_paths else []
        self.src_paths = [Path(p) for p in src_paths] if src_paths else []
        self.top_k = top_k
        self._extensions = _DEFAULT_EXTENSIONS | set(extra_extensions or [])

        log(repo_name, "INDEX", f"Initializing ChromaDB at {chroma_persist_dir}")
        self._client = chromadb.PersistentClient(path=str(chroma_persist_dir))
        log(repo_name, "INDEX", f"Loading embedding model: {embedding_model}")
        self._model = SentenceTransformer(embedding_model)
        self._docs_collection = None
        self._code_collection = None

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts in one optimised batch."""
        return self._model.encode(
            texts, batch_size=256, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    def _collection_name(self, kind: str) -> str:
        return f"repo_{self.repo_name}_{kind}"

    def _collection_exists(self, name: str) -> bool:
        return name in [c.name for c in self._client.list_collections()]

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
            self._docs_collection.add(documents=docs, embeddings=self._embed(docs), ids=ids)
        log(self.repo_name, "INDEX", f"Docs index ready: {len(docs)} chunks total")

    # ── Code index ────────────────────────────────────────────────────────────

    def _scan_src_dir(self, src_dir: Path) -> tuple[list[str], int]:
        """Read one source directory and return (chunks, file_count). Thread-safe."""
        chunks, file_count = [], 0
        for src_file in sorted(src_dir.rglob("*")):
            if src_file.suffix not in self._extensions:
                continue
            try:
                text = src_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for chunk in text.split("\n\n"):
                chunk = chunk.strip()
                if len(chunk) > 40:
                    chunks.append(chunk)
            file_count += 1
        return chunks, file_count

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

        workers = min(4, len(active))
        log(self.repo_name, "INDEX",
            f"Scanning {len(active)} source path(s) in parallel (workers={workers})...")

        all_chunks: list[str] = []
        total_files = 0
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for chunks, file_count in ex.map(self._scan_src_dir, active):
                all_chunks.extend(chunks)
                total_files += file_count

        log(self.repo_name, "INDEX",
            f"Scanned {total_files} files → {len(all_chunks)} chunks.")

        if not all_chunks:
            log(self.repo_name, "INDEX", "Code index ready: 0 chunks total")
            return

        # Sequential IDs — uniqueness guaranteed because we own the list
        all_ids = [f"code_{i}" for i in range(len(all_chunks))]

        log(self.repo_name, "INDEX",
            f"Embedding {len(all_chunks)} chunks in one batch (batch_size=256)...")
        self._code_collection = self._client.create_collection(name=name)
        self._code_collection.add(
            documents=all_chunks,
            embeddings=self._embed(all_chunks),
            ids=all_ids,
        )
        log(self.repo_name, "INDEX", f"Code index ready: {len(all_chunks)} chunks total")

    # ── Public API ────────────────────────────────────────────────────────────

    def build_or_load_index(self) -> None:
        log(self.repo_name, "INDEX", "Building/loading RAG index...")
        self._index_docs()
        self._index_code()
        log(self.repo_name, "INDEX", "RAG index ready")

    def query(self, question: str) -> str:
        if self._docs_collection is None:
            raise RuntimeError("Call build_or_load_index() before query()")

        q_emb = self._embed([question])

        log(self.repo_name, "RAG", f"Searching docs (top_k={self.top_k})...")
        doc_results = self._docs_collection.query(query_embeddings=q_emb, n_results=self.top_k)
        doc_snippets = doc_results["documents"][0] if doc_results["documents"] else []
        doc_text = "\n---\n".join(doc_snippets) if doc_snippets else ""
        log(self.repo_name, "RAG", f"Docs: {len(doc_snippets)} results, {len(doc_text)} chars")

        code_text = ""
        if self._code_collection is not None:
            log(self.repo_name, "RAG",
                "[Demo] Code search: in production this would call an LLM for deep code understanding.")
            log(self.repo_name, "RAG",
                "       Here we use RAG similarity search as a demo approximation.")
            log(self.repo_name, "RAG", f"Searching source code (top_k={self.top_k})...")
            code_results = self._code_collection.query(query_embeddings=q_emb, n_results=self.top_k)
            code_snippets = code_results["documents"][0] if code_results["documents"] else []
            code_text = "\n---\n".join(code_snippets) if code_snippets else ""
            log(self.repo_name, "RAG", f"Code: {len(code_snippets)} results, {len(code_text)} chars")
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
