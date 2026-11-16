"""GraphRAG operations shared by the setup page (setup_gui.py) and the command line used by the
/graphrag-setup agent skill (.github/skills/graphrag-setup/scripts/graphrag.py), so both behave
exactly the same: connected repositories, validated changes to a repository's settings
(components left for later, excluded folders), and running the gui/ workers.

Standard library only, plus PyYAML where a settings file is read or written.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FW = Path(__file__).resolve().parent.parent
GUI = FW / "gui"
ORCH_CFG = FW / "orchestrator" / "mcp" / "config.yaml"
if str(FW / "repo-agent" / "mcp") not in sys.path:
    sys.path.insert(0, str(FW / "repo-agent" / "mcp"))
from codegraph import components, settings  # noqa: E402  (standard library only)

PLACEHOLDER = "/absolute/path/to/"


# ── connected repositories ───────────────────────────────────────────────────
def orch_cfg() -> dict:
    try:
        import yaml
        return yaml.safe_load(ORCH_CFG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def configured_repos() -> list:
    return [{"name": r["name"], "path": r["path"]} for r in orch_cfg().get("repos") or []
            if not str(r.get("path", "")).startswith(PLACEHOLDER)]


def repo_cfg_path(r: dict) -> Path:
    return Path(r["path"]).expanduser() / "mcp" / "config.yaml"


def repo_cfg(r: dict) -> dict:
    import yaml
    p = repo_cfg_path(r)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


# ── settings files ───────────────────────────────────────────────────────────
def set_yaml_key(text: str, key: str, value) -> str:
    """Replace (or append) a top-level `key: …` in a YAML file, keeping comments and other keys."""
    import yaml
    if value is None:                                   # remove the key (back to the default)
        return re.sub(rf"(?m)^{re.escape(key)}:.*(?:\n(?:[ \t]+\S.*|-.*))*\n?", "", text)
    line = f"{key}: {yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip().removesuffix('...').strip()}"
    pattern = re.compile(rf"(?m)^{re.escape(key)}:.*(?:\n(?:[ \t]+\S.*|-.*))*")
    text = pattern.sub(lambda _: line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    if (yaml.safe_load(text) or {}).get(key) != value:
        raise ValueError(f"could not update {key!r} safely")
    return text


def plan_setting(changes: dict, key: str) -> list:
    """Validate new values of one list setting per repository ({repo: [entries]}).
    → [(repo, config path, key, clean value)] for the repositories whose value changes.
    Nothing is written; see write_settings."""
    import yaml
    repos = {r["name"]: r for r in configured_repos()}
    if not isinstance(changes or {}, dict):
        raise ValueError(f"{key} must map repositories to paths")
    out = []
    for name, entries in (changes or {}).items():
        if name not in repos:
            raise ValueError(f"unknown repository {name!r}")
        if not isinstance(entries, list) or any(not isinstance(x, str) or not x.strip() for x in entries):
            raise ValueError(f"{name}: {key} must be a list of paths")
        clean, errors = settings.validate({key: [x.strip() for x in entries]})
        if errors:
            raise ValueError(f"{name}: {'; '.join(errors)}")
        p = repo_cfg_path(repos[name])
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        if settings.load(yaml.safe_load(text) or {})[key] != clean[key]:
            set_yaml_key(text, key, clean[key])          # refuses values it can't write safely
            out.append((name, p, key, clean[key]))
    return out


def plan_deferred(deferred: dict) -> list:
    """Components left for a later batch, per repository ({repo: [component folders]})."""
    if not isinstance(deferred or {}, dict):
        raise ValueError("deferred must map repositories to folders")
    escaped = {}
    for name, paths in (deferred or {}).items():
        if not isinstance(paths, list) or any(not isinstance(x, str) or not x.strip() for x in paths):
            raise ValueError(f"{name}: components must be folder names")
        escaped[name] = [components.to_setting(x.strip()) for x in paths]
    return plan_setting(escaped, "deferred")


def write_settings(changes: list) -> None:
    for _, p, key, value in changes:
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(set_yaml_key(text, key, value), encoding="utf-8")


def clean_plan(plan) -> dict:
    """Expected seconds per repository from an estimate (weights the progress bar)."""
    names = {r["name"] for r in configured_repos()}
    out = {}
    for name, w in (plan or {}).items() if isinstance(plan, dict) else ():
        try:
            if name in names:
                out[name] = {k: min(max(float(w.get(k, 0)), 0.0), 1e7) for k in ("parse", "embed")}
        except (TypeError, ValueError, AttributeError):
            continue
    return out


# ── estimate arithmetic (the page does the same in JavaScript) ───────────────
LARGE_SECONDS = 15 * 60          # above this, creating the GraphRAG in batches is suggested


def component_seconds(c: dict, rates: dict, fresh: bool = False) -> float:
    """Every file is parsed; only new text is embedded (tests never)."""
    return c["bytes"] * rates["parse"] + (c["embed_bytes_all"] if fresh else c["embed_bytes"]) * rates["embed"]


def total_seconds(est: dict, included, fresh=()) -> float:
    """included(repo, component) → bool. Includes the model load and the linking pass."""
    r = est["rates"]
    secs = r.get("model_load", 0)
    for rp in est["repos"]:
        inc = [c for c in rp["components"] if c["always"] or included(rp["name"], c)]
        secs += sum(component_seconds(c, r, rp["name"] in fresh) for c in inc) + sum(c["bytes"] for c in inc) * r["parse"]
    return secs


def work_plan(est: dict, included, fresh=()) -> dict:
    """Expected parse and embed seconds per repository → weights of the progress bar."""
    r, out = est["rates"], {}
    for rp in est["repos"]:
        inc = [c for c in rp["components"] if c["always"] or included(rp["name"], c)]
        out[rp["name"]] = {"parse": sum(c["bytes"] for c in inc) * r["parse"],
                           "embed": sum(c["embed_bytes_all"] if rp["name"] in fresh else c["embed_bytes"]
                                        for c in inc) * r["embed"]}
    return out


def suggest_batch(est: dict, budget: float = LARGE_SECONDS) -> set:
    """What is already in the GraphRAG stays; then the cheapest new components up to the budget.
    → {(repo, component path)}."""
    r = est["rates"]
    budget -= r.get("model_load", 0)
    chosen, pool = set(), []
    for rp in est["repos"]:
        for c in rp["components"]:
            if c["always"]:
                continue
            s = component_seconds(c, r)
            if c["state"] == "indexed" and not c["deferred"]:
                chosen.add((rp["name"], c["path"]))
                budget -= s
            else:
                pool.append((s, rp["name"], c["path"]))
    for s, name, path in sorted(pool):
        if s <= budget:
            chosen.add((name, path))
            budget -= s
    return chosen


def fmt_duration(s: float) -> str:
    if s < 60:
        return "under a minute"
    if s < 5400:
        return f"about {round(s / 60)} min"
    return f"about {int(s // 3600)} h {round((s % 3600) / 60)} min"


def fmt_bytes(n: float) -> str:
    for unit, size in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= size:
            return f"{n / size:.1f} {unit}" if unit != "KB" else f"{round(n / size)} KB"
    return f"{int(n)} B"


# ── workers ──────────────────────────────────────────────────────────────────
def run_worker(script: str, args=(), on_message=lambda msg: None, on_bar=lambda bar: None) -> dict:
    """Run a gui/ worker that prints JSON lines: progress messages → on_message(text),
    progress-bar events → on_bar(event); returns its result. If the worker dies without a
    result (e.g. out of memory), the end of its error output is reported."""
    errf = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    p = subprocess.Popen([sys.executable, str(GUI / script), *args], stdout=subprocess.PIPE,
                         stderr=errf, text=True, bufsize=1, encoding="utf-8", errors="replace")
    result = None
    for line in p.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") == "progress":
            on_message(msg["msg"])
        elif msg.get("event") == "bar":
            on_bar(msg)
        elif msg.get("event") == "result":
            result = msg
    p.wait()
    try:
        if not result:
            errf.seek(0)
            tail = [l for l in errf.read().splitlines() if l.strip()][-3:]
            raise RuntimeError(f"{script} stopped (exit code {p.returncode})" + (": " + " | ".join(tail) if tail else ""))
    finally:
        errf.close()
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or f"{script} failed")
    return result


def verify_args(fresh=(), plan=None) -> list:
    return [a for n in fresh for a in ("--fresh", n)] + (["--plan", json.dumps(plan)] if plan else [])
