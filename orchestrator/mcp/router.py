"""OrchestratorRouter — picks the peer repos a feature request touches.

Primary: searches every repo's code graph directly (read-only, one model load)
and follows IDL interface links: repos that define, serve or call an interface
used by the requesting repo's matching code are selected with the reason.
Fallback for repos whose index does not exist yet: spawn the repo's MCP server
and score its query_repo answer (the original method). Config: config.yaml.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from demo_logger import log, content


def _mcp_call(server_script: str, tool_name: str, arguments: dict, timeout: int = 60) -> str:
    """Send a single tool call to an MCP server over stdio and return the text result."""
    python = sys.executable
    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "orchestrator_router", "version": "1.0"}}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": tool_name, "arguments": arguments}})
        + "\n"
    )
    try:
        proc = subprocess.run(
            [python, server_script],
            input=payload, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired:
        return "(MCP timeout)"
    except Exception as e:
        return f"(MCP error: {e})"

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if msg.get("id") == 2:
                parts = msg.get("result", {}).get("content", [])
                if parts:
                    return parts[0].get("text", "(empty response)")
        except json.JSONDecodeError:
            continue
    return "(no response)"


def _score_response(response: str) -> float:
    """Score relevance: 0.0 if no content found, else min(content_length/800, 1.0)."""
    if not response:
        return 0.0
    lower = response.lower()
    if "no relevant knowledge found" in lower:
        return 0.0
    if "(mcp" in lower or "(no response" in lower:
        return 0.0
    if "relevant knowledge:" in lower:
        idx = lower.index("relevant knowledge:")
        body = response[idx + len("relevant knowledge:"):]
    else:
        body = response
    # Ignore the untrusted-content frame lines so they don't inflate every score
    body = "\n".join(l for l in body.splitlines() if not l.startswith("<<<")).strip()
    return min(len(body) / 800.0, 1.0)


class OrchestratorRouter:
    """
    Routes a feature request to the most relevant repos by querying each repo's
    RAG via MCP and ranking by how much relevant content is returned.
    Configuration is read from config.yaml in the same directory.
    """

    def __init__(self, config_path: Path):
        with open(config_path) as f:
            config = yaml.safe_load(f)

        self._repo_display: dict[str, str] = {}
        self._repo_mcp_scripts: dict[str, str] = {}

        for repo in config.get("repos", []):
            name = repo["name"]
            display = repo.get("display_name", name)
            raw_path = repo["path"]

            repo_path = Path(raw_path)
            if not repo_path.is_absolute():
                repo_path = (config_path.parent / repo_path).resolve()

            script = repo_path / "mcp" / "mcp_server.py"
            self._repo_display[name] = display
            self._repo_mcp_scripts[name] = str(script)
            self._repo_paths = getattr(self, "_repo_paths", {})
            self._repo_paths[name] = repo_path
        self._embed = None
        fw = Path(__file__).resolve().parents[2] / "repo-agent" / "mcp"
        if str(fw) not in sys.path:
            sys.path.insert(0, str(fw))
        from codegraph import settings as _settings
        self._settings = _settings
        self.router_settings = _settings.load(config, _settings.ROUTER_SETTINGS)

        log("orchestrator_router", "INFO",
            f"Router ready — will query {len(self._repo_mcp_scripts)} repo MCP servers")

    # ── code-graph routing ────────────────────────────────────────────────────
    def _embedder(self):
        if self._embed is None:
            fw = Path(__file__).resolve().parents[2] / "repo-agent" / "mcp"
            if str(fw) not in sys.path:
                sys.path.insert(0, str(fw))
            import os
            from repo_rag import RepoRAG  # noqa: F401  (sets offline mode before ML imports)
            from repo_rag import verify_model_dir
            import numpy as np
            import torch
            from sentence_transformers import SentenceTransformer
            path = os.environ.get("EMBEDDING_MODEL_PATH", "")
            verify_model_dir(path)
            model = SentenceTransformer(path, local_files_only=True, trust_remote_code=False,
                                        model_kwargs={"dtype": torch.float32})
            self._embed = lambda q: np.asarray(model.encode([q], normalize_embeddings=True,
                                                            show_progress_bar=False)[0], dtype=np.float32)
        return self._embed

    def _graph_view(self, name):
        fw = Path(__file__).resolve().parents[2] / "repo-agent" / "mcp"
        if str(fw) not in sys.path:
            sys.path.insert(0, str(fw))
        from codegraph.store import Store
        return Store.open_readonly(self._repo_paths[name] / ".rag_index")

    def get_relevant_repos(
        self, requesting_repo: str, feature_description: str, top_k: int = 2, timeout: int = 600
    ) -> tuple[list[str], dict[str, float]]:
        """Returns (selected_repo_names, all_scores); reasons are in self.last_reasons."""
        log("orchestrator_router", "ROUTING", f"Routing: {content(feature_description[:70])}")
        self.last_reasons: dict[str, list[str]] = {}
        try:
            return self._graph_route(requesting_repo, feature_description, top_k, timeout)
        except Exception as e:                      # never fail routing: fall back to the original method
            log("orchestrator_router", "WARN", f"Graph routing unavailable ({type(e).__name__}); using MCP scoring")
            return self._mcp_route(requesting_repo, feature_description, top_k, timeout)

    def _graph_route(self, requesting_repo, feature, top_k, timeout):
        fw = Path(__file__).resolve().parents[2] / "repo-agent" / "mcp"
        if str(fw) not in sys.path:
            sys.path.insert(0, str(fw))
        from codegraph.search import GraphSearch
        embed = self._embedder()
        q = embed(feature)
        scores, linked_ifaces = {}, set()
        registries = {}
        for name in self._repo_mcp_scripts:
            reg = self._repo_paths[name] / ".rag_index" / "interfaces.json"
            try:
                registries[name] = json.loads(reg.read_text())
            except (OSError, ValueError):
                pass
            store = self._graph_view(name)
            if store is None:
                if name != requesting_repo:         # not indexed yet: original method for this repo
                    resp = _mcp_call(self._repo_mcp_scripts[name], "query_repo", {"feature_request": feature},
                                     timeout=timeout)
                    scores[name] = _score_response(resp)
                    self.last_reasons.setdefault(name, []).append("scored by query_repo (no index yet)")
                continue
            rcp = self._repo_paths[name] / "mcp" / "config.yaml"
            try:
                rs = self._settings.load(yaml.safe_load(rcp.read_text()) if rcp.exists() else {})
            except (OSError, yaml.YAMLError):
                rs = self._settings.load({})
            gs = GraphSearch(store, name, lambda _q: q, peers=[], candidate_pool=rs["candidate_pool"],
                             rrf_k=rs["rrf_k"], role_weights=rs["role_weights"])   # that repo's own settings
            best = float((gs.mat @ q).max()) if len(gs.uids) else 0.0
            hits = gs.search(feature, k=self.router_settings["router_hits_per_repo"])
            if name == requesting_repo:
                frontier, seen = [u for u, _ in hits], set()
                for _ in range(2):                  # matched code + its callers (2 levels): a C helper
                    nxt = []                        # called by a servant still belongs to its interface
                    for uid in frontier:
                        for src, *_ in store.edges_to(uid, ("CALLS",)):
                            if src not in seen:
                                seen.add(src)
                                nxt.append(src)
                    frontier = nxt
                for uid in [u for u, _ in hits] + list(seen):   # interfaces the requesting repo's matches touch
                    for _, dst, t, dname, _ in store.edges_from(uid, ("IMPLEMENTS", "CALLS_OPERATION")):
                        linked_ifaces.add(dname)    # interface or interface::operation, resolved below
                for uid, _ in hits:
                    n = store.node(uid)
                    if n and n["kind"] in ("idl_interface", "idl_operation", "idl_attribute"):
                        linked_ifaces.add(n["qualname"] if n["kind"] == "idl_interface"
                                          else n["qualname"].rsplit("::", 1)[0])
                continue
            scores[name] = round(max(best, 0.0), 3)
            log("orchestrator_router", "ROUTING", f"  {name}: semantic relevance={scores[name]:.3f}")
        # repos linked by an interface the requesting repo's code uses or provides
        defined = {d["id"] for reg in registries.values() for d in reg.get("defines", [])}
        linked_ifaces = {x if x in defined else x.rsplit("::", 1)[0] for x in linked_ifaces}
        for iid in sorted(linked_ifaces):
            for name, reg in registries.items():
                if name == requesting_repo:
                    continue
                if any(d["id"] == iid for d in reg.get("defines", [])):
                    self.last_reasons.setdefault(name, []).append(f"defines IDL interface {iid}")
                links = reg.get("links", {})
                procs = sorted({l.get("target") or "-" for l in links.get("implements", []) if l["interface"] == iid})
                if procs:
                    self.last_reasons.setdefault(name, []).append(f"serves {iid} (process {', '.join(procs)})")
                procs = sorted({l.get("target") or "-" for l in links.get("consumes", []) if l["interface"] == iid})
                if procs:
                    self.last_reasons.setdefault(name, []).append(f"calls {iid} (process {', '.join(procs)})")
        linked = [n for n in self.last_reasons if any(not r.startswith("scored") for r in self.last_reasons[n])]
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        targets = sorted(linked, key=lambda n: -scores.get(n, 0))
        for name, sc in ranked:
            if len(targets) >= max(top_k, len(linked)):
                break
            if name not in targets:
                targets.append(name)
        for name in scores:
            self.last_reasons.setdefault(name, [])
        log("orchestrator_router", "RESULT", f"Selected: {targets}")
        return targets, scores

    def _mcp_route(self, requesting_repo, feature_description, top_k, timeout):
        peer_count = sum(1 for n in self._repo_mcp_scripts if n != requesting_repo)
        log("orchestrator_router", "ROUTING", f"Querying {peer_count} peer repos via MCP...")
        scores: dict[str, float] = {}
        for repo_name, script_path in self._repo_mcp_scripts.items():
            if repo_name == requesting_repo:
                log("orchestrator_router", "ROUTING", f"  {repo_name}: excluded (requesting repo)")
                continue
            log("orchestrator_router", "ROUTING",
                f"  Calling {self._repo_display.get(repo_name, repo_name)} MCP → query_repo...")
            response = _mcp_call(script_path, "query_repo", {"feature_request": feature_description}, timeout=timeout)
            score = _score_response(response)
            scores[repo_name] = score
            log("orchestrator_router", "ROUTING",
                f"  {repo_name}: relevance={score:.3f} ({len(response)} chars returned)")
        sorted_repos = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        targets = [n for n, s in sorted_repos[:top_k] if s > 0]
        if len(targets) < top_k:
            for name, _ in sorted_repos:
                if name not in targets:
                    targets.append(name)
                if len(targets) >= top_k:
                    break
        log("orchestrator_router", "RESULT", f"Selected: {targets}")
        return targets, scores
