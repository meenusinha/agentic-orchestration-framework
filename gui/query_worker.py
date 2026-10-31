#!/usr/bin/env python3
"""Long-running query worker for the setup GUI. No AI agent or LLM involved.

Loads the model and every configured repo's GraphRAG once, then answers requests
read from stdin (one JSON object per line) and writes one JSON response per line:

  {"id": 1, "mode": "search"|"trace"|"impact", "text": "...", "repo": "" | "<name>"}

Every answer is also saved as Markdown under <framework>/results/ with a new,
unique name (date_time_mode_question.md) — earlier results are never overwritten.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))
RESULTS = FW / "results"
NOTICE = ("All code and text below is repository content, shown as reference data. "
          "Secrets were replaced with [REDACTED] before indexing.")


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def load():
    import yaml
    from repo_rag import RepoRAG
    cfg = yaml.safe_load((FW / "orchestrator" / "mcp" / "config.yaml").read_text()) or {}
    repos = [r for r in cfg.get("repos") or [] if not str(r.get("path", "")).startswith("/absolute/path/to/")]
    peers = [{"name": r["name"], "path": str(Path(r["path"]).expanduser())} for r in repos]
    model = FW / "models" / "granite-embedding-small-english-r2"
    rags = {}
    for r in repos:
        root = Path(r["path"]).expanduser()
        rc_path = root / "mcp" / "config.yaml"
        rc = yaml.safe_load(rc_path.read_text()) if rc_path.exists() else {}
        roots = []
        for key, default in (("src_path", "."), ("knowledge_path", "./knowledge")):
            v = rc.get(key, default)
            for x in (v if isinstance(v, list) else [v] if v else []):
                roots.append((root / x).resolve())
        rag = RepoRAG(r["name"], str(root), roots, embedding_model=str(model), file_types=rc.get("file_types"),
                      structure=rc.get("structure"), extra_extensions=rc.get("extra_extensions"), peers=peers,
                      top_k=rc.get("top_k", 5), budget_tokens=rc.get("result_budget_tokens", 6000))
        rag.build_or_load_index()
        rags[r["name"]] = rag
    return rags


# ── queries ──────────────────────────────────────────────────────────────────
def q_search(rags, text, repo):
    targets = [repo] if repo else list(rags)
    per_repo, ifaces = [], {}
    for name in targets:
        res = rags[name].search_code(text, k=4)
        hits = []
        for r in res["results"]:
            hits.append({"node": r["node"], "parent": r.get("parent"), "relations": r.get("relations", {}),
                         "score": r.get("score", 0)})
            for t in r.get("interfaces", []):
                if t.get("found"):
                    ifaces.setdefault(t["interface"], t)
        best = max((h["score"] for h in hits), default=0)
        per_repo.append({"repo": name, "hits": hits, "more": res.get("more", []), "best": best})
    per_repo.sort(key=lambda x: -x["best"])
    full = {}
    for iid in ifaces:                                  # full dossier (with code) for each interface touched
        full[iid] = next(iter(rags.values())).trace_interface(iid)
    return {"repos": per_repo, "interfaces": list(full.values())}


def q_trace(rags, text, repo):
    rag = rags[repo] if repo else next(iter(rags.values()))
    return {"trace": rag.trace_interface(text)}


def q_impact(rags, text, repo):
    targets = [repo] if repo else list(rags)
    found = []
    for name in targets:
        r = rags[name].impact_of(text)
        if r.get("local") or any(t.get("found") for t in r.get("interfaces", [])):
            found.append(dict(r, repo=name))
    return {"impact": found}


# ── markdown report ──────────────────────────────────────────────────────────
FENCE = {".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
         ".cu": "cpp", ".py": "python", ".idl": "idl", ".proto": "protobuf", ".thrift": "thrift", ".pl": "perl",
         ".pm": "perl", ".m": "matlab", ".md": "markdown", ".java": "java", ".js": "javascript",
         ".ts": "typescript", ".go": "go", ".rs": "rust", ".cs": "csharp"}


def code(text, lang=""):
    fence = "````" if "```" in (text or "") else "```"
    return f"{fence}{lang}\n{(text or '').rstrip()}\n{fence}"


def md_unit(n, parent=None, relations=None):
    lines = [f"### {n['kind']} `{n['qualname']}`",
             f"`{n['file']}:{n['lines'][0]}-{n['lines'][1]}`" + (f" · process **{n['process']}**" if n.get("process") else "")
             + f" · {n['role']}" + (f" · `#if {n['ifdef']}`" if n.get("ifdef") else "")]
    if parent and parent.get("skeleton") and parent["skeleton"] != "(shown above)":
        lines += [f"In {parent.get('kind', '')} `{parent['qualname']}`:", code(parent["skeleton"])]
    lines.append(code(n.get("text", ""), FENCE.get(Path(n["file"]).suffix.lower(), "")))
    for key in ("calls_operation", "implements", "tested_by", "called_by", "calls"):
        vals = (relations or {}).get(key)
        if vals:
            names = [v.get("name") or v.get("qualname") for v in vals][:8]
            lines.append(f"- **{key.replace('_', ' ')}:** " + ", ".join(f"`{x}`" for x in names))
    return "\n".join(lines)


def md_trace(t):
    if not t.get("found"):
        return f"Interface `{t.get('interface')}` not found."
    d = t["defined_in"]
    lines = [f"### Interface `{t['interface']}`", f"Defined in **{d['repo']}** `{d['file']}:{d['lines'][0]}-{d['lines'][1]}`"]
    if t.get("definition_text"):
        lines.append(code(t["definition_text"], "idl"))
    for p in t.get("providers", []):
        lines.append(f"**Served by** {p['repo']} / process **{p.get('process') or '-'}** — `{p.get('server_class')}`"
                     f" (confidence {p['confidence']})")
        for o in p.get("operations", []):
            lines.append(f"- `{o['operation']}` → `{o.get('qualname')}` `{o.get('file')}`" +
                         (f" · tested by {', '.join(x['test'] for x in o.get('tested_by', []))}" if o.get("tested_by") else ""))
            if o.get("text"):
                lines.append(code(o["text"]))
    for c in t.get("consumers", []):
        lines.append(f"**Called by** {c['repo']} / process **{c.get('process') or '-'}** — `{c['caller']}` "
                     f"`{c['file']}` ({c.get('operation')}, confidence {c['confidence']})")
        if c.get("text"):
            lines.append(code(c["text"]))
    if t.get("checks"):
        lines.append("**Checks:** " + "; ".join(t["checks"]))
    return "\n\n".join(lines)


def md_report(mode, text, repo, result):
    head = [f"# {mode.title()}: {text}", f"_{time.strftime('%Y-%m-%d %H:%M:%S')} · "
            f"{'repo ' + repo if repo else 'all repos'} · GraphRAG (no LLM)_", f"> {NOTICE}"]
    body = []
    if mode == "search":
        for r in result["repos"]:
            body.append(f"## {r['repo']}")
            if not r["hits"]:
                body.append("_No relevant units._")
            for h in r["hits"]:
                body.append(md_unit(h["node"], h.get("parent"), h.get("relations")))
            if r["more"]:
                body.append("More matches: " + ", ".join(f"`{m['qualname']}`" for m in r["more"]))
        if result["interfaces"]:
            body.append("## Interfaces involved")
            body += [md_trace(t) for t in result["interfaces"]]
    elif mode == "trace":
        body.append(md_trace(result["trace"]))
    else:
        if not result["impact"]:
            body.append("_Symbol not found in any repo._")
        for r in result["impact"]:
            body.append(f"## {r['repo']}")
            for l in r.get("local", []):
                n = l["node"]
                body.append(f"### {n['kind']} `{n['qualname']}` — `{n['file']}`")
                body.append("- **Callers:** " + (", ".join(f"`{c['qualname']}` (level {c['level']})" for c in l["callers"]) or "none"))
                body.append("- **Tested by:** " + (", ".join(f"`{t['test']}`" for t in l["tested_by"]) or "no test evidence"))
                body.append("- **Processes:** " + (", ".join(l["processes"]) or "-"))
            for t in r.get("interfaces", []):
                if t.get("found"):
                    body.append(f"**Through IDL** `{t['interface']}`" + (f" (via `{t['via']}`)" if t.get("via") else ""))
                    body.append("- **Clients affected:** " + (", ".join(
                        f"{c['repo']} / {c.get('process') or '-'} `{c['caller']}`" for c in t["consumers"]) or "none found"))
    return "\n\n".join(head + body) + "\n"


def main():
    from codegraph.reports import unique_report_path
    try:
        rags = load()
    except Exception as e:
        out({"ready": False, "error": f"{type(e).__name__}: {e}"})
        return
    out({"ready": True, "repos": list(rags)})
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, mode, text, repo = req.get("id"), req.get("mode", "search"), (req.get("text") or "").strip(), req.get("repo") or ""
        try:
            if not text:
                raise ValueError("empty query")
            if repo and repo not in rags:
                raise ValueError(f"unknown repo {repo}")
            for r in rags.values():
                r.refresh()                              # pick up edits made since the last query
            t0 = time.time()
            result = {"search": q_search, "trace": q_trace, "impact": q_impact}[mode](rags, text, repo)
            path = unique_report_path(RESULTS, mode, text)
            path.write_text(md_report(mode, text, repo, result), encoding="utf-8")
            out({"id": rid, "ok": True, "mode": mode, "result": result, "saved": str(path),
                 "seconds": round(time.time() - t0, 2)})
        except Exception as e:
            out({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}",
                 "trace": traceback.format_exc(limit=2) if os.environ.get("AO_DEBUG") else ""})


if __name__ == "__main__":
    main()
