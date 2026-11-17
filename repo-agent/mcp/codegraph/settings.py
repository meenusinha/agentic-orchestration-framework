"""GraphRAG creation and query settings: defaults, allowed ranges, validation.

One place for every setting, used by everything that builds or searches a graph (the MCP
servers, the setup page's query worker, step 6 of the setup and the router), so they always
build identical graphs. Per-repository settings live in <repo>/mcp/config.yaml; router
settings in <framework>/orchestrator/mcp/config.yaml. Missing keys take the defaults below.
Standard library only.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

ROLES = ("interface", "implementation", "internal", "library", "docs", "tooling", "example", "external",
         "generated", "build", "config", "test")
DEFAULT_ROLE_WEIGHTS = {"interface": 1.15, "implementation": 1.0, "internal": 1.0, "library": 1.0, "docs": 1.0,
                        "tooling": 0.85, "example": 0.7, "external": 0.6, "generated": 0.5, "build": 0.6,
                        "config": 1.0, "test": 0.0}

# name: (default, group, description, kind, min, max)
REPO_SETTINGS = {
    # ── GraphRAG creation ──
    "src_path":           (".", "creation", "Folders to index (\".\" = the whole repository)", "paths", None, None),
    "knowledge_path":     ("./knowledge", "creation", "Extra documentation folders", "paths", None, None),
    "exclude":            ([], "creation", "Paths never indexed (globs, e.g. build/** or *.generated.c)", "globs", None, None),
    "deferred":           ([], "creation", "Components left for a later batch (not indexed yet)", "globs", None, None),
    "extra_extensions":   ([], "creation", "Extra file extensions to index as plain text (e.g. .tmpl)", "exts", None, None),
    "chunking":           ("graph", "creation", "Chunking method", "choice", ("graph", "sliding", "hybrid"), None),
    "chunk_lines":        (60, "creation", "Chunk size in lines (sliding window, hybrid)", "int", 5, 2000),
    "chunk_overlap":      (15, "creation", "Overlap between chunks in lines", "int", 0, 1000),
    "skip_header_words":  ([], "creation", "Skip the top-of-file comment block if it contains any of these words", "words", None, None),
    "skip_patterns":      ([], "creation", "Text never indexed (regular expressions, one per line)", "regexes", None, None),
    "max_file_kb":        (2000, "creation", "Files larger than this are skipped (KB)", "int", 10, 50000),
    "embed_batch_size":   (64, "creation", "Units embedded per batch (higher = faster, more memory)", "int", 1, 512),
    "workers":            (0, "creation", "Parallel workers for reading and parsing files (0 = automatic, 1 = off)", "int", 0, 64),
    "refresh_seconds":    (60, "creation", "How often a server checks for changed files (seconds)", "int", 10, 3600),
    # ── Queries ──
    "top_k":              (5, "query", "Results per search, per repository", "int", 1, 20),
    "result_budget_tokens": (6000, "query", "Size budget of one answer (tokens ≈ characters ÷ 4)", "int", 500, 100000),
    "candidate_pool":     (60, "query", "Candidates taken from vector and from keyword search", "int", 10, 500),
    "rrf_k":              (60, "query", "Rank-fusion constant (lower = top ranks count more)", "int", 1, 1000),
    "impact_depth":       (2, "query", "Caller levels followed by impact analysis", "int", 1, 5),
    "role_weights":       (DEFAULT_ROLE_WEIGHTS, "query", "Ranking weight per folder role (0 = never returned)", "weights", 0.0, 3.0),
}
ROUTER_SETTINGS = {
    "router_top_repos":     (2, "router", "Repositories the router picks per request", "int", 1, 10),
    "router_hits_per_repo": (6, "router", "Search hits per repository used for routing", "int", 1, 50),
}
CREATION_KEYS = tuple(k for k, v in REPO_SETTINGS.items() if v[1] == "creation")

# Longer explanations, shown when hovering over a setting on the setup page.
HELP = {
    "src_path": "Which folders are parsed into the GraphRAG. \".\" indexes the whole repository; dependency, "
                "build-output, virtual-environment and cache folders are skipped automatically. Give a list (e.g. "
                "src, include, idl) to index only those folders.",
    "knowledge_path": "Folders with extra documentation (Markdown) outside the indexed folders. Markdown inside the "
                      "indexed folders is included anyway. Every document is split into heading sections, each "
                      "stored as one searchable unit.",
    "exclude": "Files and folders that are never indexed, as patterns relative to the repository root: build/** "
               "skips a folder, *.generated.c skips matching files. Excluded code can't be found or linked to.",
    "deferred": "Parts of the repository (folders) not indexed yet, because the GraphRAG is being created in "
                "batches. Step 6 of the setup page fills this in when you choose fewer components for a batch; "
                "remove an entry (or choose it in step 6) to include it in the next build. Empty = everything.",
    "extra_extensions": "File types that aren't recognised automatically but should still be searchable. Their "
                        "files are stored as whole plain-text units (no functions or classes). For code in another "
                        "language, answer the question in the Review step instead.",
    "chunking": "How code is cut into searchable pieces. graph: whole functions, classes, IDL operations and "
                "documentation sections, never cut (default). sliding: every file in chunks of 'Chunk size' lines "
                "overlapping by 'Overlap' lines; results are chunks with their line range, linked to the code they "
                "cover. hybrid: whole units as results, but long units are also searched in overlapping windows, so "
                "a match anywhere in them is found. Calls, IDL links and tests are built in every mode.",
    "chunk_lines": "Size of one chunk (sliding) or window (hybrid), in lines. Smaller chunks match more precisely "
                   "but carry less context. Not used in graph mode.",
    "chunk_overlap": "How many lines neighbouring chunks share, so code at a chunk boundary is still found whole in "
                     "one of them. Must be smaller than the chunk size. Not used in graph mode.",
    "skip_header_words": "Words such as Author, Created, Date, Copyright. If the comment block at the top of a file "
                         "(/* */, //, #, --, %, ; or a Python docstring) contains any of them, the block is never "
                         "indexed. Line numbers stay correct. Use Preview to see which files it affects.",
    "skip_patterns": "Regular expressions, one per line (multi-line mode, ^ and $ match at each line). Every match "
                     "is never indexed — e.g. version-control keyword lines or generated banners. Line numbers stay "
                     "correct. Patterns that could take extremely long, like (a+)+, are refused. Use Preview first.",
    "max_file_kb": "Files larger than this are skipped — usually generated, minified or data files. Raising it "
                   "indexes them, but building takes longer and results get noisier.",
    "embed_batch_size": "How many units are turned into vectors at once while building. Higher is faster but "
                        "uses more memory; lower it if a build runs out of memory. It doesn't change the results.",
    "workers": "How many files are read and parsed at the same time when the GraphRAG is created. 0 picks it "
               "automatically: 8 threads for reading and hashing files, and one worker process per CPU core for "
               "parsing (setup page and command line only, for repositories of 200 files or more). 1 turns it off. "
               "The result is the same either way; only the speed changes. Embedding already uses every core.",
    "refresh_seconds": "How often each running MCP server checks for changed files and updates the graph. Only "
                       "changed units are re-embedded. Lower = fresher results, a little more disk activity.",
    "top_k": "The maximum number of results one search returns from each repository. Copilot can ask for a "
             "different number per request (up to 20).",
    "result_budget_tokens": "The maximum size of one answer (about characters ÷ 4). Results are added whole, best "
                            "first; those that don't fit are listed by name under \"more\" and can be opened "
                            "one by one. Nothing is ever cut off.",
    "candidate_pool": "How many best matches are taken from the meaning-based (vector) search and from the "
                      "keyword search before the two lists are combined. Larger = more thorough, slightly slower.",
    "rrf_k": "Reciprocal-rank fusion: in each list a result scores 1 / (this value + its rank), and the scores are "
             "added. A low value lets the top few of each list dominate; a high value favours results that rank "
             "well in both lists.",
    "impact_depth": "For \"impact of change\": how many levels of callers are followed. 1 = direct callers only, "
                    "2 = their callers too. Deeper finds more indirect effects, with more noise.",
    "role_weights": "Each result's score is multiplied by the weight of its folder role, so that, for example, "
                    "interfaces rank higher and generated or vendored code lower. 0 = never returned.",
    "router_top_repos": "How many other repositories the router selects for a Copilot request, in addition to any "
                        "repository linked through an IDL interface the request touches.",
    "router_hits_per_repo": "How many search hits per repository the router looks at when judging how relevant "
                            "that repository is. Higher = better judgement, slightly slower.",
}
ROLE_HELP = {
    "interface": "Public headers, IDL and API definitions — ranked up by default.",
    "implementation": "The main source code.",
    "internal": "Private implementation details.",
    "library": "Shared libraries built in this repository.",
    "docs": "Documentation sections.",
    "tooling": "Scripts and developer tools.",
    "example": "Examples and samples.",
    "external": "Vendored third-party code (signatures only).",
    "generated": "Code generated from IDL or other sources.",
    "build": "Makefiles, CMake and other build files.",
    "config": "Configuration files.",
    "test": "Tests — 0 means never returned as results; they still appear as \"tested by\" evidence.",
}


def defaults(spec=REPO_SETTINGS) -> dict:
    return {k: (dict(v[0]) if isinstance(v[0], dict) else list(v[0]) if isinstance(v[0], list) else v[0])
            for k, v in spec.items()}


def describe(spec=REPO_SETTINGS) -> list[dict]:
    return [{"key": k, "default": v[0], "group": v[1], "label": v[2], "kind": v[3], "min": v[4], "max": v[5],
             "help": HELP.get(k, v[2])} for k, v in spec.items()]


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def validate(values: dict, spec=REPO_SETTINGS) -> tuple[dict, list[str]]:
    """→ (clean settings for the keys given, list of errors). Unknown keys are errors."""
    clean, errors = {}, []
    for key, value in values.items():
        if key not in spec:
            errors.append(f"unknown setting {key!r}")
            continue
        default, _, label, kind, lo, hi = spec[key]
        try:
            if kind == "int":
                if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value).strip()):
                    raise ValueError("must be a whole number")
                v = int(str(value).strip())
                if not lo <= v <= hi:
                    raise ValueError(f"must be between {lo} and {hi}")
                clean[key] = v
            elif kind == "paths":
                v = _as_list(value)
                if not v:
                    raise ValueError("needs at least one path")
                for p in v:
                    if "\x00" in p or not p.strip():
                        raise ValueError("invalid path")
                clean[key] = v[0] if len(v) == 1 else v
            elif kind == "globs":
                v = [p.strip().replace("\\", "/") for p in _as_list(value) if p.strip()]
                for p in v:
                    if p.startswith("/") or ".." in Path(p).parts or re.match(r"^[A-Za-z]:", p):
                        raise ValueError(f"{p!r}: use a path inside the repository")
                clean[key] = v
            elif kind == "exts":
                v = [e.strip().lower() for e in _as_list(value) if e.strip()]
                for e in v:
                    if not re.fullmatch(r"\.[a-z0-9_+-]{1,15}", e):
                        raise ValueError(f"{e!r} is not a file extension like .tmpl")
                clean[key] = v
            elif kind == "choice":
                v = str(value).strip().lower()
                if v not in lo:
                    raise ValueError(f"must be one of {', '.join(lo)}")
                clean[key] = v
            elif kind == "words":
                from .skiprules import MAX_WORDS, validate_word
                v = [w.strip() for w in _as_list(value) if w.strip()]
                if len(v) > MAX_WORDS:
                    raise ValueError(f"at most {MAX_WORDS} words")
                for w in v:
                    if validate_word(w):
                        raise ValueError(f"{w!r}: {validate_word(w)}")
                clean[key] = v
            elif kind == "regexes":
                from .skiprules import MAX_PATTERNS, validate_pattern
                v = [r for r in _as_list(value) if r.strip()]
                if len(v) > MAX_PATTERNS:
                    raise ValueError(f"at most {MAX_PATTERNS} patterns")
                for r in v:
                    if validate_pattern(r):
                        raise ValueError(f"{r!r}: {validate_pattern(r)}")
                clean[key] = v
            elif kind == "weights":
                if not isinstance(value, dict):
                    raise ValueError("must map roles to numbers")
                v = dict(DEFAULT_ROLE_WEIGHTS)
                for role, w in value.items():
                    if role not in DEFAULT_ROLE_WEIGHTS:
                        raise ValueError(f"unknown role {role!r}")
                    w = float(w)
                    if not lo <= w <= hi:
                        raise ValueError(f"{role}: must be between {lo} and {hi}")
                    v[role] = round(w, 3)
                clean[key] = v
        except (TypeError, ValueError) as e:
            errors.append(f"{label}: {e}")
    if spec is REPO_SETTINGS and ("chunk_lines" in clean or "chunk_overlap" in clean):
        size = clean.get("chunk_lines", values.get("chunk_lines", REPO_SETTINGS["chunk_lines"][0]))
        overlap = clean.get("chunk_overlap", REPO_SETTINGS["chunk_overlap"][0])
        if isinstance(size, int) and overlap >= size:
            errors.append(f"{REPO_SETTINGS['chunk_overlap'][2]}: must be smaller than the chunk size ({size})")
            clean.pop("chunk_overlap", None)
    return clean, errors


def load(cfg: dict | None, spec=REPO_SETTINGS) -> dict:
    """Settings from a config dict, with defaults for missing or invalid values."""
    out = defaults(spec)
    given = {k: v for k, v in (cfg or {}).items() if k in spec and v is not None}
    clean, _ = validate(given, spec)
    out.update(clean)
    return out


def index_kwargs(repo_cfg: dict | None) -> dict:
    """What a build indexes, and how — identical in every builder (and when a running server
    re-reads its settings)."""
    s = load(repo_cfg)
    return {"exclude": s["exclude"] + s["deferred"], "max_file_bytes": s["max_file_kb"] * 1024,
            "chunking": s["chunking"], "chunk_lines": s["chunk_lines"],
            "chunk_overlap": min(s["chunk_overlap"], s["chunk_lines"] - 1),
            "skip_header_words": s["skip_header_words"], "skip_patterns": s["skip_patterns"],
            "io_workers": _io_workers(s["workers"])}


def _io_workers(setting: int) -> int:
    from .parallel import io_workers
    return io_workers(setting)


def parse_workers(repo_cfg: dict | None) -> int:
    """Worker processes for parsing — only for the setup page's and the command line's build."""
    from .parallel import cpu_workers
    return cpu_workers(load(repo_cfg)["workers"])


def rag_kwargs(repo_cfg: dict | None) -> dict:
    """Keyword arguments for RepoRAG(...) that depend on settings — identical in every builder."""
    s = load(repo_cfg)
    return {"top_k": s["top_k"], "budget_tokens": s["result_budget_tokens"], "embed_batch_size": s["embed_batch_size"],
            "candidate_pool": s["candidate_pool"], "rrf_k": s["rrf_k"], "role_weights": s["role_weights"],
            "impact_depth": s["impact_depth"], **index_kwargs(repo_cfg)}


def roots(repo_root, repo_cfg: dict | None) -> list[Path]:
    """Folders to index (src_path + knowledge_path), resolved the same way by every builder.
    Relative paths are inside the repository; entries with * ? [ are expanded as globs."""
    s = load(repo_cfg)
    repo_root = Path(repo_root)
    out = []
    for key in ("src_path", "knowledge_path"):
        for entry in _as_list(s[key]):
            if any(c in entry for c in "*?["):
                out += [Path(m).resolve() for m in sorted(glob.glob(str(repo_root / entry), recursive=True))
                        if Path(m).is_dir()]
            else:
                path = Path(entry)
                out.append(path.resolve() if path.is_absolute() else (repo_root / path).resolve())
    return out


MODEL_DIRNAME = "granite-embedding-small-english-r2"


def model_dir(framework_dir, orch_cfg: dict | None) -> Path:
    """The model folder: `model_path` from the framework's orchestrator config (used in place,
    hash-checked like any copy), or the bundled <framework>/models/ folder by default."""
    custom = str((orch_cfg or {}).get("model_path") or "").strip()
    return Path(custom).expanduser() if custom else Path(framework_dir) / "models" / MODEL_DIRNAME
