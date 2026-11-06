#!/usr/bin/env python3
"""
Setup GUI — install and configure the framework in a few clicks. No AI agent or LLM needed.

    python3 setup_gui.py            (Linux)
    py setup_gui.py                 (Windows)

Opens a local page in your browser: install → model → repos → review → configure → verify.

- Runs inside the framework's virtual environment (<framework>/.venv): it creates it with
  the standard library only and re-launches itself inside it. --no-venv opts out.
- Local only: listens on 127.0.0.1, every request needs a random token, and requests for
  any other host name are refused. Standard library only — nothing to install first.
"""

import os
import sys


def _ignore_foreign_pythonpath():
    """Use only this environment's packages. PYTHONPATH/PYTHONHOME set for other tools would be
    searched before the virtual environment, and their libraries (possibly built for another
    Python version) imported instead. Drop them here and for every process started from here;
    only this program's own folder is kept."""
    own = os.path.dirname(os.path.abspath(__file__))
    for var in ("PYTHONPATH", "PYTHONHOME"):
        value = os.environ.pop(var, None)
        if var == "PYTHONPATH" and value:
            foreign = {os.path.abspath(p) for p in value.split(os.pathsep) if p} - {own}
            sys.path[:] = [p for p in sys.path if not p or os.path.abspath(p) not in foreign]


_ignore_foreign_pythonpath()


import argparse
import json
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

for _s in (sys.stdout, sys.stderr):      # Windows consoles/pipes default to cp1252, which cannot print ✓ — … etc.
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

FW = Path(__file__).resolve().parent
GUI = FW / "gui"
sys.path.insert(0, str(GUI))
import gitclone  # noqa: E402  (standard library only)
SCRIPTS = FW / "skills" / "cross-repo-feature-analysis" / "scripts"
sys.path.insert(0, str(FW / "repo-agent" / "mcp"))
from codegraph import settings  # noqa: E402  (standard library only)
ORCH_CFG = FW / "orchestrator" / "mcp" / "config.yaml"


def orch_cfg() -> dict:
    try:
        import yaml
        return yaml.safe_load(ORCH_CFG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def model_dir() -> Path:
    """The model folder in use: `model_path` from the config, or the bundled models/ folder."""
    return settings.model_dir(FW, orch_cfg())
WIN = os.name == "nt"
TOKEN = secrets.token_urlsafe(24)
PORT = 0


# ── venv bootstrap ────────────────────────────────────────────────────────────
def venv_python() -> Path:
    return FW / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")


def _require_supported_os(what: str) -> None:
    """Supported platforms: Windows and Linux. AO_ALLOW_UNSUPPORTED_OS=1 overrides this
    deliberately (e.g. to run the tests elsewhere); a warning is printed."""
    import os
    if sys.platform.startswith("linux") or sys.platform == "win32":
        return
    if os.environ.get("AO_ALLOW_UNSUPPORTED_OS", "").strip().lower() in ("1", "true", "yes"):
        print(f"WARNING: {what} is running on an unsupported platform ({sys.platform}) "
              "because AO_ALLOW_UNSUPPORTED_OS is set.", file=sys.stderr)
        return
    sys.exit(f"ERROR: {what} supports Windows and Linux only (this system: {sys.platform}).")


def ensure_in_venv(no_venv: bool):
    # UTF-8 mode for this process and every child: Windows otherwise reads/writes files and pipes as cp1252
    os.environ["PYTHONUTF8"] = "1"
    if no_venv:
        os.environ["AO_ALLOW_NO_VENV"] = "1"
        if sys.flags.utf8_mode:
            print(f"WARNING: --no-venv: using {sys.executable}", flush=True)
            return
        py = Path(sys.executable)
    else:
        inside = sys.prefix != sys.base_prefix and Path(sys.prefix).resolve() == (FW / ".venv").resolve()
        if inside and sys.flags.utf8_mode:
            return
        py = Path(sys.executable) if inside else venv_python()
        if not py.exists():
            print(f"Creating the virtual environment {FW / '.venv'} ...", flush=True)
            subprocess.run([sys.executable, "-m", "venv", str(FW / ".venv")], check=True)
    try:
        sys.exit(subprocess.call([str(py), str(Path(__file__).resolve()), *sys.argv[1:]]))
    except KeyboardInterrupt:
        sys.exit(0)


# ── jobs: one long-running step at a time, log streamed to the page ──────────
class Job:
    def __init__(self, name):
        self.name, self.lines, self.done, self.ok, self.result = name, [], False, None, None

    def log(self, line):
        self.lines.append(line.rstrip("\n"))


JOBS: dict[str, Job] = {}
JOB_LOCK = threading.Lock()
RUNNING = {"job": None}


def start_job(name, target) -> str:
    with JOB_LOCK:
        cur = RUNNING["job"]
        if cur is not None and not JOBS[cur].done:
            raise RuntimeError(f"'{JOBS[cur].name}' is still running")
        jid = secrets.token_hex(6)
        JOBS[jid] = Job(name)
        RUNNING["job"] = jid

    def run():
        job = JOBS[jid]
        try:
            job.result = target(job)
            job.ok = True if job.ok is None else job.ok
        except SystemExit as e:                  # scripts imported in-process report errors via sys.exit
            job.ok = False
            job.log(f"ERROR: {str(e.code).replace('ERROR: ', '', 1)}" if e.code not in (None, 0) else "ERROR: stopped")
        except Exception as e:
            job.log(f"ERROR: {e}")
            job.ok = False
        finally:
            job.done = True
    threading.Thread(target=run, daemon=True).start()
    return jid


def stream(job: Job, cmd, env=None) -> int:
    job.log("$ " + " ".join(str(c) for c in cmd))
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1", **(env or {})},
                         bufsize=1)
    for line in p.stdout:
        if "Redirects are currently not supported" in line or line.startswith("Loading weights"):
            continue
        job.log(line)
    return p.wait()


# ── query worker: loads the model + graphs once, answers queries (no LLM) ────
class QueryWorker:
    def __init__(self):
        self.proc, self.lock, self.next_id, self.repos = None, threading.Lock(), 1, []

    def _start(self):
        self.proc = subprocess.Popen([sys.executable, str(GUI / "query_worker.py")], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                     encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
        ready = json.loads(self.proc.stdout.readline() or '{"ready": false, "error": "worker exited"}')
        if not ready.get("ready"):
            self.stop()
            raise RuntimeError(ready.get("error", "query worker failed to start"))
        self.repos = ready["repos"]

    def ask(self, mode, text, repo) -> dict:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            rid, self.next_id = self.next_id, self.next_id + 1
            self.proc.stdin.write(json.dumps({"id": rid, "mode": mode, "text": text, "repo": repo}) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                self.stop()
                raise RuntimeError("query worker stopped unexpectedly")
            return json.loads(line)

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass
        self.proc = None


WORKER = QueryWorker()


# ── knowledge-graph tree view (read-only, standard library) ──────────────────
class Graph:
    def __init__(self):
        self.lock, self.view = threading.Lock(), None

    def call(self, fn, *args):
        import graphview
        with self.lock:                          # one sqlite user at a time; requests arrive on many threads
            if self.view is None:
                self.view = graphview.GraphView(configured_repos())
            return getattr(self.view, fn)(*args)

    def reset(self):
        with self.lock:
            if self.view is not None:
                self.view.close()
            self.view = None


GRAPH = Graph()
RESULTS = FW / "results"


def graph_ready() -> bool:
    repos = configured_repos()
    return bool(repos) and all((Path(r["path"]).expanduser() / ".rag_index" / "interfaces.json").exists()
                               for r in repos)


def list_results() -> list:
    if not RESULTS.is_dir():
        return []
    files = sorted(RESULTS.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.name, "size": f.stat().st_size,
             "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime))} for f in files[:200]]


def read_result(name: str) -> str:
    f = (RESULTS / name).resolve()
    if f.parent != RESULTS.resolve() or f.suffix != ".md" or not f.is_file():   # no path traversal
        raise ValueError("no such result")
    return f.read_text(encoding="utf-8")


# ── state ─────────────────────────────────────────────────────────────────────
def deps_ok() -> bool:
    return subprocess.run([sys.executable, "-c", "import mcp, sentence_transformers, tree_sitter, yaml"],
                          capture_output=True).returncode == 0


def configured_repos() -> list:
    cfg = FW / "orchestrator" / "mcp" / "config.yaml"
    try:
        import yaml
        data = yaml.safe_load(cfg.read_text()) or {}
    except Exception:
        return []
    return [{"name": r["name"], "path": r["path"]} for r in data.get("repos") or []
            if not str(r.get("path", "")).startswith("/absolute/path/to/")]


def suggestions() -> list:
    out, seen = [], set()
    for base in (FW.parent, Path.home()):
        try:
            for d in sorted(base.iterdir()):
                if (d.is_dir() and not d.name.startswith(".") and (d / ".git").exists()
                        and d.resolve() != FW and str(d) not in seen):
                    seen.add(str(d))
                    out.append(str(d))
        except OSError:
            continue
    return out[:30]


_MODEL_CHECK: dict = {"key": None, "problem": None}


def model_problem(folder: Path | None = None) -> str | None:
    """None if every model file matches its pinned SHA-256, else what is wrong. The pins are
    read from repo-agent/mcp/repo_rag.py (the one the servers use), never duplicated here.
    Cached by file size and modification time, so the page stays fast."""
    import hashlib
    import re
    src = (FW / "repo-agent" / "mcp" / "repo_rag.py").read_text(encoding="utf-8")
    pins = dict(re.findall(r'"([\w./]+\.(?:json|safetensors))":\s+"([0-9a-f]{64})"', src))
    if not pins:
        return "the pinned fingerprints could not be read"
    MODEL_DIR = Path(folder) if folder is not None else model_dir()
    if not MODEL_DIR.is_dir():
        return f"folder not found: {MODEL_DIR}"
    missing = [f for f in pins if not (MODEL_DIR / f).is_file()]
    if missing:
        return "missing " + ", ".join(missing)
    key = (str(MODEL_DIR.resolve()),) + tuple((f, (MODEL_DIR / f).stat().st_size, (MODEL_DIR / f).stat().st_mtime_ns)
                                              for f in sorted(pins))
    if _MODEL_CHECK["key"] != key:
        def digest(path: Path) -> str:      # JSON: line endings normalised, as in the servers
            data = path.read_bytes()
            return hashlib.sha256(data.replace(b"\r\n", b"\n") if path.suffix == ".json" else data).hexdigest()
        changed = [f for f, h in pins.items() if digest(MODEL_DIR / f) != h]
        _MODEL_CHECK.update(key=key, problem=(", ".join(changed) + " changed (SHA-256 differs)") if changed else None)
    return _MODEL_CHECK["problem"]


def state() -> dict:
    ok = deps_ok()
    problem = model_problem()
    copies = []
    if ok and problem is not None:
        r = subprocess.run([sys.executable, str(SCRIPTS / "configure.py"), "--framework", str(FW), "--find-model"],
                           capture_output=True, text=True)
        copies = [l.strip() for l in r.stdout.splitlines() if l.startswith("  /") or l.startswith("  ~")]
    return {"framework": str(FW), "python": sys.executable, "in_venv": sys.prefix != sys.base_prefix,
            "deps_ok": ok, "model_ok": problem is None, "model_problem": problem, "model_dir": str(model_dir()),
            "model_default_dir": str(settings.model_dir(FW, {})), "model_custom": bool(orch_cfg().get("model_path")),
            "model_copies": copies, "repos": configured_repos(), "suggestions": suggestions(),
            "graph_ready": graph_ready(), "results_dir": str(RESULTS),
            "home": str(Path.home()), "samples": gitclone.SAMPLE_REPOS, "git": gitclone.available()}


# ── actions ───────────────────────────────────────────────────────────────────
def job_install(job):
    rc = stream(job, [sys.executable, "-m", "pip", "install", "--timeout", "60", "--retries", "5",
                      "--require-hashes", "-r", FW / "requirements.lock"])
    if rc != 0:
        raise RuntimeError("installation failed")
    job.log("✓ requirements installed (hash-verified)")


def job_model(body):
    def run(job):
        path = str(body.get("path", "")).strip()
        if not path:
            raise RuntimeError("choose the folder that holds the model")
        # copy only — the GUI never downloads; the copy is checked against the pinned SHA-256
        rc = stream(job, [sys.executable, SCRIPTS / "configure.py", "--framework", FW, "--model-only",
                          "--model-from", path])
        if rc != 0:
            why = next((l for l in reversed(job.lines) if l.startswith("ERROR:")), "")
            raise RuntimeError(why.replace("ERROR: --model-from ", "", 1).replace("ERROR: ", "", 1)
                               or "model setup failed")
    return run


def scan(repos: list) -> list:
    args = [f"{r['name']}={r['path']}" if r.get("name") else r["path"] for r in repos]
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan.py"), "--framework", str(FW), "--json", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "scan failed")
    return json.loads(r.stdout.strip().splitlines()[-1])


def job_configure(body):
    def run(job):
        WORKER.stop()                                   # configuration changes → reload graphs on next query
        GRAPH.reset()
        cmd = [sys.executable, SCRIPTS / "configure.py", "--framework", FW]
        for r in body["repos"]:
            n = r["name"]
            cmd += ["--repo", f"{n}={r['path']}", "--src", f"{n}=."]
            for ext, ft in (r.get("file_types") or {}).items():
                cmd += ["--file-type", f"{n}={ext}:{ft['kind']}" + (f":{ft['parse_as']}" if ft.get("parse_as") else "")]
            for role in r.get("roles") or []:
                cmd += ["--role", f"{n}={role['path']}:{role['role']}" + (f":{role['index']}" if role.get("index") else "")]
        rc = stream(job, cmd)
        if rc != 0:
            raise RuntimeError("configuration failed — see the log")
        job.log("✓ configured")
    return run


# ── GraphRAG creation / query settings ───────────────────────────────────────
def set_yaml_key(text: str, key: str, value) -> str:
    """Replace (or append) a top-level `key: …` in a YAML file, keeping comments and other keys."""
    import re
    import yaml
    if value is None:                                   # remove the key (back to the default)
        return re.sub(rf"(?m)^{re.escape(key)}:.*(?:\n(?:[ \t]+\S.*|-.*))*\n?", "", text)
    line = f"{key}: {yaml.safe_dump(value, default_flow_style=True, sort_keys=False).strip().removesuffix('...').strip()}"
    pattern = re.compile(rf"(?m)^{re.escape(key)}:.*(?:\n(?:[ \t]+\S.*|-.*))*")
    text = pattern.sub(lambda _: line, text, count=1) if pattern.search(text) else text.rstrip("\n") + f"\n{line}\n"
    if (yaml.safe_load(text) or {}).get(key) != value:
        raise ValueError(f"could not update {key!r} safely")
    return text


def repo_cfg_path(r: dict) -> Path:
    return Path(r["path"]).expanduser() / "mcp" / "config.yaml"


def get_settings() -> dict:
    import yaml
    repos = []
    for r in configured_repos():
        p = repo_cfg_path(r)
        rc = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        repos.append({"name": r["name"], "path": r["path"], "values": settings.load(rc),
                      "set": sorted(k for k in rc if k in settings.REPO_SETTINGS)})
    return {"spec": settings.describe(), "router_spec": settings.describe(settings.ROUTER_SETTINGS),
            "repos": repos, "router": settings.load(orch_cfg(), settings.ROUTER_SETTINGS),
            "defaults": settings.defaults(), "router_defaults": settings.defaults(settings.ROUTER_SETTINGS),
            "role_help": settings.ROLE_HELP}


def save_settings(body: dict) -> tuple[int, dict]:
    import yaml
    errors, plans = {}, []
    names = {r["name"]: r for r in configured_repos()}
    for name, values in (body.get("repos") or {}).items():
        if name not in names:
            errors[name] = [f"unknown repository {name!r}"]
            continue
        clean, errs = settings.validate(values)
        if errs:
            errors[name] = errs
        plans.append((names[name], clean))
    rclean, rerrs = settings.validate(body.get("router") or {}, settings.ROUTER_SETTINGS)
    if rerrs:
        errors["router"] = rerrs
    if errors:
        return 400, {"error": "Some values are not valid", "errors": errors}
    rebuild = []
    for r, clean in plans:
        p = repo_cfg_path(r)
        text = p.read_text(encoding="utf-8") if p.exists() else ""
        before = settings.load(yaml.safe_load(text) or {})
        for k, v in clean.items():
            text = set_yaml_key(text, k, v)
        p.write_text(text, encoding="utf-8")
        after = settings.load(yaml.safe_load(text) or {})
        if any(before[k] != after[k] for k in settings.CREATION_KEYS):
            rebuild.append(r["name"])
    if rclean:
        text = ORCH_CFG.read_text(encoding="utf-8")
        for k, v in rclean.items():
            text = set_yaml_key(text, k, v)
        ORCH_CFG.write_text(text, encoding="utf-8")
    WORKER.stop()                                      # the query field picks the new values up
    GRAPH.reset()
    return 200, {"saved": True, "rebuild_needed": rebuild}


def job_model_path(body):
    """Use a model folder in place (text path). Every file is hash-checked first; then the
    VS Code configuration is regenerated so the servers use it. Empty path = the bundled model."""
    def run(job):
        raw = str(body.get("path", "")).strip()
        folder = Path(raw).expanduser().resolve() if raw else settings.model_dir(FW, {})
        problem = model_problem(folder)
        if problem:
            raise RuntimeError(f"{folder}: {problem}")
        text = ORCH_CFG.read_text(encoding="utf-8")
        ORCH_CFG.write_text(set_yaml_key(text, "model_path", str(folder) if raw else None), encoding="utf-8")
        job.log(f"✓ model folder: {folder} (all files match their fingerprints)")
        if configured_repos():
            if stream(job, [sys.executable, FW / "orchestrator" / "setup.py"]) != 0:
                raise RuntimeError("regenerating the VS Code configuration failed")
            job.log("✓ VS Code configuration updated — reload VS Code")
        WORKER.stop()
        GRAPH.reset()
        return {"model_dir": str(folder)}
    return run


def job_verify(job):
    p = subprocess.Popen([sys.executable, str(GUI / "verify.py")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    result = None
    for line in p.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") == "progress":
            job.log(msg["msg"])
        elif msg.get("event") == "result":
            result = msg
    p.wait()
    if not result or not result.get("ok"):
        raise RuntimeError((result or {}).get("error") or "verification failed")
    job.log("✓ GraphRAG created and linked")
    WORKER.stop()                                       # next query loads the fresh graphs
    GRAPH.reset()
    return result


# ── clone a repo from a URL + branch into a folder the user chose ────────────
def job_clone(body):
    def run(job):
        import gitclone
        return gitclone.clone(str(body.get("url", "")), str(body.get("branch", "")), str(body.get("parent", "")),
                              str(body.get("name", "")), log=job.log)
    return run


DRIVES = "::drives"          # Windows: the list of drives above every drive root


def list_dir(path: str) -> dict:
    if WIN and path == DRIVES:
        import string
        drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.isdir(f"{c}:\\")]
        return {"path": "This PC", "parent": None, "git": False, "pickable": False,
                "dirs": [{"name": d, "path": d, "git": False} for d in drives]}
    p = Path(path or Path.home()).expanduser().resolve()
    found = p.is_dir()
    if not found:
        p = Path.home()
    dirs = []
    try:
        entries = sorted(p.iterdir(), key=lambda x: x.name.lower())
    except OSError:
        entries = []
    for d in entries:
        try:
            if d.is_dir() and not d.name.startswith(".") and not d.name.startswith("$"):
                dirs.append({"name": d.name, "path": str(d), "git": (d / ".git").exists()})
        except OSError:                  # unreadable entries (Windows junctions, permissions) are skipped
            continue
    parent = str(p.parent) if p.parent != p else (DRIVES if WIN else None)
    return {"path": str(p), "parent": parent, "dirs": dirs, "git": (p / ".git").exists(), "pickable": True,
            "found": found}


# ── HTTP ──────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                    "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        if not self._host_ok():
            self._send(403, {"error": "forbidden host"})
            return False
        if not secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN):
            self._send(403, {"error": "bad token"})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return None
        if u.path in ("/", "/index.html"):
            if not self._host_ok():
                return self._send(403, {"error": "forbidden host"})
            return self._send(200, (GUI / "index.html").read_bytes(), "text/html; charset=utf-8")
        if not u.path.startswith("/api/") or not self._authorised():
            return None if u.path.startswith("/api/") else self._send(404, {"error": "not found"})
        q = parse_qs(u.query)
        try:
            if u.path == "/api/state":
                return self._send(200, state())
            if u.path == "/api/fs":
                return self._send(200, list_dir(q.get("path", [""])[0]))
            if u.path.startswith("/api/graph/"):
                if not graph_ready():
                    return self._send(409, {"error": "Create the GraphRAG first (step 6)"})
                arg = lambda k: q.get(k, [""])[0]
                what = u.path[len("/api/graph/"):]
                try:
                    if what == "overview":
                        return self._send(200, {"repos": GRAPH.call("overview")})
                    if what == "children":
                        return self._send(200, {"children": GRAPH.call("children", arg("repo"), arg("node"))})
                    if what == "node":
                        return self._send(200, GRAPH.call("detail", arg("repo"), arg("uid")))
                    if what == "find":
                        return self._send(200, {"results": GRAPH.call("find", arg("q")[:200])})
                except ValueError as e:
                    return self._send(404, {"error": str(e)})
                return self._send(404, {"error": "not found"})
            if u.path == "/api/settings":
                return self._send(200, get_settings())
            if u.path == "/api/results":
                return self._send(200, {"results": list_results(), "dir": str(RESULTS)})
            if u.path == "/api/result":
                name = q.get("name", [""])[0]
                try:
                    return self._send(200, {"name": name, "markdown": read_result(name)})
                except (ValueError, OSError):
                    return self._send(404, {"error": "no such result"})
            if u.path.startswith("/api/job/"):
                job = JOBS.get(u.path.rsplit("/", 1)[1])
                if job is None:
                    return self._send(404, {"error": "no such job"})
                since = int(q.get("since", ["0"])[0])
                return self._send(200, {"name": job.name, "done": job.done, "ok": job.ok,
                                        "lines": job.lines[since:], "next": len(job.lines), "result": job.result})
        except Exception as e:
            return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._authorised():
            return
        if (self.headers.get("Content-Type") or "").split(";")[0] != "application/json":
            return self._send(415, {"error": "JSON only"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if u.path == "/api/install":
                return self._send(200, {"job": start_job("Install", job_install)})
            if u.path == "/api/model":
                return self._send(200, {"job": start_job("Model", job_model(body))})
            if u.path == "/api/model_path":
                return self._send(200, {"job": start_job("Model folder", job_model_path(body))})
            if u.path == "/api/settings":
                code, out = save_settings(body)
                return self._send(code, out)
            if u.path == "/api/scan":
                return self._send(200, {"repos": scan(body["repos"])})
            if u.path == "/api/configure":
                return self._send(200, {"job": start_job("Configure", job_configure(body))})
            if u.path == "/api/verify":
                return self._send(200, {"job": start_job("Verify", job_verify)})
            if u.path == "/api/clone":
                return self._send(200, {"job": start_job("Clone", job_clone(body))})
            if u.path == "/api/query":
                if not graph_ready():
                    return self._send(409, {"error": "Create the GraphRAG first (step 6)"})
                mode = body.get("mode", "search")
                if mode not in ("search", "trace", "impact"):
                    return self._send(400, {"error": "unknown query mode"})
                r = WORKER.ask(mode, str(body.get("text", ""))[:2000], str(body.get("repo", "")))
                return self._send(200 if r.get("ok") else 400, r if r.get("ok") else {"error": r.get("error")})
            if u.path == "/api/quit":
                WORKER.stop()
                self._send(200, {"ok": True})
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
                return None
        except Exception as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})


def main():
    global PORT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0, help="local port (default: a free one)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser; just print the address")
    ap.add_argument("--no-venv", action="store_true", help="opt out of the virtual environment")
    args = ap.parse_args()
    _require_supported_os("The setup page")
    ensure_in_venv(args.no_venv)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    PORT = srv.server_address[1]
    url = f"http://127.0.0.1:{PORT}/#t={TOKEN}"
    print(f"\n  Setup GUI: {url}\n  (local only — close this window or press Ctrl+C when you're done)\n", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
