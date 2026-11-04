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
    "extra_extensions":   ([], "creation", "Extra file extensions to index as plain text (e.g. .tmpl)", "exts", None, None),
    "max_file_kb":        (2000, "creation", "Files larger than this are skipped (KB)", "int", 10, 50000),
    "embed_batch_size":   (64, "creation", "Units embedded per batch (higher = faster, more memory)", "int", 1, 512),
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


def defaults(spec=REPO_SETTINGS) -> dict:
    return {k: (dict(v[0]) if isinstance(v[0], dict) else list(v[0]) if isinstance(v[0], list) else v[0])
            for k, v in spec.items()}


def describe(spec=REPO_SETTINGS) -> list[dict]:
    return [{"key": k, "default": v[0], "group": v[1], "label": v[2], "kind": v[3], "min": v[4], "max": v[5]}
            for k, v in spec.items()]


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
    return clean, errors


def load(cfg: dict | None, spec=REPO_SETTINGS) -> dict:
    """Settings from a config dict, with defaults for missing or invalid values."""
    out = defaults(spec)
    given = {k: v for k, v in (cfg or {}).items() if k in spec and v is not None}
    clean, _ = validate(given, spec)
    out.update(clean)
    return out


def rag_kwargs(repo_cfg: dict | None) -> dict:
    """Keyword arguments for RepoRAG(...) that depend on settings — identical in every builder."""
    s = load(repo_cfg)
    return {"top_k": s["top_k"], "budget_tokens": s["result_budget_tokens"], "exclude": s["exclude"],
            "max_file_bytes": s["max_file_kb"] * 1024, "embed_batch_size": s["embed_batch_size"],
            "candidate_pool": s["candidate_pool"], "rrf_k": s["rrf_k"], "role_weights": s["role_weights"],
            "impact_depth": s["impact_depth"]}


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
