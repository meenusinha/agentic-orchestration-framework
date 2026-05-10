"""Generic RepoRAG — indexes knowledge/ docs and source code, no external dependencies."""
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from demo_logger import log

_THIN_THRESHOLD = 100

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
        self._ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
        self._docs_collection = None
        self._code_collection = None

    def _collection_name(self, kind: str) -> str:
        return f"repo_{self.repo_name}_{kind}"

    def _index_docs(self) -> None:
        name = self._collection_name("docs")
        existing = [c.name for c in self._client.list_collections()]
        if name in existing:
            log(self.repo_name, "INDEX", "Docs collection loaded from cache")
            self._docs_collection = self._client.get_collection(
                name=name, embedding_function=self._ef
            )
            return

        active = [p for p in self.knowledge_paths if p.exists()]
        log(self.repo_name, "INDEX", f"Building docs index from {len(active)} path(s)")
        self._docs_collection = self._client.create_collection(
            name=name, embedding_function=self._ef
        )
        docs, ids = [], []
        seen_ids = set()
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
            self._docs_collection.add(documents=docs, ids=ids)
        log(self.repo_name, "INDEX", f"Docs index ready: {len(docs)} chunks total")

    def _index_code(self) -> None:
        active = [p for p in self.src_paths if p.exists()]
        if not active:
            log(self.repo_name, "INDEX", "No source path configured — skipping code index")
            return

        name = self._collection_name("code")
        existing = [c.name for c in self._client.list_collections()]
        if name in existing:
            log(self.repo_name, "INDEX", "Code collection loaded from cache")
            self._code_collection = self._client.get_collection(
                name=name, embedding_function=self._ef
            )
            return

        log(self.repo_name, "INDEX", f"Building code index from {len(active)} path(s)")
        self._code_collection = self._client.create_collection(
            name=name, embedding_function=self._ef
        )
        docs, ids = [], []
        for src_dir in active:
            for src_file in sorted(src_dir.rglob("*")):
                if src_file.suffix not in self._extensions:
                    continue
                try:
                    text = src_file.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                chunks = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 40]
                for i, chunk in enumerate(chunks):
                    docs.append(chunk)
                    ids.append(f"code_{src_file.stem}_{i}_{len(docs)}")
        if docs:
            self._code_collection.add(documents=docs, ids=ids)
        log(self.repo_name, "INDEX", f"Code index ready: {len(docs)} chunks total")

    def build_or_load_index(self) -> None:
        log(self.repo_name, "INDEX", "Building/loading RAG index...")
        self._index_docs()
        self._index_code()
        log(self.repo_name, "INDEX", "RAG index ready")

    def query(self, question: str) -> str:
        if self._docs_collection is None:
            raise RuntimeError("Call build_or_load_index() before query()")

        log(self.repo_name, "RAG", f"Searching docs (top_k={self.top_k})...")
        doc_results = self._docs_collection.query(query_texts=[question], n_results=self.top_k)
        doc_snippets = doc_results["documents"][0] if doc_results["documents"] else []
        doc_text = "\n---\n".join(doc_snippets) if doc_snippets else ""
        log(self.repo_name, "RAG", f"Docs: {len(doc_snippets)} results, {len(doc_text)} chars")

        if len(doc_text) >= _THIN_THRESHOLD:
            log(self.repo_name, "RESULT", f"Docs sufficient — {len(doc_text)} chars")
            return f"RELEVANT KNOWLEDGE:\n[From documentation]\n{doc_text}"

        log(self.repo_name, "RAG", "Docs thin — searching source code...")
        if self._code_collection is None:
            result = doc_text if doc_text else "(no relevant knowledge found)"
            log(self.repo_name, "RESULT", f"No code collection — {len(result)} chars")
            return result

        code_results = self._code_collection.query(query_texts=[question], n_results=self.top_k)
        code_snippets = code_results["documents"][0] if code_results["documents"] else []
        code_text = "\n---\n".join(code_snippets) if code_snippets else ""
        log(self.repo_name, "RAG", f"Code: {len(code_snippets)} results, {len(code_text)} chars")

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
